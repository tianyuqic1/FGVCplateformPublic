from __future__ import annotations

import json
from pathlib import Path

import pytest

from finevision.ml_toolkit.datasets import scan_imagefolder
from finevision.ml_toolkit.features import TimmFeatureExtractor, extract_features
from finevision.ml_toolkit.training import train_linear_head


ROOT = Path(__file__).resolve().parents[2]


def _weight(preset: str) -> Path:
    manifest = json.loads((ROOT / "weights" / "manifest.json").read_text(encoding="utf-8"))
    item = next(entry for entry in manifest["weights"] if entry["preset"] == preset)
    return ROOT / item["lfs_path"]


def test_imagenet_vit_small_extracts_features_and_trains_lightweight_head(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    pytest.importorskip("timm")
    manifest = scan_imagefolder(
        ROOT / "data" / "examples" / "toy-shapes-imagefolder",
        dataset_id="toy-shapes",
        dataset_version_id="toy-shapes-v1",
    )
    extractor = TimmFeatureExtractor(
        model_name="vit_small_patch16_224.augreg_in21k_ft_in1k",
        pretrained=False,
        checkpoint_path=str(_weight("imagenet_vits16_augreg_in21k_ft_in1k")),
        device="cuda" if torch.cuda.is_available() else "cpu",
        batch_size=4,
        image_size=224,
        feature_pool="model",
        backbone_id="imagenet_vits16_augreg_in21k_ft_in1k",
    )
    feature_artifact, features = extract_features(manifest, extractor, tmp_path / "features")
    assert features.shape == (12, 384)
    model_artifact, report, logits = train_linear_head(
        feature_artifact,
        features,
        tmp_path / "models",
        run_id="imagenet-vits-integration",
        head_type="ridge_linear",
        device="cpu",
    )
    assert Path(model_artifact.model_path).is_file()
    assert logits.shape == (12, 3)
    assert 0.0 <= report.evaluation.accuracy <= 1.0


def test_imagenet_resnet50_one_batch_feature_smoke(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("timm")
    source = ROOT / "data" / "examples" / "toy-shapes-imagefolder"
    paths = [str(path) for path in sorted(source.rglob("*.png"))[:2]]
    extractor = TimmFeatureExtractor(
        model_name="resnet50.a1_in1k",
        pretrained=False,
        checkpoint_path=str(_weight("imagenet_resnet50_a1_in1k")),
        device="cpu",
        batch_size=2,
        image_size=224,
        feature_pool="model",
        backbone_id="imagenet_resnet50_a1_in1k",
    )
    features = extractor.extract_paths(paths)
    assert features.shape == (2, 2048)
    assert features.dtype.name == "float32"
