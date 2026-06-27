from __future__ import annotations

from pathlib import Path

from finevision.ml_toolkit.artifacts import read_json
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
    assert summary["feature_artifact"].startswith("dataset@toy-001-color_stats_v1-")
    assert summary["model_artifact"] == "dataset@toy-001-linear-head"
    assert summary["accuracy"] >= 0.8
    assert summary["macro_f1"] >= 0.8
    assert summary["threshold_points"] >= 1
    assert summary["calibration_temperature"] > 0
    assert "threshold_strategy" in summary
    assert summary["margin_threshold"] >= 0
    assert 0 <= summary["expected_coverage"] <= 1
    assert 0 <= summary["expected_selective_risk"] <= 1
    assert summary["inference_decision"] in {"accept", "abstain", "reject_ood"}
    assert "label" in summary["inference_top1"]

    artifact_root = Path(summary["dataset_manifest"]).parent
    model_dir = artifact_root / "models" / summary["model_artifact"]
    assert (model_dir / "calibration_report.json").exists()
    assert (model_dir / "threshold_strategy.json").exists()


def test_calibration_strategy_and_inference_contract(tmp_path: Path) -> None:
    summary = run_smoke_flow(tmp_path / "work")
    artifact_root = Path(summary["dataset_manifest"]).parent
    model_dir = artifact_root / "models" / summary["model_artifact"]

    calibration = read_json(model_dir / "calibration_report.json")
    sweep = read_json(model_dir / "threshold_sweep.json")
    strategy = read_json(model_dir / "threshold_strategy.json")
    inference = read_json(artifact_root / "inference_result.json")

    assert calibration["method"] == "temperature_scaling"
    assert calibration["split"] in {"val", "val_test", "all"}
    assert calibration["temperature"] > 0
    assert {"ece", "nll", "brier"} <= set(calibration["before"])
    assert {"ece", "nll", "brier"} <= set(calibration["after"])

    assert sweep["split"] == calibration["split"]
    assert sweep["calibration_artifact_id"] == calibration["artifact_id"]
    assert strategy["calibration_artifact_id"] == calibration["artifact_id"]
    assert strategy["selection_rule"] in {"max_coverage_under_target_risk", "min_risk_fallback"}
    assert strategy["accept_threshold"] in {point["threshold"] for point in sweep["points"]}
    assert strategy["selection_config"]["threshold_candidates"] == len(sweep["points"])

    assert inference["threshold_strategy_id"] == strategy["strategy_id"]
    assert inference["decision"]["thresholds"]["confidence"] == strategy["accept_threshold"]
    assert inference["decision"]["thresholds"]["margin"] == strategy["margin_threshold"]
