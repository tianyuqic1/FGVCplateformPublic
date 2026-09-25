"""RabbitMQ consumer with lease fencing, heartbeats and idempotent callbacks.

Only notification delivery/callbacks retry. A crashed build becomes failed and
requires the operator's explicit retry; compilation never runs in a request.
"""
import json
import logging
import os
import signal
from dataclasses import asdict
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4, UUID

import pika

from finevision.artifact_store import S3ArtifactStore
from finevision.compute.artifacts import create_s3_client
from finevision.observability import bind_context, configure_logging
from finevision.observability.metrics import start_metrics_server
from finevision.observability.tracing import configure_tracing, linked_message_span

LOG = logging.getLogger(__name__)


class Worker:
    def __init__(self, metrics=None):
        self.metrics = metrics
        self.runtime = os.environ["FINEVISION_INFERENCE_BACKEND"]
        if self.runtime not in ("tensorrt", "ascend_acl"):
            raise ValueError("unknown deployment runtime")
        self.profile = os.environ["FINEVISION_TARGET_PROFILE"]
        self.worker = f"{self.runtime}-{uuid4()}"
        self.token = os.environ["FINEVISION_DEPLOYMENT_TOKEN"]
        if not self.token:
            raise ValueError("deployment token is required")
        self.base = os.environ.get("FINEVISION_CONTROL_PLANE_HTTP", "http://go-control-plane:8000").rstrip("/")

    def callback(self, message, action, **body):
        payload = {"build_token": message["build_token"], "worker_id": self.worker, **body}
        request = urllib.request.Request(f"{self.base}/api/internal/deployments/{message['deployment_id']}/{action}",
                  data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}, method="POST")
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code < 500:
                    raise
            except (TimeoutError, OSError):
                pass
            if attempt < 3:
                time.sleep(2 ** attempt)
        raise RuntimeError(f"{action} callback unavailable; preserving build result")

    def finish(self, message, result):
        try:
            self.callback(message, "complete", **result)
        except urllib.error.HTTPError as error:
            if error.code == 409:
                # Another generation, expired lease or already terminal: never revive it.
                return
            if error.code in (400, 422) and not result.get("error"):
                self.callback(message, "complete", error="产物登记校验被拒绝，请检查构建日志与契约后手动重试")
                return
            raise

    def publish_build_log(self, message, claimed, root):
        path = root / "build.log"
        if not path.is_file():
            return None
        prefix = (
            f"datasets/{claimed['dataset_id']}/versions/{claimed['source']['dataset_version_id']}"
            f"/models/{claimed['model_version_id']}/deployments/{message['deployment_id']}/{message['build_token']}"
        )
        store = S3ArtifactStore(
            client=create_s3_client(),
            bucket=os.environ["FINEVISION_ARTIFACT_BUCKET"],
            prefix=prefix,
        )
        return store.put_file(
            path,
            artifact_id=str(uuid4()),
            artifact_type="deployment_log",
            content_type="text/plain; charset=utf-8",
            producer="deployment-worker/v1",
            dataset_version_id=claimed["source"]["dataset_version_id"],
            training_run_id=claimed["source"].get("training_run_id"),
            metadata={"deployment_id": message["deployment_id"], "build_token": message["build_token"]},
        )

    def consume(self, connection, channel, method, body):
        try:
            message = json.loads(body)
            UUID(message["deployment_id"])
            UUID(message["build_token"])
        except (ValueError, KeyError, TypeError, AttributeError):
            channel.basic_reject(method.delivery_tag, requeue=False)
            return
        try:
            claimed = self.callback(message, "claim", runtime=self.runtime, target_profile=self.profile)["deployment"]
        except urllib.error.HTTPError as error:
            if error.code == 409:  # duplicate or obsolete delivery
                channel.basic_ack(method.delivery_tag)
                return
            raise
        root = Path(os.environ.get("FINEVISION_BUILD_ROOT", "/data/builds")) / message["deployment_id"] / message["build_token"]
        root.mkdir(parents=True, exist_ok=True)
        outcome = root / "outcome.json"
        if outcome.exists():
            self.finish(message, json.loads(outcome.read_text()))
            channel.basic_ack(method.delivery_tag)
            return
        # A disconnected callback must not compile a completed engine again.
        if (root / "result.json").exists():
            self.finish(message, json.loads((root / "result.json").read_text()))
            channel.basic_ack(method.delivery_tag)
            return
        job = root / "job.json"
        job.write_text(json.dumps(claimed))
        started = last_heartbeat = time.monotonic()
        outcome_name = "failed"
        if self.metrics:
            self.metrics.set_inflight(kind="deployment_build", value=1)
        with (root / "build.log").open("a") as log:
            process = subprocess.Popen([sys.executable, "-m", "finevision.compute.deployment_build", str(job)], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while process.poll() is None:
                    connection.process_data_events(time_limit=1)
                    if time.monotonic() - last_heartbeat >= 20:
                        self.callback(message, "heartbeat")
                        last_heartbeat = time.monotonic()
                    if time.monotonic() - started > 2400:
                        raise TimeoutError("deployment build exceeded 40 minute limit")
                if process.returncode != 0:
                    result = {"error": f"构建/数值校验失败（exit {process.returncode}），查看 build.log：{message['deployment_id']}/{message['build_token']}"}
                else:
                    result = json.loads((root / "result.json").read_text())
                try:
                    diagnostic = self.publish_build_log(message, claimed, root)
                    if diagnostic is not None:
                        result.setdefault("validation", {})["diagnostic_log"] = asdict(diagnostic)
                except Exception as error:
                    LOG.warning("Build log upload failed", extra={"event": "deployment_log_upload_failed", "error_type": type(error).__name__, "outcome": "failed"})
                temporary = root / "outcome.tmp"
                temporary.write_text(json.dumps(result))
                temporary.replace(outcome)
                self.finish(message, result)
                channel.basic_ack(method.delivery_tag)
                outcome_name = "success" if not result.get("error") else "failed"
            except Exception:
                if not outcome.exists() and not (root / "result.json").exists():
                    outcome.write_text(json.dumps({"error": "构建被中断或超时；请检查构建日志后手动重试"}))
                raise
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                if self.metrics:
                    self.metrics.set_inflight(kind="deployment_build", value=0)
                    self.metrics.record_work(kind="deployment_build", outcome=outcome_name, duration_seconds=time.monotonic() - started)

    def run(self):
        while True:
            connection = None
            try:
                params = pika.URLParameters(os.environ["FINEVISION_RABBITMQ_URL"])
                params.heartbeat = 180
                params.blocked_connection_timeout = 30
                connection = pika.BlockingConnection(params)
                channel = connection.channel()
                queue = f"fgvc.deployment.{self.runtime}"
                channel.queue_declare(queue=queue, durable=True, arguments={"x-dead-letter-exchange": "fgvc.deployment.dlx"})
                channel.basic_qos(prefetch_count=1)
                for method, properties, body in channel.consume(queue, inactivity_timeout=1):
                    if method is not None:
                        with linked_message_span("deployment.delivery", getattr(properties, "headers", None)):
                            self.consume(connection, channel, method, body)
            except Exception as error:
                LOG.error("build consumer disconnected (%s); unconfirmed results preserved", type(error).__name__)
                time.sleep(5)
            finally:
                if connection is not None and connection.is_open:
                    connection.close()


if __name__ == "__main__":
    configure_logging("deployment-worker", force=True)
    configure_tracing("deployment-worker")
    Worker(start_metrics_server("deployment-worker", 9305)).run()
