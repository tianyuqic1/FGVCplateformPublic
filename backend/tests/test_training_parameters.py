import numpy as np
import pytest
import torch
from PIL import Image
from torch import nn

from finevision.ml_toolkit.image_training import (
    AUGMENTATION_KEYS, ImageClassifier, image_transform, training_transform,
    learning_rates, normalize_augmentations, optimizer_groups,
)


class Backbone(nn.Module):
    num_features = 12
    def __init__(self):
        super().__init__()
        self.attn = nn.Module()
        self.attn.qkv = nn.Linear(4, 12)
    def forward(self, x): return self.attn.qkv(x)


@pytest.mark.parametrize("mode", ["frozen", "lora", "full"])
def test_optimizer_groups_use_independent_rates_and_exclude_frozen_weights(mode):
    model = ImageClassifier(Backbone(), 3, mode, 8 if mode == "lora" else 0)
    config = {"head_learning_rate": 0.001}
    if mode == "lora": config["lora_learning_rate"] = 0.0002
    if mode == "full": config["backbone_learning_rate"] = 0.00001
    rates = learning_rates(config, mode)
    optimizer = torch.optim.AdamW(optimizer_groups(model, rates))
    assert {g["name"]: g["lr"] for g in optimizer.param_groups} == {k.removesuffix("_learning_rate"): v for k, v in config.items()}
    ids = [id(p) for g in optimizer.param_groups for p in g["params"]]
    assert len(ids) == len(set(ids))
    assert set(ids) == {id(p) for p in model.parameters() if p.requires_grad}
    before = {n: p.detach().clone() for n, p in model.named_parameters()}
    nn.functional.cross_entropy(model(torch.randn(2, 4)), torch.tensor([0, 1])).backward()
    optimizer.step()
    for name, p in model.named_parameters():
        if not p.requires_grad: assert torch.equal(before[name], p)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "1e-3"])
def test_invalid_learning_rates_fail_closed(value):
    with pytest.raises(ValueError): learning_rates({"head_learning_rate": value}, "frozen")


def test_inactive_rates_and_legacy_fallback():
    with pytest.raises(ValueError): learning_rates({"backbone_learning_rate": 1e-5}, "frozen")
    with pytest.raises(ValueError): learning_rates({"lora_learning_rate": 1e-5}, "full")
    assert learning_rates({"learning_rate": 0.002}, "full") == {"head_learning_rate": 0.002, "backbone_learning_rate": 0.002}


@pytest.mark.parametrize("key", AUGMENTATION_KEYS)
def test_augmentation_shapes_and_eval_is_deterministic(key):
    config = dict(input_size=(3, 224, 224), mean=(0.5,)*3, std=(0.5,)*3, interpolation="bicubic", crop_pct=0.9)
    image = Image.fromarray(np.random.default_rng(7).integers(0, 255, (256, 300, 3), dtype=np.uint8))
    evaluation = image_transform(config)
    expected = evaluation(image)
    for _ in range(3):
        actual = training_transform(config, {key: True})(image)
        assert actual.shape == (3, 224, 224) and torch.isfinite(actual).all()
        assert torch.equal(evaluation(image), expected)
    assert torch.equal(training_transform(config, {})(image), expected)


@pytest.mark.parametrize("value", [{"mixup": True}, {"horizontal_flip": "true"}, [], True])
def test_invalid_augmentations(value):
    with pytest.raises(ValueError): normalize_augmentations(value)
