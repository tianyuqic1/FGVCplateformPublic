"""Single-flight local CLIP process with explicit lifetime and deadline."""

import atexit
import contextlib
import json
import math
import os
import selectors
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .images import read_image, save_image
from .memory import vector


class ClipRuntime:
    def __init__(self, root, models, runtime, timeout=120):
        if not math.isfinite(timeout) or not 0 < timeout <= 600:
            raise ValueError("invalid CLIP deadline")
        self.root, self.models = Path(root), Path(models)
        self.runtime, self.timeout = runtime, timeout
        self.process = self.workspace = self.name = None
        self.lock = threading.RLock()
        self.closed = False
        self.starts = self.jobs = 0
        atexit.register(self.close)

    def _command(self, work, name):
        return [
            "docker",
            "run",
            "--rm",
            "-i",
            "--name",
            name,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--gpus",
            "all",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "--memory",
            "8g",
            "--tmpfs",
            "/tmp:rw,size=1g",
            "-e",
            "HF_HUB_OFFLINE=1",
            "-e",
            "TRANSFORMERS_OFFLINE=1",
            "-e",
            "HF_HOME=/tmp/hf",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{self.models}:/models:ro",
            "-v",
            f"{work}:/job:ro",
            "-v",
            f"{Path(__file__).resolve().parent}:/code:ro",
            "--entrypoint",
            "python3",
            self.runtime,
            "-u",
            "/code/clip_server.py",
        ]

    def _start(self):
        if self.process is not None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self.workspace = tempfile.TemporaryDirectory(
            prefix="clip-session-", dir=self.root
        )
        self.name = "annotation-clip-" + uuid.uuid4().hex[:12]
        self.process = subprocess.Popen(
            self._command(self.workspace.name, self.name),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.starts += 1

    def _receive(self, deadline):
        data = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b"\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise RuntimeError("CLIP worker deadline exceeded")
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError("CLIP worker exited before response")
                data.extend(chunk)
                if len(data) > 8 * 1024 * 1024:
                    raise RuntimeError("CLIP response exceeded size bound")
        if data.count(b"\n") != 1 or not data.endswith(b"\n"):
            raise RuntimeError("CLIP unexpected extra response")
        return json.loads(data)

    def encode(self, paths):
        if not 1 <= len(paths) <= 512:
            raise ValueError("invalid CLIP batch")
        with self.lock:
            if self.closed:
                raise RuntimeError("CLIP runtime closed")
            try:
                deadline = time.monotonic() + self.timeout
                self._start()
                sid = uuid.uuid4().hex
                work = Path(self.workspace.name) / sid
                work.mkdir()
                try:
                    for i, path in enumerate(paths):
                        save_image(work / f"{i}.png", read_image(path))
                    request = (
                        json.dumps({"id": sid, "count": len(paths)}) + "\n"
                    ).encode()
                    self.process.stdin.write(request)
                    self.process.stdin.flush()
                    response = self._receive(deadline)
                    if response.get("id") != sid or response.get("ok") is not True:
                        raise RuntimeError("CLIP response mismatch")
                    rows = response.get("vectors")
                    if not isinstance(rows, list) or len(rows) != len(paths):
                        raise RuntimeError("CLIP response count mismatch")
                    rows = [vector(row, 512) for row in rows]
                    self.jobs += 1
                    return rows
                finally:
                    # Only exact files created for this validated local request.
                    for p in work.glob("*.png"):
                        p.unlink()
                    work.rmdir()
            except (
                RuntimeError,
                ValueError,
                OSError,
                TypeError,
                AttributeError,
            ) as error:
                self._stop()
                raise RuntimeError(
                    "local CLIP failed; session reset: " + str(error)
                ) from error

    def _stop(self):
        process, self.process = self.process, None
        name, self.name = self.name, None
        workspace, self.workspace = self.workspace, None
        try:
            if process is not None:
                with contextlib.suppress(OSError):
                    if process.stdin:
                        process.stdin.close()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                        process.kill()
                        process.wait(timeout=5)
                finally:
                    if process.stdout:
                        process.stdout.close()
        finally:
            # Even a broken Docker client must not skip container/job cleanup.
            try:
                if name:
                    self._remove_container(name)
            finally:
                if workspace:
                    workspace.cleanup()

    @staticmethod
    def _remove_container(name):
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, timeout=15
            )

    def close(self):
        with self.lock:
            try:
                self._stop()
            finally:
                self.closed = True
                atexit.unregister(self.close)
