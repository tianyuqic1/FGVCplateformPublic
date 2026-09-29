"""Load the delivered iNat2021-mini MobileNetV3-Large weights.

Requires torch, torchvision and safetensors. Classifier logits follow the
indices in class_map.json; the backbone returns a pooled 960-D embedding.
"""
from pathlib import Path

import torch
from torch import nn
from safetensors.torch import load_file
from torchvision import transforms as T
from torchvision.models import mobilenet_v3_large


class INatMobileNetV3(nn.Module):
    def __init__(self, n_classes=10000):
        super().__init__()
        base = mobilenet_v3_large(weights=None)
        self.stem = base.features[:8]
        self.rest = base.features[8:]
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.proj = nn.Linear(960, 768)
        self.norm = nn.LayerNorm(768)
        self.head = nn.Linear(768, n_classes)

    def forward(self, x):
        x = self.pool(self.rest(self.stem(x))).flatten(1)
        return self.head(self.norm(self.proj(x)))


class INatBackbone(nn.Module):
    def __init__(self):
        super().__init__()
        self.features = mobilenet_v3_large(weights=None).features
        self.pool = nn.AdaptiveAvgPool2d(1)

    def forward(self, x):
        return self.pool(self.features(x)).flatten(1)


def load_classifier(path, *, n_classes=10000, device='cpu'):
    model = INatMobileNetV3(n_classes)
    model.load_state_dict(load_file(str(path), device='cpu'), strict=True)
    return model.to(device).eval()


def load_backbone(path, *, device='cpu'):
    model = INatBackbone()
    state = load_file(str(path), device='cpu')
    model.load_state_dict(state, strict=True)
    return model.to(device).eval()


def preprocess():
    return T.Compose([
        T.Resize(224, interpolation=T.InterpolationMode.BICUBIC),
        T.CenterCrop(224),
        T.ToTensor(),
        T.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
    ])
