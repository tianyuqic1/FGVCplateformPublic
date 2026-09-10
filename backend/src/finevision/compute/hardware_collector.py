"""Read-only Linux host metrics; no ML imports and no additional dependencies.

CPU/memory use the host proc mount. Storage is limited to configured paths,
never guessed from container overlay usage. NVIDIA metrics use nvidia-smi.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger(__name__)
NODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def worker_identity() -> str:
    identity = f"{socket.gethostname()}:{os.getpid()}"
    node = os.environ.get("FINEVISION_NODE_ID", "")
    if not node:
        return identity  # Existing unconfigured workers have no inferred node association.
    if not NODE_ID.fullmatch(node):
        raise ValueError("FINEVISION_NODE_ID must contain 1-80 letters, digits, dots, underscores or hyphens")
    return f"node/{node}/{identity}"


def number(value: str, scale: float = 1) -> float | None:
    try:
        parsed = float(value.strip()) * scale
        return parsed if math.isfinite(parsed) and parsed >= 0 else None
    except ValueError:
        return None


def parse_gpus(output: str) -> list[dict]:
    result = []
    for row in csv.reader(io.StringIO(output)):
        if not row:
            continue
        if len(row) != 9:
            raise ValueError("unexpected NVIDIA metric columns")
        gpu_id, name, utilization, used, total, temperature, power, limit, _index = row
        if not gpu_id.strip().startswith("GPU-"):
            raise ValueError("invalid NVIDIA UUID")
        result.append({
            "id": gpu_id.strip(), "name": name.strip(), "percent": number(utilization),
            "memory": {"used_bytes": number(used, 1024**2), "total_bytes": number(total, 1024**2)},
            "temperature_c": number(temperature), "power_w": number(power), "power_limit_w": number(limit),
        })
    return result


def collect_gpus(sys_root: Path = Path("/sys")) -> tuple[list[dict], str]:
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,index", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=True,
        )
        gpus = parse_gpus(completed.stdout)
        return gpus, "ok" if gpus else "none"
    except FileNotFoundError:
        # Only call it no NVIDIA GPU if PCI discovery itself succeeds.
        try:
            pci = sys_root / "bus/pci/devices"
            devices = list(pci.iterdir())
            nvidia = any((p / "vendor").read_text().strip() == "0x10de" for p in devices)
            return [], "unavailable" if nvidia else "none"
        except OSError:
            return [], "unavailable"
    except (subprocess.SubprocessError, ValueError, OSError):
        return [], "unavailable"


def cpu_counters(content: str) -> tuple[int, int, int]:
    lines = content.splitlines()
    values = [int(value) for value in lines[0].split()[1:9]]  # guest counters already included in user/nice.
    if not lines[0].startswith("cpu ") or len(values) < 4:
        raise ValueError("invalid proc stat")
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle, sum(bool(re.match(r"cpu\d+\s", line)) for line in lines)


def memory_counters(content: str) -> dict:
    values = {}
    for line in content.splitlines():
        key, _, raw = line.partition(":")
        values[key] = int(raw.split()[0]) * 1024
    total = values["MemTotal"]
    available = values.get("MemAvailable")
    return {"used_bytes": max(0, total - available) if available is not None else None, "total_bytes": total}


def collect_disks(paths: dict[str, str]) -> tuple[list[dict], list[str]]:
    disks: dict[int, dict] = {}
    errors = []
    for label, path in paths.items():
        try:
            device = os.stat(path).st_dev
            usage = os.statvfs(path)
            if device in disks:
                disks[device]["paths"].append(label)
                continue
            disks[device] = {
                "id": str(device), "paths": [label],
                "memory": {"total_bytes": usage.f_blocks * usage.f_frsize, "used_bytes": (usage.f_blocks - usage.f_bfree) * usage.f_frsize},
                "available_bytes": usage.f_bavail * usage.f_frsize,
            }
        except OSError:
            errors.append(f"disk_unavailable:{label}"[:256])
    return list(disks.values()), errors


class Collector:
    def __init__(self, node: str, name: str, proc_root: Path, disks: dict[str, str]):
        if not NODE_ID.fullmatch(node):
            raise ValueError("invalid FINEVISION_NODE_ID")
        self.node, self.name, self.proc_root, self.disks = node, name, proc_root, disks
        self.previous_cpu: tuple[int, int, int] | None = None

    def sample(self) -> dict:
        errors = []
        cpu = {"percent": None, "cores": 0}
        try:
            current = cpu_counters((self.proc_root / "stat").read_text())
            cpu["cores"] = current[2]
            if self.previous_cpu:
                total, idle = current[0] - self.previous_cpu[0], current[1] - self.previous_cpu[1]
                if total > 0 and 0 <= idle <= total:
                    cpu["percent"] = round((1 - idle / total) * 100, 2)
            self.previous_cpu = current
        except (OSError, ValueError, IndexError):
            self.previous_cpu = None
            errors.append("cpu_unavailable")
        try:
            memory = memory_counters((self.proc_root / "meminfo").read_text())
        except (OSError, ValueError, IndexError, KeyError):
            memory = {"used_bytes": None, "total_bytes": None}
            errors.append("memory_unavailable")
        gpus, gpu_status = collect_gpus()
        if gpu_status == "unavailable":
            errors.append("gpu_unavailable")
        disks, disk_errors = collect_disks(self.disks)
        return {
            "node_id": self.node, "name": self.name, "scope": "host",
            "sampled_at": datetime.now(timezone.utc).isoformat(),
            "cpu": cpu, "memory": memory, "gpus": gpus, "gpu_status": gpu_status,
            "disks": disks, "errors": errors + disk_errors,
        }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    endpoint = os.environ.get("FINEVISION_HARDWARE_ENDPOINT", "http://localhost:8001/api/internal/hardware/samples")
    token = os.environ.get("FINEVISION_HARDWARE_TOKEN", "")
    if not token:
        raise SystemExit("FINEVISION_HARDWARE_TOKEN is required")
    node = os.environ.get("FINEVISION_NODE_ID", socket.gethostname())
    paths = json.loads(os.environ.get("FINEVISION_HARDWARE_DISKS", '{"系统盘":"/"}'))
    if not isinstance(paths, dict) or not paths or len(paths) > 32 or any(not isinstance(k, str) or not isinstance(v, str) for k, v in paths.items()):
        raise SystemExit("FINEVISION_HARDWARE_DISKS must be a JSON object of labels to paths")
    collector = Collector(node, os.environ.get("FINEVISION_NODE_NAME", node), Path(os.environ.get("FINEVISION_HOST_PROC", "/proc")), paths)
    interval = max(5, float(os.environ.get("FINEVISION_HARDWARE_INTERVAL", "5")))
    if not math.isfinite(interval):
        raise SystemExit("invalid collection interval")
    LOG.info("Hardware collector started for node %s", node)
    while True:
        start = time.monotonic()
        sample = collector.sample()
        request = Request(endpoint, data=json.dumps(sample, allow_nan=False).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}, method="POST")
        try:
            with urlopen(request, timeout=5) as response:
                response.read(1024)
        except HTTPError as error:
            LOG.warning("Hardware report rejected (HTTP %s)", error.code)
        except (URLError, TimeoutError, OSError):
            LOG.warning("Hardware control plane unavailable; retrying next interval")
        # Do not replay queued samples: recovery reports current metrics.
        time.sleep(max(.1, interval - (time.monotonic() - start)))


if __name__ == "__main__":
    main()
