from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from finevision.api import create_app
from finevision.ml_toolkit.toydata import create_toy_imagefolder


def test_dataset_asset_api_import_list_detail_and_readiness(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "metadata"
    dataset_dir = create_toy_imagefolder(tmp_path / "toy-imagefolder", samples_per_class=6)
    client = TestClient(create_app(metadata_dir=metadata_dir))

    empty_response = client.get("/api/datasets")
    assert empty_response.status_code == 200
    assert empty_response.json() == {"datasets": []}

    import_response = client.post(
        "/api/datasets/import-imagefolder",
        json={
            "path": str(dataset_dir),
            "dataset_id": "toy-shapes",
            "dataset_version_id": "dataset@toy-001",
        },
    )
    assert import_response.status_code == 201
    imported = import_response.json()
    assert imported["version"]["dataset_version_id"] == "dataset@toy-001"
    assert imported["version"]["sample_count"] == 18
    assert imported["version"]["readiness"]["ready"] is True

    list_response = client.get("/api/datasets")
    assert list_response.status_code == 200
    datasets = list_response.json()["datasets"]
    assert datasets == [
        {
            "dataset_id": "toy-shapes",
            "latest_version_id": "dataset@toy-001",
            "dataset_version_id": "dataset@toy-001",
            "version_count": 1,
            "classes": ["blue_triangle", "green_circle", "red_square"],
            "class_count": 3,
            "sample_count": 18,
            "status": "ready",
            "readiness": imported["version"]["readiness"],
        }
    ]

    detail_response = client.get("/api/datasets/toy-shapes")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["dataset_id"] == "toy-shapes"
    assert detail["latest_version_id"] == "dataset@toy-001"
    assert detail["dataset_version_id"] == "dataset@toy-001"
    assert detail["classes"] == ["blue_triangle", "green_circle", "red_square"]
    assert detail["class_count"] == 3
    assert detail["sample_count"] == 18
    assert detail["status"] == "ready"
    assert detail["readiness"]["ready"] is True
    assert detail["versions"][0]["dataset_version_id"] == "dataset@toy-001"
    assert detail["versions"][0]["status"] == "ready"
    assert detail["versions"][0]["class_count"] == 3
    assert detail["versions"][0]["split_totals"] == {"test": 3, "train": 12, "val": 3}
    assert set(detail["split_counts"]) == {"train", "val", "test"}

    readiness_response = client.get("/api/dataset-versions/dataset@toy-001/readiness")
    assert readiness_response.status_code == 200
    readiness = readiness_response.json()
    assert readiness == {
        "dataset_id": "toy-shapes",
        "dataset_version_id": "dataset@toy-001",
        "readiness": detail["readiness"],
    }

    assert (
        metadata_dir / "datasets" / "toy-shapes" / "versions" / "dataset@toy-001" / "manifest.json"
    ).exists()


def test_dataset_asset_api_handles_missing_resources(tmp_path: Path) -> None:
    client = TestClient(create_app(metadata_dir=tmp_path / "metadata"))

    missing_import = client.post(
        "/api/datasets/import-imagefolder",
        json={
            "path": str(tmp_path / "missing"),
            "dataset_id": "missing",
            "dataset_version_id": "dataset@missing-001",
        },
    )
    assert missing_import.status_code == 400

    assert client.get("/api/datasets/missing").status_code == 404
    assert client.get("/api/dataset-versions/dataset@missing-001/readiness").status_code == 404
