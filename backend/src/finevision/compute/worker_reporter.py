"""Best-effort instance telemetry. Never renews, claims or completes job leases.

Set FINEVISION_WORKER_ID explicitly for stable identity across container replacement.
Only bounded, explicitly selected metadata is sent; never exception text or env dumps.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import logging
import os
import random
import socket
import threading
import urllib.error
import urllib.request
import json
from uuid import uuid4

LOG = logging.getLogger(__name__)


class WorkerReporter:
    def __init__(self, kind, capacity=1, *, base=None, token=None):
        self.base = (base or os.environ.get("FINEVISION_CONTROL_PLANE_HTTP", "http://go-control-plane:8000")).rstrip("/")
        self.token = token if token is not None else os.environ.get("FINEVISION_WORKER_TOKEN", "")
        host = socket.gethostname()
        self.data = {
            "id": os.environ.get("FINEVISION_WORKER_ID", f"{kind}-{host}"),
            "session_id": str(uuid4()), "sequence": 0,
            "name": os.environ.get("FINEVISION_WORKER_NAME", f"{kind} · {host}"),
            "kind": kind, "node_id": os.environ.get("FINEVISION_NODE_ID", ""),
            "version": os.environ.get("FINEVISION_SERVICE_VERSION", "dev"),
            "backend": os.environ.get("FINEVISION_INFERENCE_BACKEND", "pytorch" if kind == "training" else "onnx_cpu" if kind == "inference" else kind),
            "device": os.environ.get("FINEVISION_COMPUTE_DEVICE", "cpu" if kind == "annotation" else "unknown"),
            "capacity": capacity, "readiness": "initializing", "reason": "服务初始化中", "tasks": [],
        }
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.registered = False
        self.thread = None

    def start(self):
        if not self.token:
            LOG.warning("Worker inventory disabled: FINEVISION_WORKER_TOKEN is not configured")
            return self
        self.thread = threading.Thread(target=self._loop, name="worker-inventory", daemon=True)
        self.thread.start()
        return self

    def state(self, readiness, reason=""):
        with self.lock:
            self.data.update(readiness=readiness, reason=reason)

    def begin(self, task_id, stage, kind=None):
        with self.lock:
            self.data["tasks"].append({"id": str(task_id), "kind": kind or self.data["kind"],
                "stage": stage, "started_at": datetime.now(timezone.utc).isoformat()})

    def end(self, task_id):
        with self.lock:
            self.data["tasks"] = [t for t in self.data["tasks"] if t["id"] != str(task_id)]

    @contextmanager
    def task(self, task_id, stage, kind=None):
        self.begin(task_id, stage, kind)
        try:
            yield
        finally:
            self.end(task_id)

    def send_once(self):
        with self.lock:
            self.data["sequence"] = self.data["sequence"] + 1 if self.registered else 0
            payload = json.dumps(self.data).encode()
        endpoint = "heartbeat" if self.registered else "register"
        request = urllib.request.Request(f"{self.base}/api/internal/workers/{endpoint}", data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token}, method="POST")
        with urllib.request.urlopen(request, timeout=4) as response:
            response.read(1024)
        self.registered = True

    def _loop(self):
        failures = 0
        while not self.stop_event.is_set():
            try:
                self.send_once()
                failures = 0
            except urllib.error.HTTPError as error:
                if error.code in (401, 403, 409):
                    # A superseded process must never register itself again.
                    LOG.warning("Worker inventory rejected (%s); reporting stopped", error.code)
                    return
                failures += 1
            except (OSError, ValueError):
                failures += 1
            self.stop_event.wait(min(30, 10 * max(1, failures)) + random.random())

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        # Do not start a new session during shutdown or revive a superseded session.
        if self.registered and self.thread and not self.thread.is_alive():
            self.state("stopped", "服务已结束运行")
            try:
                self.send_once()
            except (OSError, ValueError):
                pass


def grpc_interceptor(reporter):
    import grpc

    class InventoryInterceptor(grpc.ServerInterceptor):
        def intercept_service(self, continuation, details):
            handler = continuation(details)
            if handler is None or handler.unary_unary is None or details.method.endswith("/Health"):
                return handler

            def invoke(request, context):
                # RPC invocation IDs are not model IDs; multiple requests can use one model.
                task_id = str(uuid4())
                with reporter.task(task_id, details.method.rsplit("/", 1)[-1]):
                    return handler.unary_unary(request, context)
            return grpc.unary_unary_rpc_method_handler(invoke, request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer)
    return InventoryInterceptor()
