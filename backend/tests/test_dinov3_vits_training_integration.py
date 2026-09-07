from __future__ import annotations

import json
from pathlib import Path

import pytest

from finevision.ml_toolkit.datasets import scan_imagefolder
from finevision.ml_toolkit.features import TimmDinoV3Extractor, extract_features
from finevision.ml_toolkit.training import train_linear_head


def test_vit_small_extracts_features_and_trains_only_supported_integration_model(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    pytest.importorskip("timm")
    root = Path(__file__).resolve().parents[2]
    manifest_data = json.loads((root / "weights" / "manifest.json").read_text(encoding="utf-8"))
    assert [item["architecture"] for item in manifest_data["weights"]] == ["vit_small_patch16_dinov3"]
    checkpoint = root / manifest_data["weights"][0]["lfs_path"]

    manifest = scan_imagefolder(
        root / "data" / "examples" / "toy-shapes-imagefolder",
        dataset_id="toy-shapes",
        dataset_version_id="toy-shapes-v1",
    )
    extractor = TimmDinoV3Extractor(
        model_name="vit_small_patch16_dinov3",
        pretrained=False,
        checkpoint_path=str(checkpoint),
        device="cuda" if torch.cuda.is_available() else "cpu",
        batch_size=4,
        image_size=224,
        feature_pool="cls",
        backbone_id="dinov3_vits16",
    )

    feature_artifact, features = extract_features(manifest, extractor, tmp_path / "features")
    assert features.shape == (12, 384)
    model_artifact, report, logits = train_linear_head(
        feature_artifact,
        features,
        tmp_path / "models",
        run_id="vits-integration",
        head_type="ridge_linear",
        device="cpu",
    )

    assert Path(model_artifact.model_path).is_file()
    assert logits.shape == (12, 3)
    assert 0.0 <= report.evaluation.accuracy <= 1.0
