from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from finevision.api import create_app
from finevision.api.db_store import DatabaseJobStore
from finevision.db.schema import artifacts, dataset_versions, datasets, job_events, jobs
from finevision.ml_toolkit.toydata import create_toy_imagefolder
from finevision.worker import run_next_job


@pytest.fixture()
def database_url() -> str:
    url = os.environ.get("FINEVISION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set FINEVISION_TEST_DATABASE_URL to run PostgreSQL store integration tests.")
    _reset_database(url)
    return url


def test_database_backed_import_job_lifecycle(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / "toy-imagefolder", samples_per_class=4)
    client = TestClient(create_app(database_url=database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("FINEVISION_METADATA_DIR", raising=False)

    create_response = client.post(
        "/api/jobs",
        json={
            "type": "import_imagefolder",
            "payload": {
                "path": str(dataset_dir),
                "dataset_id": "db-toy",
                "dataset_version_id": "dataset@db-toy-001",
            },
        },
    )
    assert create_response.status_code == 202
    created_job = create_response.json()["job"]
    assert created_job["status"] == "queued"

    completed_job = run_next_job()
    assert completed_job is not None
    assert completed_job.job_id == created_job["job_id"]
    assert completed_job.status == "succeeded"

    job_response = client.get(f"/api/jobs/{created_job['job_id']}")
    assert job_response.status_code == 200
    stored_job = job_response.json()["job"]
    assert stored_job["status"] == "succeeded"
    assert stored_job["started_at"] is not None
    assert stored_job["finished_at"] is not None

    detail_response = client.get("/api/datasets/db-toy")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["dataset_id"] == "db-toy"
    assert detail["dataset_version_id"] == "dataset@db-toy-001"
    assert detail["classes"] == ["blue_triangle", "green_circle", "red_square"]
    assert detail["sample_count"] == 12
    assert detail["status"] == "ready"

    readiness_response = client.get("/api/dataset-versions/dataset@db-toy-001/readiness")
    assert readiness_response.status_code == 200
    assert readiness_response.json()["readiness"]["ready"] is True

    engine = create_engine(database_url)
    with engine.begin() as conn:
        counts = {
            "datasets": conn.scalar(sa.select(sa.func.count()).select_from(datasets)),
            "dataset_versions": conn.scalar(sa.select(sa.func.count()).select_from(dataset_versions)),
            "artifacts": conn.scalar(sa.select(sa.func.count()).select_from(artifacts)),
            "jobs": conn.scalar(sa.select(sa.func.count()).select_from(jobs)),
            "job_events": conn.scalar(sa.select(sa.func.count()).select_from(job_events)),
        }
    assert counts == {"datasets": 1, "dataset_versions": 1, "artifacts": 1, "jobs": 1, "job_events": 3}


def test_database_backed_dataset_asset_api_import_list_detail_and_readiness(
    database_url: str,
    tmp_path: Path,
) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / "toy-imagefolder", samples_per_class=3)
    client = TestClient(create_app(database_url=database_url))

    assert client.get("/api/datasets").json() == {"datasets": []}

    import_response = client.post(
        "/api/datasets/import-imagefolder",
        json={
            "path": str(dataset_dir),
            "dataset_id": "db-sync-toy",
            "dataset_version_id": "dataset@db-sync-toy-001",
        },
    )
    assert import_response.status_code == 201
    imported = import_response.json()
    assert imported["version"]["sample_count"] == 9
    assert imported["version"]["readiness"]["ready"] is True

    list_response = client.get("/api/datasets")
    assert list_response.status_code == 200
    assert list_response.json()["datasets"][0]["dataset_id"] == "db-sync-toy"

    detail_response = client.get("/api/datasets/db-sync-toy")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["dataset_version_id"] == "dataset@db-sync-toy-001"
    assert detail["sample_count"] == 9

    engine = create_engine(database_url)
    with engine.begin() as conn:
        manifest = conn.execute(sa.select(artifacts.c.artifact_metadata)).scalar_one()["manifest"]
    assert manifest["dataset_id"] == "db-sync-toy"
    assert manifest["dataset_version_id"] == "dataset@db-sync-toy-001"
    assert len(manifest["samples"]) == 9


def test_database_backed_import_job_failure_is_persisted(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(create_app(database_url=database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("FINEVISION_METADATA_DIR", raising=False)

    create_response = client.post(
        "/api/jobs",
        json={
            "type": "import_imagefolder",
            "payload": {
                "path": str(tmp_path / "missing-imagefolder"),
                "dataset_id": "db-broken",
                "dataset_version_id": "dataset@db-broken-001",
            },
        },
    )
    assert create_response.status_code == 202
    created_job = create_response.json()["job"]

    completed_job = run_next_job()
    assert completed_job is not None
    assert completed_job.job_id == created_job["job_id"]
    assert completed_job.status == "failed"
    assert "missing-imagefolder" in str(completed_job.error)

    job_response = client.get(f"/api/jobs/{created_job['job_id']}")
    assert job_response.status_code == 200
    assert job_response.json()["job"]["status"] == "failed"
    assert client.get("/api/datasets/db-broken").status_code == 404

    engine = create_engine(database_url)
    with engine.begin() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(datasets)) == 0
        assert conn.scalar(sa.select(sa.func.count()).select_from(job_events)) == 3


def test_database_backed_queued_import_job_can_be_cancelled(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / "toy-imagefolder", samples_per_class=2)
    client = TestClient(create_app(database_url=database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("FINEVISION_METADATA_DIR", raising=False)

    create_response = client.post(
        "/api/jobs",
        json={
            "type": "import_imagefolder",
            "payload": {
                "path": str(dataset_dir),
                "dataset_id": "db-cancelled",
                "dataset_version_id": "dataset@db-cancelled-001",
            },
        },
    )
    assert create_response.status_code == 202
    created_job = create_response.json()["job"]

    cancel_response = client.post(f"/api/jobs/{created_job['job_id']}/cancel")
    assert cancel_response.status_code == 200
    cancelled_job = cancel_response.json()["job"]
    assert cancelled_job["status"] == "cancelled"
    assert cancelled_job["finished_at"] is not None

    assert run_next_job() is None
    assert client.get("/api/datasets/db-cancelled").status_code == 404

    engine = create_engine(database_url)
    with engine.begin() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(datasets)) == 0
        assert conn.scalar(sa.select(sa.func.count()).select_from(job_events)) == 2


def test_database_job_claim_is_transactional(database_url: str) -> None:
    first_worker = DatabaseJobStore(database_url, lease_owner="test-worker-a")
    second_worker = DatabaseJobStore(database_url, lease_owner="test-worker-b")
    job = first_worker.create_job(
        "import_imagefolder",
        {
            "path": "/tmp/not-needed-for-claim-test",
            "dataset_id": "claim-test",
            "dataset_version_id": "dataset@claim-test-001",
        },
    )

    claimed = first_worker.claim_next_queued_job()
    assert claimed is not None
    assert claimed.job_id == job.job_id
    assert claimed.status == "running"

    assert second_worker.claim_next_queued_job() is None

    completed = first_worker.mark_succeeded(claimed, {"ok": True})
    assert completed.status == "succeeded"
    assert first_worker.get_job(job.job_id).status == "succeeded"  # type: ignore[union-attr]


def _reset_database(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "TRUNCATE TABLE job_events, artifacts, dataset_versions, datasets, jobs RESTART IDENTITY CASCADE"
            )
        )
