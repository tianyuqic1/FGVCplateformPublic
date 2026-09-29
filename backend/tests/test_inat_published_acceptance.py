"""Opt-in acceptance test for the published iNat distilled checkpoint."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import numpy as np
import pytest

from finevision.compute.pretrained_weights import MANAGED_WEIGHTS, prepare_managed_weight
from finevision.ml_toolkit.datasets import scan_imagefolder
from finevision.ml_toolkit.image_training import (
    build_classifier,
    image_transform,
    load_classifier,
    train_images,
)


ROOT = Path(__file__).resolve().parents[2]
KEY = "inat2021_mobilenetv3_large_kd"


@pytest.mark.skipif(
    os.environ.get("RUN_INAT_MODEL_ACCEPTANCE") != "1",
    reason="set RUN_INAT_MODEL_ACCEPTANCE=1 with the published weight available",
)
def test_published_inat_weight_trains_exports_and_reloads(tmp_path: Path, monkeypatch) -> None:
    import torch
    from PIL import Image

    from finevision.compute.image_export import export_image_classifier
    from finevision.ml_toolkit.features import TimmFeatureExtractor

    weight = MANAGED_WEIGHTS[KEY]
    # The override is checked by the same path used by compute workers.
    checkpoint = prepare_managed_weight(None, tmp_path / "cache", KEY)
    assert checkpoint.stat().st_size == weight.size_bytes
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == weight.sha256

    published_loader = ROOT / "pretrained" / "inat2021-mini-mobilenetv3-large" / "load_model.py"
    spec = importlib.util.spec_from_file_location("published_inat_loader", published_loader)
    assert spec is not None and spec.loader is not None
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    reference = loader.load_backbone(checkpoint)
    model, preprocessing = build_classifier(KEY, 3, "frozen", 0, checkpoint_path=checkpoint)
    model.eval()
    image = Image.linear_gradient("L").resize((247, 269)).convert("RGB")
    actual_input = image_transform(preprocessing)(image)
    torch.testing.assert_close(actual_input, loader.preprocess()(image))
    with torch.inference_mode():
        torch.testing.assert_close(
            model.backbone(actual_input.unsqueeze(0)),
            reference(actual_input.unsqueeze(0)),
        )
    sample = tmp_path / "sample.png"
    image.save(sample)
    extractor = TimmFeatureExtractor(
        model_name="torchvision.mobilenet_v3_large",
        checkpoint_path=str(checkpoint),
        device="cpu",
        batch_size=1,
        image_size=224,
        feature_pool="model",
        backbone_id=KEY,
    )
    features = extractor.extract_paths([str(sample)])
    with torch.inference_mode():
        expected = reference(loader.preprocess()(image).unsqueeze(0)).numpy()
    np.testing.assert_allclose(features, expected, rtol=1e-5, atol=1e-6)

    monkeypatch.setenv("FINEVISION_COMPUTE_DEVICE", "cpu")
    manifest = scan_imagefolder(ROOT / "data/examples/toy-shapes-imagefolder", "toy", "inat-acceptance")
    _, artifact, report, logits = train_images(
        manifest,
        {
            "backbone_id": KEY,
            "training_run_id": "published-inat-smoke",
            "head_config": {"epochs": 1, "batch_size": 4, "head_learning_rate": 1e-3},
        },
        tmp_path,
        progress=lambda *_: None,
        check_control=lambda: None,
    )
    assert logits.shape == (len(manifest.samples), len(manifest.classes))
    assert np.isfinite(logits).all()
    assert report.evaluation.run_config["training_mode"] == "frozen"
    restored, saved = load_classifier(artifact.model_path)
    assert saved["backbone_key"] == KEY
    with torch.inference_mode():
        assert restored(actual_input.unsqueeze(0)).shape == (1, len(manifest.classes))

    _, onnx, metadata = export_image_classifier(
        artifact.model_path, tmp_path / "export", manifest.classes, "inat-acceptance"
    )
    assert onnx.is_file()
    assert metadata["parity_passed"] is True
