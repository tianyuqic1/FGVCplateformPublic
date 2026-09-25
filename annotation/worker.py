"""Single-owner low-resource annotation worker. No automatic paid-call replay."""
import argparse
import fcntl
import hashlib
import json
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from lab_v4.engine import ConfigurationBlocked, RetryDeferred
from lab_v4.images import atomic_json, sha_file
from lab_v4.persistent_provider import Provider
from lab_v4.state import UnknownOutcome
from workflow import Workflow
from finevision.observability import configure_logging
from finevision.observability.metrics import start_metrics_server
from finevision.observability.tracing import configure_tracing

LOG = logging.getLogger("annotation-worker")
OBSERVABLE_TOOLS = {"none", "lowlight", "deblur", "denoise", "sr_x2"}


class API:
    def __init__(self, base, token):
        self.base, self.token = base.rstrip("/"), token
    def request(self, path, data=None, binary=False):
        request = urllib.request.Request(self.base + "/api/annotation" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as r:
            limit = 20 * 1024 * 1024 if binary else 2 * 1024 * 1024
            raw = r.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("API response exceeds limit")
            return raw if binary else (json.loads(raw) if raw else None)


def download(api, task, root):
    root.mkdir(parents=True, exist_ok=True)
    path = root / task["id"]
    if not path.exists():
        data = api.request("/tasks/" + task["id"] + "/image", binary=True)
        if len(data) != task["image"]["size_bytes"] or hashlib.sha256(data).hexdigest() != task["sha"]:
            raise ValueError("image integrity mismatch")
        temporary = path.with_suffix(".part")
        with temporary.open("wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    if sha_file(path) != task["sha"]:
        raise ValueError("cached image changed")
    return path


def prediction(workflow, task, path):
    deadline = time.monotonic() + 900
    while True:
        try:
            result = workflow.predict(task, path)
            return {"status": "suggested" if result["status"] == "ok" else "failed",
                "result": result, "error": "" if result["status"] == "ok" else "候选不完整，可人工标注"}
        except RetryDeferred as e:
            delay = max(1, e.until - time.time())
            if time.monotonic() + delay >= deadline:
                return {"status": "failed", "result": {}, "error": "供应商冷却超过本次预算，可人工标注"}
            time.sleep(min(delay, 10))
        except UnknownOutcome:
            return {"status": "unknown", "result": {}, "error": "供应商结果未知，已隔离；不会自动重复计费，可人工标注"}
        except ConfigurationBlocked:
            return {"status": "failed", "result": {}, "error": "模型配置或预算阻塞，请检查服务；可人工标注"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--models", type=Path, required=True)
    p.add_argument("--sam", type=Path, required=True)
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--locator", default="http://127.0.0.1:8087/v1")
    p.add_argument("--api", default="http://127.0.0.1:8001")
    p.add_argument("--qdrant", default="http://127.0.0.1:6335")
    args = p.parse_args()
    metrics = start_metrics_server("annotation-worker", 9306)
    args.root = args.root.resolve()
    args.root.mkdir(parents=True, exist_ok=True)
    lock = (args.root / "worker.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    api = API(args.api, os.environ["ANNOTATION_WORKER_TOKEN"])
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    details = {"profile": "cpu-low", "concurrency": 1, "sam": "CPU · 按需加载",
               "vlm": "Qwen 4B GGUF · CPU · 单并发", "active_task": None}
    def heartbeat():
        while not stop.is_set():
            try:
                api.request("/internal/heartbeat", details.copy())
            except (OSError, ValueError):
                LOG.warning("heartbeat unavailable")
            stop.wait(10)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    with Provider(args.binary, os.environ["VLM_BASE_URL"], os.environ["VLM_MODEL"],
        os.environ["VLM_API_KEY"], allow_remote=True, timeout=120, stream=True,
        concurrency=1, max_concurrency=1) as provider:
        while not stop.is_set():
            workflow = None
            try:
                # Replay only the persisted completion ACK, never the model call.
                for saved in sorted(args.root.glob("result-*.json")):
                    payload = json.loads(saved.read_text())
                    try:
                        api.request("/internal/tasks/" + payload["id"] + "/result", payload["body"])
                    except urllib.error.HTTPError as e:
                        if e.code != 409:
                            raise
                        LOG.warning("result quarantined after lease expiry: %s", payload["id"])
                    saved.rename(saved.with_suffix(".acknowledged"))
                job = api.request("/internal/memory")
                indexing = job is not None
                if job is None:
                    job = api.request("/internal/claim", {})
                if job is None:
                    stop.wait(3)
                    continue
                task, project = job["task"], job["project"]
                details["active_task"] = task["id"]
                task_started = time.monotonic()
                task_outcome = "success"
                metrics.set_inflight(kind="annotation", value=1)
                LOG.info("%s %s", "index" if indexing else "predict", task["id"])
                result = None
                try:
                    path = download(api, task, args.root / "images")
                    workflow = Workflow(args.root / project["id"], project, args.models,
                        args.locator, args.sam, provider, args.qdrant)
                    if indexing:
                        workflow.confirm(task, path)
                        api.request("/internal/tasks/" + task["id"] + "/indexed", {"ok": True, "error": ""})
                    else:
                        result = prediction(workflow, task, path)
                except Exception as e:
                    task_outcome = "failed"
                    # Persist only a safe category, not HTTP bodies, secrets or image paths.
                    LOG.warning("task %s failed: %s", task["id"], type(e).__name__)
                    if indexing:
                        api.request("/internal/tasks/" + task["id"] + "/indexed",
                            {"ok": False, "error": "向量入库暂未完成，保留人工标签，60 秒后补偿"})
                    else:
                        # Unexpected failure could have occurred after a paid dispatch.
                        result = {"status": "unknown", "result": {}, "error": "工作流异常，已隔离以避免重复调用；可人工标注"}
                if result is not None:
                    if result.get("status") == "unknown":
                        task_outcome = "unknown"
                    elif result.get("status") == "failed":
                        task_outcome = "failed"
                    remote_seconds = max(0.0, float(result.get("remote_seconds", 0) or 0))
                    metrics.record_work(kind="vlm_provider_task", outcome=task_outcome, duration_seconds=remote_seconds)
                    tool_result = result.get("tool") if isinstance(result.get("tool"), dict) else {}
                    tool_name = str(tool_result.get("tool", "none"))
                    if tool_name in OBSERVABLE_TOOLS:
                        tool_outcome = "success" if tool_result.get("status") in {"ok", "not_requested"} else "failed"
                        metrics.record_work(kind=f"tool_{tool_name}", outcome=tool_outcome, duration_seconds=0)
                    atomic_json(args.root / ("result-" + task["id"] + ".json"),
                        {"id": task["id"], "body": {"token": task["token"], **result}})
            except (OSError, ValueError) as e:
                LOG.warning("worker transport unavailable: %s", type(e).__name__)
                stop.wait(5)
            finally:
                if workflow:
                    workflow.close()
                details["active_task"] = None
                if "task_started" in locals():
                    metrics.set_inflight(kind="annotation", value=0)
                    metrics.record_work(kind="annotation", outcome=task_outcome, duration_seconds=time.monotonic() - task_started)
                    del task_started
    stop.set()
    thread.join(timeout=2)


if __name__ == "__main__":
    configure_logging("annotation-worker", force=True)
    configure_tracing("annotation-worker")
    main()
