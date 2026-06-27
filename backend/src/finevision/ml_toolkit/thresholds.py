from __future__ import annotations

from pathlib import Path

import numpy as np

from finevision.ml_toolkit.artifacts import write_json
from finevision.ml_toolkit.metrics import softmax
from finevision.schemas.artifacts import (
    CalibrationReport,
    FeatureArtifact,
    ModelArtifact,
    ThresholdPoint,
    ThresholdStrategy,
    ThresholdSweep,
)


def _class_indices(labels: list[str], classes: list[str]) -> np.ndarray:
    class_index = {label: idx for idx, label in enumerate(classes)}
    return np.array([class_index[label] for label in labels], dtype=np.int64)


def _split_mask(splits: list[str], split: str) -> tuple[np.ndarray, str]:
    split_values = np.array(splits)
    mask = split_values == split
    if mask.any():
        return mask, split
    fallback = np.isin(split_values, ["val", "test"])
    if fallback.any():
        return fallback, "val_test"
    return np.ones(len(split_values), dtype=bool), "all"


def _threshold_candidates(confidence: np.ndarray, quantile_count: int = 21) -> list[float]:
    if len(confidence) == 0:
        return [1.0]
    quantiles = np.linspace(0.0, 1.0, quantile_count)
    values = np.quantile(confidence, quantiles)
    return sorted({float(np.clip(round(value, 6), 0.0, 1.0)) for value in values})


def sweep_confidence_thresholds(
    feature_artifact: FeatureArtifact,
    model_artifact: ModelArtifact,
    logits: np.ndarray,
    thresholds: list[float] | None = None,
    review_cost_per_item: float = 1.0,
    artifact_root: str | Path | None = None,
    calibration: CalibrationReport | None = None,
    split: str = "val",
) -> ThresholdSweep:
    true_idx = _class_indices(feature_artifact.labels, model_artifact.classes)
    split_filter, actual_split = _split_mask(feature_artifact.splits, split)
    temperature = calibration.temperature if calibration else 1.0
    probabilities = softmax(logits, temperature=temperature)
    predicted = probabilities.argmax(axis=1)
    confidence = probabilities.max(axis=1)
    thresholds = thresholds or _threshold_candidates(confidence[split_filter])

    points = []
    for threshold in thresholds:
        accepted = (confidence >= threshold) & split_filter
        considered = split_filter
        considered_count = int(considered.sum())
        coverage = float(accepted.sum() / considered_count) if considered_count else 0.0
        if accepted.any():
            risk = float((predicted[accepted] != true_idx[accepted]).mean())
        else:
            risk = 0.0
        abstention = 1.0 - coverage
        points.append(
            ThresholdPoint(
                threshold=threshold,
                coverage=coverage,
                selective_risk=risk,
                abstention_rate=abstention,
                estimated_review_cost=float((considered_count - int(accepted.sum())) * review_cost_per_item),
            )
        )

    sweep = ThresholdSweep(
        strategy_id=f"{model_artifact.artifact_id}-confidence-sweep",
        dataset_id=feature_artifact.dataset_id,
        dataset_version_id=feature_artifact.dataset_version_id,
        model_artifact_id=model_artifact.artifact_id,
        calibration_artifact_id=calibration.artifact_id if calibration else None,
        points=points,
        split=actual_split,
    )
    if artifact_root is not None:
        write_json(Path(artifact_root) / model_artifact.artifact_id / "threshold_sweep.json", sweep)
    return sweep


def estimate_margin_threshold(
    feature_artifact: FeatureArtifact,
    model_artifact: ModelArtifact,
    logits: np.ndarray,
    calibration: CalibrationReport | None = None,
    split: str = "val",
    quantile: float = 0.05,
    correct_only: bool = True,
) -> tuple[float, dict[str, float | str | bool]]:
    true_idx = _class_indices(feature_artifact.labels, model_artifact.classes)
    split_filter, actual_split = _split_mask(feature_artifact.splits, split)
    probabilities = softmax(logits, temperature=calibration.temperature if calibration else 1.0)
    ordered = np.sort(probabilities, axis=1)[:, ::-1]
    margins = ordered[:, 0] - ordered[:, 1] if ordered.shape[1] > 1 else ordered[:, 0]
    predicted = probabilities.argmax(axis=1)
    eligible = split_filter & (predicted == true_idx if correct_only else np.ones(len(true_idx), dtype=bool))
    source = margins[eligible]
    if len(source) == 0:
        source = margins[split_filter]
    threshold = float(np.quantile(source, quantile)) if len(source) else 0.0
    return threshold, {
        "margin_split": actual_split,
        "margin_quantile": float(quantile),
        "margin_correct_only": correct_only,
        "margin_source_count": int(len(source)),
    }


def select_threshold_strategy(
    sweep: ThresholdSweep,
    model_artifact: ModelArtifact,
    calibration: CalibrationReport | None = None,
    target_selective_risk: float = 0.01,
    margin_threshold: float | None = None,
    review_cost_per_item: float = 1.0,
    selection_config: dict[str, object] | None = None,
    artifact_root: str | Path | None = None,
) -> ThresholdStrategy:
    candidates = [point for point in sweep.points if point.selective_risk <= target_selective_risk]
    if candidates:
        selected = max(candidates, key=lambda point: (point.coverage, -point.threshold))
        rule = "max_coverage_under_target_risk"
    else:
        selected = min(sweep.points, key=lambda point: (point.selective_risk, -point.coverage))
        rule = "min_risk_fallback"
    strategy = ThresholdStrategy(
        strategy_id=f"{model_artifact.artifact_id}-selective-v1",
        dataset_id=model_artifact.dataset_id,
        dataset_version_id=model_artifact.dataset_version_id,
        model_artifact_id=model_artifact.artifact_id,
        calibration_artifact_id=calibration.artifact_id if calibration else None,
        calibration_method=calibration.method if calibration else "none",
        temperature=calibration.temperature if calibration else 1.0,
        split=sweep.split,
        accept_threshold=selected.threshold,
        margin_threshold=margin_threshold if margin_threshold is not None else 0.0,
        target_selective_risk=target_selective_risk,
        expected_coverage=selected.coverage,
        expected_selective_risk=selected.selective_risk,
        review_cost_per_item=review_cost_per_item,
        selection_rule=rule,
        selection_config={
            "threshold_candidates": len(sweep.points),
            **(selection_config or {}),
        },
    )
    if artifact_root is not None:
        write_json(Path(artifact_root) / model_artifact.artifact_id / "threshold_strategy.json", strategy)
    return strategy
