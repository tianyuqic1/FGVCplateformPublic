from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from finevision.db.schema import (
    dataset_versions,
    datasets,
    inference_events,
    inference_runs,
    review_items,
    vlm_review_results,
    vlm_review_runs,
)


@dataclass(frozen=True)
class VLMReviewRunRecord:
    run_id: str
    dataset_id: str | None
    inference_run_id: str | None
    mode: str
    status: str
    requested_limit: int
    total_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    fallback_count: int
    model_id: str
    model_revision: str | None
    prompt_version: str
    config: dict[str, Any]
    created_by: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VLMReviewResultRecord:
    result_id: str
    run_id: str
    review_item_id: str
    status: str
    candidate_labels: list[str]
    suggested_label: str | None
    reasoning: str | None
    raw_output: str | None
    image_sha256: str | None
    model_revision: str | None
    prompt_version: str
    latency_seconds: float | None
    input_tokens: int | None
    generated_tokens: int | None
    auto_submit_eligible: bool
    gate_report: dict[str, Any]
    error_message: str | None
    attempt_count: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClaimedVLMReview:
    result_id: str
    run_id: str
    review_item_id: str
    mode: str
    candidate_labels: list[str]
    context: dict[str, Any]
    input_ref: str | None
    sample_id: str | None
    dataset_version_id: str
    model_id: str
    model_revision: str | None
    prompt_version: str
    config: dict[str, Any]


class DatabaseVLMReviewStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    def create_run(
        self,
        *,
        mode: str,
        limit: int,
        dataset_id: str | None = None,
        inference_run_id: str | None = None,
        model_id: str = "Fine-R1-3B",
        model_revision: str | None = None,
        prompt_version: str = "finevision-finer1-v1.1",
        config: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> VLMReviewRunRecord:
        if mode not in {"assisted", "auto"}:
            raise ValueError(f"Unsupported VLM review mode: {mode}")
        safe_limit = max(1, min(int(limit), 500))
        now = datetime.now(UTC)
        run_uuid = uuid4()
        run_key = f"vlm-run-{uuid4().hex[:12]}"

        with self.engine.begin() as conn:
            dataset_uuid = _lookup_optional_key(conn, datasets, datasets.c.dataset_key, dataset_id, "Dataset")
            inference_run_uuid = _lookup_optional_key(
                conn,
                inference_runs,
                inference_runs.c.run_key,
                inference_run_id,
                "Inference run",
            )
            active_assignment = sa.exists(
                sa.select(1)
                .select_from(
                    vlm_review_results.join(
                        vlm_review_runs,
                        vlm_review_runs.c.id == vlm_review_results.c.vlm_review_run_id,
                    )
                )
                .where(
                    vlm_review_results.c.review_item_id == review_items.c.id,
                    vlm_review_results.c.status.in_(["queued", "running"]),
                    vlm_review_runs.c.status.in_(["queued", "running"]),
                )
            )
            query = (
                sa.select(
                    review_items.c.id,
                    review_items.c.context,
                )
                .select_from(review_items.join(inference_events, inference_events.c.id == review_items.c.inference_event_id))
                .where(
                    review_items.c.status == "pending",
                    inference_events.c.decision == "abstain",
                    review_items.c.risk_type != "ood_candidate",
                    ~active_assignment,
                )
                .order_by(review_items.c.priority.asc(), review_items.c.created_at.asc())
                .limit(safe_limit)
                .with_for_update(skip_locked=True)
            )
            if dataset_uuid is not None:
                query = query.where(review_items.c.dataset_id == dataset_uuid)
            if inference_run_uuid is not None:
                query = query.where(review_items.c.inference_run_id == inference_run_uuid)

            selected: list[tuple[UUID, list[str]]] = []
            for row in conn.execute(query).mappings():
                candidates = _candidate_labels(dict(row["context"] or {}))
                if len(candidates) >= 2:
                    selected.append((row["id"], candidates))
            if not selected:
                raise ValueError("No eligible pending abstain review items with at least two candidate labels")

            conn.execute(
                vlm_review_runs.insert().values(
                    id=run_uuid,
                    run_key=run_key,
                    dataset_id=dataset_uuid,
                    inference_run_id=inference_run_uuid,
                    mode=mode,
                    status="queued",
                    requested_limit=safe_limit,
                    total_count=len(selected),
                    succeeded_count=0,
                    failed_count=0,
                    skipped_count=0,
                    fallback_count=0,
                    model_id=model_id,
                    model_revision=model_revision,
                    prompt_version=prompt_version,
                    config=dict(config or {}),
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                vlm_review_results.insert(),
                [
                    {
                        "id": uuid4(),
                        "result_key": f"vlm-result-{uuid4().hex[:12]}",
                        "vlm_review_run_id": run_uuid,
                        "review_item_id": review_uuid,
                        "status": "queued",
                        "candidate_labels": candidates,
                        "prompt_version": prompt_version,
                        "auto_submit_eligible": False,
                        "gate_report": {},
                        "attempt_count": 0,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for review_uuid, candidates in selected
                ],
            )
        record = self.get_run(run_key)
        assert record is not None
        return record

    def list_runs(self, *, limit: int = 20) -> list[VLMReviewRunRecord]:
        query = _run_select().order_by(vlm_review_runs.c.created_at.desc()).limit(max(1, min(limit, 100)))
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [_run_from_row(row) for row in rows]

    def get_run(self, run_id: str) -> VLMReviewRunRecord | None:
        with self.engine.connect() as conn:
            row = conn.execute(_run_select().where(vlm_review_runs.c.run_key == run_id)).mappings().first()
        return _run_from_row(row) if row else None

    def list_results(self, run_id: str, *, limit: int = 500) -> list[VLMReviewResultRecord]:
        query = (
            _result_select()
            .where(vlm_review_runs.c.run_key == run_id)
            .order_by(vlm_review_results.c.created_at.asc())
            .limit(max(1, min(limit, 1000)))
        )
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [_result_from_row(row) for row in rows]

    def is_result_active(self, result_id: str) -> bool:
        with self.engine.connect() as conn:
            return (
                conn.execute(
                    sa.select(vlm_review_results.c.id)
                    .select_from(
                        vlm_review_results.join(
                            vlm_review_runs,
                            vlm_review_runs.c.id == vlm_review_results.c.vlm_review_run_id,
                        )
                    )
                    .where(
                        vlm_review_results.c.result_key == result_id,
                        vlm_review_results.c.status == "running",
                        vlm_review_runs.c.status == "running",
                    )
                ).first()
                is not None
            )

    def cancel_run(self, run_id: str) -> VLMReviewRunRecord:
        now = datetime.now(UTC)
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(vlm_review_runs.c.id, vlm_review_runs.c.status)
                .where(vlm_review_runs.c.run_key == run_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                raise ValueError(f"VLM review run not found: {run_id}")
            if row["status"] in {"succeeded", "partial_failed", "failed", "cancelled"}:
                raise RuntimeError(f"VLM review run is already terminal: {row['status']}")
            conn.execute(
                vlm_review_runs.update()
                .where(vlm_review_runs.c.id == row["id"])
                .values(status="cancelled", finished_at=now, updated_at=now)
            )
            conn.execute(
                vlm_review_results.update()
                .where(
                    vlm_review_results.c.vlm_review_run_id == row["id"],
                    vlm_review_results.c.status.in_(["queued", "running"]),
                )
                .values(status="cancelled", finished_at=now, updated_at=now)
            )
        record = self.get_run(run_id)
        assert record is not None
        return record

    def claim_next_result(self, *, stale_after_seconds: int = 900, max_attempts: int = 3) -> ClaimedVLMReview | None:
        now = datetime.now(UTC)
        with self.engine.begin() as conn:
            stale_before = now - timedelta(seconds=max(60, stale_after_seconds))
            stale_running = conn.execute(
                sa.select(
                    vlm_review_results.c.id,
                    vlm_review_results.c.vlm_review_run_id,
                    vlm_review_results.c.attempt_count,
                )
                .select_from(
                    vlm_review_results.join(
                        vlm_review_runs,
                        vlm_review_runs.c.id == vlm_review_results.c.vlm_review_run_id,
                    )
                )
                .where(
                    vlm_review_results.c.status == "running",
                    vlm_review_results.c.started_at < stale_before,
                    vlm_review_runs.c.status == "running",
                )
                .with_for_update(skip_locked=True)
            ).mappings().all()
            for stale in stale_running:
                exhausted = int(stale["attempt_count"]) >= max_attempts
                conn.execute(
                    vlm_review_results.update()
                    .where(vlm_review_results.c.id == stale["id"])
                    .values(
                        status="failed" if exhausted else "queued",
                        error_message="VLM worker lease expired" if exhausted else None,
                        started_at=None,
                        finished_at=now if exhausted else None,
                        updated_at=now,
                    )
                )
                if exhausted:
                    _refresh_run_counts(conn, stale["vlm_review_run_id"], fallback_delta=0)
            stale_rows = conn.execute(
                sa.select(
                    vlm_review_results.c.id,
                    vlm_review_results.c.vlm_review_run_id,
                )
                .select_from(
                    vlm_review_results.join(
                        vlm_review_runs,
                        vlm_review_runs.c.id == vlm_review_results.c.vlm_review_run_id,
                    ).join(review_items, review_items.c.id == vlm_review_results.c.review_item_id)
                )
                .where(
                    vlm_review_results.c.status == "queued",
                    vlm_review_runs.c.status.in_(["queued", "running"]),
                    review_items.c.status != "pending",
                )
                .with_for_update(skip_locked=True)
            ).mappings().all()
            if stale_rows:
                stale_ids = [item["id"] for item in stale_rows]
                conn.execute(
                    vlm_review_results.update()
                    .where(vlm_review_results.c.id.in_(stale_ids))
                    .values(status="skipped", finished_at=now, updated_at=now)
                )
                for run_uuid in {item["vlm_review_run_id"] for item in stale_rows}:
                    _refresh_run_counts(conn, run_uuid, fallback_delta=0)

            row = conn.execute(
                sa.select(
                    vlm_review_results.c.id,
                    vlm_review_results.c.result_key,
                    vlm_review_results.c.candidate_labels,
                    vlm_review_runs.c.id.label("run_uuid"),
                    vlm_review_runs.c.run_key,
                    vlm_review_runs.c.mode,
                    vlm_review_runs.c.model_id,
                    vlm_review_runs.c.model_revision,
                    vlm_review_runs.c.prompt_version,
                    vlm_review_runs.c.config,
                    review_items.c.review_key,
                    review_items.c.context,
                    review_items.c.input_ref,
                    review_items.c.sample_id,
                    dataset_versions.c.version_key.label("dataset_version_key"),
                )
                .select_from(
                    vlm_review_results.join(
                        vlm_review_runs,
                        vlm_review_runs.c.id == vlm_review_results.c.vlm_review_run_id,
                    )
                    .join(review_items, review_items.c.id == vlm_review_results.c.review_item_id)
                    .join(dataset_versions, dataset_versions.c.id == review_items.c.dataset_version_id)
                )
                .where(
                    vlm_review_results.c.status == "queued",
                    vlm_review_runs.c.status.in_(["queued", "running"]),
                    review_items.c.status == "pending",
                )
                .order_by(vlm_review_runs.c.created_at.asc(), vlm_review_results.c.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            ).mappings().first()
            if row is None:
                return None
            conn.execute(
                vlm_review_results.update()
                .where(vlm_review_results.c.id == row["id"])
                .values(status="running", attempt_count=vlm_review_results.c.attempt_count + 1, started_at=now, updated_at=now)
            )
            conn.execute(
                vlm_review_runs.update()
                .where(vlm_review_runs.c.id == row["run_uuid"])
                .values(status="running", started_at=sa.func.coalesce(vlm_review_runs.c.started_at, now), updated_at=now)
            )
        return ClaimedVLMReview(
            result_id=row["result_key"],
            run_id=row["run_key"],
            review_item_id=row["review_key"],
            mode=row["mode"],
            candidate_labels=list(row["candidate_labels"] or []),
            context=dict(row["context"] or {}),
            input_ref=row["input_ref"],
            sample_id=row["sample_id"],
            dataset_version_id=str(row["dataset_version_key"]),
            model_id=row["model_id"],
            model_revision=row["model_revision"],
            prompt_version=row["prompt_version"],
            config=dict(row["config"] or {}),
        )

    def complete_result(
        self,
        result_id: str,
        *,
        suggested_label: str,
        reasoning: str,
        raw_output: str,
        image_sha256: str,
        model_revision: str | None,
        latency_seconds: float | None,
        input_tokens: int | None,
        generated_tokens: int | None,
        auto_submit_eligible: bool,
        gate_report: dict[str, Any],
        fallback_to_human: bool,
    ) -> VLMReviewResultRecord:
        now = datetime.now(UTC)
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(vlm_review_results.c.id, vlm_review_results.c.vlm_review_run_id)
                .select_from(
                    vlm_review_results.join(
                        vlm_review_runs,
                        vlm_review_runs.c.id == vlm_review_results.c.vlm_review_run_id,
                    )
                )
                .where(
                    vlm_review_results.c.result_key == result_id,
                    vlm_review_results.c.status == "running",
                    vlm_review_runs.c.status == "running",
                )
                .with_for_update()
            ).mappings().first()
            if row is None:
                raise RuntimeError(f"VLM review result is no longer active: {result_id}")
            conn.execute(
                vlm_review_results.update()
                .where(vlm_review_results.c.id == row["id"])
                .values(
                    status="succeeded",
                    suggested_label=suggested_label,
                    reasoning=reasoning,
                    raw_output=raw_output,
                    image_sha256=image_sha256,
                    model_revision=model_revision,
                    latency_seconds=latency_seconds,
                    input_tokens=input_tokens,
                    generated_tokens=generated_tokens,
                    auto_submit_eligible=auto_submit_eligible,
                    gate_report=gate_report,
                    finished_at=now,
                    updated_at=now,
                )
            )
            _refresh_run_counts(conn, row["vlm_review_run_id"], fallback_delta=1 if fallback_to_human else 0)
        record = self._get_result(result_id)
        assert record is not None
        return record

    def fail_result(
        self,
        result_id: str,
        error_message: str,
        *,
        image_sha256: str | None = None,
        model_revision: str | None = None,
        latency_seconds: float | None = None,
        input_tokens: int | None = None,
        generated_tokens: int | None = None,
    ) -> VLMReviewResultRecord | None:
        now = datetime.now(UTC)
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(
                    vlm_review_results.c.id,
                    vlm_review_results.c.vlm_review_run_id,
                    vlm_review_results.c.status,
                    vlm_review_runs.c.mode,
                )
                .select_from(
                    vlm_review_results.join(
                        vlm_review_runs,
                        vlm_review_runs.c.id == vlm_review_results.c.vlm_review_run_id,
                    )
                )
                .where(vlm_review_results.c.result_key == result_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                raise ValueError(f"VLM review result not found: {result_id}")
            if row["status"] != "running":
                return self._get_result(result_id)
            conn.execute(
                vlm_review_results.update()
                .where(vlm_review_results.c.id == row["id"])
                .values(
                    status="failed",
                    error_message=error_message[:4000],
                    image_sha256=image_sha256,
                    model_revision=model_revision,
                    latency_seconds=latency_seconds,
                    input_tokens=input_tokens,
                    generated_tokens=generated_tokens,
                    finished_at=now,
                    updated_at=now,
                )
            )
            _refresh_run_counts(
                conn,
                row["vlm_review_run_id"],
                fallback_delta=1 if row["mode"] == "auto" else 0,
            )
        record = self._get_result(result_id)
        assert record is not None
        return record

    def _get_result(self, result_id: str) -> VLMReviewResultRecord | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                _result_select().where(vlm_review_results.c.result_key == result_id)
            ).mappings().first()
        return _result_from_row(row) if row else None


def _lookup_optional_key(
    conn: sa.Connection,
    table: sa.Table,
    key_column: sa.Column[Any],
    value: str | None,
    label: str,
) -> UUID | None:
    if not value:
        return None
    row = conn.execute(sa.select(table.c.id).where(key_column == value)).first()
    if row is None:
        raise ValueError(f"{label} not found: {value}")
    return row[0]


def _candidate_labels(context: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for candidate in context.get("top_k") or context.get("topK") or []:
        label = str(candidate.get("label", "") if isinstance(candidate, dict) else candidate).strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _run_select() -> sa.Select[Any]:
    return sa.select(
        vlm_review_runs,
        datasets.c.dataset_key,
        inference_runs.c.run_key.label("inference_run_key"),
    ).select_from(
        vlm_review_runs.outerjoin(datasets, datasets.c.id == vlm_review_runs.c.dataset_id).outerjoin(
            inference_runs,
            inference_runs.c.id == vlm_review_runs.c.inference_run_id,
        )
    )


def _result_select() -> sa.Select[Any]:
    return sa.select(
        vlm_review_results,
        vlm_review_runs.c.run_key,
        review_items.c.review_key,
    ).select_from(
        vlm_review_results.join(
            vlm_review_runs,
            vlm_review_runs.c.id == vlm_review_results.c.vlm_review_run_id,
        ).join(review_items, review_items.c.id == vlm_review_results.c.review_item_id)
    )


def _refresh_run_counts(conn: sa.Connection, run_uuid: UUID, *, fallback_delta: int) -> None:
    counts = dict(
        conn.execute(
            sa.select(vlm_review_results.c.status, sa.func.count())
            .where(vlm_review_results.c.vlm_review_run_id == run_uuid)
            .group_by(vlm_review_results.c.status)
        ).all()
    )
    active = int(counts.get("queued", 0)) + int(counts.get("running", 0))
    succeeded = int(counts.get("succeeded", 0))
    failed = int(counts.get("failed", 0))
    skipped = int(counts.get("skipped", 0))
    run_status = conn.scalar(sa.select(vlm_review_runs.c.status).where(vlm_review_runs.c.id == run_uuid))
    values: dict[str, Any] = {
        "succeeded_count": succeeded,
        "failed_count": failed,
        "skipped_count": skipped,
        "fallback_count": vlm_review_runs.c.fallback_count + fallback_delta,
        "updated_at": datetime.now(UTC),
    }
    if active == 0 and run_status != "cancelled":
        values["finished_at"] = datetime.now(UTC)
        values["status"] = "partial_failed" if failed and (succeeded or skipped) else "failed" if failed else "succeeded"
    conn.execute(vlm_review_runs.update().where(vlm_review_runs.c.id == run_uuid).values(**values))


def _run_from_row(row: Any) -> VLMReviewRunRecord:
    return VLMReviewRunRecord(
        run_id=row["run_key"],
        dataset_id=row.get("dataset_key"),
        inference_run_id=row.get("inference_run_key"),
        mode=row["mode"],
        status=row["status"],
        requested_limit=int(row["requested_limit"]),
        total_count=int(row["total_count"]),
        succeeded_count=int(row["succeeded_count"]),
        failed_count=int(row["failed_count"]),
        skipped_count=int(row["skipped_count"]),
        fallback_count=int(row["fallback_count"]),
        model_id=row["model_id"],
        model_revision=row["model_revision"],
        prompt_version=row["prompt_version"],
        config=dict(row["config"] or {}),
        created_by=row["created_by"],
        created_at=_iso(row["created_at"]),
        started_at=_iso(row["started_at"]) if row["started_at"] else None,
        finished_at=_iso(row["finished_at"]) if row["finished_at"] else None,
        updated_at=_iso(row["updated_at"]),
    )


def _result_from_row(row: Any) -> VLMReviewResultRecord:
    return VLMReviewResultRecord(
        result_id=row["result_key"],
        run_id=row["run_key"],
        review_item_id=row["review_key"],
        status=row["status"],
        candidate_labels=list(row["candidate_labels"] or []),
        suggested_label=row["suggested_label"],
        reasoning=row["reasoning"],
        raw_output=row["raw_output"],
        image_sha256=row["image_sha256"],
        model_revision=row["model_revision"],
        prompt_version=row["prompt_version"],
        latency_seconds=float(row["latency_seconds"]) if row["latency_seconds"] is not None else None,
        input_tokens=int(row["input_tokens"]) if row["input_tokens"] is not None else None,
        generated_tokens=int(row["generated_tokens"]) if row["generated_tokens"] is not None else None,
        auto_submit_eligible=bool(row["auto_submit_eligible"]),
        gate_report=dict(row["gate_report"] or {}),
        error_message=row["error_message"],
        attempt_count=int(row["attempt_count"]),
        created_at=_iso(row["created_at"]),
        started_at=_iso(row["started_at"]) if row["started_at"] else None,
        finished_at=_iso(row["finished_at"]) if row["finished_at"] else None,
        updated_at=_iso(row["updated_at"]),
    )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
