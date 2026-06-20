from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy.engine import Engine

from finevision.ml_toolkit.artifacts import read_dataset_manifest, write_dataset_manifest
from finevision.schemas.artifacts import DatasetManifest

JobStatus = Literal["queued", "paused", "running", "succeeded", "failed", "cancelled"]
JobType = Literal["import_imagefolder", "train_classifier"]


@dataclass(frozen=True)
class DatasetSummary:
    dataset_id: str
    latest_version_id: str
    dataset_version_id: str
    version_count: int
    classes: list[str]
    class_count: int
    sample_count: int
    status: str
    readiness: dict[str, Any]


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    type: JobType
    status: JobStatus
    payload: dict[str, Any]
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class MetadataStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def save_dataset_manifest(self, manifest: DatasetManifest) -> Path:
        return write_dataset_manifest(self._manifest_path(manifest.dataset_id, manifest.dataset_version_id), manifest)

    def get_dataset_manifest(self, dataset_id: str, dataset_version_id: str) -> DatasetManifest | None:
        path = self._manifest_path(dataset_id, dataset_version_id)
        if not path.exists():
            return None
        return read_dataset_manifest(path)

    def get_dataset_version(self, dataset_version_id: str) -> DatasetManifest | None:
        matches = sorted(self.root.glob(f"datasets/*/versions/{dataset_version_id}/manifest.json"))
        if not matches:
            return None
        return read_dataset_manifest(matches[0])

    def list_dataset_manifests(self, dataset_id: str | None = None) -> list[DatasetManifest]:
        pattern = f"datasets/{dataset_id}/versions/*/manifest.json" if dataset_id else "datasets/*/versions/*/manifest.json"
        return [read_dataset_manifest(path) for path in sorted(self.root.glob(pattern))]

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
        }

    @staticmethod
    def readiness_status(manifest: DatasetManifest) -> str:
        return "ready" if manifest.readiness.get("ready") is True else "needs_attention"

    @staticmethod
    def version_summary(manifest: DatasetManifest) -> dict[str, Any]:
        split_totals = Counter(sample.split for sample in manifest.samples)
        return {
            "dataset_version_id": manifest.dataset_version_id,
            "root": manifest.root,
            "classes": manifest.classes,
            "class_count": len(manifest.classes),
            "sample_count": len(manifest.samples),
            "split_totals": dict(sorted(split_totals.items())),
            "split_counts": manifest.split_counts,
            "status": MetadataStore.readiness_status(manifest),
            "readiness": manifest.readiness,
            "artifact_refs": manifest.artifact_refs,
        }

    def _manifest_path(self, dataset_id: str, dataset_version_id: str) -> Path:
        return self.root / "datasets" / dataset_id / "versions" / dataset_version_id / "manifest.json"


class JobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def create_job(self, job_type: JobType, payload: dict[str, Any]) -> JobRecord:
        now = _timestamp()
        job = JobRecord(
            job_id=f"job-{uuid4().hex[:12]}",
            type=job_type,
            status="queued",
            payload=payload,
            created_at=now,
            updated_at=now,
        )
        self.save_job(job)
        return job

    def save_job(self, job: JobRecord) -> Path:
        path = self._job_path(job.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(job), indent=2, sort_keys=True), encoding="utf-8")
        return path

    def get_job(self, job_id: str) -> JobRecord | None:
        path = self._job_path(job_id)
        if not path.exists():
            return None
        return self._read_job(path)

    def list_jobs(self) -> list[JobRecord]:
        return [self._read_job(path) for path in sorted((self.root / "jobs").glob("*.json"))]

    def next_queued_job(self) -> JobRecord | None:
        queued = [job for job in self.list_jobs() if job.status == "queued"]
        if not queued:
            return None
        return sorted(queued, key=lambda job: job.created_at)[0]

    def mark_running(self, job: JobRecord) -> JobRecord:
        now = _timestamp()
        updated = _replace_job(job, status="running", started_at=now, updated_at=now, error=None)
        self.save_job(updated)
        return updated

    def mark_succeeded(self, job: JobRecord, result: dict[str, Any]) -> JobRecord:
        now = _timestamp()
        updated = _replace_job(
            job,
            status="succeeded",
            result=result,
            error=None,
            finished_at=now,
            updated_at=now,
        )
        self.save_job(updated)
        return updated

    def mark_failed(self, job: JobRecord, error: str) -> JobRecord:
        now = _timestamp()
        updated = _replace_job(
            job,
            status="failed",
            error=error,
            finished_at=now,
            updated_at=now,
        )
        self.save_job(updated)
        return updated

    def cancel_job(self, job: JobRecord) -> JobRecord:
        now = _timestamp()
        updated = _replace_job(job, status="cancelled", finished_at=now, updated_at=now)
        self.save_job(updated)
        return updated

    def _job_path(self, job_id: str) -> Path:
        return self.root / "jobs" / f"{job_id}.json"

    @staticmethod
    def _read_job(path: Path) -> JobRecord:
        data = json.loads(path.read_text(encoding="utf-8"))
        return JobRecord(**data)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _replace_job(job: JobRecord, **changes: Any) -> JobRecord:
    data = asdict(job)
    data.update(changes)
    return JobRecord(**data)


def create_stores(
    *,
    metadata_dir: str | Path | None = None,
    database_url: str | Engine | None = None,
) -> tuple[MetadataStore, JobStore]:
    if database_url:
        from finevision.api.db_store import DatabaseJobStore, DatabaseMetadataStore

        return DatabaseMetadataStore(database_url), DatabaseJobStore(database_url)

    root = metadata_dir or ".finevision-api/metadata"
    return MetadataStore(root), JobStore(root)
