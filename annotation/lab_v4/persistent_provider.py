"""Private multiplexed stdio RPC with no listening port or automatic resend."""

import atexit
import json
import math
import os
import queue
import subprocess
import threading
import uuid
from concurrent.futures import Future, TimeoutError

UNKNOWN = {
    "kind": "unknown",
    "error": "adapter interrupted; inspect before explicit retry",
}
FIELDS = {
    "kind",
    "error",
    "raw",
    "model",
    "http_status",
    "retry_after",
    "usage",
    "latency_ms",
    "dispatched",
    "failure_phase",
    "concurrency_limit",
    "provider_request_id",
}


class Provider:
    def __init__(
        self,
        binary,
        base,
        model,
        key,
        allow_remote=False,
        timeout=120,
        connect_timeout=10,
        first_timeout=60,
        idle_timeout=45,
        stream=False,
        concurrency=2,
        max_concurrency=4,
        circuit_cooldown=30,
    ):
        if any(
            not math.isfinite(v) or v <= 0 or v > 3600
            for v in (
                timeout,
                connect_timeout,
                first_timeout,
                idle_timeout,
                circuit_cooldown,
            )
        ):
            raise ValueError("timeouts must be finite and in (0,3600] seconds")
        if not 1 <= concurrency <= max_concurrency <= 32:
            raise ValueError("invalid concurrency limits")
        self.binary, self.base, self.model, self.key = str(binary), base, model, key
        self.allow_remote, self.timeout = allow_remote, timeout
        self.policy = dict(
            timeout=timeout,
            connect_timeout=connect_timeout,
            first_timeout=first_timeout,
            idle_timeout=idle_timeout,
            stream=stream,
            concurrency=concurrency,
            max_concurrency=max_concurrency,
            circuit_cooldown=circuit_cooldown,
        )
        self.lock = threading.RLock()
        self.pending = {}
        self.process = None
        self.workers = []
        self.closed = False
        self.slots = threading.BoundedSemaphore(max_concurrency + 2)
        atexit.register(self.close)

    def _start(self):
        args = [self.binary, "--serve"]
        for key in (
            "timeout",
            "connect_timeout",
            "first_timeout",
            "idle_timeout",
            "circuit_cooldown",
        ):
            args += ["--" + key.replace("_", "-"), f"{self.policy[key]}s"]
        for key in ("concurrency", "max_concurrency"):
            args += ["--" + key.replace("_", "-"), str(self.policy[key])]
        if self.policy["stream"]:
            args.append("--stream")
        if self.allow_remote:
            args.append("--allow-remote")
        env = {
            "PATH": os.defpath,
            "VLM_BASE_URL": self.base,
            "VLM_MODEL": self.model,
            "VLM_API_KEY": self.key,
        }
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self.process = proc
        self.ready = Future()
        self.outgoing = queue.Queue()
        reader = threading.Thread(
            target=self._read, args=(proc, self.ready), daemon=True
        )
        writer = threading.Thread(
            target=self._write, args=(proc, self.outgoing), daemon=True
        )
        self.workers = [reader, writer]
        reader.start()
        writer.start()

    def _read(self, proc, ready):
        try:
            while True:
                line = proc.stdout.readline(16 * 1024 * 1024 + 1)
                if not line or len(line) > 16 * 1024 * 1024:
                    break
                value = json.loads(line)
                if not ready.done():
                    ready.set_result(
                        None
                        if value.get("ready") is True
                        else {"kind": "fatal", "error": "invalid adapter configuration"}
                    )
                    continue
                rid, result = value.get("request_id"), value.get("output")
                if not isinstance(result, dict) or result.get("kind") not in (
                    "ok",
                    "failed",
                    "fatal",
                    "unknown",
                    "retryable",
                    "deferred",
                ):
                    break
                with self.lock:
                    item = self.pending.pop(rid, None)
                if item and item[0] is proc and not item[1].done():
                    item[1].set_result({k: v for k, v in result.items() if k in FIELDS})
        except (OSError, ValueError, AttributeError):
            pass
        finally:
            if not ready.done():
                ready.set_result({"kind": "fatal", "error": "adapter startup failed"})
            self._fail_generation(proc)

    def _write(self, proc, outgoing):
        try:
            while True:
                data = outgoing.get()
                if data is None or proc.poll() is not None:
                    return
                proc.stdin.write(data)
                proc.stdin.flush()
        except (OSError, ValueError):
            self._fail_generation(proc)

    def _fail_generation(self, proc):
        with self.lock:
            for rid, (owner, future) in list(self.pending.items()):
                if owner is proc:
                    self.pending.pop(rid)
                    if not future.done():
                        future.set_result(dict(UNKNOWN))
            if proc is self.process:
                self.outgoing.put(None)
            if proc.poll() is None:
                proc.terminate()

    def with_budget(self, request, remaining):
        return self(request, timeout=min(self.timeout, remaining))

    def __call__(self, request, timeout=None):
        timeout = self.timeout if timeout is None else min(timeout, self.timeout)
        if not self.slots.acquire(blocking=False):
            return {
                "kind": "deferred",
                "dispatched": False,
                "retry_after": 0.1,
                "error": "local adapter queue full",
            }
        try:
            with self.lock:
                if self.closed:
                    return {"kind": "fatal", "error": "adapter is closed"}
                if self.process is None or self.process.poll() is not None:
                    if self.process is not None:
                        self._dispose()
                    try:
                        self._start()
                    except OSError:
                        return {"kind": "fatal", "error": "cannot launch model adapter"}
                proc, ready = self.process, self.ready
            try:
                error = ready.result(timeout=10)
                if error:
                    return error
                rid = uuid.uuid4().hex
                payload = (
                    json.dumps(
                        {
                            "request_id": rid,
                            "input": request,
                            "timeout_ms": max(1, int(timeout * 1000)),
                        },
                        allow_nan=False,
                    ).encode()
                    + b"\n"
                )
                if len(payload) >= 24 * 1024 * 1024 - 1:
                    return {"kind": "fatal", "error": "request exceeds adapter limit"}
                future = Future()
                with self.lock:
                    if proc is not self.process or proc.poll() is not None:
                        return dict(UNKNOWN)
                    self.pending[rid] = (proc, future)
                    self.outgoing.put(payload)
                return future.result(timeout=timeout + 10)
            except TimeoutError:
                self._fail_generation(proc)
                return dict(UNKNOWN)
            except KeyboardInterrupt:
                self._fail_generation(proc)
                raise
        finally:
            self.slots.release()

    def _dispose(self):
        proc = self.process
        if proc is None:
            return
        self._fail_generation(proc)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        proc.stdin.close()
        proc.stdout.close()
        self.process = None

    def close(self):
        with self.lock:
            self.closed = True
            self._dispose()
        for worker in self.workers:
            if worker is not threading.current_thread():
                worker.join(timeout=2)
        atexit.unregister(self.close)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
