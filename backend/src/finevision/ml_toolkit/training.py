from __future__ import annotations

from pathlib import Path

import numpy as np

from finevision.ml_toolkit.artifacts import write_json, write_model_artifact
from finevision.ml_toolkit.metrics import classification_report
from finevision.schemas.artifacts import FeatureArtifact, ModelArtifact, TrainingRunReport


def _encode_labels(labels: list[str], classes: list[str]) -> np.ndarray:
    index = {label: pos for pos, label in enumerate(classes)}
    return np.array([index[label] for label in labels], dtype=np.int64)


def _one_hot(y: np.ndarray, class_count: int) -> np.ndarray:
    matrix = np.zeros((len(y), class_count), dtype=np.float32)
    matrix[np.arange(len(y)), y] = 1.0
    return matrix


def _standardize_train(features: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = features.mean(axis=0)
    std = features.std(axis=0)
    std[std < 1e-6] = 1.0
    return (features - mean) / std, mean, std


def apply_linear_head(features: np.ndarray, weights: np.ndarray, bias: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    safe_std = np.where(std < 1e-6, 1.0, std)
    normalized = (features - mean) / safe_std
    return normalized @ weights + bias


def train_linear_head(
    feature_artifact: FeatureArtifact,
    features: np.ndarray,
    artifact_root: str | Path,
    run_id: str = "run-smoke",
    ridge_lambda: float = 1e-2,
    artifact_id: str | None = None,
) -> tuple[ModelArtifact, TrainingRunReport, np.ndarray]:
    labels = list(feature_artifact.labels)
    classes = sorted(set(labels))
    y = _encode_labels(labels, classes)
    splits = np.array(feature_artifact.splits)
    train_mask = splits == "train"
    eval_mask = np.isin(splits, ["val", "test"])
    if not train_mask.any():
        raise ValueError("Feature artifact has no train split samples")
    if not eval_mask.any():
        eval_mask = ~train_mask

    x_train, mean, std = _standardize_train(features[train_mask])
    y_train = y[train_mask]
    targets = _one_hot(y_train, len(classes))

    xtx = x_train.T @ x_train
    regularizer = ridge_lambda * np.eye(xtx.shape[0], dtype=np.float32)
    weights = np.linalg.solve(xtx + regularizer, x_train.T @ targets)
    bias = targets.mean(axis=0) - x_train.mean(axis=0) @ weights

    logits = apply_linear_head(features, weights, bias, mean, std)
    y_pred = logits[eval_mask].argmax(axis=1)
    report = classification_report(
        y_true=y[eval_mask],
        y_pred=y_pred,
        classes=classes,
        run_config={
            "run_id": run_id,
            "head_type": "ridge_linear",
            "ridge_lambda": ridge_lambda,
            "feature_artifact_id": feature_artifact.artifact_id,
        },
    )

    artifact_id = artifact_id or f"{feature_artifact.dataset_version_id}-linear-head"
    artifact_dir = Path(artifact_root) / artifact_id
    model_artifact = ModelArtifact(
        artifact_id=artifact_id,
        dataset_id=feature_artifact.dataset_id,
        dataset_version_id=feature_artifact.dataset_version_id,
        feature_artifact_id=feature_artifact.artifact_id,
        model_path=str(artifact_dir / "linear_head.npz"),
        classes=classes,
        head_type="ridge_linear",
        feature_dim=feature_artifact.feature_dim,
        training_config={"run_id": run_id, "ridge_lambda": ridge_lambda},
    )
    model_artifact = write_model_artifact(artifact_dir, model_artifact, weights, bias, mean, std)
    run_report = TrainingRunReport(
        run_id=run_id,
        dataset_id=feature_artifact.dataset_id,
        dataset_version_id=feature_artifact.dataset_version_id,
        feature_artifact_id=feature_artifact.artifact_id,
        model_artifact_id=model_artifact.artifact_id,
        evaluation=report,
    )
    write_json(artifact_dir / "training_report.json", run_report)
    return model_artifact, run_report, logits
