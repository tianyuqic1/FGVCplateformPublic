from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
import json
from pathlib import Path
import shutil
from typing import Any, Callable, Protocol

import numpy as np
from PIL import Image

from finevision.ml_toolkit.artifacts import write_feature_artifact
from finevision.schemas.artifacts import DatasetManifest, FeatureArtifact


BACKBONE_SPECS: dict[str, dict[str, Any]] = {
    "dinov3_vits16_lvd1689m": {
        "legacy_extractor": "dinov3_vits",
        "backbone_id": "dinov3_vits16_lvd1689m",
        "model_name": "vit_small_patch16_dinov3",
        "repo_id": "timm/vit_small_patch16_dinov3.lvd1689m",
        "architecture": "vit_small_patch16",
        "pretraining_method": "DINOv3",
        "pretraining_dataset": "LVD-1689M",
        "image_size": 224,
        "feature_dim": 384,
        "feature_pool": "cls",
        "weight_env": "FINEVISION_DINOV3_VITS_WEIGHT",
    },
    "imagenet_vits16_augreg_in21k_ft_in1k": {
        "legacy_extractor": "imagenet_vits",
        "backbone_id": "imagenet_vits16_augreg_in21k_ft_in1k",
        "model_name": "vit_small_patch16_224.augreg_in21k_ft_in1k",
        "repo_id": "timm/vit_small_patch16_224.augreg_in21k_ft_in1k",
        "architecture": "vit_small_patch16",
        "pretraining_method": "supervised",
        "pretraining_dataset": "ImageNet-21K → ImageNet-1K",
        "image_size": 224,
        "feature_dim": 384,
        "feature_pool": "model",
        "weight_env": "FINEVISION_IMAGENET_VITS_WEIGHT",
    },
    "imagenet_resnet50_a1_in1k": {
        "legacy_extractor": "imagenet_resnet50",
        "backbone_id": "imagenet_resnet50_a1_in1k",
        "model_name": "resnet50.a1_in1k",
        "repo_id": "timm/resnet50.a1_in1k",
        "architecture": "resnet50",
        "pretraining_method": "supervised",
        "pretraining_dataset": "ImageNet-1K",
        "image_size": 288,
        "feature_dim": 2048,
        "feature_pool": "model",
        "weight_env": "FINEVISION_IMAGENET_RESNET50_WEIGHT",
    },
}

BACKBONE_ALIASES = {
    str(spec["legacy_extractor"]): key for key, spec in BACKBONE_SPECS.items()
}
# Compatibility export for existing compute callers. Its contents are now the
# Phase 2 allow-list, not the historical ViT-S/B/L family.
DINOV3_MODEL_PRESETS = BACKBONE_SPECS


def _weight_info(preset: str, config: dict[str, Any], hub_root: Path) -> dict[str, Any]:
    model_name = str(config["model_name"])
    repo_id = str(config["repo_id"])
    repo_dir = hub_root / f"models--{repo_id.replace('/', '--')}"
    complete_files = [
        path
        for path in (repo_dir / "blobs").glob("*")
        if path.is_file() and not path.name.endswith(".incomplete")
    ]
    incomplete_files = [
        path
        for path in (repo_dir / "blobs").glob("*.incomplete")
        if path.is_file()
    ]
    complete_size = sum(path.stat().st_size for path in complete_files)
    incomplete_size = sum(path.stat().st_size for path in incomplete_files)
    if complete_files:
        cache_status = "cached"
    elif incomplete_files:
        cache_status = "partial"
    else:
        cache_status = "missing"

    return {
        "preset": preset,
        "extractor": config["legacy_extractor"],
        "backbone_id": config["backbone_id"],
        "model_name": model_name,
        "repo_id": repo_id,
        "cache_status": cache_status,
        "state": cache_status,
        "cached": cache_status == "cached",
        "cache_dir": str(repo_dir),
        "complete_file_count": len(complete_files),
        "complete_size_bytes": complete_size,
        "cache_bytes": complete_size,
        "incomplete_file_count": len(incomplete_files),
        "incomplete_size_bytes": incomplete_size,
        "partial_bytes": incomplete_size,
        "description": _weight_description(preset),
        "download_hint": "cached locally" if complete_files else "download with timm/Hugging Face Hub",
    }


def _weight_description(preset: str) -> str:
    descriptions = {
        "dinov3_vits16_lvd1689m": "ViT-S/16 self-supervised with DINOv3 on LVD-1689M; CLS pooled frozen features.",
        "imagenet_vits16_augreg_in21k_ft_in1k": "ViT-S/16 supervised on ImageNet-21K then fine-tuned on ImageNet-1K.",
        "imagenet_resnet50_a1_in1k": "ResNet-50 supervised on ImageNet-1K; global pooled frozen features.",
    }
    return descriptions[preset]


def inspect_dinov3_weight_cache(cache_root: str | Path | None = None) -> dict[str, Any]:
    hub_root = _resolve_huggingface_hub_cache(cache_root)
    weights = [_weight_info(preset, config, hub_root) for preset, config in BACKBONE_SPECS.items()]

    return {
        "cache_root": str(hub_root),
        "hf_token_configured": bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")),
        "weights": weights,
    }


def delete_dinov3_weight_cache(preset: str, cache_root: str | Path | None = None) -> dict[str, Any]:
    preset = BACKBONE_ALIASES.get(preset, preset)
    if preset not in BACKBONE_SPECS:
        raise ValueError(f"Unsupported pretrained weight preset: {preset}")

    hub_root = _resolve_huggingface_hub_cache(cache_root)
    config = BACKBONE_SPECS[preset]
    before = _weight_info(preset, config, hub_root)
    repo_dir = Path(before["cache_dir"])
    deleted = repo_dir.exists()
    if deleted:
        shutil.rmtree(repo_dir)

    after = _weight_info(preset, config, hub_root)
    return {
        "deleted": deleted,
        "preset": preset,
        "cache_dir": str(repo_dir),
        "before": before,
        "after": after,
    }


def _resolve_huggingface_hub_cache(cache_root: str | Path | None = None) -> Path:
    if cache_root is not None:
        return Path(cache_root).expanduser()
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def dinov3_extractor_config(
    extractor: str,
    backbone_id: str | None = None,
    *,
    image_size: int | None = None,
    feature_pool: str = "cls",
) -> dict[str, object]:
    key = BACKBONE_ALIASES.get(extractor, extractor)
    preset = BACKBONE_SPECS[key]
    config: dict[str, object] = {
        "type": "timm",
        "preset": key,
        "backbone_key": key,
        "model_name": preset["model_name"],
        "pretrained": True,
        "backbone_id": backbone_id or preset["backbone_id"],
        "feature_pool": feature_pool or str(preset["feature_pool"]),
    }
    if image_size is not None:
        config["image_size"] = image_size
    return config


class ImageFeatureExtractor(Protocol):
    backbone_id: str
    config: dict[str, object]

    def extract_paths(self, paths: list[str]) -> np.ndarray:
        ...


@dataclass
class ColorStatsExtractor:
    """Fast deterministic extractor for smoke tests and toy datasets."""

    bins: int = 8
    backbone_id: str = "color_stats_v1"
    config: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.config = {"type": "color_stats", "bins": self.bins}

    def extract_paths(self, paths: list[str]) -> np.ndarray:
        rows = []
        for path in paths:
            image = Image.open(path).convert("RGB").resize((64, 64))
            arr = np.asarray(image, dtype=np.float32) / 255.0
            mean = arr.mean(axis=(0, 1))
            std = arr.std(axis=(0, 1))
            hist_parts = []
            for channel in range(3):
                hist, _ = np.histogram(arr[:, :, channel], bins=self.bins, range=(0.0, 1.0), density=True)
                hist_parts.append(hist.astype(np.float32))
            rows.append(np.concatenate([mean, std, *hist_parts]))
        return np.vstack(rows).astype(np.float32)


@dataclass
class TimmFeatureExtractor:
    """Frozen timm backbone for the three Phase 2 managed weight identities.

    Heavy dependencies are imported lazily so CRUD and ordinary toolkit tests
    do not require torch/timm or a network connection.
    """

    model_name: str = "vit_small_patch16_dinov3"
    pretrained: bool = True
    device: str = "cpu"
    batch_size: int = 8
    image_size: int | None = None
    feature_pool: str = "cls"
    backbone_id: str = "dinov3_vits16_lvd1689m"
    checkpoint_path: str | None = None
    config: dict[str, object] = field(default_factory=dict)
    _model: Any = field(default=None, init=False, repr=False)
    _transform: Any = field(default=None, init=False, repr=False)
    progress_callback: Callable[[int, int], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        # Runtime fields such as device and batch_size do not change feature
        # semantics; keeping them out lets cache reuse survive tuning.
        self.config = {
            "type": "timm",
            "model_name": self.model_name,
            "pretrained": self.pretrained,
            "backbone_id": self.backbone_id,
            "feature_pool": self.feature_pool,
        }
        if self.image_size is not None:
            self.config["image_size"] = int(self.image_size)

    def prepare(self) -> None:
        try:
            import torch
            import timm
            from timm.data import create_transform, resolve_model_data_config
        except ImportError as exc:
            raise RuntimeError(
                "Managed timm extraction requires optional dependencies. "
                "Install with `uv sync --extra dinov3 --group dev`."
            ) from exc

        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("Managed timm extraction requested cuda, but torch.cuda.is_available() is false.")

        if self._model is None or self._transform is None:
            model = timm.create_model(
                self.model_name,
                pretrained=self.pretrained if self.checkpoint_path is None else False,
                num_classes=0,
            )
            if self.checkpoint_path is not None:
                from safetensors.torch import load_file

                incompatible = model.load_state_dict(load_file(self.checkpoint_path), strict=False)
                allowed_classifier_prefixes = ("head.", "fc.", "classifier.")
                unsupported = [
                    key for key in incompatible.unexpected_keys if not key.startswith(allowed_classifier_prefixes)
                ]
                if incompatible.missing_keys or unsupported:
                    raise RuntimeError(
                        "Managed checkpoint is incompatible with the approved backbone: "
                        f"missing={incompatible.missing_keys}, unexpected={unsupported}"
                    )
            model.eval().to(self.device)
            data_config = resolve_model_data_config(model)
            if self.image_size is not None:
                data_config["input_size"] = (3, int(self.image_size), int(self.image_size))
            self._model = model
            self._transform = create_transform(**data_config, is_training=False)

    def extract_paths(self, paths: list[str]) -> np.ndarray:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "Managed timm extraction requires optional dependencies. "
                "Install with `uv sync --extra dinov3 --group dev`."
            ) from exc

        self.prepare()
        model = self._model
        transform = self._transform

        batches: list[np.ndarray] = []
        total = len(paths)
        with torch.inference_mode():
            for start in range(0, len(paths), self.batch_size):
                images = [transform(Image.open(path).convert("RGB")) for path in paths[start : start + self.batch_size]]
                tensor = torch.stack(images).to(self.device)
                if self.feature_pool == "cls":
                    output = model.forward_features(tensor)
                    if isinstance(output, (tuple, list)):
                        output = output[0]
                    if output.ndim == 3:
                        output = output[:, 0]
                elif self.feature_pool == "model":
                    output = model(tensor)
                else:
                    raise ValueError(f"Unsupported managed feature_pool: {self.feature_pool}")
                if isinstance(output, (tuple, list)):
                    output = output[0]
                batches.append(output.detach().cpu().float().numpy())
                if self.progress_callback is not None:
                    self.progress_callback(min(start + self.batch_size, total), total)
        return np.vstack(batches).astype(np.float32)


# Kept as a source-compatible name for existing compute tests and stored
# extractor configs. New code should use TimmFeatureExtractor.
TimmDinoV3Extractor = TimmFeatureExtractor


def build_extractor_from_config(config: dict[str, Any], overrides: dict[str, Any] | None = None) -> ImageFeatureExtractor:
    merged = {**config, **(overrides or {})}
    extractor_type = str(merged.get("type") or "color_stats")
    if extractor_type == "color_stats":
        return ColorStatsExtractor(bins=int(merged.get("bins", 8)))
    requested = str(merged.get("backbone_key") or merged.get("preset") or extractor_type)
    preset_name = BACKBONE_ALIASES.get(requested, requested)
    if extractor_type in {"timm", "timm_dinov3", *BACKBONE_SPECS.keys(), *BACKBONE_ALIASES.keys()}:
        if preset_name not in BACKBONE_SPECS:
            raise ValueError(f"Unsupported Phase 2 backbone: {preset_name}")
        preset = BACKBONE_SPECS[preset_name]
        model_name = str(merged.get("model_name", preset["model_name"]))
        if model_name != preset["model_name"]:
            raise ValueError("Managed backbone model_name does not match backbone_key")
        return TimmFeatureExtractor(
            model_name=model_name,
            pretrained=bool(merged.get("pretrained", True)),
            device=str(merged.get("device", "cpu")),
            batch_size=int(merged.get("batch_size", 8)),
            image_size=int(merged.get("image_size", preset["image_size"])),
            feature_pool=str(merged.get("feature_pool") or preset["feature_pool"]),
            backbone_id=str(merged.get("backbone_id", preset["backbone_id"])),
            checkpoint_path=_managed_checkpoint_path(merged, preset_name),
        )
    raise ValueError(f"Unsupported extractor config type: {extractor_type}")


def _managed_checkpoint_path(config: dict[str, Any], backbone_key: str) -> str | None:
    explicit = str(config.get("checkpoint_path") or "").strip()
    if explicit:
        return explicit
    environment_key = str(BACKBONE_SPECS.get(backbone_key, {}).get("weight_env") or "")
    if environment_key:
        configured = os.environ.get(environment_key, "").strip()
        if configured:
            return configured
    return None


def extract_features(
    manifest: DatasetManifest,
    extractor: ImageFeatureExtractor,
    artifact_root: str | Path,
    artifact_id: str | None = None,
) -> tuple[FeatureArtifact, np.ndarray]:
    paths = [sample.path for sample in manifest.samples]
    features = extractor.extract_paths(paths)
    config_hash = hashlib.sha1(json.dumps(extractor.config, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    artifact_id = artifact_id or f"{manifest.dataset_version_id}-{extractor.backbone_id}-{config_hash}"
    artifact_dir = Path(artifact_root) / artifact_id
    artifact = FeatureArtifact(
        artifact_id=artifact_id,
        dataset_id=manifest.dataset_id,
        dataset_version_id=manifest.dataset_version_id,
        backbone_id=extractor.backbone_id,
        extractor_config=extractor.config,
        feature_dim=int(features.shape[1]),
        features_path=str(artifact_dir / "features.npz"),
        sample_ids=[sample.sample_id for sample in manifest.samples],
        labels=[sample.label for sample in manifest.samples],
        splits=[sample.split for sample in manifest.samples],
    )
    return write_feature_artifact(artifact_dir, artifact, features), features
