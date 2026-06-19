from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from finevision.api.store import create_stores
from finevision.api.training_store import DatabaseTrainingStore
from finevision.ml_toolkit.datasets import scan_imagefolder


class ImportImageFolderRequest(BaseModel):
    path: str = Field(..., min_length=1)
    dataset_id: str = Field(..., min_length=1)
    dataset_version_id: str = Field(..., min_length=1)


class CreateJobRequest(BaseModel):
    type: Literal["import_imagefolder", "train_classifier"]
    payload: dict[str, Any] = Field(default_factory=dict)


class CreateTrainingRunRequest(BaseModel):
    dataset_version_id: str = Field(..., min_length=1)
    backbone_id: str = "color_stats_v1"
    extractor: Literal["color_stats", "dinov3_vitl"] = "color_stats"
    head_config: dict[str, Any] = Field(default_factory=lambda: {"head_type": "ridge_linear", "ridge_lambda": 1e-2})
    target_selective_risk: float = 0.01
    review_cost_per_item: float = 1.0


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
        return {"job": job_store.cancel_job(job).__dict__}

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

        run_id = f"run-{uuid4().hex[:12]}"
        payload = {
            "training_run_id": run_id,
            "dataset_id": manifest.dataset_id,
            "dataset_version_id": manifest.dataset_version_id,
            "backbone_id": request.backbone_id,
            "extractor": request.extractor,
            "extractor_config": {"type": request.extractor, "backbone_id": request.backbone_id},
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


app = create_app()
