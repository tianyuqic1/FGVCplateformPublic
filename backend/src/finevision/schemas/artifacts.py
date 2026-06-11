from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal

DecisionValue = Literal["accept", "abstain", "reject_ood"]
SplitName = Literal["train", "val", "test"]


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class SampleRecord:
    sample_id: str
    path: str
    label: str
    split: SplitName
    quality_state: str = "accepted"


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    dataset_version_id: str
    root: str
    classes: list[str]
    samples: list[SampleRecord]
    split_counts: dict[str, dict[str, int]]
    readiness: dict[str, Any]
    artifact_refs: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureArtifact:
    artifact_id: str
    dataset_id: str
    dataset_version_id: str
    backbone_id: str
    extractor_config: dict[str, Any]
    feature_dim: int
    features_path: str
    sample_ids: list[str]
    labels: list[str]
    splits: list[str]


@dataclass(frozen=True)
class ModelArtifact:
    artifact_id: str
    dataset_id: str
    dataset_version_id: str
    feature_artifact_id: str
    model_path: str
    classes: list[str]
    head_type: str
    feature_dim: int
    training_config: dict[str, Any]


@dataclass(frozen=True)
class EvaluationReport:
    accuracy: float
    macro_f1: float
    per_class: dict[str, dict[str, float]]
    confusion_matrix: list[list[int]]
    run_config: dict[str, Any]


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    accuracy: float
    confidence: float


@dataclass(frozen=True)
class CalibrationReport:
    artifact_id: str
    dataset_id: str
    dataset_version_id: str
    model_artifact_id: str
    method: str
    split: str
    temperature: float
    before: dict[str, float]
    after: dict[str, float]
    bins: list[CalibrationBin]


@dataclass(frozen=True)
class TrainingRunReport:
    run_id: str
    dataset_id: str
    dataset_version_id: str
    feature_artifact_id: str
    model_artifact_id: str
    evaluation: EvaluationReport


@dataclass(frozen=True)
class ThresholdPoint:
    threshold: float
    coverage: float
    selective_risk: float
    abstention_rate: float
    estimated_review_cost: float


@dataclass(frozen=True)
class ThresholdSweep:
    strategy_id: str
    dataset_id: str
    dataset_version_id: str
    model_artifact_id: str
    calibration_artifact_id: str | None
    points: list[ThresholdPoint]
    split: str


@dataclass(frozen=True)
class ThresholdStrategy:
    strategy_id: str
    dataset_id: str
    dataset_version_id: str
    model_artifact_id: str
    calibration_artifact_id: str | None
    calibration_method: str
    temperature: float
    split: str
    accept_threshold: float
    margin_threshold: float
    target_selective_risk: float
    expected_coverage: float
    expected_selective_risk: float
    review_cost_per_item: float
    selection_rule: str
    selection_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AbstentionDecision:
    decision: DecisionValue
    reasons: list[str]
    thresholds: dict[str, float]
    margin: float
    confidence: float
    ood_score: float | None = None


@dataclass(frozen=True)
class InferenceResult:
    dataset_id: str
    dataset_version_id: str
    model_artifact_id: str
    threshold_strategy_id: str | None
    top_k: list[dict[str, float | str]]
    decision: AbstentionDecision
    nearest_neighbors: list[dict[str, float | str]] = field(default_factory=list)
