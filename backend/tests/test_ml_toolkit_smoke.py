from __future__ import annotations

from pathlib import Path

from finevision.ml_toolkit.datasets import scan_imagefolder
from finevision.ml_toolkit.smoke import run_smoke_flow
from finevision.ml_toolkit.toydata import create_toy_imagefolder


def test_scan_imagefolder_creates_manifest(tmp_path: Path) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / "toy", samples_per_class=6)
    manifest = scan_imagefolder(dataset_dir, "toy-shapes", "dataset@toy-001")

    assert manifest.dataset_id == "toy-shapes"
    assert manifest.dataset_version_id == "dataset@toy-001"
    assert manifest.classes == ["blue_triangle", "green_circle", "red_square"]
    assert manifest.readiness["ready"] is True
    assert manifest.readiness["provided_splits"] is False
    assert manifest.readiness["sample_count"] == 18
    assert all(manifest.split_counts[split] for split in ("train", "val", "test"))


def test_smoke_flow_writes_artifacts_and_inference(tmp_path: Path) -> None:
    summary = run_smoke_flow(tmp_path / "work")

    assert Path(summary["dataset_manifest"]).exists()
    assert summary["feature_artifact"] == "dataset@toy-001-color_stats_v1"
    assert summary["model_artifact"] == "dataset@toy-001-linear-head"
    assert summary["accuracy"] >= 0.8
    assert summary["macro_f1"] >= 0.8
    assert summary["threshold_points"] == 5
    assert summary["inference_decision"] in {"accept", "abstain", "reject_ood"}
    assert "label" in summary["inference_top1"]
