from __future__ import annotations

from pathlib import Path

import pytest

from finevision.schemas.artifacts import DatasetManifest
from finevision.worker.jobs import TrainingRunStopped, _load_or_extract_features


class FakeTrainingStore:
    def __init__(self, status: str) -> None:
        self.status = status
        self.progress: list[dict] = []

    def find_feature_artifact(self, dataset_version_id, backbone_id, extractor_config=None):
        return None

    def get_status(self, run_id: str) -> str:
        return self.status

    def update_progress(self, run_id: str, progress: dict) -> None:
        self.progress.append(progress)


class FakePreparedExtractor:
    backbone_id = "fake_backbone"
    config = {"type": "fake"}

    def __init__(self) -> None:
        self.prepare_calls = 0

    def prepare(self) -> None:
        self.prepare_calls += 1

    def extract_paths(self, paths: list[str]):
        raise AssertionError("cancelled runs must not extract features")


def test_worker_stops_cancelled_training_run_before_weight_prepare(tmp_path: Path) -> None:
    store = FakeTrainingStore(status="cancelled")
    extractor = FakePreparedExtractor()
    manifest = DatasetManifest(
        dataset_id="cancelled-toy",
        dataset_version_id="dataset@cancelled-toy-001",
        root=str(tmp_path),
        classes=[],
        samples=[],
        split_counts={},
        readiness={},
    )

    with pytest.raises(TrainingRunStopped):
        _load_or_extract_features(
            manifest,
            extractor,
            tmp_path / "artifacts",
            store,
            run_id="run-cancelled",
        )

    assert extractor.prepare_calls == 0
