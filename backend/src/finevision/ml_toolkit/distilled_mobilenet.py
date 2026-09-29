"""The approved iNat2021-mini distilled MobileNetV3 feature contract."""

from __future__ import annotations

from torch import nn
from torchvision.models import mobilenet_v3_large


class DistilledMobileNetBackbone(nn.Module):
    """Load `features.*` from the delivered torchvision backbone checkpoint."""

    num_features = 960

    def __init__(self) -> None:
        super().__init__()
        self.features = mobilenet_v3_large(weights=None).features
        self.pool = nn.AdaptiveAvgPool2d(1)

    def forward(self, images):
        return self.pool(self.features(images)).flatten(1)


def preprocessing() -> dict[str, object]:
    # The export manifest specifies Resize(short side=224) + CenterCrop(224),
    # so crop_pct must be 1.0 rather than timm's usual 0.875.
    return {
        "input_size": (3, 224, 224),
        "mean": (0.485, 0.456, 0.406),
        "std": (0.229, 0.224, 0.225),
        "interpolation": "bicubic",
        "crop_pct": 1.0,
    }
