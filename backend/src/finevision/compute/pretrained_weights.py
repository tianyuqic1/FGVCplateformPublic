from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from finevision.artifact_store import ArtifactDescriptor, ArtifactStore


VITS_SHA256 = "2a1ec16ae28ffa07bc0ead0241ee7df9fc26451fe6f9f839b7b3afa0a906b040"
VITS_SIZE_BYTES = 86_362_376


@dataclass(frozen=True)
class ManagedWeight:
    artifact_id: str
    backbone_key: str
    category: str
    sha256: str
    size_bytes: int
    architecture: str
    environment_key: str


MANAGED_WEIGHTS = {
    "dinov3_vits16_lvd1689m": ManagedWeight(
        artifact_id="pretrained-dinov3-vits",
        backbone_key="dinov3_vits16_lvd1689m",
        category="dinov3",
        sha256=VITS_SHA256,
        size_bytes=VITS_SIZE_BYTES,
        architecture="vit_small_patch16_dinov3",
        environment_key="FINEVISION_DINOV3_VITS_WEIGHT",
    ),
    "imagenet_vits16_augreg_in21k_ft_in1k": ManagedWeight(
        artifact_id="pretrained-imagenet-vits",
        backbone_key="imagenet_vits16_augreg_in21k_ft_in1k",
        category="imagenet/vit-small",
        sha256="79c03c635cdfd798a364a9d8c4e5c0b7255b975ea2c9616046d4f77ab01435aa",
        size_bytes=88_216_496,
        architecture="vit_small_patch16_224.augreg_in21k_ft_in1k",
        environment_key="FINEVISION_IMAGENET_VITS_WEIGHT",
    ),
    "imagenet_resnet50_a1_in1k": ManagedWeight(
        artifact_id="pretrained-imagenet-resnet50",
        backbone_key="imagenet_resnet50_a1_in1k",
        category="imagenet/resnet-50",
        sha256="773525d5821de224f8f30c33377b7a795d7863e08522698200d3217d3f2a41bb",
        size_bytes=102_469_840,
        architecture="resnet50.a1_in1k",
        environment_key="FINEVISION_IMAGENET_RESNET50_WEIGHT",
    ),
}

WEIGHT_ALIASES = {
    "dinov3_vits": "dinov3_vits16_lvd1689m",
    "imagenet_vits": "imagenet_vits16_augreg_in21k_ft_in1k",
    "imagenet_resnet50": "imagenet_resnet50_a1_in1k",
}


def managed_weight_descriptor(backbone_key: str, bucket: str = "finevision-artifacts") -> ArtifactDescriptor:
    key = WEIGHT_ALIASES.get(backbone_key, backbone_key)
    if key not in MANAGED_WEIGHTS:
        raise ValueError(f"Unsupported managed backbone: {backbone_key}")
    weight = MANAGED_WEIGHTS[key]
    return ArtifactDescriptor(
        artifact_id=weight.artifact_id,
        artifact_type="pretrained_weight",
        uri=f"s3://{bucket}/pretrained/{weight.category}/{weight.sha256[:2]}/{weight.sha256}",
        sha256=weight.sha256,
        size_bytes=weight.size_bytes,
        content_type="application/octet-stream",
        storage_version="s3-v1",
        producer="git-lfs-weight-promotion/v1",
        metadata={"architecture": weight.architecture, "backbone_key": weight.backbone_key},
    )


def managed_vits_descriptor(bucket: str = "finevision-artifacts") -> ArtifactDescriptor:
    return managed_weight_descriptor("dinov3_vits16_lvd1689m", bucket)


def prepare_managed_weight(store: ArtifactStore, cache_root: str | Path, backbone_key: str) -> Path:
    key = WEIGHT_ALIASES.get(backbone_key, backbone_key)
    descriptor = managed_weight_descriptor(key, os.environ.get("FINEVISION_ARTIFACT_BUCKET", "finevision-artifacts"))
    path = store.materialize_verified(descriptor, cache_root)
    os.environ[MANAGED_WEIGHTS[key].environment_key] = str(path)
    return path


def prepare_managed_vits_weight(store: ArtifactStore, cache_root: str | Path) -> Path:
    return prepare_managed_weight(store, cache_root, "dinov3_vits16_lvd1689m")
