from __future__ import annotations

import os
from pathlib import Path

from finevision.artifact_store import ArtifactDescriptor, ArtifactStore


VITS_SHA256 = "2a1ec16ae28ffa07bc0ead0241ee7df9fc26451fe6f9f839b7b3afa0a906b040"
VITS_SIZE_BYTES = 86_362_376


def managed_vits_descriptor(bucket: str = "finevision-artifacts") -> ArtifactDescriptor:
    return ArtifactDescriptor(
        artifact_id="pretrained-dinov3-vits",
        artifact_type="pretrained_weight",
        uri=f"s3://{bucket}/pretrained/dinov3/2a/{VITS_SHA256}",
        sha256=VITS_SHA256,
        size_bytes=VITS_SIZE_BYTES,
        content_type="application/octet-stream",
        storage_version="s3-v1",
        producer="git-lfs-weight-promotion/v1",
        metadata={"architecture": "vit_small_patch16_dinov3"},
    )


def prepare_managed_vits_weight(store: ArtifactStore, cache_root: str | Path) -> Path:
    path = store.materialize_verified(
        managed_vits_descriptor(os.environ.get("FINEVISION_ARTIFACT_BUCKET", "finevision-artifacts")),
        cache_root,
    )
    os.environ["FINEVISION_DINOV3_VITS_WEIGHT"] = str(path)
    return path
