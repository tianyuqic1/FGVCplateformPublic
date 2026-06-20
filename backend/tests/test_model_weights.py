from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from finevision.api import create_app
from finevision.ml_toolkit.features import inspect_dinov3_weight_cache


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
