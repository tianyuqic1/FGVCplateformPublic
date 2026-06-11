from __future__ import annotations

import numpy as np

from finevision.ml_toolkit.metrics import softmax
from finevision.ml_toolkit.training import apply_linear_head
from finevision.schemas.artifacts import AbstentionDecision, InferenceResult, ModelArtifact


def _nearest_neighbors(query: np.ndarray, features: np.ndarray, sample_ids: list[str], labels: list[str], k: int) -> list[dict[str, float | str]]:
    distances = np.linalg.norm(features - query.reshape(1, -1), axis=1)
    order = np.argsort(distances)[:k]
    return [
        {"sample_id": sample_ids[idx], "label": labels[idx], "distance": float(distances[idx])}
        for idx in order
    ]


def run_inference(
    model_artifact: ModelArtifact,
    model_state: dict[str, np.ndarray],
    query_features: np.ndarray,
    reference_features: np.ndarray,
    reference_sample_ids: list[str],
    reference_labels: list[str],
    confidence_threshold: float = 0.7,
    margin_threshold: float = 0.12,
    ood_distance_threshold: float | None = None,
    top_k: int = 3,
) -> InferenceResult:
    query = query_features.reshape(1, -1)
    logits = apply_linear_head(
        query,
        model_state["weights"],
        model_state["bias"],
        model_state["feature_mean"],
        model_state["feature_std"],
    )
    probabilities = softmax(logits)[0]
    order = np.argsort(probabilities)[::-1]
    capped = order[: min(top_k, len(model_artifact.classes))]
    top = [
        {"label": model_artifact.classes[idx], "score": float(probabilities[idx])}
        for idx in capped
    ]
    confidence = float(probabilities[order[0]])
    second = float(probabilities[order[1]]) if len(order) > 1 else 0.0
    margin = confidence - second
    neighbors = _nearest_neighbors(query_features, reference_features, reference_sample_ids, reference_labels, k=min(3, len(reference_sample_ids)))
    ood_score = float(neighbors[0]["distance"]) if neighbors else None

    reasons: list[str] = []
    decision = "accept"
    if ood_distance_threshold is not None and ood_score is not None and ood_score > ood_distance_threshold:
        decision = "reject_ood"
        reasons.append("embedding_distance_above_threshold")
    if confidence < confidence_threshold:
        decision = "abstain" if decision == "accept" else decision
        reasons.append("confidence_below_threshold")
    if margin < margin_threshold:
        decision = "abstain" if decision == "accept" else decision
        reasons.append("top1_top2_margin_below_threshold")
    if not reasons:
        reasons.append("meets_acceptance_thresholds")

    return InferenceResult(
        dataset_id=model_artifact.dataset_id,
        dataset_version_id=model_artifact.dataset_version_id,
        model_artifact_id=model_artifact.artifact_id,
        top_k=top,
        decision=AbstentionDecision(
            decision=decision,  # type: ignore[arg-type]
            reasons=reasons,
            thresholds={
                "confidence": confidence_threshold,
                "margin": margin_threshold,
                **({"ood_distance": ood_distance_threshold} if ood_distance_threshold is not None else {}),
            },
            margin=margin,
            confidence=confidence,
            ood_score=ood_score,
        ),
        nearest_neighbors=neighbors,
    )
