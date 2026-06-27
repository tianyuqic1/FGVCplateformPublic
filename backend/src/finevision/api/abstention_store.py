from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.pool import NullPool

from finevision.db.schema import (
    abstention_policy_versions,
    abstention_shadow_decisions,
    dataset_versions,
    datasets,
    feedback_items,
    inference_events,
    model_versions,
)
from finevision.ml_toolkit.online_abstention import (
    FeedbackDecisionSample,
    decide_with_thresholds,
    decision_diff,
    propose_risk_constrained_policy,
)


class InsufficientFeedbackError(RuntimeError):
    pass


class AbstentionPolicyGateError(RuntimeError):
    pass


MIN_ACTIVATION_FEEDBACK_COUNT = 5


@dataclass(frozen=True)
class AbstentionPolicyRecord:
    policy_id: str
    dataset_id: str
    dataset_version_id: str
    model_version_id: str
    status: str
    target_selective_risk: float
    tau_conf: float
    tau_margin: float
    tau_ood: float | None
    source_feedback_count: int
    metrics: dict[str, Any]
    selection_config: dict[str, Any]
    created_by: str | None
    activated_by: str | None
    activation_reason: str | None
    activated_at: str | None
    deactivated_by: str | None
    deactivation_reason: str | None
    deactivated_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AbstentionShadowDecisionRecord:
    shadow_decision_id: str
    policy_id: str
    inference_event_id: str
    dataset_id: str
    dataset_version_id: str
    model_version_id: str
    current_decision: str
    shadow_decision: str
    decision_diff: str
    score_snapshot: dict[str, Any]
    created_at: str


class DatabaseAbstentionStore:
    def __init__(self, database_url: str | Engine) -> None:
        self.engine = database_url if isinstance(database_url, Engine) else create_engine(database_url, poolclass=NullPool)

    def propose_policy(
        self,
        *,
        dataset_version_id: str,
        model_version_id: str,
        target_selective_risk: float,
        review_cost_per_item: float = 1.0,
        created_by: str | None = None,
    ) -> AbstentionPolicyRecord:
        with self.engine.begin() as conn:
            context = conn.execute(
                _scope_select().where(
                    dataset_versions.c.version_key == dataset_version_id,
                    model_versions.c.model_key == model_version_id,
                )
            ).mappings().first()
        if context is None:
            raise ValueError(f"Model version not found for dataset version: {model_version_id}")

        samples = self._feedback_samples(dataset_version_id=dataset_version_id, model_version_id=model_version_id)
        proposal = propose_risk_constrained_policy(
            samples,
            target_selective_risk=target_selective_risk,
            review_cost_per_item=review_cost_per_item,
        )
        if int(proposal.metrics["source_feedback_count"]) == 0:
            raise InsufficientFeedbackError(
                "At least one evaluable feedback item is required before proposing an abstention policy."
            )
        now = _now()
        policy_key = f"policy-{uuid4().hex[:12]}"
        with self.engine.begin() as conn:
            policy_db_id = uuid4()
            conn.execute(
                abstention_policy_versions.insert().values(
                    id=policy_db_id,
                    policy_key=policy_key,
                    dataset_id=context["dataset_db_id"],
                    dataset_version_id=context["dataset_version_db_id"],
                    model_version_id=context["model_version_db_id"],
                    status="shadow",
                    target_selective_risk=target_selective_risk,
                    tau_conf=proposal.tau_conf,
                    tau_margin=proposal.tau_margin,
                    tau_ood=proposal.tau_ood,
                    source_feedback_count=int(proposal.metrics["source_feedback_count"]),
                    metrics=proposal.metrics,
                    selection_config=proposal.selection_config,
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._record_shadow_rows_for_scope(
                conn,
                policy_db_id=policy_db_id,
                dataset_version_db_id=context["dataset_version_db_id"],
                model_version_db_id=context["model_version_db_id"],
                created_at=now,
            )
        policy = self.get_policy(policy_key)
        if policy is None:
            raise ValueError(f"Abstention policy was not created: {policy_key}")
        return policy

    def list_policies(
        self,
        *,
        dataset_version_id: str | None = None,
        model_version_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AbstentionPolicyRecord]:
        query = _policy_select().order_by(abstention_policy_versions.c.created_at.desc()).limit(limit)
        if dataset_version_id:
            query = query.where(dataset_versions.c.version_key == dataset_version_id)
        if model_version_id:
            query = query.where(model_versions.c.model_key == model_version_id)
        if status:
            query = query.where(abstention_policy_versions.c.status == status)
        with self.engine.begin() as conn:
            rows = conn.execute(query).mappings().all()
        return [_policy_from_row(row) for row in rows]

    def get_policy(self, policy_id: str) -> AbstentionPolicyRecord | None:
        with self.engine.begin() as conn:
            row = conn.execute(_policy_select().where(abstention_policy_versions.c.policy_key == policy_id)).mappings().first()
        return _policy_from_row(row) if row else None

    def get_active_policy(self, *, dataset_version_id: str, model_version_id: str) -> AbstentionPolicyRecord | None:
        query = (
            _policy_select()
            .where(
                dataset_versions.c.version_key == dataset_version_id,
                model_versions.c.model_key == model_version_id,
                abstention_policy_versions.c.status == "active",
            )
            .order_by(abstention_policy_versions.c.activated_at.desc().nullslast(), abstention_policy_versions.c.updated_at.desc())
            .limit(1)
        )
        with self.engine.begin() as conn:
            row = conn.execute(query).mappings().first()
        return _policy_from_row(row) if row else None

    def activate_policy(
        self,
        policy_id: str,
        *,
        activated_by: str | None = None,
        activation_reason: str | None = None,
        min_feedback_count: int = MIN_ACTIVATION_FEEDBACK_COUNT,
    ) -> AbstentionPolicyRecord:
        reason = (activation_reason or "").strip()
        if not reason:
            raise AbstentionPolicyGateError("Activation requires a human-readable reason.")

        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(
                _policy_select_with_ids().where(abstention_policy_versions.c.policy_key == policy_id)
            ).mappings().first()
            if row is None:
                raise ValueError(f"Abstention policy not found: {policy_id}")
            if row["status"] not in {"shadow", "candidate", "superseded", "deactivated"}:
                raise AbstentionPolicyGateError(
                    "Only shadow, candidate, superseded, or deactivated policies can be activated; "
                    f"current status is {row['status']}."
                )

            self._assert_activation_gate(row, min_feedback_count=min_feedback_count)
            conn.execute(
                abstention_policy_versions.update()
                .where(
                    abstention_policy_versions.c.dataset_version_id == row["dataset_version_db_id"],
                    abstention_policy_versions.c.model_version_id == row["model_version_db_id"],
                    abstention_policy_versions.c.status == "active",
                    abstention_policy_versions.c.id != row["policy_db_id"],
                )
                .values(
                    status="superseded",
                    deactivated_by=activated_by,
                    deactivation_reason=f"superseded by {policy_id}",
                    deactivated_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                abstention_policy_versions.update()
                .where(abstention_policy_versions.c.id == row["policy_db_id"])
                .values(
                    status="active",
                    activated_by=activated_by,
                    activation_reason=reason,
                    activated_at=now,
                    deactivated_by=None,
                    deactivation_reason=None,
                    deactivated_at=None,
                    updated_at=now,
                )
            )
        policy = self.get_policy(policy_id)
        if policy is None:
            raise ValueError(f"Abstention policy was not activated: {policy_id}")
        return policy

    def deactivate_policy(
        self,
        policy_id: str,
        *,
        deactivated_by: str | None = None,
        deactivation_reason: str | None = None,
    ) -> AbstentionPolicyRecord:
        reason = (deactivation_reason or "").strip()
        if not reason:
            raise AbstentionPolicyGateError("Deactivation requires a human-readable reason.")

        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(
                _policy_select_with_ids().where(abstention_policy_versions.c.policy_key == policy_id)
            ).mappings().first()
            if row is None:
                raise ValueError(f"Abstention policy not found: {policy_id}")
            if row["status"] != "active":
                raise AbstentionPolicyGateError(f"Only active policies can be deactivated; current status is {row['status']}.")
            conn.execute(
                abstention_policy_versions.update()
                .where(abstention_policy_versions.c.id == row["policy_db_id"])
                .values(
                    status="deactivated",
                    deactivated_by=deactivated_by,
                    deactivation_reason=reason,
                    deactivated_at=now,
                    updated_at=now,
                )
            )
        policy = self.get_policy(policy_id)
        if policy is None:
            raise ValueError(f"Abstention policy was not deactivated: {policy_id}")
        return policy

    def list_shadow_decisions(
        self,
        *,
        policy_id: str,
        diff: str | None = None,
        limit: int = 100,
    ) -> list[AbstentionShadowDecisionRecord]:
        query = (
            _shadow_decision_select()
            .where(abstention_policy_versions.c.policy_key == policy_id)
            .order_by(abstention_shadow_decisions.c.created_at.desc())
            .limit(limit)
        )
        if diff:
            query = query.where(abstention_shadow_decisions.c.decision_diff == diff)
        with self.engine.begin() as conn:
            rows = conn.execute(query).mappings().all()
        return [_shadow_decision_from_row(row) for row in rows]

    def record_shadow_for_inference_event(self, inference_event_id: str) -> int:
        now = _now()
        with self.engine.begin() as conn:
            event_row = conn.execute(
                _inference_sample_select().where(inference_events.c.event_key == inference_event_id)
            ).mappings().first()
            if event_row is None:
                return 0
            policies = conn.execute(
                sa.select(
                    abstention_policy_versions.c.id,
                    abstention_policy_versions.c.tau_conf,
                    abstention_policy_versions.c.tau_margin,
                    abstention_policy_versions.c.tau_ood,
                ).where(
                    abstention_policy_versions.c.dataset_version_id == event_row["dataset_version_db_id"],
                    abstention_policy_versions.c.model_version_id == event_row["model_version_db_id"],
                    abstention_policy_versions.c.status.in_(["shadow", "candidate"]),
                )
            ).mappings().all()
            inserted = 0
            for policy in policies:
                if self._record_shadow_row_for_event(conn, policy=policy, row=event_row, created_at=now):
                    inserted += 1
            return inserted

    def _feedback_samples(self, *, dataset_version_id: str, model_version_id: str) -> list[FeedbackDecisionSample]:
        query = (
            _inference_sample_select()
            .where(dataset_versions.c.version_key == dataset_version_id, model_versions.c.model_key == model_version_id)
            .where(feedback_items.c.id.is_not(None))
            .order_by(feedback_items.c.created_at.desc())
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query).mappings().all()
        return [_feedback_sample_from_row(row) for row in rows]

    def _assert_activation_gate(self, row: Any, *, min_feedback_count: int) -> None:
        source_count = int(row["source_feedback_count"])
        if source_count < min_feedback_count:
            raise AbstentionPolicyGateError(
                f"Activation requires at least {min_feedback_count} evaluable feedback item; current count is {source_count}."
            )
        target_risk = float(row["target_selective_risk"])
        metrics = dict(row["metrics"] or {})
        estimated_risk = float(metrics.get("selective_risk", 1.0))
        if estimated_risk > target_risk:
            raise AbstentionPolicyGateError(
                f"Estimated selective risk {estimated_risk:.6f} exceeds target risk {target_risk:.6f}."
            )

    def _record_shadow_rows_for_scope(
        self,
        conn: sa.Connection,
        *,
        policy_db_id: Any,
        dataset_version_db_id: Any,
        model_version_db_id: Any,
        created_at: datetime,
    ) -> None:
        policy = conn.execute(
            sa.select(
                abstention_policy_versions.c.id,
                abstention_policy_versions.c.tau_conf,
                abstention_policy_versions.c.tau_margin,
                abstention_policy_versions.c.tau_ood,
            ).where(abstention_policy_versions.c.id == policy_db_id)
        ).mappings().one()
        rows = conn.execute(
            _inference_sample_select()
            .where(
                inference_events.c.dataset_version_id == dataset_version_db_id,
                inference_events.c.model_version_id == model_version_db_id,
            )
            .order_by(inference_events.c.created_at.asc())
        ).mappings().all()
        for row in rows:
            self._record_shadow_row_for_event(conn, policy=policy, row=row, created_at=created_at)

    def _record_shadow_row_for_event(
        self,
        conn: sa.Connection,
        *,
        policy: Any,
        row: Any,
        created_at: datetime,
    ) -> bool:
        confidence = _optional_float(row["confidence"], default=0.0)
        margin = _optional_float(row["margin"], default=0.0)
        ood_score = _optional_float(row["ood_score"], default=None)
        shadow, reasons = decide_with_thresholds(
            confidence=confidence,
            margin=margin,
            ood_score=ood_score,
            tau_conf=float(policy["tau_conf"]),
            tau_margin=float(policy["tau_margin"]),
            tau_ood=_optional_float(policy["tau_ood"], default=None),
        )
        current = str(row["decision"])
        statement = (
            pg_insert(abstention_shadow_decisions)
            .values(
                id=uuid4(),
                policy_version_id=policy["id"],
                inference_event_id=row["inference_event_db_id"],
                current_decision=current,
                shadow_decision=shadow,
                decision_diff=decision_diff(current, shadow),  # type: ignore[arg-type]
                score_snapshot={
                    "confidence": confidence,
                    "margin": margin,
                    "ood_score": ood_score,
                    "reasons": reasons,
                    "top_k": _top_k(row["result_payload"]),
                    "sample_id": row["sample_id"],
                    "input_ref": row["input_ref"],
                    "final_outcome": row.get("final_outcome"),
                    "final_label": row.get("final_label"),
                },
                created_at=created_at,
            )
            .on_conflict_do_nothing(index_elements=["policy_version_id", "inference_event_id"])
        )
        result = conn.execute(statement)
        return bool(result.rowcount)


def _scope_select() -> sa.Select[Any]:
    return (
        sa.select(
            datasets.c.id.label("dataset_db_id"),
            dataset_versions.c.id.label("dataset_version_db_id"),
            model_versions.c.id.label("model_version_db_id"),
        )
        .select_from(
            model_versions.join(dataset_versions, dataset_versions.c.id == model_versions.c.dataset_version_id).join(
                datasets, datasets.c.id == model_versions.c.dataset_id
            )
        )
    )


def _policy_select() -> sa.Select[Any]:
    return (
        sa.select(
            abstention_policy_versions.c.policy_key,
            datasets.c.dataset_key,
            dataset_versions.c.version_key,
            model_versions.c.model_key,
            abstention_policy_versions.c.status,
            abstention_policy_versions.c.target_selective_risk,
            abstention_policy_versions.c.tau_conf,
            abstention_policy_versions.c.tau_margin,
            abstention_policy_versions.c.tau_ood,
            abstention_policy_versions.c.source_feedback_count,
            abstention_policy_versions.c.metrics,
            abstention_policy_versions.c.selection_config,
            abstention_policy_versions.c.created_by,
            abstention_policy_versions.c.activated_by,
            abstention_policy_versions.c.activation_reason,
            abstention_policy_versions.c.activated_at,
            abstention_policy_versions.c.deactivated_by,
            abstention_policy_versions.c.deactivation_reason,
            abstention_policy_versions.c.deactivated_at,
            abstention_policy_versions.c.created_at,
            abstention_policy_versions.c.updated_at,
        )
        .select_from(
            abstention_policy_versions.join(datasets, datasets.c.id == abstention_policy_versions.c.dataset_id)
            .join(dataset_versions, dataset_versions.c.id == abstention_policy_versions.c.dataset_version_id)
            .join(model_versions, model_versions.c.id == abstention_policy_versions.c.model_version_id)
        )
    )


def _policy_select_with_ids() -> sa.Select[Any]:
    return (
        sa.select(
            abstention_policy_versions.c.id.label("policy_db_id"),
            datasets.c.id.label("dataset_db_id"),
            dataset_versions.c.id.label("dataset_version_db_id"),
            model_versions.c.id.label("model_version_db_id"),
            abstention_policy_versions.c.policy_key,
            abstention_policy_versions.c.status,
            abstention_policy_versions.c.target_selective_risk,
            abstention_policy_versions.c.source_feedback_count,
            abstention_policy_versions.c.metrics,
            abstention_policy_versions.c.selection_config,
        )
        .select_from(
            abstention_policy_versions.join(datasets, datasets.c.id == abstention_policy_versions.c.dataset_id)
            .join(dataset_versions, dataset_versions.c.id == abstention_policy_versions.c.dataset_version_id)
            .join(model_versions, model_versions.c.id == abstention_policy_versions.c.model_version_id)
        )
    )


def _inference_sample_select() -> sa.Select[Any]:
    return (
        sa.select(
            inference_events.c.id.label("inference_event_db_id"),
            inference_events.c.event_key,
            inference_events.c.dataset_version_id.label("dataset_version_db_id"),
            inference_events.c.model_version_id.label("model_version_db_id"),
            datasets.c.dataset_key,
            dataset_versions.c.version_key,
            model_versions.c.model_key,
            inference_events.c.input_ref,
            inference_events.c.sample_id,
            inference_events.c.decision,
            inference_events.c.confidence,
            inference_events.c.margin,
            inference_events.c.ood_score,
            inference_events.c.result_payload,
            feedback_items.c.final_label,
            feedback_items.c.final_outcome,
        )
        .select_from(
            inference_events.join(datasets, datasets.c.id == inference_events.c.dataset_id)
            .join(dataset_versions, dataset_versions.c.id == inference_events.c.dataset_version_id)
            .join(model_versions, model_versions.c.id == inference_events.c.model_version_id)
            .outerjoin(feedback_items, feedback_items.c.inference_event_id == inference_events.c.id)
        )
    )


def _shadow_decision_select() -> sa.Select[Any]:
    return (
        sa.select(
            abstention_shadow_decisions.c.id,
            abstention_policy_versions.c.policy_key,
            inference_events.c.event_key,
            datasets.c.dataset_key,
            dataset_versions.c.version_key,
            model_versions.c.model_key,
            abstention_shadow_decisions.c.current_decision,
            abstention_shadow_decisions.c.shadow_decision,
            abstention_shadow_decisions.c.decision_diff,
            abstention_shadow_decisions.c.score_snapshot,
            abstention_shadow_decisions.c.created_at,
        )
        .select_from(
            abstention_shadow_decisions.join(
                abstention_policy_versions,
                abstention_policy_versions.c.id == abstention_shadow_decisions.c.policy_version_id,
            )
            .join(inference_events, inference_events.c.id == abstention_shadow_decisions.c.inference_event_id)
            .join(datasets, datasets.c.id == inference_events.c.dataset_id)
            .join(dataset_versions, dataset_versions.c.id == inference_events.c.dataset_version_id)
            .join(model_versions, model_versions.c.id == inference_events.c.model_version_id)
        )
    )


def _feedback_sample_from_row(row: Any) -> FeedbackDecisionSample:
    return FeedbackDecisionSample(
        inference_event_id=row["event_key"],
        current_decision=row["decision"],
        confidence=_optional_float(row["confidence"], default=0.0),
        margin=_optional_float(row["margin"], default=0.0),
        ood_score=_optional_float(row["ood_score"], default=None),
        predicted_label=_predicted_label(row["result_payload"]),
        final_label=row["final_label"],
        final_outcome=row["final_outcome"] or "ignore",
    )


def _policy_from_row(row: Any) -> AbstentionPolicyRecord:
    return AbstentionPolicyRecord(
        policy_id=row["policy_key"],
        dataset_id=row["dataset_key"],
        dataset_version_id=row["version_key"],
        model_version_id=row["model_key"],
        status=row["status"],
        target_selective_risk=float(row["target_selective_risk"]),
        tau_conf=float(row["tau_conf"]),
        tau_margin=float(row["tau_margin"]),
        tau_ood=_optional_float(row["tau_ood"], default=None),
        source_feedback_count=int(row["source_feedback_count"]),
        metrics=dict(row["metrics"] or {}),
        selection_config=dict(row["selection_config"] or {}),
        created_by=row["created_by"],
        activated_by=row["activated_by"],
        activation_reason=row["activation_reason"],
        activated_at=_to_iso(row["activated_at"]) if row["activated_at"] else None,
        deactivated_by=row["deactivated_by"],
        deactivation_reason=row["deactivation_reason"],
        deactivated_at=_to_iso(row["deactivated_at"]) if row["deactivated_at"] else None,
        created_at=_to_iso(row["created_at"]),
        updated_at=_to_iso(row["updated_at"]),
    )


def _shadow_decision_from_row(row: Any) -> AbstentionShadowDecisionRecord:
    return AbstentionShadowDecisionRecord(
        shadow_decision_id=str(row["id"]),
        policy_id=row["policy_key"],
        inference_event_id=row["event_key"],
        dataset_id=row["dataset_key"],
        dataset_version_id=row["version_key"],
        model_version_id=row["model_key"],
        current_decision=row["current_decision"],
        shadow_decision=row["shadow_decision"],
        decision_diff=row["decision_diff"],
        score_snapshot=dict(row["score_snapshot"] or {}),
        created_at=_to_iso(row["created_at"]),
    )


def _predicted_label(result_payload: dict[str, Any] | None) -> str | None:
    top_k = _top_k(result_payload)
    if not top_k:
        return None
    label = top_k[0].get("label")
    return str(label) if label is not None else None


def _top_k(result_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    result = dict(result_payload or {}).get("result") or {}
    top_k = result.get("top_k") or result.get("topK") or []
    return list(top_k) if isinstance(top_k, list) else []


def _optional_float(value: Any, *, default: float | None) -> float | None:
    if value is None:
        return default
    return float(value)


def _now() -> datetime:
    return datetime.now(UTC)


def _to_iso(value: datetime) -> str:
    return value.isoformat()
