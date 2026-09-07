from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from finevision.artifact_store import LocalFilesystemArtifactStore
from finevision.compute.queue_worker import RemoteTrainingStore, parse_dispatch_message


def test_dispatch_message_contains_only_stable_identifiers() -> None:
    message = parse_dispatch_message(
        b'{"message_id":"11111111-1111-4111-8111-111111111111",'
        b'"event_type":"training.job.ready","schema_version":1,'
        b'"job_id":"22222222-2222-4222-8222-222222222222",'
        b'"dispatch_generation":3,"occurred_at":"2026-09-07T00:00:00Z"}'
    )
    assert message.job_id == "22222222-2222-4222-8222-222222222222"
    assert message.dispatch_generation == 3

    with pytest.raises(ValueError, match="unsupported dispatch schema"):
        parse_dispatch_message(
            b'{"message_id":"m","event_type":"training.job.ready",'
            b'"schema_version":2,"job_id":"j","dispatch_generation":1,"occurred_at":"now"}'
        )


@dataclass
class FakeLifecycle:
    progress_requests: list[object]
    complete_requests: list[object]

    def ReportProgress(self, request, timeout):  # noqa: N802 - generated gRPC interface
        self.progress_requests.append(request)
        return SimpleNamespace()

    def Heartbeat(self, request, timeout):  # noqa: N802
        return SimpleNamespace(directive="continue")

    def Fail(self, request, timeout):  # noqa: N802
        return SimpleNamespace()

    def Complete(self, request, timeout):  # noqa: N802
        self.complete_requests.append(request)
        return SimpleNamespace(
            job_id=request.job_id,
            training_run_id="33333333-3333-4333-8333-333333333333",
            model_version_id="44444444-4444-4444-8444-444444444444",
            metrics=request.metrics,
        )


def test_remote_training_store_uploads_verified_artifacts_before_complete(tmp_path: Path) -> None:
    model_dir = tmp_path / "models" / "model-artifact"
    feature_dir = tmp_path / "features"
    model_dir.mkdir(parents=True)
    feature_dir.mkdir()
    (model_dir / "linear_head.npz").write_bytes(b"model")
    (feature_dir / "features.npz").write_bytes(b"features")
    for name in ("training_report.json", "calibration_report.json", "threshold_sweep.json", "threshold_strategy.json"):
        (model_dir / name).write_text("{}", encoding="utf-8")

    lifecycle = FakeLifecycle([], [])
    store = RemoteTrainingStore(
        lifecycle=lifecycle,
        artifact_store=LocalFilesystemArtifactStore(tmp_path / "objects"),
        job_id="11111111-1111-4111-8111-111111111111",
        training_run_id="33333333-3333-4333-8333-333333333333",
        dataset_version_id="dataset-version",
        attempt_id="55555555-5555-4555-8555-555555555555",
        execution_epoch=1,
    )

    record = store.complete_training_run(
        run_id="33333333-3333-4333-8333-333333333333",
        artifact_root=tmp_path,
        feature_artifact=SimpleNamespace(
            features_path=str(feature_dir / "features.npz"),
            artifact_id="feature",
            extractor_config={"type": "color_stats"},
        ),
        model_artifact=SimpleNamespace(
            model_path=str(model_dir / "linear_head.npz"),
            artifact_id="model-artifact",
            classes=["a", "b"],
            feature_dim=2,
            head_type="ridge_linear",
        ),
        training_report=SimpleNamespace(evaluation=SimpleNamespace(accuracy=1.0, macro_f1=1.0)),
        calibration_report=SimpleNamespace(artifact_id="calibration"),
        threshold_sweep=SimpleNamespace(strategy_id="sweep"),
        threshold_strategy=SimpleNamespace(
            strategy_id="strategy",
            expected_coverage=1.0,
            expected_selective_risk=0.0,
            accept_threshold=0.9,
            margin_threshold=0.2,
        ),
    )

    assert record.model_version_id == "44444444-4444-4444-8444-444444444444"
    request = lifecycle.complete_requests[0]
    assert {item.artifact_type for item in request.artifacts} >= {"model", "features", "report", "model_bundle"}
    assert all(len(item.sha256) == 64 and item.size_bytes > 0 for item in request.artifacts)
    assert all(item.uri.startswith("file://") for item in request.artifacts)


def test_remote_training_store_emits_epoch_metric_points() -> None:
    lifecycle = FakeLifecycle([], [])
    store = RemoteTrainingStore(
        lifecycle=lifecycle,
        artifact_store=LocalFilesystemArtifactStore("/tmp/finevision-unused-artifacts"),
        job_id="11111111-1111-4111-8111-111111111111",
        training_run_id="33333333-3333-4333-8333-333333333333",
        dataset_version_id="dataset-version",
        attempt_id="55555555-5555-4555-8555-555555555555",
        execution_epoch=7,
    )

    store.update_progress(
        "33333333-3333-4333-8333-333333333333",
        {
            "current_stage": "head",
            "latest_metrics": {"epoch": 3.0, "train_loss": 0.42, "eval_accuracy": 0.91},
        },
    )

    request = lifecycle.progress_requests[0]
    assert [(point.name, point.step, point.value) for point in request.metric_points] == [
        ("train_loss", 3, 0.42),
        ("eval_accuracy", 3, 0.91),
    ]
    assert request.metric_points[0].context.fields["split"].string_value == "train"
