from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from finevision.api import create_app
from finevision.db.schema import (
    abstention_shadow_decisions,
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
        pytest.skip("Set FINEVISION_TEST_DATABASE_URL to run PostgreSQL online policy API contract tests.")
    _reset_database(url)
    return url


def test_policy_propose_list_and_detail_contract(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, _sample_id, _label = _trained_toy_context(database_url, tmp_path, monkeypatch)
    _create_nonfeedback_inference_sample(client, model_version_id=model_version_id, sample_id=_sample_id)
    review_item_id = _create_feedback_sample(client, model_version_id=model_version_id, tmp_path=tmp_path)

    propose_response = client.post(
        "/api/abstention-policies/propose",
        json={
            "dataset_version_id": "dataset@policy-api-toy-001",
            "model_version_id": model_version_id,
            "target_selective_risk": 0.05,
            "review_cost_per_item": 2.0,
        },
    )
    assert propose_response.status_code == 201
    proposal = propose_response.json()["policy"]

    assert proposal["policy_id"].startswith("policy-")
    assert proposal["dataset_id"] == "policy-api-toy"
    assert proposal["dataset_version_id"] == "dataset@policy-api-toy-001"
    assert proposal["model_version_id"] == model_version_id
    assert proposal["status"] == "shadow"
    assert proposal["selection_config"]["selection_rule"] in {"max_coverage_under_target_risk", "min_risk_fallback"}
    assert proposal["target_selective_risk"] == 0.05
    assert proposal["source_feedback_count"] == 1
    assert 0.0 <= proposal["estimated_selective_risk"] <= 1.0
    assert 0.0 <= proposal["estimated_coverage"] <= 1.0
    assert 0.0 <= proposal["tau_conf"] <= 1.0
    assert 0.0 <= proposal["tau_margin"] <= 1.0
    assert proposal["created_at"]

    list_response = client.get(
        "/api/abstention-policies",
        params={
            "dataset_version_id": "dataset@policy-api-toy-001",
            "model_version_id": model_version_id,
        },
    )
    assert list_response.status_code == 200
    policies = list_response.json()["policies"]
    assert [policy["policy_id"] for policy in policies] == [proposal["policy_id"]]
    assert policies[0]["status"] == "shadow"

    detail_response = client.get(f"/api/abstention-policies/{proposal['policy_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["policy"]
    assert detail == proposal

    shadow_response = client.get(f"/api/abstention-policies/{proposal['policy_id']}/shadow-decisions")
    assert shadow_response.status_code == 200
    shadows = shadow_response.json()["shadow_decisions"]
    assert len(shadows) == 2
    assert {item["score_snapshot"]["final_outcome"] for item in shadows} == {None, "ood"}
    assert {item["policy_id"] for item in shadows} == {proposal["policy_id"]}
    assert review_item_id


def test_shadow_policy_write_does_not_change_real_inference_or_review_route(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, sample_id, _label = _trained_toy_context(database_url, tmp_path, monkeypatch)
    _create_feedback_sample(client, model_version_id=model_version_id, tmp_path=tmp_path)
    proposal_response = client.post(
        "/api/abstention-policies/propose",
        json={
            "dataset_version_id": "dataset@policy-api-toy-001",
            "model_version_id": model_version_id,
            "target_selective_risk": 0.05,
        },
    )
    assert proposal_response.status_code == 201
    policy_id = proposal_response.json()["policy"]["policy_id"]

    engine = create_engine(database_url)
    before = _event_counts(engine)
    inference_response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@policy-api-toy-001",
            "model_version_id": model_version_id,
            "sample_id": sample_id,
            "accept_threshold": 0.0,
            "margin_threshold": 0.0,
            "route_to_review": True,
        },
    )
    after = _event_counts(engine)

    assert inference_response.status_code == 200
    inference = inference_response.json()["inference_result"]
    assert inference["result"]["decision"]["decision"] == "accept"
    assert inference["review_item_id"] is None
    assert inference["shadow_policy_count"] == 1
    assert after["inference_events"] == before["inference_events"] + 1
    assert after["review_items"] == before["review_items"]
    assert after["feedback_items"] == before["feedback_items"]
    assert after["shadow_decisions"] == before["shadow_decisions"] + 1

    shadow_response = client.get(f"/api/abstention-policies/{policy_id}/shadow-decisions")
    assert shadow_response.status_code == 200
    assert len(shadow_response.json()["shadow_decisions"]) == 2


def test_policy_propose_without_feedback_is_rejected(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, sample_id, _label = _trained_toy_context(database_url, tmp_path, monkeypatch)
    _create_nonfeedback_inference_sample(client, model_version_id=model_version_id, sample_id=sample_id)

    propose_response = client.post(
        "/api/abstention-policies/propose",
        json={
            "dataset_version_id": "dataset@policy-api-toy-001",
            "model_version_id": model_version_id,
            "target_selective_risk": 0.05,
        },
    )

    assert propose_response.status_code == 409
    assert "evaluable feedback item" in propose_response.json()["detail"]


def test_route_to_review_false_still_records_inference_event_and_shadow(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, sample_id, _label = _trained_toy_context(database_url, tmp_path, monkeypatch)
    _create_feedback_sample(client, model_version_id=model_version_id, tmp_path=tmp_path)
    proposal_response = client.post(
        "/api/abstention-policies/propose",
        json={
            "dataset_version_id": "dataset@policy-api-toy-001",
            "model_version_id": model_version_id,
            "target_selective_risk": 0.05,
        },
    )
    assert proposal_response.status_code == 201

    engine = create_engine(database_url)
    before = _event_counts(engine)
    inference_response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@policy-api-toy-001",
            "model_version_id": model_version_id,
            "sample_id": sample_id,
            "accept_threshold": 0.0,
            "margin_threshold": 0.0,
            "route_to_review": False,
        },
    )
    after = _event_counts(engine)

    assert inference_response.status_code == 200
    inference = inference_response.json()["inference_result"]
    assert inference["inference_event_id"]
    assert inference["review_item_id"] is None
    assert inference["shadow_policy_count"] == 1
    assert after["inference_events"] == before["inference_events"] + 1
    assert after["review_items"] == before["review_items"]
    assert after["shadow_decisions"] == before["shadow_decisions"] + 1


def test_shadow_policy_write_error_is_exposed_in_inference_payload(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, sample_id, _label = _trained_toy_context(database_url, tmp_path, monkeypatch)

    def fail_shadow_write(_inference_event_id: str) -> int:
        raise RuntimeError("shadow write failed")

    monkeypatch.setattr(client.app.state.abstention_store, "record_shadow_for_inference_event", fail_shadow_write)

    inference_response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@policy-api-toy-001",
            "model_version_id": model_version_id,
            "sample_id": sample_id,
            "accept_threshold": 0.0,
            "margin_threshold": 0.0,
            "route_to_review": True,
        },
    )

    assert inference_response.status_code == 200
    inference = inference_response.json()["inference_result"]
    assert inference["shadow_policy_count"] is None
    assert inference["shadow_policy_error"] == {
        "type": "RuntimeError",
        "message": "shadow write failed",
    }


def _trained_toy_context(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, str, str, str]:
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
            "dataset_id": "policy-api-toy",
            "dataset_version_id": "dataset@policy-api-toy-001",
        },
    )
    assert import_response.status_code == 201

    create_run_response = client.post(
        "/api/training-runs",
        json={"dataset_version_id": "dataset@policy-api-toy-001"},
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
    label = str(feature_metadata["feature_artifact"]["labels"][-1])
    return client, model_version_id, sample_id, label


def _create_feedback_sample(client: TestClient, *, model_version_id: str, tmp_path: Path) -> str:
    query_path = tmp_path / f"policy-ood-{os.urandom(3).hex()}.png"
    Image.new("RGB", (96, 96), (0, 0, 0)).save(query_path)
    inference_response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@policy-api-toy-001",
            "model_version_id": model_version_id,
            "image_path": str(query_path),
            "accept_threshold": 1.0,
            "margin_threshold": 1.0,
            "ood_distance_threshold": 0.0,
        },
    )
    assert inference_response.status_code == 200
    review_item_id = inference_response.json()["inference_result"]["review_item_id"]
    assert review_item_id
    submit_response = client.post(
        f"/api/review-items/{review_item_id}/submit",
        json={
            "final_outcome": "ood",
            "destination": "ood_stress",
            "final_label": None,
            "reviewer_note": "policy feedback fixture",
            "reviewer": "qa",
        },
    )
    assert submit_response.status_code == 200
    return str(review_item_id)


def _create_nonfeedback_inference_sample(client: TestClient, *, model_version_id: str, sample_id: str) -> str:
    inference_response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@policy-api-toy-001",
            "model_version_id": model_version_id,
            "sample_id": sample_id,
            "accept_threshold": 0.0,
            "margin_threshold": 0.0,
            "route_to_review": True,
        },
    )
    assert inference_response.status_code == 200
    inference = inference_response.json()["inference_result"]
    assert inference["review_item_id"] is None
    return str(inference["inference_event_id"])


def _event_counts(engine: sa.Engine) -> dict[str, int]:
    with engine.begin() as conn:
        return {
            "inference_events": conn.execute(sa.select(sa.func.count()).select_from(inference_events)).scalar_one(),
            "review_items": conn.execute(sa.select(sa.func.count()).select_from(review_items)).scalar_one(),
            "feedback_items": conn.execute(sa.select(sa.func.count()).select_from(feedback_items)).scalar_one(),
            "shadow_decisions": conn.execute(sa.select(sa.func.count()).select_from(abstention_shadow_decisions)).scalar_one(),
        }


def _reset_database(database_url: str) -> None:
    _assert_test_database_url(database_url)
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "TRUNCATE TABLE model_versions, training_runs, job_events, artifacts, "
                "dataset_versions, datasets, jobs RESTART IDENTITY CASCADE"
            )
        )


def _assert_test_database_url(database_url: str) -> None:
    database_name = (make_url(database_url).database or "").lower()
    if "test" not in database_name:
        pytest.fail(
            f"Refusing to reset non-test database {database_name!r}; "
            "set FINEVISION_TEST_DATABASE_URL to a dedicated test database."
        )
