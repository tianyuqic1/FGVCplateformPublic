from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from finevision.api import create_app
from finevision.ml_toolkit.features import (
    build_extractor_from_config,
    delete_dinov3_weight_cache,
    dinov3_extractor_config,
    inspect_dinov3_weight_cache,
)


def test_inspect_dinov3_weight_cache_reports_cached_partial_and_missing(tmp_path: Path, monkeypatch) -> None:
    hub_cache = tmp_path / "hub"
    small_blobs = hub_cache / "models--timm--vit_small_patch16_dinov3.lvd1689m" / "blobs"
    base_blobs = hub_cache / "models--timm--vit_base_patch16_dinov3.lvd1689m" / "blobs"
    small_blobs.mkdir(parents=True)
    base_blobs.mkdir(parents=True)
    (small_blobs / "model").write_bytes(b"complete")
    (base_blobs / "model.incomplete").write_bytes(b"partial")
    monkeypatch.setenv("HF_HUB_CACHE", str(hub_cache))
    monkeypatch.setenv("HF_TOKEN", "hf-test")

    payload = inspect_dinov3_weight_cache()
    by_preset = {item["preset"]: item for item in payload["weights"]}

    assert payload["cache_root"] == str(hub_cache)
    assert payload["hf_token_configured"] is True
    assert by_preset["dinov3_vits"]["cache_status"] == "cached"
    assert "ViT-S/16" in by_preset["dinov3_vits"]["description"]
    assert by_preset["dinov3_vits"]["complete_size_bytes"] == len(b"complete")
    assert by_preset["dinov3_vitb"]["cache_status"] == "partial"
    assert by_preset["dinov3_vitb"]["incomplete_size_bytes"] == len(b"partial")
    assert by_preset["dinov3_vitl"]["cache_status"] == "missing"


def test_model_weights_api_returns_cache_status(tmp_path: Path, monkeypatch) -> None:
    hub_cache = tmp_path / "hub"
    (hub_cache / "models--timm--vit_small_patch16_dinov3.lvd1689m" / "blobs").mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_CACHE", str(hub_cache))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)

    response = TestClient(create_app(metadata_dir=tmp_path / "metadata")).get("/api/model-weights")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cache_root"] == str(hub_cache)
    assert payload["hf_token_configured"] is False
    assert {item["preset"] for item in payload["weights"]} == {"dinov3_vits", "dinov3_vitb", "dinov3_vitl"}


def test_delete_dinov3_weight_cache_removes_known_repo_dir(tmp_path: Path, monkeypatch) -> None:
    hub_cache = tmp_path / "hub"
    small_blobs = hub_cache / "models--timm--vit_small_patch16_dinov3.lvd1689m" / "blobs"
    small_blobs.mkdir(parents=True)
    (small_blobs / "model").write_bytes(b"complete")
    monkeypatch.setenv("HF_HUB_CACHE", str(hub_cache))

    result = delete_dinov3_weight_cache("dinov3_vits")

    assert result["deleted"] is True
    assert result["before"]["cache_status"] == "cached"
    assert result["after"]["cache_status"] == "missing"
    assert not (hub_cache / "models--timm--vit_small_patch16_dinov3.lvd1689m").exists()


def test_model_weights_api_deletes_known_cache(tmp_path: Path, monkeypatch) -> None:
    hub_cache = tmp_path / "hub"
    small_blobs = hub_cache / "models--timm--vit_small_patch16_dinov3.lvd1689m" / "blobs"
    small_blobs.mkdir(parents=True)
    (small_blobs / "model").write_bytes(b"complete")
    monkeypatch.setenv("HF_HUB_CACHE", str(hub_cache))
    client = TestClient(create_app(metadata_dir=tmp_path / "metadata"))

    response = client.delete("/api/model-weights/dinov3_vits")

    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted"] is True
    assert payload["after"]["cache_status"] == "missing"


def test_model_weights_api_rejects_unknown_weight(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))

    response = TestClient(create_app(metadata_dir=tmp_path / "metadata")).delete("/api/model-weights/not-a-weight")

    assert response.status_code == 404


def test_dinov3_image_size_is_part_of_feature_config() -> None:
    config = dinov3_extractor_config("dinov3_vitl", image_size=448)
    extractor = build_extractor_from_config(config, {"device": "cpu", "batch_size": 2})

    assert extractor.config["image_size"] == 448
    assert extractor.config["model_name"] == "vit_large_patch16_dinov3"
    assert extractor.config["feature_pool"] == "cls"


def test_legacy_dinov3_config_keeps_model_pooling_for_artifact_compatibility() -> None:
    extractor = build_extractor_from_config(
        {
            "type": "timm_dinov3",
            "preset": "dinov3_vitl",
            "model_name": "vit_large_patch16_dinov3",
            "pretrained": True,
            "backbone_id": "dinov3_vitl16",
            "image_size": 448,
        },
        {"device": "cpu", "batch_size": 2},
    )

    assert extractor.config["feature_pool"] == "model"
