from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest
from finevision.compute import hardware_collector as hw


def test_nvidia_missing_fields_remain_unknown():
    rows = hw.parse_gpus("GPU-abc, Test GPU, 88, 2048, 8192, 70, [N/A], 300, 0\n")
    assert rows[0]["memory"]["used_bytes"] == 2048 * 1024**2
    assert rows[0]["power_w"] is None
    assert rows[0]["percent"] == 88
    assert hw.number("nan") is None
    assert hw.number("inf") is None
    with pytest.raises(ValueError):
        hw.parse_gpus("not a metric row")


def test_cpu_excludes_double_counted_guest_time():
    assert hw.cpu_counters("cpu  100 20 30 400 50 0 0 0 80 10\ncpu0 0\ncpu1 0\n") == (600, 450, 2)
    assert hw.memory_counters("MemTotal: 100 kB\nMemAvailable: 30 kB\n")["used_bytes"] == 70 * 1024
    assert hw.memory_counters("MemTotal: 100 kB\n")["used_bytes"] is None


def test_disks_deduplicate_filesystems_and_report_missing(tmp_path):
    child = tmp_path / "cache"
    child.mkdir()
    disks, errors = hw.collect_disks({"data": str(tmp_path), "cache": str(child), "missing": str(child / "missing")})
    assert len(disks) == 1
    assert disks[0]["paths"] == ["data", "cache"]
    assert 0 <= disks[0]["available_bytes"] <= disks[0]["memory"]["total_bytes"]
    assert errors == ["disk_unavailable:missing"]


def test_collector_partial_failure_and_cpu_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(hw, "collect_gpus", lambda: ([], "none"))
    (tmp_path / "stat").write_text("cpu  100 0 0 100\ncpu0 0\n")
    collector = hw.Collector("test-node", "Test", tmp_path, {"data": str(tmp_path)})
    first = collector.sample()
    assert first["cpu"]["percent"] is None  # No fabricated zero on warmup.
    assert "memory_unavailable" in first["errors"]
    (tmp_path / "stat").write_text("cpu  150 0 0 150\ncpu0 0\n")
    assert collector.sample()["cpu"]["percent"] == 50
    (tmp_path / "stat").unlink()
    assert collector.sample()["cpu"]["percent"] is None


def test_no_gpu_and_broken_driver_are_distinct(tmp_path, monkeypatch):
    pci = tmp_path / "bus/pci/devices"
    pci.mkdir(parents=True)
    def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", missing)
    assert hw.collect_gpus(tmp_path) == ([], "none")
    device = pci / "0"
    device.mkdir()
    (device / "vendor").write_text("0x10de")
    assert hw.collect_gpus(tmp_path) == ([], "unavailable")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="GPU-1, GPU, 1, 2, 3, 4, N/A, N/A, 0\n"))
    assert hw.collect_gpus(tmp_path)[1] == "ok"


def test_worker_node_identity_is_explicit(monkeypatch):
    monkeypatch.delenv("FINEVISION_NODE_ID", raising=False)
    assert not hw.worker_identity().startswith("node/")
    monkeypatch.setenv("FINEVISION_NODE_ID", "node-1")
    assert hw.worker_identity().startswith("node/node-1/")
    monkeypatch.setenv("FINEVISION_NODE_ID", "node/injected")
    with pytest.raises(ValueError):
        hw.worker_identity()
