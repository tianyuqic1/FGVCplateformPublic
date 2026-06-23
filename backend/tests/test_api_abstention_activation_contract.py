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
    abstention_policy_versions,
    artifacts,
    dataset_versions,
    datasets,
    job_events,
    jobs,
    model_versions,
    training_runs,
)
from finevision.ml_toolkit.toydata import create_toy_imagefolder
from finevision.worker import run_next_job


@pytest.fixture()
def database_url() -> str:
    url = os.environ.get("FINEVISION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set FINEVISION_TEST_DATABASE_URL to run PostgreSQL abstention activation contract tests.")
    _reset_database(url)
    return url


def test_activation_gate_blocks_until_min_feedback_is_satisfied(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, sample_id = _trained_toy_context(database_url, tmp_path, monkeypatch)
    _create_ood_feedback_sample(client, model_version_id=model_version_id, tmp_path=tmp_path)
    policy = _propose_policy(client, model_version_id=model_version_id)

    blocked = client.post(
        f"/api/abstention-policies/{policy['policy_id']}/activate",
        json={
            "activated_by": "qa",
            "activation_reason": "gate should reject under-sized feedback pool",
            "min_feedback_count": 2,
        },
    )
    assert blocked.status_code == 409
    assert "at least 2" in blocked.json()["detail"]

    allowed = client.post(
        f"/api/abstention-policies/{policy['policy_id']}/activate",
        json={
            "activated_by": "qa",
            "activation_reason": "small test fixture override",
            "min_feedback_count": 1,
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["policy"]["status"] == "active"


def test_active_policy_overrides_live_inference_thresholds(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, sample_id = _trained_toy_context(database_url, tmp_path, monkeypatch)
    _create_ood_feedback_sample(client, model_version_id=model_version_id, tmp_path=tmp_path)
    policy = _propose_policy(client, model_version_id=model_version_id)
    activated = client.post(
        f"/api/abstention-policies/{policy['policy_id']}/activate",
        json={
            "activated_by": "qa",
            "activation_reason": "verify active policy threshold application",
            "min_feedback_count": 1,
        },
    ).json()["policy"]

    inference_response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@activation-toy-001",
            "model_version_id": model_version_id,
            "sample_id": sample_id,
            "route_to_review": False,
        },
    )
    assert inference_response.status_code == 200
    inference = inference_response.json()["inference_result"]

    assert inference["applied_policy_id"] == activated["policy_id"]
    assert inference["applied_policy_source"] == "active_abstention_policy"
    assert inference["result"]["threshold_strategy_id"] == f"{activated['policy_id']}:active"
    assert inference["result"]["decision"]["thresholds"]["confidence"] == pytest.approx(activated["tau_conf"])
    assert inference["result"]["decision"]["thresholds"]["margin"] == pytest.approx(activated["tau_margin"])


def test_activate_new_policy_supersedes_deactivate_and_rollback(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, model_version_id, sample_id = _trained_toy_context(database_url, tmp_path, monkeypatch)
    _create_ood_feedback_sample(client, model_version_id=model_version_id, tmp_path=tmp_path)
    first = _propose_policy(client, model_version_id=model_version_id, target_risk=0.05)
    second = _propose_policy(client, model_version_id=model_version_id, target_risk=0.1)

    first_active = _activate(client, first["policy_id"])
    assert first_active["status"] == "active"
    second_active = _activate(client, second["policy_id"])
    assert second_active["status"] == "active"

    first_after = client.get(f"/api/abstention-policies/{first['policy_id']}").json()["policy"]
    assert first_after["status"] == "superseded"
    assert first_after["deactivation_reason"] == f"superseded by {second['policy_id']}"

    deactivated = client.post(
        f"/api/abstention-policies/{second['policy_id']}/deactivate",
        json={"deactivated_by": "qa", "deactivation_reason": "manual rollback rehearsal"},
    ).json()["policy"]
    assert deactivated["status"] == "deactivated"

    rolled_back = _activate(client, first["policy_id"], reason="rollback to previous active policy")
    assert rolled_back["status"] == "active"
    assert rolled_back["deactivated_at"] is None

    active_list = client.get(
        "/api/abstention-policies",
        params={
            "dataset_version_id": "dataset@activation-toy-001",
            "model_version_id": model_version_id,
            "status": "active",
        },
    ).json()["policies"]
    assert [item["policy_id"] for item in active_list] == [first["policy_id"]]


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
            "dataset_id": "activation-toy",
            "dataset_version_id": "dataset@activation-toy-001",
        },
    )
    assert import_response.status_code == 201

    create_run_response = client.post(
        "/api/training-runs",
        json={"dataset_version_id": "dataset@activation-toy-001"},
    )
    assert create_run_response.status_code == 202
    completed_job = run_next_job()
    assert completed_job is not None
    assert completed_job.status == "succeeded"

    engine = create_engine(database_url)
    with engine.begin() as conn:
        feature_metadata = conn.execute(
            sa.select(artifacts.c.artifact_metadata).where(artifacts.c.artifact_type == "feature_matrix")
        ).scalar_one()
    return client, str(completed_job.result["model_version_id"]), str(feature_metadata["feature_artifact"]["sample_ids"][-1])


def _create_ood_feedback_sample(client: TestClient, *, model_version_id: str, tmp_path: Path) -> str:
    query_path = tmp_path / f"activation-ood-{os.urandom(3).hex()}.png"
    Image.new("RGB", (96, 96), (0, 0, 0)).save(query_path)
    inference_response = client.post(
        "/api/inference",
        json={
            "dataset_version_id": "dataset@activation-toy-001",
            "model_version_id": model_version_id,
            "image_path": str(query_path),
            "accept_threshold": 1.0,
            "margin_threshold": 1.0,
            "ood_distance_threshold": 0.0,
        },
    )
    assert inference_response.status_code == 200
    inference = inference_response.json()["inference_result"]
    review_item_id = inference["review_item_id"]
    assert review_item_id
    submit_response = client.post(
        f"/api/review-items/{review_item_id}/submit",
        json={
            "final_outcome": "ood",
            "destination": "ood_stress",
            "final_label": None,
            "reviewer_note": "activation fixture",
            "reviewer": "qa",
        },
    )
    assert submit_response.status_code == 200
    return str(review_item_id)


def _propose_policy(client: TestClient, *, model_version_id: str, target_risk: float = 0.05) -> dict[str, object]:
    response = client.post(
        "/api/abstention-policies/propose",
        json={
            "dataset_version_id": "dataset@activation-toy-001",
            "model_version_id": model_version_id,
            "target_selective_risk": target_risk,
        },
    )
    assert response.status_code == 201
    return response.json()["policy"]


def _activate(client: TestClient, policy_id: str, *, reason: str = "activate contract fixture") -> dict[str, object]:
    response = client.post(
        f"/api/abstention-policies/{policy_id}/activate",
        json={"activated_by": "qa", "activation_reason": reason, "min_feedback_count": 1},
    )
    assert response.status_code == 200
    return response.json()["policy"]


def _reset_database(database_url: str) -> None:
    _assert_test_database_url(database_url)
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "TRUNCATE TABLE inference_runs, model_versions, training_runs, job_events, artifacts, "
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
