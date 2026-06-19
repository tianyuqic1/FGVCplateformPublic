from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import numpy as np
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from finevision.api.inference_store import DatabaseInferenceStore, InferenceContext
from finevision.api.store import create_stores
from finevision.api.training_store import DatabaseTrainingStore
from finevision.ml_toolkit.datasets import scan_imagefolder
from finevision.ml_toolkit.features import build_extractor_from_config
from finevision.ml_toolkit.inference import run_image_inference, run_inference
from finevision.schemas.artifacts import InferenceResult, to_jsonable


class ImportImageFolderRequest(BaseModel):
    path: str = Field(..., min_length=1)
    dataset_id: str = Field(..., min_length=1)
    dataset_version_id: str = Field(..., min_length=1)


class CreateJobRequest(BaseModel):
    type: Literal["import_imagefolder", "train_classifier"]
    payload: dict[str, Any] = Field(default_factory=dict)


class CreateTrainingRunRequest(BaseModel):
    dataset_version_id: str = Field(..., min_length=1)
    backbone_id: str | None = None
    extractor: Literal["color_stats", "dinov3_vitl"] = "color_stats"
    head_config: dict[str, Any] = Field(default_factory=lambda: {"head_type": "ridge_linear", "ridge_lambda": 1e-2})
    target_selective_risk: float = Field(default=0.01, ge=0.0, le=1.0)
    review_cost_per_item: float = Field(default=1.0, ge=0.0)


class RunInferenceRequest(BaseModel):
    dataset_version_id: str = Field(..., min_length=1)
    model_version_id: str = Field(..., min_length=1)
    image_path: str | None = Field(default=None, min_length=1)
    sample_id: str | None = Field(default=None, min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)
    evidence_k: int = Field(default=3, ge=0, le=10)
    accept_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    margin_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    ood_distance_threshold: float | None = Field(default=None, ge=0.0)


def create_app(metadata_dir: str | Path | None = None, database_url: str | None = None) -> FastAPI:
    resolved_database_url: str | None = None
    if database_url is not None:
        resolved_database_url = database_url
        store, job_store = create_stores(database_url=database_url)
    elif metadata_dir is not None:
        store, job_store = create_stores(metadata_dir=metadata_dir)
    else:
        resolved_database_url = os.environ.get("DATABASE_URL")
        resolved_metadata_dir = os.environ.get("FINEVISION_METADATA_DIR", ".finevision-api/metadata")
        store, job_store = create_stores(metadata_dir=resolved_metadata_dir, database_url=resolved_database_url)
    training_store = DatabaseTrainingStore(resolved_database_url) if resolved_database_url else None
    api = FastAPI(title="FineVision Control Plane API", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api.state.metadata_store = store
    api.state.job_store = job_store
    api.state.training_store = training_store
    api.state.inference_store = DatabaseInferenceStore(resolved_database_url) if resolved_database_url else None

    @api.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/api/datasets")
    def list_datasets() -> dict[str, list[dict[str, object]]]:
        return {"datasets": [summary.__dict__ for summary in store.list_datasets()]}

    @api.get("/api/datasets/{dataset_id}")
    def get_dataset(dataset_id: str) -> dict[str, object]:
        detail = store.dataset_detail(dataset_id)
        if detail is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
        return detail

    @api.post("/api/datasets/import-imagefolder", status_code=status.HTTP_201_CREATED)
    def import_imagefolder(request: ImportImageFolderRequest) -> dict[str, object]:
        root = Path(request.path)
        if not root.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ImageFolder path does not exist")
        if not root.is_dir():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ImageFolder path must be a directory")

        manifest = scan_imagefolder(root, request.dataset_id, request.dataset_version_id)
        store.save_dataset_manifest(manifest)
        return {
            "dataset": store.dataset_detail(manifest.dataset_id),
            "version": store.version_summary(manifest),
        }

    @api.post("/api/jobs", status_code=status.HTTP_202_ACCEPTED)
    def create_job(request: CreateJobRequest) -> dict[str, object]:
        if request.type == "train_classifier":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Create training jobs through POST /api/training-runs",
            )
        payload = _validate_job_payload(request.type, request.payload)
        job = job_store.create_job(request.type, payload)
        return {"job": job.__dict__}

    @api.get("/api/jobs")
    def list_jobs() -> dict[str, list[dict[str, object]]]:
        return {"jobs": [job.__dict__ for job in job_store.list_jobs()]}

    @api.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
        job = job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return {"job": job.__dict__}

    @api.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, object]:
        job = job_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        if job.status not in {"queued"}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only queued jobs can be cancelled")
        cancelled_job = job_store.cancel_job(job)
        if job.type == "train_classifier" and training_store is not None:
            try:
                training_store.mark_cancelled_by_job(job_id, "Training job was cancelled before worker execution.")
            except ValueError:
                pass
        return {"job": cancelled_job.__dict__}

    @api.get("/api/dataset-versions/{dataset_version_id}/readiness")
    def get_dataset_version_readiness(dataset_version_id: str) -> dict[str, object]:
        manifest = store.get_dataset_version(dataset_version_id)
        if manifest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset version not found")
        return {
            "dataset_id": manifest.dataset_id,
            "dataset_version_id": manifest.dataset_version_id,
            "readiness": manifest.readiness,
        }

    @api.post("/api/training-runs", status_code=status.HTTP_202_ACCEPTED)
    def create_training_run(request: CreateTrainingRunRequest) -> dict[str, object]:
        if training_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Training runs require DATABASE_URL-backed persistence",
            )
        manifest = store.get_dataset_version(request.dataset_version_id)
        if manifest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset version not found")
        if manifest.readiness.get("ready") is not True:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Dataset version is not ready for training",
                    "readiness": manifest.readiness,
                },
            )

        backbone_id = request.backbone_id or _default_backbone_id(request.extractor)
        _validate_training_config(request.extractor, backbone_id, request.head_config)

        run_id = f"run-{uuid4().hex[:12]}"
        payload = {
            "training_run_id": run_id,
            "dataset_id": manifest.dataset_id,
            "dataset_version_id": manifest.dataset_version_id,
            "backbone_id": backbone_id,
            "extractor": request.extractor,
            "extractor_config": _extractor_config(request.extractor, backbone_id),
            "head_config": request.head_config,
            "target_selective_risk": request.target_selective_risk,
            "review_cost_per_item": request.review_cost_per_item,
        }
        job = job_store.create_job("train_classifier", payload)
        try:
            training_run = training_store.create_training_run(job=job, payload=payload)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"training_run": training_run.__dict__, "job": job.__dict__}

    @api.get("/api/training-runs")
    def list_training_runs() -> dict[str, list[dict[str, object]]]:
        if training_store is None:
            return {"training_runs": []}
        return {"training_runs": [run.__dict__ for run in training_store.list_training_runs()]}

    @api.get("/api/training-runs/{run_id}")
    def get_training_run(run_id: str) -> dict[str, object]:
        if training_store is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training run not found")
        training_run = training_store.get_training_run(run_id)
        if training_run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training run not found")
        return {"training_run": training_run.__dict__}

    @api.post("/api/inference")
    def run_scoped_inference(request: RunInferenceRequest) -> dict[str, object]:
        inference_store: DatabaseInferenceStore | None = api.state.inference_store
        if inference_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Inference requires DATABASE_URL-backed model metadata",
            )
        if not request.image_path and not request.sample_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Either image_path or sample_id is required",
            )

        context = inference_store.load_context(
            dataset_version_id=request.dataset_version_id,
            model_version_id=request.model_version_id,
        )
        if context is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model version not found for dataset version")

        strategy = context.threshold_strategy
        if request.accept_threshold is not None or request.margin_threshold is not None:
            strategy = replace(
                strategy,
                accept_threshold=request.accept_threshold
                if request.accept_threshold is not None
                else strategy.accept_threshold,
                margin_threshold=request.margin_threshold
                if request.margin_threshold is not None
                else strategy.margin_threshold,
            )

        try:
            result = _run_inference_from_context(
                context=context,
                request=request,
                strategy=strategy,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

        payload = {
            "dataset_id": context.dataset_id,
            "dataset_version_id": context.dataset_version_id,
            "model_version_id": context.model_version_id,
            "model_status": context.model_status,
            "model_artifact_id": context.model_artifact.artifact_id,
            "feature_artifact_id": context.feature_artifact.artifact_id,
            "threshold_strategy_id": result.threshold_strategy_id,
            "input": {
                "image_path": request.image_path,
                "sample_id": request.sample_id,
            },
            "result": to_jsonable(result),
        }
        return {"inference_result": payload}

    return api


def _validate_job_payload(job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if job_type == "import_imagefolder":
        return _validate_import_imagefolder_payload(payload)
    if job_type == "train_classifier":
        return _validate_train_classifier_payload(payload)
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unsupported job type: {job_type}")


def _validate_import_imagefolder_payload(payload: dict[str, Any]) -> dict[str, str]:
    required = ("path", "dataset_id", "dataset_version_id")
    missing = [field for field in required if not str(payload.get(field, "")).strip()]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing import_imagefolder payload fields: {', '.join(missing)}",
        )
    return {field: str(payload[field]) for field in required}


def _validate_train_classifier_payload(payload: dict[str, Any]) -> dict[str, Any]:
    required = ("training_run_id", "dataset_version_id")
    missing = [field for field in required if not str(payload.get(field, "")).strip()]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing train_classifier payload fields: {', '.join(missing)}",
        )
    return {
        **payload,
        "training_run_id": str(payload["training_run_id"]),
        "dataset_version_id": str(payload["dataset_version_id"]),
    }


def _run_inference_from_context(
    *,
    context: InferenceContext,
    request: RunInferenceRequest,
    strategy: Any,
) -> InferenceResult:
    if request.sample_id:
        try:
            sample_index = context.feature_artifact.sample_ids.index(request.sample_id)
        except ValueError as exc:
            raise ValueError(f"Sample id not found in feature artifact: {request.sample_id}") from exc
        query_features = np.asarray(context.features[sample_index], dtype=np.float32)
        return run_inference(
            context.model_artifact,
            context.model_state,
            strategy,
            query_features,
            context.features,
            context.feature_artifact.sample_ids,
            context.feature_artifact.labels,
            ood_distance_threshold=request.ood_distance_threshold,
            top_k=request.top_k,
            evidence_k=request.evidence_k,
            exclude_sample_ids={request.sample_id},
        )

    image_path = Path(str(request.image_path)).expanduser()
    if not image_path.exists():
        raise FileNotFoundError(f"Image path not found: {image_path}")
    if not image_path.is_file():
        raise ValueError(f"Image path must be a file: {image_path}")

    extractor = build_extractor_from_config(context.feature_artifact.extractor_config)
    return run_image_inference(
        image_path=str(image_path),
        extractor=extractor,
        model_artifact=context.model_artifact,
        model_state=context.model_state,
        threshold_strategy=strategy,
        reference_features=context.features,
        reference_sample_ids=context.feature_artifact.sample_ids,
        reference_labels=context.feature_artifact.labels,
        ood_distance_threshold=request.ood_distance_threshold,
        top_k=request.top_k,
        evidence_k=request.evidence_k,
    )


def _default_backbone_id(extractor: str) -> str:
    if extractor == "dinov3_vitl":
        return "dinov3_vitl16"
    return "color_stats_v1"


def _extractor_config(extractor: str, backbone_id: str) -> dict[str, Any]:
    if extractor == "dinov3_vitl":
        return {
            "type": "timm_dinov3",
            "model_name": "vit_large_patch16_dinov3.lvd1689m",
            "pretrained": True,
            "backbone_id": backbone_id,
        }
    return {"type": "color_stats", "bins": 8, "backbone_id": backbone_id}


def _validate_training_config(extractor: str, backbone_id: str, head_config: dict[str, Any]) -> None:
    expected_backbone = _default_backbone_id(extractor)
    if backbone_id != expected_backbone:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"backbone_id must be {expected_backbone} for extractor {extractor}",
        )
    if head_config.get("head_type", "ridge_linear") != "ridge_linear":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only ridge_linear head_config.head_type is supported",
        )
    try:
        ridge_lambda = float(head_config.get("ridge_lambda", 1e-2))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="head_config.ridge_lambda must be a positive number",
        ) from exc
    if ridge_lambda <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="head_config.ridge_lambda must be greater than 0",
        )


app = create_app()
