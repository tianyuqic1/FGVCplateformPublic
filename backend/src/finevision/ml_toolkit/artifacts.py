from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypeVar

import numpy as np

from finevision.schemas.artifacts import (
    DatasetManifest,
    FeatureArtifact,
    ModelArtifact,
    SampleRecord,
    to_jsonable,
)

T = TypeVar("T")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> Path:
    ensure_dir(path.parent)
    path.write_text(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_dataset_manifest(path: Path, manifest: DatasetManifest) -> Path:
    return write_json(path, manifest)


def read_dataset_manifest(path: Path) -> DatasetManifest:
    raw = read_json(path)
    return DatasetManifest(
        dataset_id=raw["dataset_id"],
        dataset_version_id=raw["dataset_version_id"],
        root=raw["root"],
        classes=list(raw["classes"]),
        samples=[SampleRecord(**sample) for sample in raw["samples"]],
        split_counts=raw["split_counts"],
        readiness=raw["readiness"],
        artifact_refs=raw.get("artifact_refs", {}),
    )


def write_feature_artifact(
    artifact_dir: Path,
    artifact: FeatureArtifact,
    features: np.ndarray,
) -> FeatureArtifact:
    ensure_dir(artifact_dir)
    features_path = artifact_dir / "features.npz"
    np.savez_compressed(
        features_path,
        features=features.astype(np.float32),
        sample_ids=np.array(artifact.sample_ids),
        labels=np.array(artifact.labels),
        splits=np.array(artifact.splits),
    )
    updated = FeatureArtifact(**{**asdict(artifact), "features_path": str(features_path)})
    write_json(artifact_dir / "feature_artifact.json", updated)
    return updated


def load_feature_artifact(artifact_dir: Path) -> tuple[FeatureArtifact, np.ndarray]:
    raw = read_json(artifact_dir / "feature_artifact.json")
    artifact = FeatureArtifact(**raw)
    data = np.load(artifact.features_path, allow_pickle=False)
    return artifact, data["features"].astype(np.float32)


def write_model_artifact(
    artifact_dir: Path,
    artifact: ModelArtifact,
    weights: np.ndarray,
    bias: np.ndarray,
    feature_mean: np.ndarray,
    feature_std: np.ndarray,
) -> ModelArtifact:
    ensure_dir(artifact_dir)
    model_path = artifact_dir / "linear_head.npz"
    np.savez_compressed(
        model_path,
        weights=weights.astype(np.float32),
        bias=bias.astype(np.float32),
        feature_mean=feature_mean.astype(np.float32),
        feature_std=feature_std.astype(np.float32),
        classes=np.array(artifact.classes),
    )
    updated = ModelArtifact(**{**asdict(artifact), "model_path": str(model_path)})
    write_json(artifact_dir / "model_artifact.json", updated)
    return updated


def load_model_artifact(artifact_dir: Path) -> tuple[ModelArtifact, dict[str, np.ndarray]]:
    raw = read_json(artifact_dir / "model_artifact.json")
    artifact = ModelArtifact(**raw)
    data = np.load(artifact.model_path, allow_pickle=False)
    return artifact, {key: data[key] for key in data.files}
