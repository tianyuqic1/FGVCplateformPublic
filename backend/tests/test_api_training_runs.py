from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from finevision.api import create_app


def test_training_runs_require_database_backing(tmp_path: Path) -> None:
    client = TestClient(create_app(metadata_dir=tmp_path / "metadata"))

    list_response = client.get("/api/training-runs")
    assert list_response.status_code == 200
    assert list_response.json() == {"training_runs": []}

    create_response = client.post(
        "/api/training-runs",
        json={"dataset_version_id": "dataset@missing-001"},
    )
    assert create_response.status_code == 503


def test_train_classifier_jobs_are_created_through_training_runs(tmp_path: Path) -> None:
    client = TestClient(create_app(metadata_dir=tmp_path / "metadata"))

    response = client.post(
        "/api/jobs",
        json={
            "type": "train_classifier",
            "payload": {
                "training_run_id": "run-direct",
                "dataset_version_id": "dataset@direct-001",
            },
        },
    )
    assert response.status_code == 422
