from __future__ import annotations

import numpy as np

from finevision.ml_toolkit.online_abstention import (
    FeedbackDecisionSample,
    decide_with_thresholds,
    evaluate_policy,
    propose_risk_constrained_policy,
)
from finevision.ml_toolkit.inference import run_inference
from finevision.ml_toolkit.thresholds import select_threshold_strategy
from finevision.schemas.artifacts import ModelArtifact, ThresholdPoint, ThresholdStrategy, ThresholdSweep
from finevision.db.schema import abstention_policy_versions, abstention_shadow_decisions


def test_policy_proposal_selects_max_coverage_under_target_risk() -> None:
    model_artifact = _model_artifact()
    sweep = ThresholdSweep(
        strategy_id="model-policy-confidence-sweep",
        dataset_id=model_artifact.dataset_id,
        dataset_version_id=model_artifact.dataset_version_id,
        model_artifact_id=model_artifact.artifact_id,
        calibration_artifact_id=None,
        split="validation",
        points=[
            ThresholdPoint(
                threshold=0.2,
                coverage=0.95,
                selective_risk=0.08,
                abstention_rate=0.05,
                estimated_review_cost=5.0,
            ),
            ThresholdPoint(
                threshold=0.35,
                coverage=0.82,
                selective_risk=0.04,
                abstention_rate=0.18,
                estimated_review_cost=18.0,
            ),
            ThresholdPoint(
                threshold=0.55,
                coverage=0.74,
                selective_risk=0.01,
                abstention_rate=0.26,
                estimated_review_cost=26.0,
            ),
            ThresholdPoint(
                threshold=0.75,
                coverage=0.82,
                selective_risk=0.03,
                abstention_rate=0.18,
                estimated_review_cost=18.0,
            ),
        ],
    )

    proposal = select_threshold_strategy(
        sweep=sweep,
        model_artifact=model_artifact,
        target_selective_risk=0.05,
        review_cost_per_item=2.0,
        selection_config={"proposal_mode": "risk_constrained"},
    )

    assert proposal.selection_rule == "max_coverage_under_target_risk"
    assert proposal.accept_threshold == 0.35
    assert proposal.expected_coverage == 0.82
    assert proposal.expected_selective_risk == 0.04
    assert proposal.target_selective_risk == 0.05
    assert proposal.review_cost_per_item == 2.0
    assert proposal.selection_config["threshold_candidates"] == 4
    assert proposal.selection_config["proposal_mode"] == "risk_constrained"


def test_policy_decision_accept_abstain_and_reject_ood() -> None:
    model_artifact = _model_artifact()
    model_state = {
        "weights": np.array([[1.0, -1.0], [-1.0, 1.0]], dtype=np.float32),
        "bias": np.array([0.0, 0.0], dtype=np.float32),
        "feature_mean": np.array([0.0, 0.0], dtype=np.float32),
        "feature_std": np.array([1.0, 1.0], dtype=np.float32),
    }
    reference_features = np.array([[2.0, 0.0], [0.0, 2.0]], dtype=np.float32)
    reference_sample_ids = ["sample-a", "sample-b"]
    reference_labels = ["class-a", "class-b"]

    accepted = run_inference(
        model_artifact=model_artifact,
        model_state=model_state,
        threshold_strategy=_threshold_strategy(accept_threshold=0.8, margin_threshold=0.2),
        query_features=np.array([2.0, 0.0], dtype=np.float32),
        reference_features=reference_features,
        reference_sample_ids=reference_sample_ids,
        reference_labels=reference_labels,
    )
    assert accepted.decision.decision == "accept"
    assert accepted.decision.reasons == ["meets_acceptance_thresholds"]

    abstained = run_inference(
        model_artifact=model_artifact,
        model_state=model_state,
        threshold_strategy=_threshold_strategy(accept_threshold=0.8, margin_threshold=0.2),
        query_features=np.array([0.1, 0.0], dtype=np.float32),
        reference_features=reference_features,
        reference_sample_ids=reference_sample_ids,
        reference_labels=reference_labels,
    )
    assert abstained.decision.decision == "abstain"
    assert "confidence_below_threshold" in abstained.decision.reasons
    assert "top1_top2_margin_below_threshold" in abstained.decision.reasons

    rejected = run_inference(
        model_artifact=model_artifact,
        model_state=model_state,
        threshold_strategy=_threshold_strategy(accept_threshold=0.0, margin_threshold=0.0),
        query_features=np.array([20.0, 20.0], dtype=np.float32),
        reference_features=reference_features,
        reference_sample_ids=reference_sample_ids,
        reference_labels=reference_labels,
        ood_distance_threshold=5.0,
    )
    assert rejected.decision.decision == "reject_ood"
    assert rejected.decision.reasons == ["embedding_distance_above_threshold"]
    assert rejected.decision.ood_score is not None
    assert rejected.decision.ood_score > 5.0


def test_online_abstention_decision_rules_are_threshold_only() -> None:
    assert decide_with_thresholds(confidence=0.91, margin=0.44, ood_score=0.3, tau_conf=0.9, tau_margin=0.2, tau_ood=1.0) == (
        "accept",
        ["meets_acceptance_thresholds"],
    )
    decision, reasons = decide_with_thresholds(
        confidence=0.88,
        margin=0.11,
        ood_score=0.3,
        tau_conf=0.9,
        tau_margin=0.2,
        tau_ood=1.0,
    )
    assert decision == "abstain"
    assert reasons == ["confidence_below_threshold", "top1_top2_margin_below_threshold"]
    assert decide_with_thresholds(confidence=0.99, margin=0.9, ood_score=2.0, tau_conf=0.9, tau_margin=0.2, tau_ood=1.0) == (
        "reject_ood",
        ["embedding_distance_above_threshold"],
    )


def test_feedback_driven_policy_proposal_maximizes_coverage_under_risk() -> None:
    samples = [
        _feedback("e1", confidence=0.95, margin=0.8, predicted="class-a", final="class-a"),
        _feedback("e2", confidence=0.86, margin=0.7, predicted="class-a", final="class-a"),
        _feedback("e3", confidence=0.81, margin=0.3, predicted="class-a", final="class-b"),
        _feedback("e4", confidence=0.62, margin=0.5, predicted="class-b", final="class-b"),
    ]

    proposal = propose_risk_constrained_policy(samples, target_selective_risk=0.0, review_cost_per_item=2.0)

    assert proposal.selection_config["selection_rule"] == "max_coverage_under_target_risk"
    assert proposal.metrics["selective_risk"] == 0.0
    assert proposal.metrics["coverage"] >= 0.5
    assert proposal.metrics["source_feedback_count"] == 4
    assert proposal.tau_conf > 0.81 or proposal.tau_margin > 0.3


def test_policy_proposal_without_feedback_reports_insufficient_feedback() -> None:
    proposal = propose_risk_constrained_policy([], target_selective_risk=0.05, review_cost_per_item=2.0)

    assert proposal.tau_conf == 1.0
    assert proposal.tau_margin == 1.0
    assert proposal.tau_ood is None
    assert proposal.metrics["source_feedback_count"] == 0
    assert proposal.metrics["coverage"] == 0.0
    assert proposal.selection_config["selection_rule"] == "insufficient_feedback"
    assert proposal.selection_config["eligible_feedback_count"] == 0


def test_policy_evaluation_counts_ood_as_risk_when_accepted() -> None:
    samples = [
        _feedback("e1", confidence=0.95, margin=0.8, predicted="class-a", final="class-a"),
        FeedbackDecisionSample(
            inference_event_id="e2",
            current_decision="reject_ood",
            confidence=0.97,
            margin=0.9,
            ood_score=5.0,
            predicted_label="class-a",
            final_label=None,
            final_outcome="ood",
        ),
    ]

    loose = evaluate_policy(samples, tau_conf=0.5, tau_margin=0.0, tau_ood=None, target_selective_risk=0.05)
    strict_ood = evaluate_policy(samples, tau_conf=0.5, tau_margin=0.0, tau_ood=1.0, target_selective_risk=0.05)

    assert loose["accepted_count"] == 2
    assert loose["error_count"] == 1
    assert loose["selective_risk"] == 0.5
    assert strict_ood["accepted_count"] == 1
    assert strict_ood["distribution"]["reject_ood"] == 1
    assert strict_ood["selective_risk"] == 0.0


def test_abstention_schema_metadata_includes_shadow_uniqueness_and_indexes() -> None:
    assert "ix_abstention_policy_versions_scope_created_at" in {
        index.name for index in abstention_policy_versions.indexes
    }
    assert "ix_abstention_policy_versions_status_created_at" in {
        index.name for index in abstention_policy_versions.indexes
    }
    assert "uq_abstention_shadow_policy_inference" in {
        constraint.name for constraint in abstention_shadow_decisions.constraints
    }
    assert "ix_abstention_shadow_policy_diff_created_at" in {
        index.name for index in abstention_shadow_decisions.indexes
    }


def _model_artifact() -> ModelArtifact:
    return ModelArtifact(
        artifact_id="model-policy-v1",
        dataset_id="policy-toy",
        dataset_version_id="dataset@policy-toy-001",
        feature_artifact_id="features-policy-v1",
        model_path="/tmp/model.npz",
        classes=["class-a", "class-b"],
        head_type="linear",
        feature_dim=2,
        training_config={},
    )


def _threshold_strategy(*, accept_threshold: float, margin_threshold: float) -> ThresholdStrategy:
    return ThresholdStrategy(
        strategy_id="policy-thresholds-v1",
        dataset_id="policy-toy",
        dataset_version_id="dataset@policy-toy-001",
        model_artifact_id="model-policy-v1",
        calibration_artifact_id=None,
        calibration_method="none",
        temperature=1.0,
        split="validation",
        accept_threshold=accept_threshold,
        margin_threshold=margin_threshold,
        target_selective_risk=0.05,
        expected_coverage=1.0,
        expected_selective_risk=0.0,
        review_cost_per_item=1.0,
        selection_rule="test",
    )


def _feedback(
    inference_event_id: str,
    *,
    confidence: float,
    margin: float,
    predicted: str,
    final: str,
) -> FeedbackDecisionSample:
    return FeedbackDecisionSample(
        inference_event_id=inference_event_id,
        current_decision="abstain",
        confidence=confidence,
        margin=margin,
        ood_score=0.2,
        predicted_label=predicted,
        final_label=final,
        final_outcome="confirmed_label" if predicted == final else "corrected_label",
    )
