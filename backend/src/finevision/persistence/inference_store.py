from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.pool import NullPool

from finevision.db.schema import artifacts, dataset_versions, datasets, model_versions, training_runs
from finevision.ml_toolkit.artifacts import load_feature_artifact, load_model_artifact
from finevision.schemas.artifacts import FeatureArtifact, ModelArtifact, ThresholdStrategy


@dataclass(frozen=True)
class InferenceContext:
    dataset_id: str
    dataset_version_id: str
    model_version_id: str
    model_status: str
    model_artifact: ModelArtifact
    model_state: dict[str, Any]
    feature_artifact: FeatureArtifact
    features: Any
    threshold_strategy: ThresholdStrategy


class DatabaseInferenceStore:
    def __init__(self, database_url: str | Engine) -> None:
        self.engine = database_url if isinstance(database_url, Engine) else create_engine(database_url, poolclass=NullPool)

    def load_context(self, *, dataset_version_id: str, model_version_id: str) -> InferenceContext | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                _inference_context_select().where(
                    dataset_versions.c.version_key == dataset_version_id,
                    model_versions.c.model_key == model_version_id,
                )
            ).mappings().first()
        if row is None:
            return None

        model_artifact, model_state = load_model_artifact(Path(row["model_uri"]).parent)
        feature_artifact, features = load_feature_artifact(Path(row["feature_uri"]).parent)
        threshold_strategy = _threshold_strategy_from_row(row)
        return InferenceContext(
            dataset_id=row["dataset_key"],
            dataset_version_id=row["version_key"],
            model_version_id=row["model_key"],
            model_status=row["model_status"],
            model_artifact=model_artifact,
            model_state=model_state,
            feature_artifact=feature_artifact,
            features=features,
            threshold_strategy=threshold_strategy,
        )


model_artifacts = artifacts.alias("model_artifacts")
feature_artifacts = artifacts.alias("feature_artifacts")
threshold_artifacts = artifacts.alias("threshold_artifacts")


def _inference_context_select() -> sa.Select[Any]:
    return (
        sa.select(
            datasets.c.dataset_key,
            dataset_versions.c.version_key,
            model_versions.c.model_key,
            model_versions.c.status.label("model_status"),
            model_artifacts.c.uri.label("model_uri"),
            model_artifacts.c.artifact_metadata.label("model_metadata"),
            feature_artifacts.c.uri.label("feature_uri"),
            feature_artifacts.c.artifact_metadata.label("feature_metadata"),
            threshold_artifacts.c.artifact_metadata.label("threshold_metadata"),
        )
        .select_from(
            model_versions.join(datasets, datasets.c.id == model_versions.c.dataset_id)
            .join(dataset_versions, dataset_versions.c.id == model_versions.c.dataset_version_id)
            .join(training_runs, training_runs.c.id == model_versions.c.training_run_id)
            .join(model_artifacts, model_artifacts.c.id == model_versions.c.model_artifact_id)
            .join(feature_artifacts, feature_artifacts.c.id == training_runs.c.feature_artifact_id)
            .outerjoin(threshold_artifacts, threshold_artifacts.c.id == model_versions.c.threshold_strategy_artifact_id)
        )
    )


def _threshold_strategy_from_row(row: Any) -> ThresholdStrategy:
    metadata = row["threshold_metadata"] or {}
    strategy = metadata.get("threshold_strategy")
    if strategy:
        return ThresholdStrategy(**strategy)

    model_artifact = (row["model_metadata"] or {}).get("model_artifact") or {}
    return ThresholdStrategy(
        strategy_id=f"{row['model_key']}:default-thresholds",
        dataset_id=row["dataset_key"],
        dataset_version_id=row["version_key"],
        model_artifact_id=model_artifact.get("artifact_id", row["model_key"]),
        calibration_artifact_id=None,
        calibration_method="none",
        temperature=1.0,
        split="all",
        accept_threshold=0.0,
        margin_threshold=0.0,
        target_selective_risk=1.0,
        expected_coverage=1.0,
        expected_selective_risk=0.0,
        review_cost_per_item=1.0,
        selection_rule="no_threshold_strategy_available",
    )
