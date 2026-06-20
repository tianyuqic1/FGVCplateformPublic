from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any, Protocol

from finevision.api.store import JobRecord, MetadataStore, create_stores
from finevision.api.training_store import DatabaseTrainingStore
from finevision.ml_toolkit.artifacts import load_feature_artifact
from finevision.ml_toolkit.calibration import fit_temperature_scaling
from finevision.ml_toolkit.datasets import scan_imagefolder
from finevision.ml_toolkit.features import DINOV3_MODEL_PRESETS, ColorStatsExtractor, build_extractor_from_config, extract_features
from finevision.ml_toolkit.thresholds import estimate_margin_threshold, select_threshold_strategy, sweep_confidence_thresholds
from finevision.ml_toolkit.training import train_linear_head


DEFAULT_METADATA_DIR = ".finevision-api/metadata"
DEFAULT_ARTIFACT_DIR = ".finevision-api/artifacts"


class JobStoreLike(Protocol):
    def claim_next_queued_job(self) -> JobRecord | None: ...
    def next_queued_job(self) -> JobRecord | None: ...
    def mark_running(self, job: JobRecord) -> JobRecord: ...
    def mark_succeeded(self, job: JobRecord, result: dict[str, Any]) -> JobRecord: ...
    def mark_failed(self, job: JobRecord, error: str) -> JobRecord: ...


class TrainingStoreLike(Protocol):
    def mark_running(self, run_id: str) -> None: ...
    def mark_failed(self, run_id: str, error: str) -> None: ...
    def update_progress(self, run_id: str, progress: dict[str, Any]) -> None: ...
    def find_feature_artifact(
        self,
        dataset_version_id: str,
        backbone_id: str,
        extractor_config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...
    def complete_training_run(self, **kwargs: Any) -> Any: ...


def run_next_job(metadata_dir: str | Path | None = None) -> JobRecord | None:
    metadata_store, job_store, training_store = _create_worker_stores(metadata_dir)
    running = _claim_job(job_store)
    if running is None:
        return None

    try:
        result = _run_job(running, metadata_store, training_store)
    except Exception as exc:  # The worker boundary persists failures for API inspection.
        return job_store.mark_failed(running, str(exc))
    return job_store.mark_succeeded(running, result)


def run_worker_loop(
    metadata_dir: str | Path | None = None,
    *,
    poll_interval_seconds: float = 2.0,
    max_jobs: int | None = None,
) -> int:
    completed = 0
    while max_jobs is None or completed < max_jobs:
        job = run_next_job(metadata_dir)
        if job is None:
            time.sleep(poll_interval_seconds)
            continue
        completed += 1
    return completed


def _run_job(job: JobRecord, metadata_store: MetadataStore, training_store: TrainingStoreLike | None) -> dict[str, Any]:
    if job.type == "import_imagefolder":
        return _run_import_imagefolder(job.payload, metadata_store)
    if job.type == "train_classifier":
        if training_store is None:
            raise ValueError("train_classifier jobs require DATABASE_URL-backed training store")
        return _run_train_classifier(job.payload, metadata_store, training_store)
    raise ValueError(f"Unsupported job type: {job.type}")


def _run_import_imagefolder(payload: dict[str, Any], metadata_store: MetadataStore) -> dict[str, Any]:
    root = Path(_required_payload_value(payload, "path"))
    dataset_id = _required_payload_value(payload, "dataset_id")
    dataset_version_id = _required_payload_value(payload, "dataset_version_id")
    manifest = scan_imagefolder(root, dataset_id, dataset_version_id)
    metadata_store.save_dataset_manifest(manifest)
    return {
        "dataset_id": manifest.dataset_id,
        "dataset_version_id": manifest.dataset_version_id,
        "sample_count": len(manifest.samples),
        "class_count": len(manifest.classes),
        "ready": manifest.readiness.get("ready") is True,
    }


def _required_payload_value(payload: dict[str, Any], field: str) -> str:
    value = str(payload.get(field, "")).strip()
    if not value:
        raise ValueError(f"Missing job payload field: {field}")
    return value


def _run_train_classifier(
    payload: dict[str, Any],
    metadata_store: MetadataStore,
    training_store: TrainingStoreLike,
) -> dict[str, Any]:
    run_id = _required_payload_value(payload, "training_run_id")
    dataset_version_id = _required_payload_value(payload, "dataset_version_id")
    manifest = metadata_store.get_dataset_version(dataset_version_id)
    if manifest is None:
        raise ValueError(f"Dataset version not found: {dataset_version_id}")

    training_store.mark_running(run_id)
    try:
        artifact_root = Path(os.environ.get("FINEVISION_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR)).resolve()
        _update_training_progress(training_store, run_id, "dataset", "running")
        extractor = _build_extractor(payload)
        _update_training_progress(training_store, run_id, "dataset", "completed")
        feature_artifact, features, reused_feature_cache = _load_or_extract_features(
            manifest,
            extractor,
            artifact_root,
            training_store,
            run_id=run_id,
        )
        if reused_feature_cache:
            _update_training_progress(training_store, run_id, "weights", "completed", note="feature cache reused")
            _update_training_progress(training_store, run_id, "features", "completed", note="feature cache reused")
        head_config = dict(payload.get("head_config") or {})
        ridge_lambda = float(head_config.get("ridge_lambda", 1e-2))
        _update_training_progress(training_store, run_id, "head", "running")
        model_artifact, training_report, logits = train_linear_head(
            feature_artifact,
            features,
            artifact_root / "models",
            run_id=run_id,
            ridge_lambda=ridge_lambda,
            artifact_id=f"{manifest.dataset_version_id}-{run_id}-linear-head",
            device=str(
                payload.get("head_device")
                or os.environ.get("FINEVISION_LINEAR_HEAD_DEVICE")
                or os.environ.get("FINEVISION_DINOV3_DEVICE", "cpu")
            ),
        )
        _update_training_progress(training_store, run_id, "head", "completed")
        _update_training_progress(training_store, run_id, "calibration", "running")
        calibration = fit_temperature_scaling(
            model_artifact,
            logits,
            feature_artifact.labels,
            feature_artifact.splits,
            artifact_root=artifact_root / "models",
        )
        _update_training_progress(training_store, run_id, "calibration", "completed")
        _update_training_progress(training_store, run_id, "thresholds", "running")
        threshold_sweep = sweep_confidence_thresholds(
            feature_artifact,
            model_artifact,
            logits,
            artifact_root=artifact_root / "models",
            calibration=calibration,
            review_cost_per_item=float(payload.get("review_cost_per_item", 1.0)),
        )
        margin_threshold, margin_config = estimate_margin_threshold(
            feature_artifact,
            model_artifact,
            logits,
            calibration=calibration,
            split=calibration.split,
        )
        threshold_strategy = select_threshold_strategy(
            threshold_sweep,
            model_artifact,
            calibration=calibration,
            margin_threshold=margin_threshold,
            target_selective_risk=float(payload.get("target_selective_risk", 0.01)),
            review_cost_per_item=float(payload.get("review_cost_per_item", 1.0)),
            selection_config=margin_config,
            artifact_root=artifact_root / "models",
        )
        _update_training_progress(training_store, run_id, "thresholds", "completed")
        record = training_store.complete_training_run(
            run_id=run_id,
            artifact_root=artifact_root,
            feature_artifact=feature_artifact,
            model_artifact=model_artifact,
            training_report=training_report,
            calibration_report=calibration,
            threshold_sweep=threshold_sweep,
            threshold_strategy=threshold_strategy,
        )
        _update_training_progress(training_store, run_id, "completed", "completed")
        return {
            "training_run_id": record.run_id,
            "dataset_id": record.dataset_id,
            "dataset_version_id": record.dataset_version_id,
            "feature_artifact_id": record.feature_artifact_id,
            "model_artifact_id": record.model_artifact_id,
            "model_version_id": record.model_version_id,
            "report_artifact_id": record.report_artifact_id,
            "calibration_artifact_id": record.calibration_artifact_id,
            "threshold_strategy_artifact_id": record.threshold_strategy_artifact_id,
            "metrics": record.metrics,
        }
    except Exception as exc:
        training_store.mark_failed(run_id, str(exc))
        raise


def _build_extractor(payload: dict[str, Any]):
    extractor_name = str(payload.get("extractor") or "color_stats")
    if extractor_name == "color_stats":
        return ColorStatsExtractor()
    if extractor_name in DINOV3_MODEL_PRESETS:
        config = dict(payload.get("extractor_config") or {"type": extractor_name})
        return build_extractor_from_config(
            config,
            overrides={
                "device": str(payload.get("device") or os.environ.get("FINEVISION_DINOV3_DEVICE", "cpu")),
                "batch_size": int(payload.get("batch_size") or os.environ.get("FINEVISION_DINOV3_BATCH_SIZE", "8")),
            },
        )
    raise ValueError(f"Unsupported extractor: {extractor_name}")


def _load_or_extract_features(
    manifest,
    extractor,
    artifact_root: Path,
    training_store: TrainingStoreLike,
    *,
    run_id: str,
):
    existing = training_store.find_feature_artifact(manifest.dataset_version_id, extractor.backbone_id, extractor.config)
    if existing is not None:
        metadata = existing.get("artifact_metadata") or {}
        feature_data = metadata.get("feature_artifact")
        uri = Path(str(existing.get("uri", "")))
        if feature_data and uri.exists():
            feature_artifact, features = load_feature_artifact(uri.parent)
            return feature_artifact, features, True

    prepare = getattr(extractor, "prepare", None)
    if prepare is not None:
        _update_training_progress(training_store, run_id, "weights", "running")
        prepare()
        _update_training_progress(training_store, run_id, "weights", "completed")
    else:
        _update_training_progress(training_store, run_id, "weights", "completed", note="no external weights")

    _update_training_progress(training_store, run_id, "features", "running")
    feature_artifact, features = extract_features(manifest, extractor, artifact_root / "features")
    _update_training_progress(training_store, run_id, "features", "completed")
    return feature_artifact, features, False


TRAINING_PROGRESS_STAGES: tuple[dict[str, Any], ...] = (
    {"id": "dataset", "label": "数据快照", "weight": 5},
    {"id": "weights", "label": "权重准备", "weight": 15},
    {"id": "features", "label": "特征提取", "weight": 45},
    {"id": "head", "label": "分类头训练", "weight": 15},
    {"id": "calibration", "label": "置信度校准", "weight": 10},
    {"id": "thresholds", "label": "阈值策略", "weight": 10},
)


def _update_training_progress(
    training_store: TrainingStoreLike,
    run_id: str,
    stage_id: str,
    status: str,
    *,
    note: str | None = None,
) -> None:
    stages = []
    seen_current = False
    overall = 0.0
    for stage in TRAINING_PROGRESS_STAGES:
        current = dict(stage)
        current_id = str(current["id"])
        if stage_id == "completed":
            current_status = "completed"
        elif current_id == stage_id:
            current_status = status
            seen_current = True
        elif seen_current:
            current_status = "pending"
        else:
            current_status = "completed"

        if current_status == "completed":
            percent = 100
            overall += float(current["weight"])
        elif current_status == "running":
            percent = 50
            overall += float(current["weight"]) * 0.5
        else:
            percent = 0

        current["status"] = current_status
        current["percent"] = percent
        if current_id == stage_id and note:
            current["note"] = note
        stages.append(current)

    current_stage = "completed" if stage_id == "completed" else stage_id
    training_store.update_progress(
        run_id,
        {
            "current_stage": current_stage,
            "overall_percent": round(min(100.0, overall), 1),
            "stages": stages,
        },
    )


def _create_worker_stores(metadata_dir: str | Path | None) -> tuple[MetadataStore, JobStoreLike, TrainingStoreLike | None]:
    if metadata_dir is not None:
        metadata_store, job_store = create_stores(metadata_dir=metadata_dir)
        return metadata_store, job_store, None

    database_url = os.environ.get("DATABASE_URL")
    metadata_root = os.environ.get("FINEVISION_METADATA_DIR", DEFAULT_METADATA_DIR)
    metadata_store, job_store = create_stores(metadata_dir=metadata_root, database_url=database_url)
    training_store = DatabaseTrainingStore(database_url) if database_url else None
    return metadata_store, job_store, training_store


def _claim_job(job_store: JobStoreLike) -> JobRecord | None:
    claim_next = getattr(job_store, "claim_next_queued_job", None)
    if claim_next is not None:
        return claim_next()

    job = job_store.next_queued_job()
    if job is None:
        return None
    return job_store.mark_running(job)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FineVision worker jobs.")
    parser.add_argument("--metadata-dir", default=None)
    parser.add_argument("--once", action="store_true", help="Execute at most one queued job.")
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    args = parser.parse_args()

    if args.once:
        job = run_next_job(args.metadata_dir)
        if job is None:
            print("No queued job.")
            return
        print(f"{job.job_id} {job.status}")
        return

    run_worker_loop(args.metadata_dir, poll_interval_seconds=args.poll_interval_seconds)


if __name__ == "__main__":
    main()
