from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from finevision.artifact_store import ArtifactDescriptor, ArtifactIntegrityError, ArtifactStore


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
    source_url: str | None = None


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
    "inat2021_mobilenetv3_large_kd": ManagedWeight(
        artifact_id="pretrained-inat2021-mini-mobilenetv3-kd",
        backbone_key="inat2021_mobilenetv3_large_kd",
        category="inat2021-mini/mobilenetv3-large",
        sha256="ae3990c5d4655d71fd988f0246dfe5e372ed88e5b9ac78cf18b681d56a7986b9",
        size_bytes=12_015_440,
        architecture="mobilenet_v3_large",
        environment_key="FINEVISION_INAT_MOBILENETV3_WEIGHT",
        source_url="https://www.modelscope.cn/models/Tianyuqi/inat2021-mini-mobilenetv3-large/resolve/master/inat2021-mini-mobilenetv3-large-backbone.safetensors",
    ),
}

WEIGHT_ALIASES = {
    "dinov3_vits": "dinov3_vits16_lvd1689m",
    "imagenet_vits": "imagenet_vits16_augreg_in21k_ft_in1k",
    "imagenet_resnet50": "imagenet_resnet50_a1_in1k",
    "inat_mobilenetv3": "inat2021_mobilenetv3_large_kd",
}


def managed_weight_descriptor(backbone_key: str, bucket: str = "finevision-artifacts") -> ArtifactDescriptor:
    key = WEIGHT_ALIASES.get(backbone_key, backbone_key)
    if key not in MANAGED_WEIGHTS:
        raise ValueError(f"Unsupported managed backbone: {backbone_key}")
    weight = MANAGED_WEIGHTS[key]
    external = weight.source_url is not None
    return ArtifactDescriptor(
        artifact_id=weight.artifact_id,
        artifact_type="pretrained_weight",
        uri=weight.source_url if external else f"s3://{bucket}/pretrained/{weight.category}/{weight.sha256[:2]}/{weight.sha256}",
        sha256=weight.sha256,
        size_bytes=weight.size_bytes,
        content_type="application/octet-stream",
        storage_version="https-v1" if external else "s3-v1",
        producer="modelscope-verified/v1" if external else "git-lfs-weight-promotion/v1",
        metadata={"architecture": weight.architecture, "backbone_key": weight.backbone_key},
    )


def managed_vits_descriptor(bucket: str = "finevision-artifacts") -> ArtifactDescriptor:
    return managed_weight_descriptor("dinov3_vits16_lvd1689m", bucket)


def prepare_managed_weight(store: ArtifactStore, cache_root: str | Path, backbone_key: str) -> Path:
    key = WEIGHT_ALIASES.get(backbone_key, backbone_key)
    weight = MANAGED_WEIGHTS[key]
    if weight.source_url:
        path = _materialize_external_weight(weight, cache_root)
    else:
        descriptor = managed_weight_descriptor(key, os.environ.get("FINEVISION_ARTIFACT_BUCKET", "finevision-artifacts"))
        path = store.materialize_verified(descriptor, cache_root)
    os.environ[weight.environment_key] = str(path)
    return path


def _verify_weight(path: Path, weight: ManagedWeight) -> bool:
    if not path.is_file() or path.stat().st_size != weight.size_bytes:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == weight.sha256


def _materialize_external_weight(weight: ManagedWeight, cache_root: str | Path) -> Path:
    override = os.environ.get(weight.environment_key)
    if override:
        path = Path(override).resolve()
        if not _verify_weight(path, weight):
            raise ArtifactIntegrityError(f"published weight override failed SHA-256 or size check: {path}")
        return path

    path = Path(cache_root).resolve() / "sha256" / weight.sha256
    path.parent.mkdir(parents=True, exist_ok=True)
    if _verify_weight(path, weight):
        return path
    path.unlink(missing_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{weight.sha256}.", dir=path.parent)
    try:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "wb") as destination, urlopen(weight.source_url, timeout=60) as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > weight.size_bytes:
                    raise ArtifactIntegrityError("published weight exceeds expected size")
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if size != weight.size_bytes or digest.hexdigest() != weight.sha256:
            raise ArtifactIntegrityError("published weight failed SHA-256 or size check")
        os.replace(temporary_name, path)
        return path
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def prepare_managed_vits_weight(store: ArtifactStore, cache_root: str | Path) -> Path:
    return prepare_managed_weight(store, cache_root, "dinov3_vits16_lvd1689m")
