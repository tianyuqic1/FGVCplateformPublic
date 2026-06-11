from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from finevision.ml_toolkit.artifacts import load_model_artifact, write_dataset_manifest, write_json
from finevision.ml_toolkit.datasets import scan_imagefolder
from finevision.ml_toolkit.features import ColorStatsExtractor, TimmDinoV3Extractor, extract_features
from finevision.ml_toolkit.inference import run_inference
from finevision.ml_toolkit.thresholds import sweep_confidence_thresholds
from finevision.ml_toolkit.toydata import create_toy_imagefolder
from finevision.ml_toolkit.training import train_linear_head


def build_extractor(name: str, device: str = "cpu", batch_size: int = 8):
    if name == "color_stats":
        return ColorStatsExtractor()
    if name == "dinov3_vitl":
        return TimmDinoV3Extractor(device=device, batch_size=batch_size)
    raise ValueError(f"Unknown extractor: {name}")


def run_smoke_flow(
    work_dir: str | Path,
    dataset_dir: str | Path | None = None,
    extractor_name: str = "color_stats",
    device: str = "cpu",
    batch_size: int = 8,
    dataset_id: str | None = None,
    dataset_version_id: str | None = None,
) -> dict[str, object]:
    work_path = Path(work_dir).resolve()
    dataset_path = Path(dataset_dir).resolve() if dataset_dir else work_path / "toy_imagefolder"
    artifact_root = work_path / "artifacts"
    if dataset_dir is None:
        create_toy_imagefolder(dataset_path)
    dataset_id = dataset_id or ("toy-shapes" if dataset_dir is None else dataset_path.name)
    dataset_version_id = dataset_version_id or ("dataset@toy-001" if dataset_dir is None else f"dataset@{dataset_id}-001")

    manifest = scan_imagefolder(dataset_path, dataset_id=dataset_id, dataset_version_id=dataset_version_id)
    write_dataset_manifest(artifact_root / "dataset_manifest.json", manifest)

    extractor = build_extractor(extractor_name, device=device, batch_size=batch_size)
    feature_artifact, features = extract_features(manifest, extractor, artifact_root / "features")
    model_artifact, training_report, logits = train_linear_head(feature_artifact, features, artifact_root / "models", run_id="run-toy-001")
    threshold_sweep = sweep_confidence_thresholds(feature_artifact, model_artifact, logits, artifact_root=artifact_root / "models")

    loaded_model, model_state = load_model_artifact(artifact_root / "models" / model_artifact.artifact_id)
    query_index = int(np.where(np.array(feature_artifact.splits) == "test")[0][0])
    inference = run_inference(
        loaded_model,
        model_state,
        features[query_index],
        features,
        feature_artifact.sample_ids,
        feature_artifact.labels,
        confidence_threshold=0.55,
        margin_threshold=0.05,
    )
    write_json(artifact_root / "inference_result.json", inference)

    summary = {
        "dataset_manifest": str(artifact_root / "dataset_manifest.json"),
        "dataset_id": manifest.dataset_id,
        "dataset_version_id": manifest.dataset_version_id,
        "feature_artifact": feature_artifact.artifact_id,
        "model_artifact": model_artifact.artifact_id,
        "accuracy": training_report.evaluation.accuracy,
        "macro_f1": training_report.evaluation.macro_f1,
        "threshold_points": len(threshold_sweep.points),
        "inference_decision": inference.decision.decision,
        "inference_top1": inference.top_k[0],
        "extractor": extractor_name,
        "device": device,
        "batch_size": batch_size,
    }
    write_json(artifact_root / "smoke_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the FineVision ML toolkit smoke flow.")
    parser.add_argument("--work-dir", default=".finevision-smoke", help="Directory for generated dataset and artifacts.")
    parser.add_argument("--dataset-dir", default=None, help="Optional ImageFolder dataset directory.")
    parser.add_argument(
        "--extractor",
        default="color_stats",
        choices=["color_stats", "dinov3_vitl"],
        help="Feature extractor. dinov3_vitl uses timm ViT-L and may download model weights.",
    )
    parser.add_argument("--device", default="cpu", help="Torch device for DINOv3 extraction, for example cpu or cuda.")
    parser.add_argument("--batch-size", type=int, default=8, help="Feature extraction batch size.")
    parser.add_argument("--dataset-id", default=None, help="Dataset id to write into artifact metadata.")
    parser.add_argument("--dataset-version-id", default=None, help="Dataset version id to write into artifact metadata.")
    args = parser.parse_args()
    summary = run_smoke_flow(
        args.work_dir,
        args.dataset_dir,
        args.extractor,
        args.device,
        args.batch_size,
        args.dataset_id,
        args.dataset_version_id,
    )
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
