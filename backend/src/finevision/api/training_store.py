from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.pool import NullPool

from finevision.api.store import JobRecord
from finevision.db.schema import artifacts, dataset_versions, datasets, job_events, jobs, model_versions, training_runs
from finevision.schemas.artifacts import to_jsonable


@dataclass(frozen=True)
class TrainingRunRecord:
    run_id: str
    status: str
    job_id: str
    dataset_id: str
    dataset_version_id: str
    backbone_id: str
    extractor_config: dict[str, Any]
    head_config: dict[str, Any]
    feature_artifact_id: str | None
    model_artifact_id: str | None
    model_version_id: str | None
    report_artifact_id: str | None
    calibration_artifact_id: str | None
    threshold_strategy_artifact_id: str | None
    metrics: dict[str, Any]
    error: str | None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None


class DatabaseTrainingStore:
    def __init__(self, database_url: str | Engine) -> None:
        self.engine = database_url if isinstance(database_url, Engine) else create_engine(database_url, poolclass=NullPool)

    def create_training_run(self, *, job: JobRecord, payload: dict[str, Any]) -> TrainingRunRecord:
        now = _now()
        run_id = str(payload["training_run_id"])
        dataset_version_key = str(payload["dataset_version_id"])
        backbone_id = str(payload.get("backbone_id") or "color_stats_v1")
        extractor_config = dict(payload.get("extractor_config") or {"type": "color_stats"})
        head_config = dict(
            payload.get("head_config")
            or {
                "head_type": "torch_linear_adam",
                "learning_rate": 1e-3,
                "epochs": 50,
                "batch_size": 256,
                "weight_decay": 1e-4,
            }
        )

        with self.engine.begin() as conn:
            version_row = conn.execute(
                sa.select(
                    dataset_versions.c.id.label("dataset_version_db_id"),
                    dataset_versions.c.version_key,
                    datasets.c.id.label("dataset_db_id"),
                    datasets.c.dataset_key,
                )
                .select_from(dataset_versions.join(datasets, datasets.c.id == dataset_versions.c.dataset_id))
                .where(dataset_versions.c.version_key == dataset_version_key)
            ).mappings().first()
            if version_row is None:
                raise ValueError(f"Dataset version not found: {dataset_version_key}")

            job_db_id = conn.execute(sa.select(jobs.c.id).where(jobs.c.job_key == job.job_id)).scalar_one()
            run_db_id = uuid4()
            conn.execute(
                training_runs.insert().values(
                    id=run_db_id,
                    run_key=run_id,
                    job_id=job_db_id,
                    dataset_id=version_row["dataset_db_id"],
                    dataset_version_id=version_row["dataset_version_db_id"],
                    status="queued",
                    backbone_id=backbone_id,
                    extractor_config=extractor_config,
                    head_config=head_config,
                    metrics={},
                    created_at=now,
                    updated_at=now,
                )
            )
        record = self.get_training_run(run_id)
        if record is None:
            raise ValueError(f"Training run was not created: {run_id}")
        return record

    def get_training_run(self, run_id: str) -> TrainingRunRecord | None:
        with self.engine.begin() as conn:
            row = conn.execute(_training_run_select().where(training_runs.c.run_key == run_id)).mappings().first()
        return _training_run_from_row(row) if row else None

    def get_status(self, run_id: str) -> str | None:
        with self.engine.begin() as conn:
            return conn.scalar(sa.select(training_runs.c.status).where(training_runs.c.run_key == run_id))

    def list_training_runs(self) -> list[TrainingRunRecord]:
        with self.engine.begin() as conn:
            rows = conn.execute(_training_run_select().order_by(training_runs.c.created_at.desc())).mappings().all()
        return [_training_run_from_row(row) for row in rows]

    def mark_running(self, run_id: str) -> None:
        now = _now()
        with self.engine.begin() as conn:
            conn.execute(
                training_runs.update()
                .where(training_runs.c.run_key == run_id, training_runs.c.status.in_(["queued", "running"]))
                .values(status="running", started_at=now, updated_at=now, error_message=None)
            )

    def update_progress(self, run_id: str, progress: dict[str, Any]) -> None:
        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(training_runs.c.metrics).where(training_runs.c.run_key == run_id)
            ).mappings().first()
            if row is None:
                raise ValueError(f"Training run not found: {run_id}")
            metrics = dict(row["metrics"] or {})
            metrics["training_progress"] = {**progress, "updated_at": _to_iso(now)}
            conn.execute(
                training_runs.update()
                .where(training_runs.c.run_key == run_id)
                .values(metrics=metrics, updated_at=now)
            )

    def mark_failed(self, run_id: str, error: str) -> None:
        now = _now()
        with self.engine.begin() as conn:
            result = conn.execute(
                training_runs.update()
                .where(training_runs.c.run_key == run_id)
                .values(status="failed", error_message=error, finished_at=now, updated_at=now)
            )
        if result.rowcount == 0:
            raise ValueError(f"Training run not found: {run_id}")

    def mark_cancelled_by_job(self, job_id: str, reason: str) -> None:
        now = _now()
        with self.engine.begin() as conn:
            result = conn.execute(
                training_runs.update()
                .where(
                    training_runs.c.job_id == sa.select(jobs.c.id).where(jobs.c.job_key == job_id).scalar_subquery(),
                    training_runs.c.status == "queued",
                )
                .values(status="cancelled", error_message=reason, finished_at=now, updated_at=now)
            )
        if result.rowcount == 0:
            raise ValueError(f"Queued training run not found for job: {job_id}")

    def is_cancelled(self, run_id: str) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(training_runs.c.status, jobs.c.status.label("job_status"))
                .select_from(training_runs.join(jobs, jobs.c.id == training_runs.c.job_id))
                .where(training_runs.c.run_key == run_id)
            ).mappings().first()
        if row is None:
            raise ValueError(f"Training run not found: {run_id}")
        return row["status"] == "cancelled" or row["job_status"] == "cancelled"

    def pause_training_run(self, run_id: str) -> TrainingRunRecord:
        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(training_runs.c.id, training_runs.c.job_id)
                .where(training_runs.c.run_key == run_id, training_runs.c.status.in_(["queued", "running"]))
            ).mappings().first()
            if row is None:
                raise ValueError(f"Only queued or running training runs can be paused: {run_id}")
            conn.execute(
                training_runs.update()
                .where(training_runs.c.id == row["id"])
                .values(status="paused", updated_at=now)
            )
            conn.execute(
                jobs.update()
                .where(jobs.c.id == row["job_id"])
                .values(status="paused", updated_at=now, lease_owner=None, lease_expires_at=None)
            )
        record = self.get_training_run(run_id)
        if record is None:
            raise ValueError(f"Training run not found after pause: {run_id}")
        return record

    def resume_training_run(self, run_id: str) -> TrainingRunRecord:
        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(training_runs.c.id, training_runs.c.job_id)
                .where(training_runs.c.run_key == run_id, training_runs.c.status == "paused")
            ).mappings().first()
            if row is None:
                raise ValueError(f"Only paused training runs can be resumed: {run_id}")
            conn.execute(
                training_runs.update()
                .where(training_runs.c.id == row["id"])
                .values(status="queued", updated_at=now)
            )
            conn.execute(
                jobs.update()
                .where(jobs.c.id == row["job_id"])
                .values(status="queued", queued_at=now, updated_at=now)
            )
        record = self.get_training_run(run_id)
        if record is None:
            raise ValueError(f"Training run not found after resume: {run_id}")
        return record

    def cancel_training_run(self, run_id: str, reason: str) -> TrainingRunRecord:
        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(training_runs.c.id, training_runs.c.job_id)
                .where(training_runs.c.run_key == run_id, training_runs.c.status.in_(["queued", "paused", "running"]))
            ).mappings().first()
            if row is None:
                raise ValueError(f"Only queued, paused, or running training runs can be cancelled: {run_id}")
            conn.execute(
                training_runs.update()
                .where(training_runs.c.id == row["id"])
                .values(status="cancelled", error_message=reason, finished_at=now, updated_at=now)
            )
            conn.execute(
                jobs.update()
                .where(jobs.c.id == row["job_id"])
                .values(
                    status="cancelled",
                    error_message=reason,
                    finished_at=now,
                    updated_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
        record = self.get_training_run(run_id)
        if record is None:
            raise ValueError(f"Training run not found after cancel: {run_id}")
        return record

    def delete_training_run(self, run_id: str) -> None:
        with self.engine.begin() as conn:
            row = conn.execute(
                sa.select(
                    training_runs.c.id,
                    training_runs.c.job_id,
                    training_runs.c.status,
                    training_runs.c.feature_artifact_id,
                    training_runs.c.model_artifact_id,
                    training_runs.c.report_artifact_id,
                    training_runs.c.calibration_artifact_id,
                    training_runs.c.threshold_strategy_artifact_id,
                ).where(training_runs.c.run_key == run_id)
            ).mappings().first()
            if row is None:
                raise ValueError(f"Training run not found: {run_id}")
            if row["status"] == "running":
                raise ValueError(f"Running training runs cannot be deleted: {run_id}")
            has_artifact = any(
                row[field] is not None
                for field in [
                    "feature_artifact_id",
                    "model_artifact_id",
                    "report_artifact_id",
                    "calibration_artifact_id",
                    "threshold_strategy_artifact_id",
                ]
            )
            has_model_version = conn.scalar(
                sa.select(sa.func.count()).select_from(model_versions).where(model_versions.c.training_run_id == row["id"])
            )
            if has_artifact or has_model_version:
                raise ValueError(f"Training runs with artifacts or model versions cannot be deleted: {run_id}")
            conn.execute(training_runs.delete().where(training_runs.c.id == row["id"]))
            conn.execute(job_events.delete().where(job_events.c.job_id == row["job_id"]))
            conn.execute(jobs.delete().where(jobs.c.id == row["job_id"]))

    def find_feature_artifact(
        self,
        dataset_version_id: str,
        backbone_id: str,
        extractor_config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        artifact_key = _feature_artifact_key(dataset_version_id, backbone_id, extractor_config)
        with self.engine.begin() as conn:
            row = conn.execute(sa.select(artifacts).where(artifacts.c.artifact_key == artifact_key)).mappings().first()
        return dict(row) if row else None

    def complete_training_run(
        self,
        *,
        run_id: str,
        artifact_root: Path,
        feature_artifact: Any,
        model_artifact: Any,
        training_report: Any,
        calibration_report: Any,
        threshold_sweep: Any,
        threshold_strategy: Any,
    ) -> TrainingRunRecord:
        now = _now()
        feature_key = _feature_artifact_key(
            feature_artifact.dataset_version_id,
            feature_artifact.backbone_id,
            feature_artifact.extractor_config,
        )
        model_key = str(model_artifact.artifact_id)
        report_key = f"{run_id}:training_report"
        calibration_key = str(calibration_report.artifact_id)
        sweep_key = str(threshold_sweep.strategy_id)
        strategy_key = str(threshold_strategy.strategy_id)
        model_version_key = f"{model_artifact.dataset_id}-{run_id}-candidate"
        existing_record = self.get_training_run(run_id)
        existing_metrics = dict(existing_record.metrics) if existing_record is not None else {}
        metrics = {
            **existing_metrics,
            "accuracy": training_report.evaluation.accuracy,
            "macro_f1": training_report.evaluation.macro_f1,
            "expected_coverage": threshold_strategy.expected_coverage,
            "expected_selective_risk": threshold_strategy.expected_selective_risk,
            "accept_threshold": threshold_strategy.accept_threshold,
            "margin_threshold": threshold_strategy.margin_threshold,
        }

        with self.engine.begin() as conn:
            run_row = conn.execute(
                sa.select(
                    training_runs.c.id,
                    training_runs.c.dataset_id,
                    training_runs.c.dataset_version_id,
                    training_runs.c.job_id,
                ).where(training_runs.c.run_key == run_id)
            ).mappings().first()
            if run_row is None:
                raise ValueError(f"Training run not found: {run_id}")

            feature_db_id = _upsert_artifact(
                conn,
                artifact_key=feature_key,
                artifact_type="feature_matrix",
                dataset_id=run_row["dataset_id"],
                dataset_version_id=run_row["dataset_version_id"],
                job_id=run_row["job_id"],
                uri=str(Path(feature_artifact.features_path)),
                content_type="application/octet-stream",
                metadata={"feature_artifact": to_jsonable(feature_artifact)},
                now=now,
            )
            model_db_id = _upsert_artifact(
                conn,
                artifact_key=model_key,
                artifact_type="model_artifact",
                dataset_id=run_row["dataset_id"],
                dataset_version_id=run_row["dataset_version_id"],
                job_id=run_row["job_id"],
                uri=str(Path(model_artifact.model_path)),
                content_type="application/octet-stream",
                metadata={"model_artifact": to_jsonable(model_artifact)},
                now=now,
            )
            report_db_id = _upsert_artifact(
                conn,
                artifact_key=report_key,
                artifact_type="training_report",
                dataset_id=run_row["dataset_id"],
                dataset_version_id=run_row["dataset_version_id"],
                job_id=run_row["job_id"],
                uri=str(artifact_root / "models" / model_artifact.artifact_id / "training_report.json"),
                content_type="application/json",
                metadata={"training_report": to_jsonable(training_report)},
                now=now,
            )
            calibration_db_id = _upsert_artifact(
                conn,
                artifact_key=calibration_key,
                artifact_type="calibration_report",
                dataset_id=run_row["dataset_id"],
                dataset_version_id=run_row["dataset_version_id"],
                job_id=run_row["job_id"],
                uri=str(artifact_root / "models" / model_artifact.artifact_id / "calibration_report.json"),
                content_type="application/json",
                metadata={"calibration_report": to_jsonable(calibration_report)},
                now=now,
            )
            _upsert_artifact(
                conn,
                artifact_key=sweep_key,
                artifact_type="threshold_sweep",
                dataset_id=run_row["dataset_id"],
                dataset_version_id=run_row["dataset_version_id"],
                job_id=run_row["job_id"],
                uri=str(artifact_root / "models" / model_artifact.artifact_id / "threshold_sweep.json"),
                content_type="application/json",
                metadata={"threshold_sweep": to_jsonable(threshold_sweep)},
                now=now,
            )
            strategy_db_id = _upsert_artifact(
                conn,
                artifact_key=strategy_key,
                artifact_type="threshold_strategy",
                dataset_id=run_row["dataset_id"],
                dataset_version_id=run_row["dataset_version_id"],
                job_id=run_row["job_id"],
                uri=str(artifact_root / "models" / model_artifact.artifact_id / "threshold_strategy.json"),
                content_type="application/json",
                metadata={"threshold_strategy": to_jsonable(threshold_strategy)},
                now=now,
            )
            conn.execute(
                training_runs.update()
                .where(training_runs.c.id == run_row["id"])
                .values(
                    status="succeeded",
                    feature_artifact_id=feature_db_id,
                    model_artifact_id=model_db_id,
                    report_artifact_id=report_db_id,
                    calibration_artifact_id=calibration_db_id,
                    threshold_strategy_artifact_id=strategy_db_id,
                    metrics=metrics,
                    finished_at=now,
                    updated_at=now,
                    error_message=None,
                )
            )
            model_version_row = conn.execute(
                sa.select(model_versions.c.id).where(model_versions.c.model_key == model_version_key)
            ).first()
            if model_version_row is None:
                conn.execute(
                    model_versions.insert().values(
                        id=uuid4(),
                        model_key=model_version_key,
                        dataset_id=run_row["dataset_id"],
                        dataset_version_id=run_row["dataset_version_id"],
                        training_run_id=run_row["id"],
                        status="candidate",
                        model_artifact_id=model_db_id,
                        calibration_artifact_id=calibration_db_id,
                        threshold_strategy_artifact_id=strategy_db_id,
                        metrics=metrics,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                conn.execute(
                    model_versions.update()
                    .where(model_versions.c.model_key == model_version_key)
                    .values(
                        model_artifact_id=model_db_id,
                        calibration_artifact_id=calibration_db_id,
                        threshold_strategy_artifact_id=strategy_db_id,
                        metrics=metrics,
                        updated_at=now,
                    )
                )

        record = self.get_training_run(run_id)
        if record is None:
            raise ValueError(f"Training run not found after completion: {run_id}")
        return record


def _training_run_select() -> sa.Select[Any]:
    model_version_subquery = (
        sa.select(model_versions.c.model_key)
        .where(model_versions.c.training_run_id == training_runs.c.id)
        .limit(1)
        .scalar_subquery()
    )
    return (
        sa.select(
            training_runs.c.run_key,
            training_runs.c.status,
            jobs.c.job_key,
            datasets.c.dataset_key,
            dataset_versions.c.version_key,
            training_runs.c.backbone_id,
            training_runs.c.extractor_config,
            training_runs.c.head_config,
            training_runs.c.metrics,
            training_runs.c.error_message,
            training_runs.c.created_at,
            training_runs.c.updated_at,
            training_runs.c.started_at,
            training_runs.c.finished_at,
            feature_artifacts.c.artifact_key.label("feature_artifact_key"),
            model_artifacts.c.artifact_key.label("model_artifact_key"),
            report_artifacts.c.artifact_key.label("report_artifact_key"),
            calibration_artifacts.c.artifact_key.label("calibration_artifact_key"),
            threshold_artifacts.c.artifact_key.label("threshold_strategy_artifact_key"),
            model_version_subquery.label("model_version_key"),
        )
        .select_from(
            training_runs.join(jobs, jobs.c.id == training_runs.c.job_id)
            .join(datasets, datasets.c.id == training_runs.c.dataset_id)
            .join(dataset_versions, dataset_versions.c.id == training_runs.c.dataset_version_id)
            .outerjoin(feature_artifacts, feature_artifacts.c.id == training_runs.c.feature_artifact_id)
            .outerjoin(model_artifacts, model_artifacts.c.id == training_runs.c.model_artifact_id)
            .outerjoin(report_artifacts, report_artifacts.c.id == training_runs.c.report_artifact_id)
            .outerjoin(calibration_artifacts, calibration_artifacts.c.id == training_runs.c.calibration_artifact_id)
            .outerjoin(threshold_artifacts, threshold_artifacts.c.id == training_runs.c.threshold_strategy_artifact_id)
        )
    )


feature_artifacts = artifacts.alias("feature_artifacts")
model_artifacts = artifacts.alias("model_artifacts")
report_artifacts = artifacts.alias("report_artifacts")
calibration_artifacts = artifacts.alias("calibration_artifacts")
threshold_artifacts = artifacts.alias("threshold_artifacts")


def _training_run_from_row(row: Any) -> TrainingRunRecord:
    return TrainingRunRecord(
        run_id=row["run_key"],
        status=row["status"],
        job_id=row["job_key"],
        dataset_id=row["dataset_key"],
        dataset_version_id=row["version_key"],
        backbone_id=row["backbone_id"],
        extractor_config=dict(row["extractor_config"]),
        head_config=dict(row["head_config"]),
        feature_artifact_id=row["feature_artifact_key"],
        model_artifact_id=row["model_artifact_key"],
        model_version_id=row["model_version_key"],
        report_artifact_id=row["report_artifact_key"],
        calibration_artifact_id=row["calibration_artifact_key"],
        threshold_strategy_artifact_id=row["threshold_strategy_artifact_key"],
        metrics=dict(row["metrics"] or {}),
        error=row["error_message"],
        created_at=_to_iso(row["created_at"]),
        updated_at=_to_iso(row["updated_at"]),
        started_at=_to_iso(row["started_at"]) if row["started_at"] else None,
        finished_at=_to_iso(row["finished_at"]) if row["finished_at"] else None,
    )


def _upsert_artifact(
    conn: sa.Connection,
    *,
    artifact_key: str,
    artifact_type: str,
    dataset_id: UUID,
    dataset_version_id: UUID,
    job_id: UUID,
    uri: str,
    content_type: str,
    metadata: dict[str, Any],
    now: datetime,
) -> UUID:
    row = conn.execute(sa.select(artifacts.c.id).where(artifacts.c.artifact_key == artifact_key)).mappings().first()
    if row is None:
        artifact_id = uuid4()
        conn.execute(
            artifacts.insert().values(
                id=artifact_id,
                artifact_key=artifact_key,
                artifact_type=artifact_type,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
                job_id=job_id,
                uri=uri,
                content_type=content_type,
                artifact_metadata=metadata,
                created_at=now,
            )
        )
        return artifact_id

    artifact_id = row["id"]
    conn.execute(
        artifacts.update()
        .where(artifacts.c.id == artifact_id)
        .values(
            artifact_type=artifact_type,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            job_id=job_id,
            uri=uri,
            content_type=content_type,
            artifact_metadata=metadata,
        )
    )
    return artifact_id


def _feature_artifact_key(
    dataset_version_id: str,
    backbone_id: str,
    extractor_config: dict[str, Any] | None = None,
) -> str:
    config = extractor_config or {}
    config_hash = hashlib.sha1(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    return f"feature:{dataset_version_id}:{backbone_id}:{config_hash}"


def _now() -> datetime:
    return datetime.now(UTC)


def _to_iso(value: datetime) -> str:
    return value.isoformat()
