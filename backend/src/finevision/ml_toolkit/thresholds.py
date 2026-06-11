from __future__ import annotations

from pathlib import Path

import numpy as np

from finevision.ml_toolkit.artifacts import write_json
from finevision.ml_toolkit.metrics import softmax
from finevision.schemas.artifacts import FeatureArtifact, ModelArtifact, ThresholdPoint, ThresholdSweep


def sweep_confidence_thresholds(
    feature_artifact: FeatureArtifact,
    model_artifact: ModelArtifact,
    logits: np.ndarray,
    thresholds: list[float] | None = None,
    review_cost_per_item: float = 1.0,
    artifact_root: str | Path | None = None,
) -> ThresholdSweep:
    thresholds = thresholds or [0.1, 0.15, 0.2, 0.3, 0.5, 0.7, 0.9]
    labels = np.array(feature_artifact.labels)
    classes = np.array(model_artifact.classes)
    true_idx = np.array([int(np.where(classes == label)[0][0]) for label in labels], dtype=np.int64)
    probabilities = softmax(logits)
    predicted = probabilities.argmax(axis=1)
    confidence = probabilities.max(axis=1)

    points = []
    for threshold in thresholds:
        accepted = confidence >= threshold
        coverage = float(accepted.mean()) if len(accepted) else 0.0
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
                estimated_review_cost=float((~accepted).sum() * review_cost_per_item),
            )
        )

    sweep = ThresholdSweep(
        strategy_id=f"{model_artifact.artifact_id}-confidence-sweep",
        dataset_id=feature_artifact.dataset_id,
        dataset_version_id=feature_artifact.dataset_version_id,
        model_artifact_id=model_artifact.artifact_id,
        points=points,
    )
    if artifact_root is not None:
        write_json(Path(artifact_root) / model_artifact.artifact_id / "threshold_sweep.json", sweep)
    return sweep
