from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from finevision.artifact_store import ArtifactIntegrityError
from finevision.compute.pretrained_weights import MANAGED_WEIGHTS, _materialize_external_weight, managed_weight_descriptor


ROOT = Path(__file__).resolve().parents[2]


def test_only_approved_phase_two_weights_are_manifested_and_integrity_checked() -> None:
    manifest = json.loads((ROOT / "weights" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert [weight["preset"] for weight in manifest["weights"]] == [key for key, weight in MANAGED_WEIGHTS.items() if weight.source_url is None]
    for weight in manifest["weights"]:
        path = ROOT / weight["lfs_path"]
        assert path.stat().st_size == weight["size_bytes"]
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        assert digest.hexdigest() == weight["sha256"]
        runtime_descriptor = managed_weight_descriptor(weight["preset"])
        assert runtime_descriptor.sha256 == weight["sha256"]
        assert runtime_descriptor.size_bytes == weight["size_bytes"]
        assert runtime_descriptor.uri.endswith(weight["sha256"])
    assert (ROOT / "weights" / "pretrained" / "dinov3" / "LICENSE.md").is_file()
    assert (ROOT / "weights" / "pretrained" / "imagenet" / "LICENSE").is_file()
    published = MANAGED_WEIGHTS["inat2021_mobilenetv3_large_kd"]
    assert published.source_url.startswith("https://www.modelscope.cn/models/")
    assert managed_weight_descriptor(published.backbone_key).uri == published.source_url


def test_git_attributes_scopes_lfs_to_approved_pretrained_weight_files() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "weights/pretrained/dinov3/*.safetensors filter=lfs" in attributes
    assert "weights/pretrained/imagenet/*.safetensors filter=lfs" in attributes
    assert "weights/pretrained/inat2021-mini/*.safetensors filter=lfs" not in attributes
    assert "*.npz filter=lfs" not in attributes


def test_published_weight_download_verifies_bytes_and_reuses_cache(tmp_path: Path, monkeypatch) -> None:
    original = MANAGED_WEIGHTS["inat2021_mobilenetv3_large_kd"]
    monkeypatch.delenv(original.environment_key, raising=False)
    source = tmp_path / "published.safetensors"
    source.write_bytes(b"verified published artifact")
    weight = replace(original, sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                     size_bytes=source.stat().st_size, source_url=source.as_uri())
    cached = _materialize_external_weight(weight, tmp_path / "cache")
    assert cached.read_bytes() == source.read_bytes()
    source.write_bytes(b"changed upstream")
    assert _materialize_external_weight(weight, tmp_path / "cache") == cached
    with pytest.raises(ArtifactIntegrityError):
        _materialize_external_weight(replace(weight, sha256="0" * 64), tmp_path / "other-cache")
    monkeypatch.setenv(weight.environment_key, str(source))
    with pytest.raises(ArtifactIntegrityError):
        _materialize_external_weight(weight, tmp_path / "cache")
