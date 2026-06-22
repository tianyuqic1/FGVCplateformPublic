from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine

from finevision.api import create_app
from finevision.db.schema import (
    artifacts,
    dataset_versions,
    datasets,
    feedback_items,
    inference_events,
    job_events,
    jobs,
    model_versions,
    review_items,
    training_runs,
)
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
    assert body["inference_event_id"]
    assert body["review_item_id"] is None

    engine = create_engine(database_url)
    with engine.begin() as conn:
        inference_count = conn.execute(sa.select(sa.func.count()).select_from(inference_events)).scalar_one()
        review_count = conn.execute(sa.select(sa.func.count()).select_from(review_items)).scalar_one()
    assert inference_count == 1
    assert review_count == 0


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


def test_abstain_inference_creates_review_item_and_feedback(
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
            "accept_threshold": 1.0,
            "margin_threshold": 1.0,
        },
    )
    assert response.status_code == 200
    body = response.json()["inference_result"]
    review_item_id = body["review_item_id"]
    assert body["inference_event_id"]
    assert review_item_id

    list_response = client.get("/api/review-items")
    assert list_response.status_code == 200
    items = list_response.json()["review_items"]
    assert [item["review_item_id"] for item in items] == [review_item_id]
    assert items[0]["status"] == "pending"
    assert items[0]["context"]["decision"]["decision"] == "abstain"
    assert items[0]["context"]["nearest_neighbors"]
    assert items[0]["image_url"] == f"/api/dataset-versions/dataset@infer-toy-001/samples/{sample_id}/image"
    assert items[0]["context"]["input"]["image_url"] == items[0]["image_url"]

    detail_response = client.get(f"/api/review-items/{review_item_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["review_item"]
    assert detail["risk_type"] in {"mixed", "low_confidence", "low_margin"}
    assert detail["image_url"] == f"/api/dataset-versions/dataset@infer-toy-001/samples/{sample_id}/image"

    image_response = client.get(detail["image_url"])
    assert image_response.status_code == 200
    assert image_response.content

    submit_response = client.post(
        f"/api/review-items/{review_item_id}/submit",
        json={
            "final_outcome": "corrected_label",
            "destination": "training_candidate",
            "final_label": "red_square",
            "reviewer_note": "human correction for test",
            "reviewer": "qa",
        },
    )
    assert submit_response.status_code == 200
    submit_body = submit_response.json()
    completed = submit_body["review_item"]
    assert completed["status"] == "feedbacked"
    assert completed["feedback"]["destination"] == "training_candidate"
    assert completed["feedback"]["review_item_id"] == review_item_id
    assert submit_body["feedback_item"]["review_item_id"] == review_item_id

    pending_response = client.get("/api/review-items?status=pending&dataset_id=infer-toy")
    assert pending_response.status_code == 200
    assert pending_response.json()["review_items"] == []

    completed_response = client.get("/api/review-items?status=feedbacked&dataset_id=infer-toy")
    assert completed_response.status_code == 200
    assert [item["review_item_id"] for item in completed_response.json()["review_items"]] == [review_item_id]

    feedback_response = client.get("/api/feedback-items?destination=training_candidate&dataset_id=infer-toy")
    assert feedback_response.status_code == 200
    feedback_items_payload = feedback_response.json()["feedback_items"]
    assert [item["review_item_id"] for item in feedback_items_payload] == [review_item_id]
    assert feedback_items_payload[0]["feedback_item_id"] == submit_body["feedback_item"]["feedback_item_id"]
    assert feedback_items_payload[0]["inference_event_id"] == body["inference_event_id"]
    assert feedback_items_payload[0]["dataset_id"] == "infer-toy"
    assert feedback_items_payload[0]["dataset_version_id"] == "dataset@infer-toy-001"
    assert feedback_items_payload[0]["model_version_id"] == model_version_id
    assert feedback_items_payload[0]["destination"] == "training_candidate"
    assert feedback_items_payload[0]["final_label"] == "red_square"

    all_response = client.get("/api/review-items?status=all")
    assert all_response.status_code == 200
    assert [item["review_item_id"] for item in all_response.json()["review_items"]] == [review_item_id]

    missing_dataset_response = client.get("/api/review-items?status=all&dataset_id=missing-dataset")
    assert missing_dataset_response.status_code == 200
    assert missing_dataset_response.json()["review_items"] == []

    invalid_status_response = client.get("/api/review-items?status=unknown")
    assert invalid_status_response.status_code == 422

    invalid_destination_response = client.get("/api/feedback-items?destination=unknown")
    assert invalid_destination_response.status_code == 422

    duplicate_response = client.post(
        f"/api/review-items/{review_item_id}/submit",
        json={
            "final_outcome": "ood",
            "destination": "ood_stress",
            "reviewer_note": "duplicate",
            "reviewer": "qa",
        },
    )
    assert duplicate_response.status_code == 409

    engine = create_engine(database_url)
    with engine.begin() as conn:
        feedback_count = conn.execute(sa.select(sa.func.count()).select_from(feedback_items)).scalar_one()
        dataset_count = conn.execute(sa.select(sa.func.count()).select_from(dataset_versions)).scalar_one()
    assert feedback_count == 1
    assert dataset_count == 1


def test_review_item_payload_exposes_sample_image_url_in_context() -> None:
    app_module = importlib.import_module("finevision.api.app")
    item = SimpleNamespace(
        review_item_id="review-123",
        inference_event_id="inference-123",
        dataset_id="infer-toy",
        dataset_version_id="dataset@infer-toy-001",
        model_version_id="model-123",
        sample_id="sample-abc123",
        input_ref="sample-abc123",
        status="pending",
        risk_type="low_confidence",
        priority=50,
        reason="Needs review",
        reason_codes=["confidence_below_threshold"],
        context={"input": {"sample_id": "sample-abc123"}, "decision": {"decision": "abstain"}},
        assistance_metadata={},
        created_at="2026-06-20T00:00:00+00:00",
        updated_at="2026-06-20T00:00:00+00:00",
        submitted_at=None,
        feedbacked_at=None,
        completed_by=None,
        feedback=None,
    )

    payload = app_module._review_item_payload(item)

    expected_image_url = "/api/dataset-versions/dataset@infer-toy-001/samples/sample-abc123/image"
    assert payload["image_url"] == expected_image_url
    assert payload["context"]["input"]["image_url"] == expected_image_url
    assert item.context["input"] == {"sample_id": "sample-abc123"}


def test_review_assistance_is_advisory_and_does_not_complete_review(
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
            "accept_threshold": 1.0,
            "margin_threshold": 1.0,
        },
    )
    assert response.status_code == 200
    review_item_id = response.json()["inference_result"]["review_item_id"]
    assert review_item_id

    def fake_assistance(*, task: str, context: dict[str, object]) -> dict[str, object]:
        assert task == "review_assistance"
        assert context["review_item_id"] == review_item_id
        assert context["dataset_version_id"] == "dataset@infer-toy-001"
        assert context["dataset_summary"]["dataset_version_id"] == "dataset@infer-toy-001"
        assert context["dataset_summary"]["task"] == "image_classification"
        assert context["top_k"]
        return {
            "task": task,
            "advisory_only": True,
            "model": "test-llm",
            "summary": "Check top-k and confirm the final label manually.",
            "holistic_analysis": "Dataset and model evidence point to a manual visual check first.",
            "inspection_notes": ["Compare the top two candidates."],
            "suggested_actions": ["Human reviewer must choose the final outcome."],
            "risk_flags": ["Do not auto-submit this advice."],
            "created_at": "2026-06-20T00:00:00+00:00",
        }

    app_module = importlib.import_module("finevision.api.app")
    monkeypatch.setattr(app_module, "generate_assistance", fake_assistance)

    assistance_response = client.post(
        f"/api/review-items/{review_item_id}/assist",
        json={"question": "What should I inspect?"},
    )
    assert assistance_response.status_code == 200
    assistance = assistance_response.json()["assistance"]
    assert assistance["advisory_only"] is True
    assert assistance["summary"].startswith("Check top-k")

    detail_response = client.get(f"/api/review-items/{review_item_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["review_item"]
    assert detail["status"] == "pending"
    assert detail["assistance_metadata"]["llm_assistance"]["summary"] == assistance["summary"]

    engine = create_engine(database_url)
    with engine.begin() as conn:
        feedback_count = conn.execute(sa.select(sa.func.count()).select_from(feedback_items)).scalar_one()
    assert feedback_count == 0


def test_generic_llm_assistance_returns_advisory_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(metadata_dir=".finevision-test-metadata"))

    def fake_assistance(*, task: str, context: dict[str, object]) -> dict[str, object]:
        assert task == "training_diagnosis"
        assert context["error"] == "missing model artifact"
        return {
            "task": task,
            "advisory_only": True,
            "model": "test-llm",
            "summary": "Model artifact is missing.",
            "holistic_analysis": "Training evidence is incomplete, so inspect logs before changing config.",
            "inspection_notes": ["Check worker logs."],
            "suggested_actions": ["Re-run training after feature extraction succeeds."],
            "risk_flags": [],
            "created_at": "2026-06-20T00:00:00+00:00",
        }

    app_module = importlib.import_module("finevision.api.app")
    monkeypatch.setattr(app_module, "generate_assistance", fake_assistance)
    response = client.post(
        "/api/llm/assist",
        json={"task": "training_diagnosis", "context": {"error": "missing model artifact"}},
    )
    assert response.status_code == 200
    assert response.json()["assistance"]["advisory_only"] is True


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
    review_item_id = response.json()["inference_result"]["review_item_id"]
    assert review_item_id
    detail_response = client.get(f"/api/review-items/{review_item_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["review_item"]
    assert detail["risk_type"] == "ood_candidate"
    assert detail["priority"] == 10


def test_scoped_inference_accepts_uploaded_image(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, _sample_id = _trained_toy_context(database_url, tmp_path, monkeypatch)
    query_path = tmp_path / "query.png"
    Image.new("RGB", (96, 96), (220, 40, 40)).save(query_path)

    with query_path.open("rb") as image:
        response = client.post(
            "/api/inference/upload",
            data={
                "dataset_version_id": "dataset@infer-toy-001",
                "model_version_id": model_version_id,
                "top_k": "3",
                "evidence_k": "2",
                "accept_threshold": "0.0",
                "margin_threshold": "0.0",
            },
            files={"image": ("query.png", image, "image/png")},
        )

    assert response.status_code == 200
    body = response.json()["inference_result"]
    result = body["result"]

    assert body["input"]["upload_filename"] == "query.png"
    assert body["input"]["uploaded_image_path"].endswith(".png")
    assert len(result["top_k"]) == 3
    assert result["nearest_neighbors"]


def test_uploaded_image_review_item_exposes_public_image_url(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINEVISION_UPLOAD_DIR", str(tmp_path / "uploads"))
    client, model_version_id, _sample_id = _trained_toy_context(database_url, tmp_path, monkeypatch)
    query_path = tmp_path / "needs-review.jpg"
    Image.new("RGB", (96, 96), (0, 0, 0)).save(query_path)

    with query_path.open("rb") as image:
        response = client.post(
            "/api/inference/upload",
            data={
                "dataset_version_id": "dataset@infer-toy-001",
                "model_version_id": model_version_id,
                "top_k": "3",
                "evidence_k": "2",
                "accept_threshold": "0.0",
                "margin_threshold": "0.0",
                "ood_distance_threshold": "0.0",
            },
            files={"image": ("needs-review.jpg", image, "image/jpeg")},
        )

    assert response.status_code == 200
    review_item_id = response.json()["inference_result"]["review_item_id"]
    assert review_item_id

    detail_response = client.get(f"/api/review-items/{review_item_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["review_item"]
    assert detail["image_url"].startswith("/api/uploads/")
    assert detail["image_url"].endswith(".jpg")

    image_response = client.get(detail["image_url"])
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/jpeg"


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
