from __future__ import annotations

import pytest

from finevision.ml_toolkit.features import (
    build_extractor_from_config,
    dinov3_extractor_config,
)


def test_dinov3_image_size_is_part_of_feature_config() -> None:
    config = dinov3_extractor_config("dinov3_vits", image_size=448)
    extractor = build_extractor_from_config(config, {"device": "cpu", "batch_size": 2})

    assert extractor.config["image_size"] == 448
    assert extractor.config["model_name"] == "vit_small_patch16_dinov3"
    assert extractor.config["feature_pool"] == "cls"


def test_imagenet_vits_uses_model_pooling_and_rejects_unknown_backbone() -> None:
    extractor = build_extractor_from_config(
        {
            "type": "timm",
            "backbone_key": "imagenet_vits16_augreg_in21k_ft_in1k",
            "model_name": "vit_small_patch16_224.augreg_in21k_ft_in1k",
            "pretrained": True,
        },
        {"device": "cpu", "batch_size": 2},
    )

    assert extractor.config["feature_pool"] == "model"
    assert extractor.config["image_size"] == 224

    with pytest.raises(ValueError, match="Unsupported managed backbone"):
        build_extractor_from_config({"type": "timm", "backbone_key": "dinov3_vitb"})
