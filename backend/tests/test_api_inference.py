from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine

from finevision.api import create_app
from finevision.db.schema import artifacts, dataset_versions, datasets, job_events, jobs, model_versions, training_runs
from finevision.ml_toolkit.toydata import create_toy_imagefolder
from finevision.worker import run_next_job


@pytest.fixture()
def database_url() -> str:
    url = os.environ.get("FINEVISION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set FINEVISION_TEST_DATABASE_URL to run PostgreSQL inference integration tests.")
    _reset_database(url)
    return url


def test_scoped_inference_returns_accept_decision_and_nearest_neighbors(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, sample_id = _trained_toy_context(database_url, tmp_path, monkeypatch)

    response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@infer-toy-001",
            "model_version_id": model_version_id,
            "sample_id": sample_id,
            "top_k": 10,
            "accept_threshold": 0.0,
            "margin_threshold": 0.0,
        },
    )
    assert response.status_code == 200
    body = response.json()["inference_result"]
    result = body["result"]
    decision = result["decision"]

    assert body["dataset_id"] == "infer-toy"
    assert body["dataset_version_id"] == "dataset@infer-toy-001"
    assert body["model_version_id"] == model_version_id
    assert body["model_artifact_id"]
    assert body["threshold_strategy_id"]
    assert len(result["top_k"]) == 3
    assert decision["decision"] == "accept"
    assert decision["reasons"] == ["meets_acceptance_thresholds"]
    assert decision["confidence"] >= 0.0
    assert decision["margin"] >= 0.0
    assert result["nearest_neighbors"]
    assert result["nearest_neighbors"][0]["sample_id"] != sample_id
    assert {"sample_id", "label", "distance"} <= set(result["nearest_neighbors"][0])


def test_scoped_inference_can_abstain_for_low_confidence_and_low_margin(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, sample_id = _trained_toy_context(database_url, tmp_path, monkeypatch)

    confidence_response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@infer-toy-001",
            "model_version_id": model_version_id,
            "sample_id": sample_id,
            "accept_threshold": 1.0,
            "margin_threshold": 0.0,
        },
    )
    assert confidence_response.status_code == 200
    confidence_decision = confidence_response.json()["inference_result"]["result"]["decision"]
    assert confidence_decision["decision"] == "abstain"
    assert "confidence_below_threshold" in confidence_decision["reasons"]

    margin_response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@infer-toy-001",
            "model_version_id": model_version_id,
            "sample_id": sample_id,
            "accept_threshold": 0.0,
            "margin_threshold": 1.0,
        },
    )
    assert margin_response.status_code == 200
    margin_decision = margin_response.json()["inference_result"]["result"]["decision"]
    assert margin_decision["decision"] == "abstain"
    assert "top1_top2_margin_below_threshold" in margin_decision["reasons"]


def test_scoped_inference_can_reject_ood_image(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, _sample_id = _trained_toy_context(database_url, tmp_path, monkeypatch)
    query_path = tmp_path / "ood.png"
    Image.new("RGB", (96, 96), (0, 0, 0)).save(query_path)

    response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@infer-toy-001",
            "model_version_id": model_version_id,
            "image_path": str(query_path),
            "accept_threshold": 0.0,
            "margin_threshold": 0.0,
            "ood_distance_threshold": 0.0,
        },
    )
    assert response.status_code == 200
    decision = response.json()["inference_result"]["result"]["decision"]
    assert decision["decision"] == "reject_ood"
    assert "embedding_distance_above_threshold" in decision["reasons"]
    assert decision["ood_score"] > 0.0
    assert decision["thresholds"]["ood_distance"] == 0.0


def test_scoped_inference_rejects_unknown_model_version(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _model_version_id, sample_id = _trained_toy_context(database_url, tmp_path, monkeypatch)

    response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@infer-toy-001",
            "model_version_id": "missing-model",
            "sample_id": sample_id,
        },
    )
    assert response.status_code == 404


def _trained_toy_context(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, str, str]:
    dataset_dir = create_toy_imagefolder(tmp_path / "toy-imagefolder", samples_per_class=5)
    artifact_dir = tmp_path / "artifacts"
    client = TestClient(create_app(database_url=database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("FINEVISION_ARTIFACT_DIR", str(artifact_dir))
    monkeypatch.delenv("FINEVISION_METADATA_DIR", raising=False)

    import_response = client.post(
        "/api/datasets/import-imagefolder",
        json={
            "path": str(dataset_dir),
            "dataset_id": "infer-toy",
            "dataset_version_id": "dataset@infer-toy-001",
        },
    )
    assert import_response.status_code == 201

    create_run_response = client.post(
        "/api/training-runs",
        json={"dataset_version_id": "dataset@infer-toy-001"},
    )
    assert create_run_response.status_code == 202

    completed_job = run_next_job()
    assert completed_job is not None
    assert completed_job.status == "succeeded"
    model_version_id = str(completed_job.result["model_version_id"])

    engine = create_engine(database_url)
    with engine.begin() as conn:
        feature_metadata = conn.execute(
            sa.select(artifacts.c.artifact_metadata).where(artifacts.c.artifact_type == "feature_matrix")
        ).scalar_one()
    sample_id = str(feature_metadata["feature_artifact"]["sample_ids"][-1])
    return client, model_version_id, sample_id


def _reset_database(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "TRUNCATE TABLE model_versions, training_runs, job_events, artifacts, dataset_versions, datasets, jobs RESTART IDENTITY CASCADE"
            )
        )
