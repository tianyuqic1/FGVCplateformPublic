from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finevision.compute.pretrained_weights import managed_vits_descriptor


ROOT = Path(__file__).resolve().parents[2]


def test_only_vit_small_pretrained_weight_is_manifested_and_integrity_checked() -> None:
    manifest = json.loads((ROOT / "weights" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert len(manifest["weights"]) == 1
    weight = manifest["weights"][0]
    assert weight["preset"] == "dinov3_vits"
    assert weight["architecture"] == "vit_small_patch16_dinov3"
    path = ROOT / weight["lfs_path"]
    assert path.stat().st_size == weight["size_bytes"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == weight["sha256"]
    assert (path.parent / "LICENSE.md").is_file()
    runtime_descriptor = managed_vits_descriptor()
    assert runtime_descriptor.sha256 == weight["sha256"]
    assert runtime_descriptor.size_bytes == weight["size_bytes"]
    assert runtime_descriptor.uri.endswith(weight["sha256"])


def test_git_attributes_scopes_lfs_to_approved_pretrained_weight_files() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "weights/pretrained/dinov3/*.safetensors filter=lfs" in attributes
    assert "*.npz filter=lfs" not in attributes
