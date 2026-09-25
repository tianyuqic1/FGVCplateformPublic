from __future__ import annotations

import hashlib
import json
import logging
import os
from finevision.compute.hardware_collector import worker_identity
import threading
import time
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct
from google.protobuf.timestamp_pb2 import Timestamp
import pika

from finevision.persistence.store import MetadataStore
from finevision.artifact_store import ArtifactDescriptor, ArtifactStore, S3ArtifactStore
from finevision.compute.v1 import artifact_pb2, training_lifecycle_pb2, training_lifecycle_pb2_grpc
from finevision.compute.pretrained_weights import MANAGED_WEIGHTS, WEIGHT_ALIASES, prepare_managed_weight
from finevision.worker.jobs import TrainingRunStopped, _run_train_classifier
from finevision.observability import bind_context, configure_logging
from finevision.observability.metrics import RuntimeMetrics, start_metrics_server
from finevision.observability.tracing import configure_tracing, observed_message_callback


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchMessage:
    message_id: str
    event_type: str
    schema_version: int
    job_id: str
    dispatch_generation: int
    occurred_at: str


def parse_dispatch_message(body: bytes) -> DispatchMessage:
    payload = json.loads(body)
    allowed = {"message_id", "event_type", "schema_version", "job_id", "dispatch_generation", "occurred_at"}
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise ValueError("dispatch message must contain only the versioned stable identifier fields")
    if payload.get("schema_version") != 1 or payload.get("event_type") not in {
        "training.job.ready",
        "training.job.ready.v1",
    }:
        raise ValueError("unsupported dispatch schema")
    UUID(str(payload["message_id"]))
    UUID(str(payload["job_id"]))
    generation = int(payload["dispatch_generation"])
    if generation < 1:
        raise ValueError("dispatch_generation must be positive")
    datetime.fromisoformat(str(payload["occurred_at"]).replace("Z", "+00:00"))
    return DispatchMessage(
        message_id=str(payload["message_id"]),
        event_type=str(payload["event_type"]),
        schema_version=1,
        job_id=str(payload["job_id"]),
        dispatch_generation=generation,
        occurred_at=str(payload["occurred_at"]),
    )


class LifecycleStub(Protocol):
    def Heartbeat(self, request: object, timeout: float) -> object: ...  # noqa: N802
    def ReportProgress(self, request: object, timeout: float) -> object: ...  # noqa: N802
    def Complete(self, request: object, timeout: float) -> object: ...  # noqa: N802
    def Fail(self, request: object, timeout: float) -> object: ...  # noqa: N802


class RemoteTrainingStore:
    """Adapts the existing numerical pipeline to the Go-owned lifecycle contract."""

    def __init__(
        self,
        *,
        lifecycle: LifecycleStub,
        artifact_store: ArtifactStore,
        job_id: str,
        training_run_id: str,
        dataset_version_id: str,
        attempt_id: str,
        execution_epoch: int,
        dataset_id: str = "",
        rpc_timeout: float = 10.0,
    ) -> None:
        self.lifecycle = lifecycle
        self.artifact_store = artifact_store
        self.job_id = job_id
        self.training_run_id = training_run_id
        self.dataset_version_id = dataset_version_id
        self.attempt_id = attempt_id
        self.execution_epoch = execution_epoch
        self.dataset_id = dataset_id
        self.rpc_timeout = rpc_timeout
        self.directive = "continue"
        self.failed = False

    def mark_running(self, run_id: str) -> None:
        self._check_run(run_id)

    def mark_failed(self, run_id: str, error: str) -> None:
        self._check_run(run_id)
        if self.failed:
            return
        self.lifecycle.Fail(
            training_lifecycle_pb2.FailRequest(
                job_id=self.job_id,
                attempt_id=self.attempt_id,
                execution_epoch=self.execution_epoch,
                retryable=_is_retryable(error),
                error_code="COMPUTE_FAILED",
                error_message=str(error)[:1000],
                request_id=str(uuid4()),
            ),
            timeout=self.rpc_timeout,
        )
        self.failed = True

    def get_status(self, run_id: str) -> str:
        self._check_run(run_id)
        if self.directive == "pause":
            return "paused"
        if self.directive in {"cancel", "fenced"}:
            return "cancelled"
        return "running"

    def heartbeat(self) -> str:
        response = self.lifecycle.Heartbeat(
            training_lifecycle_pb2.HeartbeatRequest(
                job_id=self.job_id,
                attempt_id=self.attempt_id,
                execution_epoch=self.execution_epoch,
                request_id=str(uuid4()),
            ),
            timeout=self.rpc_timeout,
        )
        self.directive = str(response.directive)
        return self.directive

    def update_progress(self, run_id: str, progress: dict[str, Any]) -> None:
        self._check_run(run_id)
        progress_message = Struct()
        progress_message.update(progress)
        metric_points = []
        latest_metrics = progress.get("latest_metrics")
        if isinstance(latest_metrics, dict) and "epoch" in latest_metrics:
            step = int(latest_metrics["epoch"])
            for name, split in (("train_loss", "train"), ("eval_accuracy", "validation")):
                if name in latest_metrics:
                    context = Struct()
                    context.update({"split": split, "phase": progress.get("current_stage", "head")})
                    metric_points.append(
                        training_lifecycle_pb2.MetricPoint(
                            name=name,
                            step=step,
                            value=float(latest_metrics[name]),
                            context=context,
                        )
                    )
        self.lifecycle.ReportProgress(
            training_lifecycle_pb2.ProgressRequest(
                job_id=self.job_id,
                attempt_id=self.attempt_id,
                execution_epoch=self.execution_epoch,
                progress=progress_message,
                request_id=str(uuid4()),
                metric_points=metric_points,
            ),
            timeout=self.rpc_timeout,
        )

    def find_feature_artifact(
        self,
        dataset_version_id: str,
        backbone_id: str,
        extractor_config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        # Phase-one cache lookup is exposed by Artifact Registry in a later read RPC.
        return None

    def complete_training_run(
        self,
        *,
        run_id: str,
        artifact_root: Path,
        feature_artifact: Any,
        model_artifact: Any,
        training_report: Any,
        calibration_report: Any,
        threshold_sweep: Any,
        threshold_strategy: Any,
    ) -> SimpleNamespace:
        self._check_run(run_id)
        model_directory = Path(artifact_root) / "models" / str(model_artifact.artifact_id)
        candidates = [
            ("features", Path(feature_artifact.features_path), "application/octet-stream"),
            ("model", Path(model_artifact.model_path), "application/octet-stream"),
            ("report", model_directory / "training_report.json", "application/json"),
            ("calibration", model_directory / "calibration_report.json", "application/json"),
            ("threshold_sweep", model_directory / "threshold_sweep.json", "application/json"),
            ("threshold_strategy", model_directory / "threshold_strategy.json", "application/json"),
        ]
        descriptors: list[ArtifactDescriptor] = []
        for artifact_type, path, content_type in candidates:
            if not path.is_file():
                continue
            descriptors.append(
                self.artifact_store.put_file(
                    path,
                    artifact_id=str(uuid4()),
                    artifact_type=artifact_type,
                    content_type=content_type,
                    producer="python-training-worker/v1",
                    dataset_version_id=self.dataset_version_id,
                    training_run_id=self.training_run_id,
                    attempt_id=self.attempt_id,
                    metadata={"source_name": path.name},
                )
            )

        # Publish one verified, self-contained descriptor document for the
        # inference runtime. It contains no raw bytes and all referenced objects
        # remain content-addressed artifacts.
        by_type = {item.artifact_type: item for item in descriptors}
        image_model = model_artifact.head_type == "image_classifier_v2"
        if "model" not in by_type or (not image_model and "features" not in by_type):
            raise ValueError("training completion requires model and feature artifacts")
        bundle_path = model_directory / "model_bundle.json"
        bundle_path.write_text(
            json.dumps(
                {
                    "schema_version": 2 if image_model else 1,
                    "model_format": "image_classifier_v2" if image_model else "linear_head_v1",
                    "model": by_type["model"].__dict__,
                    **({"features": by_type["features"].__dict__} if not image_model else {}),
                    "model_artifact": _jsonable(model_artifact),
                    "threshold_strategy": _jsonable(threshold_strategy),
                    "extractor_config": _jsonable(feature_artifact.extractor_config),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        descriptors.append(
            self.artifact_store.put_file(
                bundle_path,
                artifact_id=str(uuid4()),
                artifact_type="model_bundle",
                content_type="application/json",
                producer="python-training-worker/v1",
                dataset_version_id=self.dataset_version_id,
                training_run_id=self.training_run_id,
                attempt_id=self.attempt_id,
                metadata={"schema_version": 1},
            )
        )
        metrics = {
            "accuracy": float(training_report.evaluation.accuracy),
            "macro_f1": float(training_report.evaluation.macro_f1),
            "expected_coverage": float(threshold_strategy.expected_coverage),
            "expected_selective_risk": float(threshold_strategy.expected_selective_risk),
            "accept_threshold": float(threshold_strategy.accept_threshold),
            "margin_threshold": float(threshold_strategy.margin_threshold),
        }
        metrics_message = Struct()
        metrics_message.update(metrics)
        digest_input = json.dumps(
            [{"uri": item.uri, "sha256": item.sha256, "size_bytes": item.size_bytes} for item in descriptors],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        response = self.lifecycle.Complete(
            training_lifecycle_pb2.CompleteRequest(
                job_id=self.job_id,
                attempt_id=self.attempt_id,
                execution_epoch=self.execution_epoch,
                completion_key=f"{self.job_id}:{self.attempt_id}:complete-v1",
                result_digest=hashlib.sha256(digest_input).hexdigest(),
                artifacts=[_descriptor_message(item) for item in descriptors],
                metrics=metrics_message,
                request_id=str(uuid4()),
            ),
            timeout=self.rpc_timeout,
        )
        artifact_ids = {item.artifact_type: item.artifact_id for item in descriptors}
        return SimpleNamespace(
            run_id=response.training_run_id,
            dataset_id=self.dataset_id,
            dataset_version_id=self.dataset_version_id,
            feature_artifact_id=artifact_ids.get("features"),
            model_artifact_id=artifact_ids.get("model"),
            model_version_id=response.model_version_id,
            report_artifact_id=artifact_ids.get("report"),
            calibration_artifact_id=artifact_ids.get("calibration"),
            threshold_strategy_artifact_id=artifact_ids.get("threshold_strategy"),
            metrics=metrics,
        )

    def _check_run(self, run_id: str) -> None:
        if run_id != self.training_run_id:
            raise ValueError("training run does not match claimed job")


class HeartbeatLoop:
    def __init__(self, store: RemoteTrainingStore, interval_seconds: float = 30.0) -> None:
        self.store = store
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="training-heartbeat", daemon=True)

    def __enter__(self) -> "HeartbeatLoop":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval_seconds + 1)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                directive = self.store.heartbeat()
                if directive != "continue":
                    return
            except grpc.RpcError:
                # Lease expiry and the reaper provide recovery if connectivity is not restored.
                continue


def _descriptor_message(descriptor: ArtifactDescriptor) -> artifact_pb2.ArtifactDescriptor:
    created_at, verified_at = Timestamp(), Timestamp()
    created_at.FromDatetime(datetime.fromisoformat(descriptor.created_at))
    if descriptor.verified_at:
        verified_at.FromDatetime(datetime.fromisoformat(descriptor.verified_at))
    metadata = Struct()
    metadata.update(descriptor.metadata)
    return artifact_pb2.ArtifactDescriptor(
        artifact_id=descriptor.artifact_id,
        artifact_type=descriptor.artifact_type,
        uri=descriptor.uri,
        sha256=descriptor.sha256,
        size_bytes=descriptor.size_bytes,
        content_type=descriptor.content_type,
        storage_version=descriptor.storage_version,
        producer=descriptor.producer,
        dataset_version_id=descriptor.dataset_version_id or "",
        training_run_id=descriptor.training_run_id or "",
        attempt_id=descriptor.attempt_id or "",
        schema_version=descriptor.schema_version,
        created_at=created_at,
        verified_at=verified_at,
        metadata=metadata,
    )


def _is_retryable(error: str) -> bool:
    lowered = error.lower()
    return any(token in lowered for token in ("timeout", "temporarily", "connection", "unavailable"))


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__") or hasattr(value, "__dict__"):
        return {key: _jsonable(item) for key, item in vars(value).items() if not key.startswith("_")}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def create_s3_artifact_store() -> S3ArtifactStore:
    endpoint = os.environ["FINEVISION_S3_ENDPOINT"]
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.environ.get("FINEVISION_S3_REGION", "us-east-1"),
        aws_access_key_id=os.environ["FINEVISION_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["FINEVISION_S3_SECRET_KEY"],
        config=Config(s3={"addressing_style": "path"}),
    )
    return S3ArtifactStore(
        client=client,
        bucket=os.environ.get("FINEVISION_ARTIFACT_BUCKET", "finevision-artifacts"),
        prefix="compute",
    )


class QueueTrainingWorker:
    def __init__(self, lifecycle: Any, artifact_store: ArtifactStore, channel: Any, queue: str, metrics: RuntimeMetrics | None = None) -> None:
        self.lifecycle = lifecycle
        self.artifact_store = artifact_store
        self.channel = channel
        self.queue = queue
        self.metrics = metrics

    def run(self) -> None:
        self.channel.basic_qos(prefetch_count=1)
        self.channel.basic_consume(queue=self.queue, on_message_callback=observed_message_callback("training.delivery", self._on_message), auto_ack=False)
        self.channel.start_consuming()

    def _on_message(self, channel: Any, method: Any, _properties: Any, body: bytes) -> None:
        try:
            dispatch = parse_dispatch_message(body)
        except (ValueError, TypeError, json.JSONDecodeError):
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        try:
            claim = self.lifecycle.Claim(
                training_lifecycle_pb2.ClaimRequest(
                    job_id=dispatch.job_id,
                    dispatch_generation=dispatch.dispatch_generation,
                    worker_id=worker_identity(),
                    request_id=str(uuid4()),
                ),
                timeout=10.0,
            )
        except grpc.RpcError:
            time.sleep(1.0)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return
        if claim.disposition != "claimed":
            LOG.info("Training delivery was not claimed", extra={"event": "training_delivery_skipped", "job_id": dispatch.job_id, "message_id": dispatch.message_id, "outcome": claim.disposition})
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return
        channel.basic_ack(delivery_tag=method.delivery_tag)
        payload = MessageToDict(claim.payload, preserving_proto_field_name=True)
        payload["training_run_id"] = claim.training_run_id
        remote_store = RemoteTrainingStore(
            lifecycle=self.lifecycle,
            artifact_store=self.artifact_store,
            job_id=dispatch.job_id,
            training_run_id=claim.training_run_id,
            dataset_id=str(payload.get("dataset_id", "")),
            dataset_version_id=str(payload.get("dataset_version_id", "")),
            attempt_id=claim.attempt_id,
            execution_epoch=claim.execution_epoch,
        )
        with bind_context(
            message_id=dispatch.message_id,
            job_id=dispatch.job_id,
            training_run_id=claim.training_run_id,
            attempt_id=claim.attempt_id,
            execution_epoch=claim.execution_epoch,
            worker_id=worker_identity(),
            dataset_version_id=str(payload.get("dataset_version_id", "")),
        ):
            started = time.monotonic()
            outcome = "success"
            if self.metrics:
                self.metrics.set_inflight(kind="training", value=1)
            LOG.info("Training attempt started", extra={"event": "training_attempt_started", "outcome": "running"})
            metadata_store = MetadataStore(os.environ.get("FINEVISION_METADATA_DIR", ".finevision/metadata"))
            dataset_files = ExitStack()
            try:
                dataset_files.enter_context(HeartbeatLoop(remote_store))
                if payload.get("dataset_archive"):
                    from finevision.compute.artifacts import descriptor_from_dict
                    from finevision.compute.dataset_compute import unpack_dataset
                    from finevision.ml_toolkit.datasets import scan_imagefolder
                    descriptor = descriptor_from_dict(payload["dataset_archive"])
                    if descriptor.dataset_version_id != payload["dataset_version_id"]:
                        raise ValueError("Dataset archive scope mismatch")
                    local_archive = self.artifact_store.materialize_verified(descriptor, os.environ.get("FINEVISION_ARTIFACT_CACHE_DIR", "/data/cache"))
                    root = Path(dataset_files.enter_context(tempfile.TemporaryDirectory(prefix="finevision-training-data-")))
                    unpack_dataset(local_archive, root)
                    manifest = scan_imagefolder(root, str(payload["dataset_id"]), str(payload["dataset_version_id"]))
                    metadata_store = SimpleNamespace(get_dataset_version=lambda version: manifest if version == manifest.dataset_version_id else None)
                backbone_key = str(payload.get("backbone_id") or payload.get("extractor") or "")
                backbone_key = WEIGHT_ALIASES.get(backbone_key, backbone_key)
                if backbone_key in MANAGED_WEIGHTS:
                    prepare_managed_weight(
                        self.artifact_store,
                        os.environ.get("FINEVISION_ARTIFACT_CACHE_DIR", "/data/cache"),
                        backbone_key,
                    )
                _run_train_classifier(payload, metadata_store, remote_store)
                LOG.info("Training attempt completed", extra={"event": "training_attempt_completed", "outcome": "success"})
            except TrainingRunStopped:
                outcome = "cancelled"
                LOG.warning("Training attempt stopped", extra={"event": "training_attempt_stopped", "outcome": "cancelled"})
                return
            except Exception as error:  # The lifecycle RPC is the error persistence boundary.
                outcome = "failed"
                LOG.exception("Training attempt failed", extra={"event": "training_attempt_failed", "outcome": "failed", "error_type": type(error).__name__})
                if not remote_store.failed:
                    try:
                        remote_store.mark_failed(claim.training_run_id, str(error))
                    except grpc.RpcError:
                        LOG.exception("Training failure callback failed", extra={"event": "training_failure_callback_failed", "outcome": "unknown"})
            finally:
                dataset_files.close()
                if self.metrics:
                    self.metrics.set_inflight(kind="training", value=0)
                    self.metrics.record_work(kind="training", outcome=outcome, duration_seconds=time.monotonic() - started)


def main() -> None:
    configure_logging("python-training-worker", force=True)
    trace_provider = configure_tracing("python-training-worker")
    metrics = start_metrics_server("python-training-worker", 9301)
    LOG.info("Training worker starting", extra={"event": "worker_starting"})
    rabbit = pika.BlockingConnection(pika.URLParameters(os.environ["FINEVISION_RABBITMQ_URL"]))
    channel = rabbit.channel()
    grpc_channel = grpc.insecure_channel(os.environ.get("FINEVISION_CONTROL_PLANE_GRPC", "go-control-plane:9000"))
    lifecycle = training_lifecycle_pb2_grpc.TrainingLifecycleStub(grpc_channel)
    artifact_store = create_s3_artifact_store()
    worker = QueueTrainingWorker(
        lifecycle,
        artifact_store,
        channel,
        os.environ.get("FINEVISION_TRAINING_QUEUE", "finevision.training.v1"),
        metrics,
    )
    try:
        worker.run()
    finally:
        grpc_channel.close()
        if rabbit.is_open:
            rabbit.close()
        if trace_provider is not None:
            trace_provider.shutdown()


if __name__ == "__main__":
    main()
