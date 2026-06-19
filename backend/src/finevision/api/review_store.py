from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.pool import NullPool

from finevision.db.schema import (
    dataset_versions,
    datasets,
    feedback_items,
    inference_events,
    model_versions,
    review_items,
    training_runs,
)


@dataclass(frozen=True)
class InferenceEventRecord:
    inference_event_id: str
    dataset_id: str
    dataset_version_id: str
    model_version_id: str
    input_type: str
    input_ref: str | None
    sample_id: str | None
    decision: str
    created_at: str


@dataclass(frozen=True)
class FeedbackItemRecord:
    feedback_item_id: str
    review_item_id: str
    final_outcome: str
    destination: str
    final_label: str | None
    reviewer_note: str | None
    created_by: str | None
    created_at: str


@dataclass(frozen=True)
class ReviewItemRecord:
    review_item_id: str
    inference_event_id: str
    dataset_id: str
    dataset_version_id: str
    model_version_id: str
    sample_id: str | None
    input_ref: str | None
    status: str
    risk_type: str
    priority: int
    reason: str
    reason_codes: list[str]
    context: dict[str, Any]
    assistance_metadata: dict[str, Any]
    created_at: str
    updated_at: str
    submitted_at: str | None = None
    feedbacked_at: str | None = None
    completed_by: str | None = None
    feedback: FeedbackItemRecord | None = None


class DatabaseReviewStore:
    def __init__(self, database_url: str | Engine) -> None:
        self.engine = database_url if isinstance(database_url, Engine) else create_engine(database_url, poolclass=NullPool)

    def record_inference_result(
        self,
        *,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
    ) -> tuple[InferenceEventRecord, ReviewItemRecord | None]:
        now = _now()
        dataset_version_key = str(response_payload["dataset_version_id"])
        model_key = str(response_payload["model_version_id"])
        input_payload = dict(response_payload.get("input") or {})
        result_payload = dict(response_payload.get("result") or {})
        decision_payload = dict(result_payload.get("decision") or {})
        decision = str(decision_payload.get("decision") or decision_payload.get("value") or "abstain")
        reasons = list(decision_payload.get("reasons") or [])
        input_type, input_ref, sample_id = _input_identity(input_payload)

        with self.engine.begin() as conn:
            context_row = conn.execute(
                _review_context_select().where(
                    dataset_versions.c.version_key == dataset_version_key,
                    model_versions.c.model_key == model_key,
                )
            ).mappings().first()
            if context_row is None:
                raise ValueError(f"Model version not found for dataset version: {model_key}")

            event_db_id = uuid4()
            event_key = f"inference-{uuid4().hex[:12]}"
            conn.execute(
                inference_events.insert().values(
                    id=event_db_id,
                    event_key=event_key,
                    dataset_id=context_row["dataset_db_id"],
                    dataset_version_id=context_row["dataset_version_db_id"],
                    model_version_id=context_row["model_version_db_id"],
                    model_status=str(response_payload.get("model_status") or context_row["model_status"]),
                    model_artifact_id=context_row["model_artifact_id"],
                    feature_artifact_id=context_row["feature_artifact_id"],
                    threshold_strategy_artifact_id=context_row["threshold_strategy_artifact_id"],
                    input_type=input_type,
                    input_ref=input_ref,
                    sample_id=sample_id,
                    decision=decision,
                    confidence=_optional_float(decision_payload.get("confidence")),
                    margin=_optional_float(decision_payload.get("margin")),
                    ood_score=_optional_float(decision_payload.get("ood_score")),
                    reasons=reasons,
                    request_payload=request_payload,
                    result_payload=response_payload,
                    created_at=now,
                )
            )
            review_key: str | None = None
            if decision in {"abstain", "reject_ood"}:
                risk_type, priority, reason = _review_routing(decision, reasons)
                review_key = f"review-{uuid4().hex[:12]}"
                conn.execute(
                    review_items.insert().values(
                        id=uuid4(),
                        review_key=review_key,
                        inference_event_id=event_db_id,
                        dataset_id=context_row["dataset_db_id"],
                        dataset_version_id=context_row["dataset_version_db_id"],
                        model_version_id=context_row["model_version_db_id"],
                        sample_id=sample_id,
                        input_ref=input_ref,
                        status="pending",
                        risk_type=risk_type,
                        priority=priority,
                        reason=reason,
                        reason_codes=reasons,
                        context=_review_context_payload(response_payload),
                        assistance_metadata={},
                        created_at=now,
                        updated_at=now,
                    )
                )

        event = self.get_inference_event(event_key)
        if event is None:
            raise ValueError(f"Inference event was not created: {event_key}")
        return event, self.get_review_item(review_key) if review_key else None

    def get_inference_event(self, event_id: str) -> InferenceEventRecord | None:
        with self.engine.begin() as conn:
            row = conn.execute(_inference_event_select().where(inference_events.c.event_key == event_id)).mappings().first()
        return _inference_event_from_row(row) if row else None

    def list_review_items(
        self,
        *,
        status: str | None = "pending",
        dataset_id: str | None = None,
        limit: int = 50,
    ) -> list[ReviewItemRecord]:
        query = _review_item_select().order_by(review_items.c.priority.asc(), review_items.c.created_at.asc()).limit(limit)
        if status:
            query = query.where(review_items.c.status == status)
        if dataset_id:
            query = query.where(datasets.c.dataset_key == dataset_id)
        with self.engine.begin() as conn:
            rows = conn.execute(query).mappings().all()
        return [_review_item_from_row(row) for row in rows]

    def get_review_item(self, review_id: str | None) -> ReviewItemRecord | None:
        if not review_id:
            return None
        with self.engine.begin() as conn:
            row = conn.execute(_review_item_select().where(review_items.c.review_key == review_id)).mappings().first()
        return _review_item_from_row(row) if row else None

    def complete_review(
        self,
        *,
        review_id: str,
        final_outcome: str,
        destination: str,
        final_label: str | None = None,
        reviewer_note: str | None = None,
        reviewer: str | None = None,
    ) -> tuple[ReviewItemRecord, FeedbackItemRecord]:
        _validate_feedback(final_outcome, destination, final_label)
        now = _now()
        feedback_key = f"feedback-{uuid4().hex[:12]}"
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(review_items)
                .where(review_items.c.review_key == review_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                raise ValueError(f"Review item not found: {review_id}")
            if row["status"] != "pending":
                raise RuntimeError(f"Review item is already completed: {review_id}")

            feedback_db_id = uuid4()
            conn.execute(
                feedback_items.insert().values(
                    id=feedback_db_id,
                    feedback_key=feedback_key,
                    review_item_id=row["id"],
                    inference_event_id=row["inference_event_id"],
                    dataset_id=row["dataset_id"],
                    dataset_version_id=row["dataset_version_id"],
                    model_version_id=row["model_version_id"],
                    sample_id=row["sample_id"],
                    final_label=final_label,
                    final_outcome=final_outcome,
                    destination=destination,
                    reviewer_note=reviewer_note,
                    feedback_metadata={"source": "human_review_mvp"},
                    created_by=reviewer,
                    created_at=now,
                )
            )
            conn.execute(
                review_items.update()
                .where(review_items.c.id == row["id"])
                .values(
                    status="feedbacked",
                    submitted_at=now,
                    feedbacked_at=now,
                    completed_by=reviewer,
                    updated_at=now,
                )
            )

        review = self.get_review_item(review_id)
        feedback = self.get_feedback_item(feedback_key)
        if review is None or feedback is None:
            raise ValueError(f"Review completion failed: {review_id}")
        return review, feedback

    def get_feedback_item(self, feedback_id: str) -> FeedbackItemRecord | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(feedback_items).where(feedback_items.c.feedback_key == feedback_id)
            ).mappings().first()
        return _feedback_item_from_row(row) if row else None


def _review_context_select() -> sa.Select[Any]:
    return (
        sa.select(
            datasets.c.id.label("dataset_db_id"),
            dataset_versions.c.id.label("dataset_version_db_id"),
            model_versions.c.id.label("model_version_db_id"),
            model_versions.c.status.label("model_status"),
            model_versions.c.model_artifact_id,
            training_runs.c.feature_artifact_id,
            model_versions.c.threshold_strategy_artifact_id,
        )
        .select_from(
            model_versions.join(datasets, datasets.c.id == model_versions.c.dataset_id)
            .join(dataset_versions, dataset_versions.c.id == model_versions.c.dataset_version_id)
            .join(training_runs, training_runs.c.id == model_versions.c.training_run_id)
        )
    )


def _inference_event_select() -> sa.Select[Any]:
    return (
        sa.select(
            inference_events.c.event_key,
            datasets.c.dataset_key,
            dataset_versions.c.version_key,
            model_versions.c.model_key,
            inference_events.c.input_type,
            inference_events.c.input_ref,
            inference_events.c.sample_id,
            inference_events.c.decision,
            inference_events.c.created_at,
        )
        .select_from(
            inference_events.join(datasets, datasets.c.id == inference_events.c.dataset_id)
            .join(dataset_versions, dataset_versions.c.id == inference_events.c.dataset_version_id)
            .join(model_versions, model_versions.c.id == inference_events.c.model_version_id)
        )
    )


def _review_item_select() -> sa.Select[Any]:
    feedback_subquery = (
        sa.select(
            feedback_items.c.review_item_id,
            feedback_items.c.feedback_key,
            feedback_items.c.final_outcome,
            feedback_items.c.destination,
            feedback_items.c.final_label,
            feedback_items.c.reviewer_note,
            feedback_items.c.created_by,
            feedback_items.c.created_at.label("feedback_created_at"),
        ).subquery()
    )
    return (
        sa.select(
            review_items.c.review_key,
            inference_events.c.event_key,
            datasets.c.dataset_key,
            dataset_versions.c.version_key,
            model_versions.c.model_key,
            review_items.c.sample_id,
            review_items.c.input_ref,
            review_items.c.status,
            review_items.c.risk_type,
            review_items.c.priority,
            review_items.c.reason,
            review_items.c.reason_codes,
            review_items.c.context,
            review_items.c.assistance_metadata,
            review_items.c.created_at,
            review_items.c.updated_at,
            review_items.c.submitted_at,
            review_items.c.feedbacked_at,
            review_items.c.completed_by,
            feedback_subquery.c.feedback_key,
            feedback_subquery.c.final_outcome,
            feedback_subquery.c.destination,
            feedback_subquery.c.final_label,
            feedback_subquery.c.reviewer_note,
            feedback_subquery.c.created_by,
            feedback_subquery.c.feedback_created_at,
        )
        .select_from(
            review_items.join(inference_events, inference_events.c.id == review_items.c.inference_event_id)
            .join(datasets, datasets.c.id == review_items.c.dataset_id)
            .join(dataset_versions, dataset_versions.c.id == review_items.c.dataset_version_id)
            .join(model_versions, model_versions.c.id == review_items.c.model_version_id)
            .outerjoin(feedback_subquery, feedback_subquery.c.review_item_id == review_items.c.id)
        )
    )


def _input_identity(input_payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    sample_id = input_payload.get("sample_id")
    if sample_id:
        return "sample", str(sample_id), str(sample_id)
    if input_payload.get("uploaded_image_path"):
        return "upload", str(input_payload.get("uploaded_image_path")), None
    image_path = input_payload.get("image_path")
    return "image_path", str(image_path) if image_path else None, None


def _review_context_payload(response_payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(response_payload.get("result") or {})
    return {
        "input": dict(response_payload.get("input") or {}),
        "dataset_id": response_payload.get("dataset_id"),
        "dataset_version_id": response_payload.get("dataset_version_id"),
        "model_version_id": response_payload.get("model_version_id"),
        "model_status": response_payload.get("model_status"),
        "model_artifact_id": response_payload.get("model_artifact_id"),
        "feature_artifact_id": response_payload.get("feature_artifact_id"),
        "threshold_strategy_id": response_payload.get("threshold_strategy_id"),
        "top_k": result.get("top_k") or [],
        "decision": result.get("decision") or {},
        "nearest_neighbors": result.get("nearest_neighbors") or [],
    }


def _review_routing(decision: str, reasons: list[str]) -> tuple[str, int, str]:
    if decision == "reject_ood":
        return "ood_candidate", 10, "Model rejected the sample as an OOD candidate."
    if "confidence_below_threshold" in reasons and "top1_top2_margin_below_threshold" in reasons:
        return "mixed", 40, "Model abstained for multiple threshold reasons."
    if "confidence_below_threshold" in reasons:
        return "low_confidence", 50, "Model confidence is below the acceptance threshold."
    if "top1_top2_margin_below_threshold" in reasons:
        return "low_margin", 60, "Top-1 and top-2 scores are too close."
    return "mixed", 70, "Model abstained and requires human review."


def _validate_feedback(final_outcome: str, destination: str, final_label: str | None) -> None:
    allowed_destinations = {
        "confirmed_label": {"training_candidate"},
        "corrected_label": {"training_candidate"},
        "ood": {"ood_stress"},
        "bad_image": {"bad_image"},
        "uncertain": {"taxonomy_dispute", "ignore"},
        "ignore": {"ignore"},
    }
    if final_outcome not in allowed_destinations:
        raise ValueError(f"Unsupported final_outcome: {final_outcome}")
    if destination not in allowed_destinations[final_outcome]:
        raise ValueError(f"destination {destination} is not valid for final_outcome {final_outcome}")
    if final_outcome in {"confirmed_label", "corrected_label"} and not str(final_label or "").strip():
        raise ValueError("final_label is required for label feedback outcomes")


def _inference_event_from_row(row: Any) -> InferenceEventRecord:
    return InferenceEventRecord(
        inference_event_id=row["event_key"],
        dataset_id=row["dataset_key"],
        dataset_version_id=row["version_key"],
        model_version_id=row["model_key"],
        input_type=row["input_type"],
        input_ref=row["input_ref"],
        sample_id=row["sample_id"],
        decision=row["decision"],
        created_at=_to_iso(row["created_at"]),
    )


def _review_item_from_row(row: Any) -> ReviewItemRecord:
    feedback = None
    if row["feedback_key"]:
        feedback = FeedbackItemRecord(
            feedback_item_id=row["feedback_key"],
            review_item_id=row["review_key"],
            final_outcome=row["final_outcome"],
            destination=row["destination"],
            final_label=row["final_label"],
            reviewer_note=row["reviewer_note"],
            created_by=row["created_by"],
            created_at=_to_iso(row["feedback_created_at"]),
        )
    return ReviewItemRecord(
        review_item_id=row["review_key"],
        inference_event_id=row["event_key"],
        dataset_id=row["dataset_key"],
        dataset_version_id=row["version_key"],
        model_version_id=row["model_key"],
        sample_id=row["sample_id"],
        input_ref=row["input_ref"],
        status=row["status"],
        risk_type=row["risk_type"],
        priority=int(row["priority"]),
        reason=row["reason"],
        reason_codes=list(row["reason_codes"] or []),
        context=dict(row["context"] or {}),
        assistance_metadata=dict(row["assistance_metadata"] or {}),
        created_at=_to_iso(row["created_at"]),
        updated_at=_to_iso(row["updated_at"]),
        submitted_at=_to_iso(row["submitted_at"]) if row["submitted_at"] else None,
        feedbacked_at=_to_iso(row["feedbacked_at"]) if row["feedbacked_at"] else None,
        completed_by=row["completed_by"],
        feedback=feedback,
    )


def _feedback_item_from_row(row: Any) -> FeedbackItemRecord:
    return FeedbackItemRecord(
        feedback_item_id=row["feedback_key"],
        review_item_id=str(row["review_item_id"]),
        final_outcome=row["final_outcome"],
        destination=row["destination"],
        final_label=row["final_label"],
        reviewer_note=row["reviewer_note"],
        created_by=row["created_by"],
        created_at=_to_iso(row["created_at"]),
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _now() -> datetime:
    return datetime.now(UTC)


def _to_iso(value: datetime) -> str:
    return value.isoformat()
