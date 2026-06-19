from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from finevision.ml_toolkit.artifacts import write_feature_artifact
from finevision.schemas.artifacts import DatasetManifest, FeatureArtifact


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
class TimmDinoV3Extractor:
    """DINOv3 feature extractor backed by timm.

    The default model uses ViT-L/16. The heavy dependencies are imported lazily
    so ordinary toolkit tests do not require torch/timm or model downloads.
    """

    model_name: str = "vit_large_patch16_dinov3.lvd1689m"
    pretrained: bool = True
    device: str = "cpu"
    batch_size: int = 8
    backbone_id: str = "dinov3_vitl16"
    config: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.config = {
            "type": "timm_dinov3",
            "model_name": self.model_name,
            "pretrained": self.pretrained,
            "device": self.device,
            "batch_size": self.batch_size,
        }

    def extract_paths(self, paths: list[str]) -> np.ndarray:
        try:
            import torch
            import timm
            from timm.data import create_transform, resolve_model_data_config
        except ImportError as exc:
            raise RuntimeError(
                "DINOv3 extraction requires optional dependencies. "
                "Install with `uv sync --extra dinov3 --group dev`."
            ) from exc

        model = timm.create_model(self.model_name, pretrained=self.pretrained, num_classes=0)
        model.eval().to(self.device)
        data_config = resolve_model_data_config(model)
        transform = create_transform(**data_config, is_training=False)

        batches: list[np.ndarray] = []
        with torch.inference_mode():
            for start in range(0, len(paths), self.batch_size):
                images = [transform(Image.open(path).convert("RGB")) for path in paths[start : start + self.batch_size]]
                tensor = torch.stack(images).to(self.device)
                output = model(tensor)
                if isinstance(output, (tuple, list)):
                    output = output[0]
                batches.append(output.detach().cpu().float().numpy())
        return np.vstack(batches).astype(np.float32)


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
