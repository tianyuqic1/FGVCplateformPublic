from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.pool import NullPool

from finevision.api.store import (
    DatasetSummary,
    JobRecord,
    JobStatus,
    JobType,
    MetadataStore,
    default_dataset_card,
    normalize_dataset_card,
)
from finevision.db.schema import artifacts, dataset_versions, datasets, job_events, jobs, training_runs
from finevision.schemas.artifacts import DatasetManifest, SampleRecord, to_jsonable


class DatabaseMetadataStore:
    def __init__(self, database_url: str | Engine) -> None:
        self.engine = database_url if isinstance(database_url, Engine) else create_engine(database_url, poolclass=NullPool)

    def save_dataset_manifest(self, manifest: DatasetManifest) -> Path:
        now = _now()
        manifest_data = to_jsonable(manifest)
        dataset_status = "ready" if self.readiness_status(manifest) == "ready" else "draft"
        artifact_key = f"{manifest.dataset_version_id}:manifest"
        artifact_uri = f"finevision://datasets/{manifest.dataset_id}/versions/{manifest.dataset_version_id}/manifest.json"
        card_key = f"{manifest.dataset_version_id}:dataset_card"
        card_uri = f"finevision://datasets/{manifest.dataset_id}/versions/{manifest.dataset_version_id}/dataset_card.json"

        with self.engine.begin() as conn:
            dataset_row = conn.execute(
                sa.select(datasets.c.id).where(datasets.c.dataset_key == manifest.dataset_id)
            ).mappings().first()
            if dataset_row is None:
                dataset_db_id = uuid4()
                conn.execute(
                    datasets.insert().values(
                        id=dataset_db_id,
                        dataset_key=manifest.dataset_id,
                        name=manifest.dataset_id,
                        status=dataset_status,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                dataset_db_id = dataset_row["id"]
                conn.execute(
                    datasets.update()
                    .where(datasets.c.id == dataset_db_id)
                    .values(status=dataset_status, updated_at=now)
                )

            version_row = conn.execute(
                sa.select(dataset_versions.c.id).where(dataset_versions.c.version_key == manifest.dataset_version_id)
            ).mappings().first()
            if version_row is None:
                version_db_id = uuid4()
                conn.execute(
                    dataset_versions.insert().values(
                        id=version_db_id,
                        dataset_id=dataset_db_id,
                        version_key=manifest.dataset_version_id,
                        root_uri=manifest.root,
                        sample_count=len(manifest.samples),
                        class_count=len(manifest.classes),
                        split_summary=manifest.split_counts,
                        readiness_status=self.readiness_status(manifest),
                        readiness_report=manifest.readiness,
                        created_at=now,
                    )
                )
            else:
                version_db_id = version_row["id"]
                conn.execute(
                    dataset_versions.update()
                    .where(dataset_versions.c.id == version_db_id)
                    .values(
                        dataset_id=dataset_db_id,
                        root_uri=manifest.root,
                        sample_count=len(manifest.samples),
                        class_count=len(manifest.classes),
                        split_summary=manifest.split_counts,
                        readiness_status=self.readiness_status(manifest),
                        readiness_report=manifest.readiness,
                    )
                )

            artifact_row = conn.execute(
                sa.select(artifacts.c.id).where(artifacts.c.artifact_key == artifact_key)
            ).mappings().first()
            if artifact_row is None:
                artifact_db_id = uuid4()
                conn.execute(
                    artifacts.insert().values(
                        id=artifact_db_id,
                        artifact_key=artifact_key,
                        artifact_type="dataset_manifest",
                        dataset_id=dataset_db_id,
                        dataset_version_id=version_db_id,
                        uri=artifact_uri,
                        content_type="application/json",
                        artifact_metadata={"manifest": manifest_data},
                        created_at=now,
                    )
                )
            else:
                artifact_db_id = artifact_row["id"]
                conn.execute(
                    artifacts.update()
                    .where(artifacts.c.id == artifact_db_id)
                    .values(
                        dataset_id=dataset_db_id,
                        dataset_version_id=version_db_id,
                        uri=artifact_uri,
                        content_type="application/json",
                        artifact_metadata={"manifest": manifest_data},
                    )
                )

            conn.execute(
                dataset_versions.update()
                .where(dataset_versions.c.id == version_db_id)
                .values(manifest_artifact_id=artifact_db_id)
            )
            card_row = conn.execute(
                sa.select(artifacts.c.id).where(artifacts.c.artifact_key == card_key)
            ).mappings().first()
            if card_row is None:
                conn.execute(
                    artifacts.insert().values(
                        id=uuid4(),
                        artifact_key=card_key,
                        artifact_type="dataset_card",
                        dataset_id=dataset_db_id,
                        dataset_version_id=version_db_id,
                        uri=card_uri,
                        content_type="application/json",
                        artifact_metadata={"dataset_card": default_dataset_card(manifest)},
                        created_at=now,
                    )
                )

        return Path(artifact_uri)

    def get_dataset_manifest(self, dataset_id: str, dataset_version_id: str) -> DatasetManifest | None:
        statement = _manifest_select().where(
            datasets.c.dataset_key == dataset_id,
            dataset_versions.c.version_key == dataset_version_id,
        )
        with self.engine.begin() as conn:
            row = conn.execute(statement).mappings().first()
        return _manifest_from_row(row) if row else None

    def get_dataset_version(self, dataset_version_id: str) -> DatasetManifest | None:
        statement = _manifest_select().where(dataset_versions.c.version_key == dataset_version_id)
        with self.engine.begin() as conn:
            row = conn.execute(statement).mappings().first()
        return _manifest_from_row(row) if row else None

    def list_dataset_manifests(self, dataset_id: str | None = None) -> list[DatasetManifest]:
        statement = _manifest_select().order_by(datasets.c.dataset_key, dataset_versions.c.version_key)
        if dataset_id is not None:
            statement = statement.where(datasets.c.dataset_key == dataset_id)
        with self.engine.begin() as conn:
            rows = conn.execute(statement).mappings().all()
        return [_manifest_from_row(row) for row in rows]

    def list_datasets(self) -> list[DatasetSummary]:
        grouped: dict[str, list[DatasetManifest]] = {}
        for manifest in self.list_dataset_manifests():
            grouped.setdefault(manifest.dataset_id, []).append(manifest)

        summaries: list[DatasetSummary] = []
        for dataset_id, manifests in sorted(grouped.items()):
            ordered = sorted(manifests, key=lambda item: item.dataset_version_id)
            latest = ordered[-1]
            summaries.append(
                DatasetSummary(
                    dataset_id=dataset_id,
                    latest_version_id=latest.dataset_version_id,
                    dataset_version_id=latest.dataset_version_id,
                    version_count=len(ordered),
                    classes=latest.classes,
                    class_count=len(latest.classes),
                    sample_count=len(latest.samples),
                    status=self.readiness_status(latest),
                    readiness=latest.readiness,
                )
            )
        return summaries

    def dataset_detail(self, dataset_id: str) -> dict[str, Any] | None:
        manifests = self.list_dataset_manifests(dataset_id)
        if not manifests:
            return None

        ordered = sorted(manifests, key=lambda item: item.dataset_version_id)
        latest = ordered[-1]
        return {
            "dataset_id": dataset_id,
            "latest_version_id": latest.dataset_version_id,
            "dataset_version_id": latest.dataset_version_id,
            "versions": [self.version_summary(manifest) for manifest in ordered],
            "classes": latest.classes,
            "class_count": len(latest.classes),
            "sample_count": len(latest.samples),
            "split_counts": latest.split_counts,
            "status": self.readiness_status(latest),
            "readiness": latest.readiness,
            "dataset_card": self.get_dataset_card(latest.dataset_version_id),
        }

    def get_dataset_card(self, dataset_version_id: str) -> dict[str, Any] | None:
        manifest = self.get_dataset_version(dataset_version_id)
        if manifest is None:
            return None
        card_key = f"{dataset_version_id}:dataset_card"
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(artifacts.c.artifact_metadata)
                .where(artifacts.c.artifact_key == card_key, artifacts.c.artifact_type == "dataset_card")
            ).mappings().first()
        if row is None:
            return default_dataset_card(manifest)
        return normalize_dataset_card(dict(row["artifact_metadata"] or {}).get("dataset_card") or {}, manifest=manifest)

    def update_dataset_card(self, dataset_version_id: str, card: dict[str, Any]) -> dict[str, Any] | None:
        manifest = self.get_dataset_version(dataset_version_id)
        if manifest is None:
            return None
        normalized = normalize_dataset_card(card, manifest=manifest)
        card_key = f"{dataset_version_id}:dataset_card"
        card_uri = f"finevision://datasets/{manifest.dataset_id}/versions/{dataset_version_id}/dataset_card.json"
        now = _now()
        with self.engine.begin() as conn:
            version_row = conn.execute(
                sa.select(
                    datasets.c.id.label("dataset_db_id"),
                    dataset_versions.c.id.label("dataset_version_db_id"),
                )
                .select_from(dataset_versions.join(datasets, datasets.c.id == dataset_versions.c.dataset_id))
                .where(dataset_versions.c.version_key == dataset_version_id)
            ).mappings().first()
            if version_row is None:
                return None
            row = conn.execute(sa.select(artifacts.c.id).where(artifacts.c.artifact_key == card_key)).mappings().first()
            values = {
                "artifact_key": card_key,
                "artifact_type": "dataset_card",
                "dataset_id": version_row["dataset_db_id"],
                "dataset_version_id": version_row["dataset_version_db_id"],
                "uri": card_uri,
                "content_type": "application/json",
                "artifact_metadata": {"dataset_card": normalized},
            }
            if row is None:
                conn.execute(artifacts.insert().values(id=uuid4(), created_at=now, **values))
            else:
                conn.execute(artifacts.update().where(artifacts.c.id == row["id"]).values(**values))
        return normalized

    readiness_status = staticmethod(MetadataStore.readiness_status)
    version_summary = staticmethod(MetadataStore.version_summary)


class DatabaseJobStore:
    def __init__(self, database_url: str | Engine, *, lease_owner: str | None = None) -> None:
        self.engine = database_url if isinstance(database_url, Engine) else create_engine(database_url, poolclass=NullPool)
        self.lease_owner = lease_owner or f"worker-{uuid4().hex[:8]}"

    def create_job(self, job_type: JobType, payload: dict[str, Any]) -> JobRecord:
        now = _now()
        job = JobRecord(
            job_id=f"job-{uuid4().hex[:12]}",
            type=job_type,
            status="queued",
            payload=payload,
            created_at=_to_iso(now),
            updated_at=_to_iso(now),
        )
        with self.engine.begin() as conn:
            job_db_id = uuid4()
            conn.execute(
                jobs.insert().values(
                    id=job_db_id,
                    job_key=job.job_id,
                    job_type=job.type,
                    status=job.status,
                    payload=job.payload,
                    created_at=now,
                    queued_at=now,
                    updated_at=now,
                )
            )
            _insert_job_event(conn, job_db_id, "created", "Job queued.", {"job_key": job.job_id})
        return job

    def save_job(self, job: JobRecord) -> Path:
        now = _now()
        with self.engine.begin() as conn:
            conn.execute(
                jobs.update()
                .where(jobs.c.job_key == job.job_id)
                .values(
                    job_type=job.type,
                    status=job.status,
                    payload=job.payload,
                    result=job.result,
                    error_message=job.error,
                    started_at=_parse_optional_datetime(job.started_at),
                    finished_at=_parse_optional_datetime(job.finished_at),
                    updated_at=now,
                )
            )
        return Path(f"postgres://jobs/{job.job_id}")

    def get_job(self, job_id: str) -> JobRecord | None:
        with self.engine.begin() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_key == job_id)).mappings().first()
        return _job_from_row(row) if row else None

    def list_jobs(self) -> list[JobRecord]:
        with self.engine.begin() as conn:
            rows = conn.execute(sa.select(jobs).order_by(jobs.c.created_at, jobs.c.job_key)).mappings().all()
        return [_job_from_row(row) for row in rows]

    def next_queued_job(self) -> JobRecord | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(jobs)
                .where(jobs.c.status == "queued", jobs.c.attempt_count < jobs.c.max_attempts)
                .order_by(jobs.c.priority, jobs.c.queued_at, jobs.c.job_key)
                .limit(1)
            ).mappings().first()
        return _job_from_row(row) if row else None

    def claim_next_queued_job(self, *, lease_seconds: int = 300) -> JobRecord | None:
        now = _now()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        with self.engine.begin() as conn:
            _recover_expired_running_jobs(conn, now=now)
            row = conn.execute(
                sa.select(jobs)
                .where(jobs.c.status == "queued", jobs.c.attempt_count < jobs.c.max_attempts)
                .order_by(jobs.c.priority, jobs.c.queued_at, jobs.c.job_key)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).mappings().first()
            if row is None:
                return None

            updated = conn.execute(
                jobs.update()
                .where(jobs.c.id == row["id"], jobs.c.status == "queued")
                .values(
                    status="running",
                    started_at=now,
                    updated_at=now,
                    error_message=None,
                    lease_owner=self.lease_owner,
                    lease_expires_at=lease_expires_at,
                    attempt_count=jobs.c.attempt_count + 1,
                )
                .returning(*jobs.c)
            ).mappings().first()
            if updated is None:
                return None
            _insert_job_event(conn, updated["id"], "claimed", "Job claimed by worker.", {"lease_owner": self.lease_owner})
        return _job_from_row(updated)

    def mark_running(self, job: JobRecord) -> JobRecord:
        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(
                jobs.update()
                .where(jobs.c.job_key == job.job_id)
                .values(status="running", started_at=now, updated_at=now, error_message=None)
                .returning(*jobs.c)
            ).mappings().first()
            if row is not None:
                _insert_job_event(conn, row["id"], "running", "Job marked running.", {})
        if row is None:
            raise ValueError(f"Job not found: {job.job_id}")
        return _job_from_row(row)

    def mark_succeeded(self, job: JobRecord, result: dict[str, Any]) -> JobRecord:
        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(
                jobs.update()
                .where(
                    jobs.c.job_key == job.job_id,
                    jobs.c.status == "running",
                    jobs.c.lease_owner == self.lease_owner,
                )
                .values(
                    status="succeeded",
                    result=result,
                    error_message=None,
                    finished_at=now,
                    updated_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                )
                .returning(*jobs.c)
            ).mappings().first()
            if row is not None:
                _insert_job_event(conn, row["id"], "succeeded", "Job completed.", {"result": result})
        if row is None:
            raise ValueError(f"Running job lease not held by this worker: {job.job_id}")
        return _job_from_row(row)

    def mark_failed(self, job: JobRecord, error: str) -> JobRecord:
        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(
                jobs.update()
                .where(
                    jobs.c.job_key == job.job_id,
                    jobs.c.status == "running",
                    jobs.c.lease_owner == self.lease_owner,
                )
                .values(
                    status="failed",
                    error_message=error,
                    finished_at=now,
                    updated_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                )
                .returning(*jobs.c)
            ).mappings().first()
            if row is not None:
                _insert_job_event(conn, row["id"], "failed", "Job failed.", {"error": error})
        if row is None:
            raise ValueError(f"Running job lease not held by this worker: {job.job_id}")
        return _job_from_row(row)

    def cancel_job(self, job: JobRecord) -> JobRecord:
        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(
                jobs.update()
                .where(jobs.c.job_key == job.job_id, jobs.c.status == "queued")
                .values(status="cancelled", finished_at=now, updated_at=now)
                .returning(*jobs.c)
            ).mappings().first()
            if row is not None:
                _insert_job_event(conn, row["id"], "cancelled", "Queued job cancelled.", {})
        if row is None:
            raise ValueError(f"Only queued jobs can be cancelled: {job.job_id}")
        return _job_from_row(row)


def _recover_expired_running_jobs(conn: sa.Connection, *, now: datetime) -> None:
    expired_rows = conn.execute(
        sa.select(jobs.c.id, jobs.c.job_key, jobs.c.attempt_count, jobs.c.max_attempts)
        .where(jobs.c.status == "running", jobs.c.lease_expires_at.is_not(None), jobs.c.lease_expires_at <= now)
        .with_for_update(skip_locked=True)
    ).mappings().all()
    for row in expired_rows:
        if int(row["attempt_count"] or 0) >= int(row["max_attempts"] or 1):
            message = "Worker lease expired and max attempts were exhausted."
            conn.execute(
                jobs.update()
                .where(jobs.c.id == row["id"], jobs.c.status == "running")
                .values(
                    status="failed",
                    error_message=message,
                    finished_at=now,
                    updated_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
            conn.execute(
                training_runs.update()
                .where(training_runs.c.job_id == row["id"], training_runs.c.status.in_(["queued", "running"]))
                .values(status="failed", error_message=message, finished_at=now, updated_at=now)
            )
            _insert_job_event(
                conn,
                row["id"],
                "lease_expired_failed",
                message,
                {"job_key": row["job_key"], "attempt_count": row["attempt_count"], "max_attempts": row["max_attempts"]},
            )
            continue

        message = "Worker lease expired; job requeued for retry."
        conn.execute(
            jobs.update()
            .where(jobs.c.id == row["id"], jobs.c.status == "running")
            .values(
                status="queued",
                queued_at=now,
                updated_at=now,
                error_message=message,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        conn.execute(
            training_runs.update()
            .where(training_runs.c.job_id == row["id"], training_runs.c.status == "running")
            .values(status="queued", error_message=message, updated_at=now)
        )
        _insert_job_event(
            conn,
            row["id"],
            "lease_expired_requeued",
            message,
            {"job_key": row["job_key"], "attempt_count": row["attempt_count"], "max_attempts": row["max_attempts"]},
        )


def _manifest_select() -> sa.Select[Any]:
    return (
        sa.select(artifacts.c.artifact_metadata)
        .select_from(
            datasets.join(dataset_versions, dataset_versions.c.dataset_id == datasets.c.id).join(
                artifacts, artifacts.c.id == dataset_versions.c.manifest_artifact_id
            )
        )
        .where(artifacts.c.artifact_type == "dataset_manifest")
    )


def _manifest_from_row(row: Mapping[str, Any]) -> DatasetManifest:
    data = row["artifact_metadata"]["manifest"]
    return DatasetManifest(
        dataset_id=data["dataset_id"],
        dataset_version_id=data["dataset_version_id"],
        root=data["root"],
        classes=list(data["classes"]),
        samples=[SampleRecord(**sample) for sample in data["samples"]],
        split_counts=dict(data["split_counts"]),
        readiness=dict(data["readiness"]),
        artifact_refs=dict(data.get("artifact_refs", {})),
    )


def _job_from_row(row: Mapping[str, Any]) -> JobRecord:
    return JobRecord(
        job_id=row["job_key"],
        type=row["job_type"],
        status=_job_status(row["status"]),
        payload=dict(row["payload"]),
        created_at=_to_iso(row["created_at"]),
        updated_at=_to_iso(row["updated_at"]),
        started_at=_to_iso(row["started_at"]) if row["started_at"] else None,
        finished_at=_to_iso(row["finished_at"]) if row["finished_at"] else None,
        result=dict(row["result"]) if row["result"] is not None else None,
        error=row["error_message"],
    )


def _insert_job_event(
    conn: sa.Connection,
    job_id: UUID,
    event_type: str,
    message: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        job_events.insert().values(
            id=uuid4(),
            job_id=job_id,
            event_type=event_type,
            message=message,
            payload=payload,
            created_at=_now(),
        )
    )


def _job_status(value: str) -> JobStatus:
    if value not in {"queued", "paused", "running", "succeeded", "failed", "cancelled"}:
        raise ValueError(f"Unknown job status: {value}")
    return value  # type: ignore[return-value]


def _now() -> datetime:
    return datetime.now(UTC)


def _to_iso(value: datetime) -> str:
    return value.isoformat()


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)
