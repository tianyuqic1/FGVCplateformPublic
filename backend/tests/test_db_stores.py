from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from finevision.api import create_app
from finevision.api.db_store import DatabaseJobStore
from finevision.api.training_store import DatabaseTrainingStore
from finevision.db.schema import artifacts, dataset_versions, datasets, job_events, jobs, model_versions, training_runs
from finevision.ml_toolkit.toydata import create_toy_imagefolder
from finevision.schemas.artifacts import (
    CalibrationReport,
    EvaluationReport,
    FeatureArtifact,
    ModelArtifact,
    ThresholdStrategy,
    ThresholdSweep,
    TrainingRunReport,
)
from finevision.worker import run_next_job


@pytest.fixture()
def database_url() -> str:
    url = os.environ.get("FINEVISION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set FINEVISION_TEST_DATABASE_URL to run PostgreSQL store integration tests.")
    _reset_database(url)
    return url


def _fake_training_completion(
    *,
    run_id: str,
    dataset_id: str,
    dataset_version_id: str,
    artifact_root: Path,
) -> dict[str, object]:
    feature_artifact = FeatureArtifact(
        artifact_id=f"{dataset_version_id}-fake-features",
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        backbone_id="color_stats_v1",
        extractor_config={"type": "color_stats"},
        feature_dim=3,
        features_path=str(artifact_root / "features" / "features.npz"),
        sample_ids=["sample-1"],
        labels=["class-a"],
        splits=["train"],
    )
    model_artifact = ModelArtifact(
        artifact_id=f"{dataset_version_id}-{run_id}-linear-head",
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        feature_artifact_id=feature_artifact.artifact_id,
        model_path=str(artifact_root / "models" / "linear_head.npz"),
        classes=["class-a"],
        head_type="torch_linear_adam",
        feature_dim=3,
        training_config={"run_id": run_id},
    )
    evaluation = EvaluationReport(
        accuracy=1.0,
        macro_f1=1.0,
        per_class={"class-a": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "support": 1}},
        confusion_matrix=[[1]],
        run_config={"head_type": "torch_linear_adam"},
    )
    training_report = TrainingRunReport(
        run_id=run_id,
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        feature_artifact_id=feature_artifact.artifact_id,
        model_artifact_id=model_artifact.artifact_id,
        run_config={"head_type": "torch_linear_adam"},
        evaluation=evaluation,
    )
    calibration_report = CalibrationReport(
        artifact_id=f"{model_artifact.artifact_id}-temperature-scaling",
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        model_artifact_id=model_artifact.artifact_id,
        method="temperature_scaling",
        split="validation",
        temperature=1.0,
        before={"ece": 0.0},
        after={"ece": 0.0},
        bins=[],
    )
    threshold_sweep = ThresholdSweep(
        strategy_id=f"{model_artifact.artifact_id}-confidence-sweep",
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        model_artifact_id=model_artifact.artifact_id,
        calibration_artifact_id=calibration_report.artifact_id,
        points=[],
        split="validation",
    )
    threshold_strategy = ThresholdStrategy(
        strategy_id=f"{model_artifact.artifact_id}-selective-v1",
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        model_artifact_id=model_artifact.artifact_id,
        calibration_artifact_id=calibration_report.artifact_id,
        calibration_method="temperature_scaling",
        temperature=1.0,
        split="validation",
        accept_threshold=0.5,
        margin_threshold=0.0,
        target_selective_risk=0.01,
        expected_coverage=1.0,
        expected_selective_risk=0.0,
        review_cost_per_item=1.0,
        selection_rule="confidence",
    )
    return {
        "run_id": run_id,
        "artifact_root": artifact_root,
        "feature_artifact": feature_artifact,
        "model_artifact": model_artifact,
        "training_report": training_report,
        "calibration_report": calibration_report,
        "threshold_sweep": threshold_sweep,
        "threshold_strategy": threshold_strategy,
    }


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
    assert counts == {"datasets": 1, "dataset_versions": 1, "artifacts": 2, "jobs": 1, "job_events": 3}


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
        manifest = conn.execute(
            sa.select(artifacts.c.artifact_metadata).where(artifacts.c.artifact_type == "dataset_manifest")
        ).scalar_one()["manifest"]
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


def test_database_job_claim_requeues_expired_running_lease(database_url: str) -> None:
    first_worker = DatabaseJobStore(database_url, lease_owner="expired-worker-a")
    second_worker = DatabaseJobStore(database_url, lease_owner="expired-worker-b")
    job = first_worker.create_job(
        "import_imagefolder",
        {
            "path": "/tmp/not-needed-for-expired-lease-test",
            "dataset_id": "expired-lease-test",
            "dataset_version_id": "dataset@expired-lease-test-001",
        },
    )

    claimed = first_worker.claim_next_queued_job(lease_seconds=-1)
    assert claimed is not None
    assert claimed.status == "running"

    reclaimed = second_worker.claim_next_queued_job()

    assert reclaimed is not None
    assert reclaimed.job_id == job.job_id
    assert reclaimed.status == "running"
    with create_engine(database_url).begin() as conn:
        events = conn.execute(
            sa.select(job_events.c.event_type)
            .select_from(job_events.join(jobs, jobs.c.id == job_events.c.job_id))
            .where(jobs.c.job_key == job.job_id)
            .order_by(job_events.c.created_at)
        ).scalars().all()
    assert "lease_expired_requeued" in events


def test_database_job_claim_fails_expired_running_lease_after_max_attempts(database_url: str) -> None:
    worker = DatabaseJobStore(database_url, lease_owner="expired-max-worker")
    job = worker.create_job(
        "import_imagefolder",
        {
            "path": "/tmp/not-needed-for-expired-max-test",
            "dataset_id": "expired-max-test",
            "dataset_version_id": "dataset@expired-max-test-001",
        },
    )
    claimed = worker.claim_next_queued_job(lease_seconds=-1)
    assert claimed is not None

    with create_engine(database_url).begin() as conn:
        conn.execute(
            jobs.update()
            .where(jobs.c.job_key == job.job_id)
            .values(attempt_count=jobs.c.max_attempts)
        )

    assert worker.claim_next_queued_job() is None
    stored = worker.get_job(job.job_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error == "Worker lease expired and max attempts were exhausted."
    with create_engine(database_url).begin() as conn:
        events = conn.execute(
            sa.select(job_events.c.event_type)
            .select_from(job_events.join(jobs, jobs.c.id == job_events.c.job_id))
            .where(jobs.c.job_key == job.job_id)
            .order_by(job_events.c.created_at)
        ).scalars().all()
    assert "lease_expired_failed" in events


def test_database_backed_training_run_executes_toolkit_flow(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            "dataset_id": "train-toy",
            "dataset_version_id": "dataset@train-toy-001",
        },
    )
    assert import_response.status_code == 201

    create_run_response = client.post(
        "/api/training-runs",
        json={
            "dataset_version_id": "dataset@train-toy-001",
            "extractor": "color_stats",
            "backbone_id": "color_stats_v1",
        },
    )
    assert create_run_response.status_code == 202
    created_run = create_run_response.json()["training_run"]
    assert created_run["status"] == "queued"
    assert created_run["dataset_version_id"] == "dataset@train-toy-001"

    completed_job = run_next_job()
    assert completed_job is not None
    assert completed_job.type == "train_classifier"
    assert completed_job.status == "succeeded"
    assert completed_job.result["training_run_id"] == created_run["run_id"]
    assert completed_job.result["model_version_id"] is not None

    detail_response = client.get(f"/api/training-runs/{created_run['run_id']}")
    assert detail_response.status_code == 200
    stored_run = detail_response.json()["training_run"]
    assert stored_run["status"] == "succeeded"
    assert stored_run["feature_artifact_id"].startswith("feature:dataset@train-toy-001:color_stats_v1:")
    assert stored_run["model_artifact_id"] == f"dataset@train-toy-001-{created_run['run_id']}-linear-head"
    assert stored_run["model_version_id"] is not None
    assert stored_run["metrics"]["accuracy"] >= 0.0
    assert stored_run["metrics"]["macro_f1"] >= 0.0

    list_response = client.get("/api/training-runs")
    assert list_response.status_code == 200
    assert [run["run_id"] for run in list_response.json()["training_runs"]] == [created_run["run_id"]]

    engine = create_engine(database_url)
    with engine.begin() as conn:
        artifact_types = set(conn.execute(sa.select(artifacts.c.artifact_type)).scalars().all())
        feature_uri = conn.execute(
            sa.select(artifacts.c.uri).where(artifacts.c.artifact_key == stored_run["feature_artifact_id"])
        ).scalar_one()
        training_report = conn.execute(
            sa.select(artifacts.c.artifact_metadata).where(artifacts.c.artifact_key == stored_run["report_artifact_id"])
        ).scalar_one()["training_report"]
        assert conn.scalar(sa.select(sa.func.count()).select_from(training_runs)) == 1
        assert conn.scalar(sa.select(sa.func.count()).select_from(model_versions)) == 1
    assert Path(feature_uri).exists()
    assert training_report["evaluation"]["per_class"]
    assert training_report["evaluation"]["confusion_matrix"]
    assert training_report["run_config"]["head_type"] == "torch_linear_adam"
    assert training_report["run_config"]["learning_rate"] == 0.001
    assert training_report["run_config"]["optimizer_history"]
    assert {
        "dataset_manifest",
        "feature_matrix",
        "model_artifact",
        "training_report",
        "calibration_report",
        "threshold_sweep",
        "threshold_strategy",
    }.issubset(artifact_types)


def test_training_run_rejects_dataset_version_that_is_not_ready(
    database_url: str,
    tmp_path: Path,
) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / "not-ready-imagefolder", samples_per_class=2)
    client = TestClient(create_app(database_url=database_url))

    import_response = client.post(
        "/api/datasets/import-imagefolder",
        json={
            "path": str(dataset_dir),
            "dataset_id": "not-ready-toy",
            "dataset_version_id": "dataset@not-ready-toy-001",
        },
    )
    assert import_response.status_code == 201
    assert import_response.json()["version"]["readiness"]["ready"] is False

    create_run_response = client.post(
        "/api/training-runs",
        json={"dataset_version_id": "dataset@not-ready-toy-001"},
    )
    assert create_run_response.status_code == 409
    assert create_run_response.json()["detail"]["readiness"]["ready"] is False

    engine = create_engine(database_url)
    with engine.begin() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(training_runs)) == 0
        assert conn.scalar(sa.select(sa.func.count()).select_from(jobs)) == 0


def test_dinov3_training_request_uses_canonical_backbone_metadata(
    database_url: str,
    tmp_path: Path,
) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / "dinov3-imagefolder", samples_per_class=5)
    client = TestClient(create_app(database_url=database_url))

    assert (
        client.post(
            "/api/datasets/import-imagefolder",
            json={
                "path": str(dataset_dir),
                "dataset_id": "dinov3-toy",
                "dataset_version_id": "dataset@dinov3-toy-001",
            },
        ).status_code
        == 201
    )

    variants = [
        ("dinov3_vits", "dinov3_vits16", "vit_small_patch16_dinov3"),
        ("dinov3_vitb", "dinov3_vitb16", "vit_base_patch16_dinov3"),
        ("dinov3_vitl", "dinov3_vitl16", "vit_large_patch16_dinov3"),
    ]
    for extractor, backbone_id, model_name in variants:
        create_run_response = client.post(
            "/api/training-runs",
            json={
                "dataset_version_id": "dataset@dinov3-toy-001",
                "extractor": extractor,
                "feature_batch_size": 4,
            },
        )
        assert create_run_response.status_code == 202
        body = create_run_response.json()
        created_run = body["training_run"]
        assert created_run["backbone_id"] == backbone_id
        assert created_run["extractor_config"]["type"] == "timm_dinov3"
        assert created_run["extractor_config"]["preset"] == extractor
        assert created_run["extractor_config"]["model_name"] == model_name
        assert created_run["extractor_config"]["feature_pool"] == "cls"
        assert created_run["extractor_config"]["runtime"]["feature_batch_size"] == 4
        assert body["job"]["payload"]["batch_size"] == 4
        assert body["job"]["payload"]["feature_pool"] == "cls"


def test_training_run_cancel_tracks_business_run_status(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / "cancel-training-imagefolder", samples_per_class=5)
    client = TestClient(create_app(database_url=database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("FINEVISION_METADATA_DIR", raising=False)

    assert (
        client.post(
            "/api/datasets/import-imagefolder",
            json={
                "path": str(dataset_dir),
                "dataset_id": "cancel-training-toy",
                "dataset_version_id": "dataset@cancel-training-toy-001",
            },
        ).status_code
        == 201
    )
    create_run_response = client.post(
        "/api/training-runs",
        json={"dataset_version_id": "dataset@cancel-training-toy-001"},
    )
    assert create_run_response.status_code == 202
    body = create_run_response.json()
    run_id = body["training_run"]["run_id"]
    job_id = body["job"]["job_id"]

    cancel_response = client.post(f"/api/jobs/{job_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["job"]["status"] == "cancelled"

    detail_response = client.get(f"/api/training-runs/{run_id}")
    assert detail_response.status_code == 200
    stored_run = detail_response.json()["training_run"]
    assert stored_run["status"] == "cancelled"
    assert stored_run["error"] == "Training job was cancelled before worker execution."
    assert run_next_job() is None


def test_training_run_queue_controls_pause_resume_cancel_and_delete(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / "queue-controls-imagefolder", samples_per_class=5)
    client = TestClient(create_app(database_url=database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("FINEVISION_METADATA_DIR", raising=False)

    assert (
        client.post(
            "/api/datasets/import-imagefolder",
            json={
                "path": str(dataset_dir),
                "dataset_id": "queue-controls-toy",
                "dataset_version_id": "dataset@queue-controls-toy-001",
            },
        ).status_code
        == 201
    )

    create_response = client.post(
        "/api/training-runs",
        json={"dataset_version_id": "dataset@queue-controls-toy-001"},
    )
    assert create_response.status_code == 202
    run_id = create_response.json()["training_run"]["run_id"]

    pause_response = client.post(f"/api/training-runs/{run_id}/pause")
    assert pause_response.status_code == 200
    assert pause_response.json()["training_run"]["status"] == "paused"
    assert run_next_job() is None

    resume_response = client.post(f"/api/training-runs/{run_id}/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()["training_run"]["status"] == "queued"

    cancel_response = client.post(f"/api/training-runs/{run_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["training_run"]["status"] == "cancelled"

    delete_response = client.delete(f"/api/training-runs/{run_id}")
    assert delete_response.status_code == 204
    assert client.get(f"/api/training-runs/{run_id}").status_code == 404


def test_running_training_run_can_be_cancelled_and_releases_worker_lease(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / "running-cancel-imagefolder", samples_per_class=5)
    client = TestClient(create_app(database_url=database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("FINEVISION_METADATA_DIR", raising=False)

    assert (
        client.post(
            "/api/datasets/import-imagefolder",
            json={
                "path": str(dataset_dir),
                "dataset_id": "running-cancel-toy",
                "dataset_version_id": "dataset@running-cancel-toy-001",
            },
        ).status_code
        == 201
    )
    create_response = client.post(
        "/api/training-runs",
        json={"dataset_version_id": "dataset@running-cancel-toy-001"},
    )
    assert create_response.status_code == 202
    run_id = create_response.json()["training_run"]["run_id"]
    job_id = create_response.json()["job"]["job_id"]

    worker_store = DatabaseJobStore(database_url, lease_owner="test-running-cancel-worker")
    claimed = worker_store.claim_next_queued_job()
    assert claimed is not None
    assert claimed.job_id == job_id
    assert claimed.status == "running"
    DatabaseTrainingStore(database_url).mark_running(run_id)

    cancel_response = client.post(f"/api/training-runs/{run_id}/cancel")

    assert cancel_response.status_code == 200
    assert cancel_response.json()["training_run"]["status"] == "cancelled"
    assert cancel_response.json()["training_run"]["error"] == "Cancelled by user from training queue."
    assert worker_store.get_job(job_id).status == "cancelled"  # type: ignore[union-attr]

    engine = create_engine(database_url)
    with engine.begin() as conn:
        row = conn.execute(sa.select(jobs.c.lease_owner, jobs.c.lease_expires_at).where(jobs.c.job_key == job_id)).one()
    assert row.lease_owner is None
    assert row.lease_expires_at is None


@pytest.mark.parametrize("control_action", ["cancel", "pause"])
def test_training_run_complete_does_not_write_model_after_cancel_or_pause(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_action: str,
) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / f"{control_action}-complete-imagefolder", samples_per_class=5)
    artifact_dir = tmp_path / "artifacts"
    client = TestClient(create_app(database_url=database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("FINEVISION_METADATA_DIR", raising=False)

    assert (
        client.post(
            "/api/datasets/import-imagefolder",
            json={
                "path": str(dataset_dir),
                "dataset_id": f"{control_action}-complete-toy",
                "dataset_version_id": f"dataset@{control_action}-complete-toy-001",
            },
        ).status_code
        == 201
    )
    create_response = client.post(
        "/api/training-runs",
        json={"dataset_version_id": f"dataset@{control_action}-complete-toy-001"},
    )
    assert create_response.status_code == 202
    run_id = create_response.json()["training_run"]["run_id"]
    job_id = create_response.json()["job"]["job_id"]

    worker_store = DatabaseJobStore(database_url, lease_owner=f"test-{control_action}-complete-worker")
    claimed = worker_store.claim_next_queued_job()
    assert claimed is not None
    assert claimed.job_id == job_id
    training_store = DatabaseTrainingStore(database_url)
    training_store.mark_running(run_id)

    if control_action == "cancel":
        controlled = training_store.cancel_training_run(run_id, "Cancelled during completion race.")
    else:
        controlled = training_store.pause_training_run(run_id)
    expected_status = "cancelled" if control_action == "cancel" else "paused"
    assert controlled.status == expected_status

    completion = _fake_training_completion(
        run_id=run_id,
        dataset_id=controlled.dataset_id,
        dataset_version_id=controlled.dataset_version_id,
        artifact_root=artifact_dir,
    )
    with pytest.raises(ValueError, match="Training run cannot be completed"):
        training_store.complete_training_run(**completion)

    stored = training_store.get_training_run(run_id)
    assert stored is not None
    assert stored.status == controlled.status
    assert stored.model_artifact_id is None
    assert stored.model_version_id is None
    assert stored.report_artifact_id is None

    engine = create_engine(database_url)
    with engine.begin() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(model_versions)) == 0
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(artifacts)
                .where(
                    artifacts.c.artifact_type.in_(
                        [
                            "feature_matrix",
                            "model_artifact",
                            "training_report",
                            "calibration_report",
                            "threshold_sweep",
                            "threshold_strategy",
                        ]
                    )
                )
            )
            == 0
        )


def test_training_run_running_controls_request_stop_at_worker_checkpoint(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / "running-controls-imagefolder", samples_per_class=5)
    client = TestClient(create_app(database_url=database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("FINEVISION_METADATA_DIR", raising=False)

    assert (
        client.post(
            "/api/datasets/import-imagefolder",
            json={
                "path": str(dataset_dir),
                "dataset_id": "running-controls-toy",
                "dataset_version_id": "dataset@running-controls-toy-001",
            },
        ).status_code
        == 201
    )

    create_response = client.post(
        "/api/training-runs",
        json={"dataset_version_id": "dataset@running-controls-toy-001"},
    )
    assert create_response.status_code == 202
    run_id = create_response.json()["training_run"]["run_id"]

    engine = create_engine(database_url)
    with engine.begin() as conn:
        run_row = conn.execute(
            sa.select(training_runs.c.id, training_runs.c.job_id).where(training_runs.c.run_key == run_id)
        ).mappings().one()
        conn.execute(training_runs.update().where(training_runs.c.id == run_row["id"]).values(status="running"))
        conn.execute(
            jobs.update()
            .where(jobs.c.id == run_row["job_id"])
            .values(status="running", lease_owner="test-worker", lease_expires_at=sa.func.now())
        )

    pause_response = client.post(f"/api/training-runs/{run_id}/pause")
    assert pause_response.status_code == 200
    assert pause_response.json()["training_run"]["status"] == "paused"

    resume_response = client.post(f"/api/training-runs/{run_id}/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()["training_run"]["status"] == "queued"

    with engine.begin() as conn:
        run_row = conn.execute(
            sa.select(training_runs.c.id, training_runs.c.job_id).where(training_runs.c.run_key == run_id)
        ).mappings().one()
        conn.execute(training_runs.update().where(training_runs.c.id == run_row["id"]).values(status="running"))
        conn.execute(jobs.update().where(jobs.c.id == run_row["job_id"]).values(status="running"))

    cancel_response = client.post(f"/api/training-runs/{run_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["training_run"]["status"] == "cancelled"

    with engine.begin() as conn:
        statuses = conn.execute(
            sa.select(
                training_runs.c.status.label("run_status"),
                jobs.c.status.label("job_status"),
                jobs.c.lease_owner,
                jobs.c.lease_expires_at,
            )
            .select_from(training_runs.join(jobs, jobs.c.id == training_runs.c.job_id))
            .where(training_runs.c.run_key == run_id)
        ).mappings().one()
    assert statuses["run_status"] == "cancelled"
    assert statuses["job_status"] == "cancelled"
    assert statuses["lease_owner"] is None
    assert statuses["lease_expires_at"] is None


def test_training_run_reuses_feature_artifact_for_same_dataset_and_extractor_config(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_dir = create_toy_imagefolder(tmp_path / "reuse-imagefolder", samples_per_class=5)
    artifact_dir = tmp_path / "artifacts"
    client = TestClient(create_app(database_url=database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("FINEVISION_ARTIFACT_DIR", str(artifact_dir))
    monkeypatch.delenv("FINEVISION_METADATA_DIR", raising=False)

    assert (
        client.post(
            "/api/datasets/import-imagefolder",
            json={
                "path": str(dataset_dir),
                "dataset_id": "reuse-toy",
                "dataset_version_id": "dataset@reuse-toy-001",
            },
        ).status_code
        == 201
    )

    run_ids = []
    for _ in range(2):
        create_run_response = client.post(
            "/api/training-runs",
            json={
                "dataset_version_id": "dataset@reuse-toy-001",
                "extractor": "color_stats",
                "head_config": {"head_type": "ridge_linear", "ridge_lambda": 0.01},
            },
        )
        assert create_run_response.status_code == 202
        run_ids.append(create_run_response.json()["training_run"]["run_id"])
        completed_job = run_next_job()
        assert completed_job is not None
        assert completed_job.status == "succeeded"

    first = client.get(f"/api/training-runs/{run_ids[0]}").json()["training_run"]
    second = client.get(f"/api/training-runs/{run_ids[1]}").json()["training_run"]
    assert first["feature_artifact_id"] == second["feature_artifact_id"]

    engine = create_engine(database_url)
    with engine.begin() as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count()).select_from(artifacts).where(artifacts.c.artifact_type == "feature_matrix")
            )
            == 1
        )
        assert conn.scalar(sa.select(sa.func.count()).select_from(model_versions)) == 2


def _reset_database(database_url: str) -> None:
    _assert_test_database_url(database_url)
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "TRUNCATE TABLE model_versions, training_runs, job_events, artifacts, dataset_versions, datasets, jobs RESTART IDENTITY CASCADE"
            )
        )


def _assert_test_database_url(database_url: str) -> None:
    database_name = (make_url(database_url).database or "").lower()
    if "test" not in database_name:
        pytest.fail(
            f"Refusing to reset non-test database {database_name!r}; "
            "set FINEVISION_TEST_DATABASE_URL to a dedicated test database."
        )
