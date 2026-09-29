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


def test_inat_distilled_mobilenet_contract_loads_for_features_and_training(tmp_path: Path) -> None:
    import importlib.util
    torch = pytest.importorskip("torch")
    pytest.importorskip("timm")
    pytest.importorskip("torchvision")
    from safetensors.torch import save_file
    from PIL import Image
    from finevision.ml_toolkit.distilled_mobilenet import DistilledMobileNetBackbone
    from finevision.ml_toolkit.image_training import build_classifier, image_transform, training_mode

    key = "inat2021_mobilenetv3_large_kd"
    weight = tmp_path / "mobilenet-backbone.safetensors"
    save_file(DistilledMobileNetBackbone().state_dict(), str(weight))
    frozen, rank = training_mode(key, {})
    model, preprocessing = build_classifier(key, 2, frozen, rank, checkpoint_path=weight)
    assert preprocessing["crop_pct"] == 1.0
    assert preprocessing["interpolation"] == "bicubic"
    assert model.backbone.num_features == 960
    assert all(not parameter.requires_grad for parameter in model.backbone.parameters())
    assert all(parameter.requires_grad for parameter in model.head.parameters())

    image = Image.linear_gradient("L").resize((240, 270)).convert("RGB")
    tensor = image_transform(preprocessing)(image).unsqueeze(0)
    published_loader = ROOT / "pretrained" / "inat2021-mini-mobilenetv3-large" / "load_model.py"
    loader_spec = importlib.util.spec_from_file_location("published_inat_loader", published_loader)
    assert loader_spec is not None and loader_spec.loader is not None
    loader = importlib.util.module_from_spec(loader_spec)
    loader_spec.loader.exec_module(loader)
    torch.testing.assert_close(tensor[0], loader.preprocess()(image))
    extractor = TimmFeatureExtractor(
        model_name="torchvision.mobilenet_v3_large", pretrained=False,
        checkpoint_path=str(weight), device="cpu", batch_size=1, image_size=224,
        feature_pool="model", backbone_id=key,
    )
    extractor.prepare()
    model.eval()
    with torch.no_grad():
        expected = model.backbone(tensor)
        actual = extractor._model(extractor._transform(image).unsqueeze(0))
    assert expected.shape == (1, 960)
    torch.testing.assert_close(actual, expected)

    full, rank = training_mode(key, {"train_backbone": True})
    trainable, _ = build_classifier(key, 2, full, rank, checkpoint_path=weight)
    assert all(parameter.requires_grad for parameter in trainable.backbone.parameters())
