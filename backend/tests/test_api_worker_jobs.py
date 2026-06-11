from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from finevision.api import create_app
from finevision.ml_toolkit.toydata import create_toy_imagefolder
from finevision.worker import run_next_job


def test_import_imagefolder_job_lifecycle_creates_dataset_metadata(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata"
    dataset_dir = create_toy_imagefolder(tmp_path / "toy-imagefolder", samples_per_class=6)
    client = TestClient(create_app(metadata_dir=metadata_dir))

    create_response = client.post(
        "/api/jobs",
        json={
            "type": "import_imagefolder",
            "payload": {
                "path": str(dataset_dir),
                "dataset_id": "toy-shapes",
                "dataset_version_id": "dataset@toy-001",
            },
        },
    )
    assert create_response.status_code == 202
    created_job = create_response.json()["job"]
    assert created_job["status"] == "queued"
    assert created_job["type"] == "import_imagefolder"
    assert created_job["result"] is None
    assert created_job["error"] is None

    assert client.get("/api/datasets/toy-shapes").status_code == 404

    completed_job = run_next_job(metadata_dir)
    assert completed_job is not None
    assert completed_job.job_id == created_job["job_id"]
    assert completed_job.status == "succeeded"
    assert completed_job.result == {
        "dataset_id": "toy-shapes",
        "dataset_version_id": "dataset@toy-001",
        "sample_count": 18,
        "class_count": 3,
        "ready": True,
    }

    job_response = client.get(f"/api/jobs/{created_job['job_id']}")
    assert job_response.status_code == 200
    stored_job = job_response.json()["job"]
    assert stored_job["status"] == "succeeded"
    assert stored_job["started_at"] is not None
    assert stored_job["finished_at"] is not None

    list_response = client.get("/api/jobs")
    assert list_response.status_code == 200
    assert [job["job_id"] for job in list_response.json()["jobs"]] == [created_job["job_id"]]

    detail_response = client.get("/api/datasets/toy-shapes")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["dataset_id"] == "toy-shapes"
    assert detail["sample_count"] == 18
    assert detail["status"] == "ready"

    readiness_response = client.get("/api/dataset-versions/dataset@toy-001/readiness")
    assert readiness_response.status_code == 200
    assert readiness_response.json()["readiness"]["ready"] is True


def test_import_imagefolder_job_failure_is_persisted(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata"
    client = TestClient(create_app(metadata_dir=metadata_dir))

    create_response = client.post(
        "/api/jobs",
        json={
            "type": "import_imagefolder",
            "payload": {
                "path": str(tmp_path / "missing-imagefolder"),
                "dataset_id": "broken",
                "dataset_version_id": "dataset@broken-001",
            },
        },
    )
    assert create_response.status_code == 202
    created_job = create_response.json()["job"]
    assert created_job["status"] == "queued"

    completed_job = run_next_job(metadata_dir)
    assert completed_job is not None
    assert completed_job.job_id == created_job["job_id"]
    assert completed_job.status == "failed"
    assert "missing-imagefolder" in str(completed_job.error)

    job_response = client.get(f"/api/jobs/{created_job['job_id']}")
    assert job_response.status_code == 200
    stored_job = job_response.json()["job"]
    assert stored_job["status"] == "failed"
    assert "missing-imagefolder" in stored_job["error"]

    assert client.get("/api/datasets/broken").status_code == 404
