from __future__ import annotations

import numpy as np

from finevision.ml_toolkit.training import train_linear_head
from finevision.schemas.artifacts import FeatureArtifact


def test_train_linear_head_uses_torch_adam(tmp_path) -> None:
    feature_artifact = FeatureArtifact(
        artifact_id="feature:toy:linear",
        dataset_id="toy",
        dataset_version_id="dataset@toy-001",
        backbone_id="toy_backbone",
        extractor_config={"type": "toy"},
        feature_dim=2,
        features_path=str(tmp_path / "features.npz"),
        sample_ids=[f"sample-{index}" for index in range(8)],
        labels=["left", "left", "left", "left", "right", "right", "right", "right"],
        splits=["train", "train", "train", "val", "train", "train", "train", "val"],
    )
    features = np.array(
        [
            [-2.0, -1.0],
            [-1.5, -0.7],
            [-1.0, -0.2],
            [-1.7, -0.5],
            [1.0, 0.2],
            [1.5, 0.8],
            [2.0, 1.0],
            [1.7, 0.5],
        ],
        dtype=np.float32,
    )

    model_artifact, training_report, logits = train_linear_head(
        feature_artifact,
        features,
        tmp_path / "models",
        run_id="run-adam",
        head_type="torch_linear_adam",
        learning_rate=0.05,
        epochs=20,
        batch_size=4,
        weight_decay=0.0,
        device="cpu",
    )

    assert model_artifact.head_type == "torch_linear_adam"
    assert model_artifact.training_config["head_solver"] == "torch_adam"
    assert len(model_artifact.training_config["optimizer_history"]) == 20
    assert training_report.run_config["learning_rate"] == 0.05
    assert training_report.evaluation.accuracy == 1.0
    assert logits.shape == (8, 2)
