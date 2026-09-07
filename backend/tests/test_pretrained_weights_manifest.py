from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finevision.compute.pretrained_weights import MANAGED_WEIGHTS, managed_weight_descriptor


ROOT = Path(__file__).resolve().parents[2]


def test_only_approved_phase_two_weights_are_manifested_and_integrity_checked() -> None:
    manifest = json.loads((ROOT / "weights" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert [weight["preset"] for weight in manifest["weights"]] == list(MANAGED_WEIGHTS)
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


def test_git_attributes_scopes_lfs_to_approved_pretrained_weight_files() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "weights/pretrained/dinov3/*.safetensors filter=lfs" in attributes
    assert "weights/pretrained/imagenet/*.safetensors filter=lfs" in attributes
    assert "*.npz filter=lfs" not in attributes
