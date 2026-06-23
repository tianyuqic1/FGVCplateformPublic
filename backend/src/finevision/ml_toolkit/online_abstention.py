from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

Decision = Literal["accept", "abstain", "reject_ood"]
DecisionDiff = Literal[
    "same",
    "new_accepts_old_abstains",
    "new_abstains_old_accepts",
    "new_rejects_ood",
    "other_change",
]


@dataclass(frozen=True)
class FeedbackDecisionSample:
    inference_event_id: str
    current_decision: Decision
    confidence: float
    margin: float
    ood_score: float | None
    predicted_label: str | None
    final_label: str | None
    final_outcome: str


@dataclass(frozen=True)
class AbstentionPolicyCandidate:
    tau_conf: float
    tau_margin: float
    tau_ood: float | None
    target_selective_risk: float
    metrics: dict[str, float | int | str | dict[str, int]]
    selection_config: dict[str, object]


def decide_with_thresholds(
    *,
    confidence: float,
    margin: float,
    ood_score: float | None,
    tau_conf: float,
    tau_margin: float,
    tau_ood: float | None,
) -> tuple[Decision, list[str]]:
    reasons: list[str] = []
    if tau_ood is not None and ood_score is not None and ood_score > tau_ood:
        return "reject_ood", ["embedding_distance_above_threshold"]
    if confidence < tau_conf:
        reasons.append("confidence_below_threshold")
    if margin < tau_margin:
        reasons.append("top1_top2_margin_below_threshold")
    if reasons:
        return "abstain", reasons
    return "accept", ["meets_acceptance_thresholds"]


def decision_diff(current: Decision, shadow: Decision) -> DecisionDiff:
    if current == shadow:
        return "same"
    if shadow == "accept" and current == "abstain":
        return "new_accepts_old_abstains"
    if shadow == "abstain" and current == "accept":
        return "new_abstains_old_accepts"
    if shadow == "reject_ood":
        return "new_rejects_ood"
    return "other_change"


def evaluate_policy(
    samples: list[FeedbackDecisionSample],
    *,
    tau_conf: float,
    tau_margin: float,
    tau_ood: float | None,
    target_selective_risk: float,
    review_cost_per_item: float = 1.0,
) -> dict[str, float | int | str | dict[str, int]]:
    eligible = [_sample for _sample in samples if _is_evaluable(_sample)]
    distribution = {"accept": 0, "abstain": 0, "reject_ood": 0}
    diff_counts = {
        "same": 0,
        "new_accepts_old_abstains": 0,
        "new_abstains_old_accepts": 0,
        "new_rejects_ood": 0,
        "other_change": 0,
    }
    accepted = 0
    errors = 0

    for sample in eligible:
        shadow, _ = decide_with_thresholds(
            confidence=sample.confidence,
            margin=sample.margin,
            ood_score=sample.ood_score,
            tau_conf=tau_conf,
            tau_margin=tau_margin,
            tau_ood=tau_ood,
        )
        distribution[shadow] += 1
        diff_counts[decision_diff(sample.current_decision, shadow)] += 1
        if shadow == "accept":
            accepted += 1
            if not _accepted_prediction_is_correct(sample):
                errors += 1

    source_count = len(eligible)
    coverage = float(accepted / source_count) if source_count else 0.0
    selective_risk = float(errors / accepted) if accepted else 0.0
    review_count = source_count - accepted
    return {
        "source_feedback_count": source_count,
        "accepted_count": accepted,
        "error_count": errors,
        "coverage": coverage,
        "selective_risk": selective_risk,
        "abstention_rate": 1.0 - coverage if source_count else 0.0,
        "review_count": review_count,
        "estimated_review_cost": float(review_count * review_cost_per_item),
        "target_selective_risk": float(target_selective_risk),
        "distribution": distribution,
        "decision_diff_counts": diff_counts,
        "status_note": "ok" if source_count >= 5 else "insufficient_feedback",
    }


def propose_risk_constrained_policy(
    samples: list[FeedbackDecisionSample],
    *,
    target_selective_risk: float,
    review_cost_per_item: float = 1.0,
) -> AbstentionPolicyCandidate:
    eligible = [_sample for _sample in samples if _is_evaluable(_sample)]
    if not eligible:
        return AbstentionPolicyCandidate(
            tau_conf=1.0,
            tau_margin=1.0,
            tau_ood=None,
            target_selective_risk=target_selective_risk,
            metrics=evaluate_policy(
                [],
                tau_conf=1.0,
                tau_margin=1.0,
                tau_ood=None,
                target_selective_risk=target_selective_risk,
                review_cost_per_item=review_cost_per_item,
            ),
            selection_config={
                "selection_rule": "insufficient_feedback",
                "candidate_count": 0,
                "eligible_feedback_count": 0,
            },
        )

    candidate_count = 0
    best_feasible: tuple[float, float, float, float | None, dict[str, object]] | None = None
    best_fallback: tuple[float, float, float, float | None, dict[str, object]] | None = None
    conf_candidates = _candidate_values([sample.confidence for sample in eligible], low=0.0, high=1.0)
    margin_candidates = _candidate_values([sample.margin for sample in eligible], low=0.0, high=1.0)
    ood_scores = [sample.ood_score for sample in eligible if sample.ood_score is not None]
    ood_candidates = [None] + _ood_candidate_values(ood_scores)

    for tau_conf in conf_candidates:
        for tau_margin in margin_candidates:
            for tau_ood in ood_candidates:
                candidate_count += 1
                metrics = evaluate_policy(
                    eligible,
                    tau_conf=tau_conf,
                    tau_margin=tau_margin,
                    tau_ood=tau_ood,
                    target_selective_risk=target_selective_risk,
                    review_cost_per_item=review_cost_per_item,
                )
                score = (
                    float(metrics["coverage"]),
                    -float(metrics["estimated_review_cost"]),
                    -tau_conf,
                    -tau_margin,
                )
                fallback_score = (
                    -float(metrics["selective_risk"]),
                    float(metrics["coverage"]),
                    -float(metrics["estimated_review_cost"]),
                )
                payload = (tau_conf, tau_margin, tau_ood, metrics)
                if float(metrics["selective_risk"]) <= target_selective_risk:
                    if best_feasible is None or score > best_feasible[:4]:
                        best_feasible = (*score, payload)  # type: ignore[assignment]
                if best_fallback is None or fallback_score > best_fallback[:3]:
                    best_fallback = (*fallback_score, payload)  # type: ignore[assignment]

    if best_feasible is not None:
        tau_conf, tau_margin, tau_ood, metrics = best_feasible[4]  # type: ignore[index,assignment]
        selection_rule = "max_coverage_under_target_risk"
    else:
        assert best_fallback is not None
        tau_conf, tau_margin, tau_ood, metrics = best_fallback[3]  # type: ignore[index,assignment]
        metrics = {**metrics, "status_note": "no_feasible_candidate"}
        selection_rule = "min_risk_fallback"

    return AbstentionPolicyCandidate(
        tau_conf=float(tau_conf),
        tau_margin=float(tau_margin),
        tau_ood=float(tau_ood) if tau_ood is not None else None,
        target_selective_risk=target_selective_risk,
        metrics=metrics,
        selection_config={
            "selection_rule": selection_rule,
            "candidate_count": candidate_count,
            "eligible_feedback_count": len(eligible),
        },
    )


def _candidate_values(values: list[float], *, low: float, high: float) -> list[float]:
    if not values:
        return [high]
    quantiles = np.linspace(0.0, 1.0, min(11, len(values) + 2))
    candidates = {low, high}
    candidates.update(float(np.clip(np.quantile(values, quantile), low, high)) for quantile in quantiles)
    return sorted(round(value, 6) for value in candidates)


def _ood_candidate_values(values: list[float]) -> list[float | None]:
    if not values:
        return []
    candidates = {0.0, max(values) + 1e-6}
    candidates.update(float(np.quantile(values, quantile)) for quantile in np.linspace(0.0, 1.0, min(11, len(values) + 2)))
    return sorted(round(value, 6) for value in candidates)


def _is_evaluable(sample: FeedbackDecisionSample) -> bool:
    return sample.final_outcome in {"confirmed_label", "corrected_label", "ood", "bad_image"}


def _accepted_prediction_is_correct(sample: FeedbackDecisionSample) -> bool:
    if sample.final_outcome in {"ood", "bad_image"}:
        return False
    if sample.final_label:
        return sample.predicted_label == sample.final_label
    return sample.final_outcome == "confirmed_label"
