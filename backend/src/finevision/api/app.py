from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
from typing import Any, Literal
from uuid import uuid4

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from starlette.datastructures import UploadFile as StarletteUploadFile

from finevision.api.abstention_store import AbstentionPolicyGateError, DatabaseAbstentionStore, InsufficientFeedbackError
from finevision.api.inference_store import DatabaseInferenceStore, InferenceContext
from finevision.api.llm import (
    LLMConfigurationError,
    LLMRequestError,
    generate_assistance,
    generate_dataset_card as generate_dataset_card_from_llm,
)
from finevision.api.review_store import DatabaseReviewStore, FeedbackItemRecord
from finevision.api.store import create_stores
from finevision.api.training_store import DatabaseTrainingStore
from finevision.ml_toolkit.datasets import EXPLICIT_SPLITS, IMAGE_EXTENSIONS, scan_imagefolder
from finevision.ml_toolkit.features import (
    DINOV3_MODEL_PRESETS,
    build_extractor_from_config,
    delete_dinov3_weight_cache,
    dinov3_extractor_config,
    inspect_dinov3_weight_cache,
)
from finevision.ml_toolkit.inference import run_image_inference, run_inference
from finevision.schemas.artifacts import InferenceResult, to_jsonable

MAX_DATASET_UPLOAD_FILES = 1_000_000


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
    extractor: Literal["color_stats", "dinov3_vits", "dinov3_vitb", "dinov3_vitl"] = "color_stats"
    feature_batch_size: int | None = Field(default=None, ge=1, le=128)
    image_size: int | None = Field(default=None, ge=128, le=1024)
    feature_pool: Literal["cls", "model"] = "cls"
    head_config: dict[str, Any] = Field(
        default_factory=lambda: {
            "head_type": "torch_linear_adam",
            "learning_rate": 1e-3,
            "epochs": 100,
            "batch_size": 256,
            "weight_decay": 1e-4,
        }
    )
    target_selective_risk: float = Field(default=0.01, ge=0.0, le=1.0)
    review_cost_per_item: float = Field(default=1.0, ge=0.0)


class RunInferenceRequest(BaseModel):
    dataset_version_id: str = Field(..., min_length=1)
    model_version_id: str = Field(..., min_length=1)
    inference_run_id: str | None = Field(default=None, min_length=1)
    image_path: str | None = Field(default=None, min_length=1)
    sample_id: str | None = Field(default=None, min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)
    evidence_k: int = Field(default=3, ge=0, le=10)
    accept_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    margin_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    ood_distance_threshold: float | None = Field(default=None, ge=0.0)
    route_to_review: bool = True


class SubmitReviewRequest(BaseModel):
    final_outcome: Literal["confirmed_label", "corrected_label", "ood", "bad_image", "uncertain", "ignore"]
    destination: Literal["training_candidate", "ood_stress", "bad_image", "taxonomy_dispute", "ignore"]
    final_label: str | None = Field(default=None)
    reviewer_note: str | None = Field(default=None)
    reviewer: str | None = Field(default="local-reviewer")


class LLMAssistanceRequest(BaseModel):
    task: Literal["inference_explanation", "review_assistance", "training_diagnosis", "feedback_curation"]
    context: dict[str, Any] = Field(default_factory=dict)


class ReviewAssistanceRequest(BaseModel):
    question: str | None = Field(default=None, max_length=1200)


class DatasetCardRequest(BaseModel):
    dataset_card: dict[str, Any] = Field(default_factory=dict)


class ProposeAbstentionPolicyRequest(BaseModel):
    dataset_version_id: str = Field(..., min_length=1)
    model_version_id: str = Field(..., min_length=1)
    target_selective_risk: float = Field(default=0.05, ge=0.0, le=1.0)
    review_cost_per_item: float = Field(default=1.0, ge=0.0)
    created_by: str | None = Field(default="local-operator")


class ActivateAbstentionPolicyRequest(BaseModel):
    activated_by: str | None = Field(default="local-operator")
    activation_reason: str = Field(..., min_length=1, max_length=1200)
    min_feedback_count: int = Field(default=5, ge=1, le=100000)


class DeactivateAbstentionPolicyRequest(BaseModel):
    deactivated_by: str | None = Field(default="local-operator")
    deactivation_reason: str = Field(..., min_length=1, max_length=1200)


def create_app(metadata_dir: str | Path | None = None, database_url: str | None = None) -> FastAPI:
    resolved_database_url: str | None = None
    database_engine: Engine | None = None
    if database_url is not None:
        resolved_database_url = database_url
        database_engine = create_engine(database_url, poolclass=NullPool)
        store, job_store = create_stores(database_url=database_engine)
    elif metadata_dir is not None:
        store, job_store = create_stores(metadata_dir=metadata_dir)
    else:
        resolved_database_url = os.environ.get("DATABASE_URL")
        resolved_metadata_dir = os.environ.get("FINEVISION_METADATA_DIR", ".finevision-api/metadata")
        if resolved_database_url:
            database_engine = create_engine(resolved_database_url, poolclass=NullPool)
        store, job_store = create_stores(metadata_dir=resolved_metadata_dir, database_url=database_engine)
    training_store = DatabaseTrainingStore(database_engine) if database_engine is not None else None
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
    api.state.inference_store = DatabaseInferenceStore(database_engine) if database_engine is not None else None
    api.state.review_store = DatabaseReviewStore(database_engine) if database_engine is not None else None
    api.state.abstention_store = DatabaseAbstentionStore(database_engine) if database_engine is not None else None
    api.state.upload_dir = Path(os.environ.get("FINEVISION_UPLOAD_DIR", ".finevision-api/uploads"))
    api.state.upload_dir.mkdir(parents=True, exist_ok=True)
    api.state.imported_dataset_dir = _default_imported_dataset_dir()
    api.mount("/api/uploads", StaticFiles(directory=str(api.state.upload_dir)), name="uploads")
    sample_asset_dir = Path(os.environ.get("FINEVISION_SAMPLE_ASSET_DIR", "/app/data/test/cifar10-mini-imagefolder"))
    if not sample_asset_dir.exists():
        sample_asset_dir = Path("data/test/cifar10-mini-imagefolder")
    if sample_asset_dir.exists():
        api.mount("/api/sample-assets", StaticFiles(directory=str(sample_asset_dir)), name="sample-assets")

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

    @api.post("/api/datasets/upload-imagefolder", status_code=status.HTTP_201_CREATED)
    async def upload_imagefolder(
        request: Request,
    ) -> dict[str, object]:
        form = await request.form(max_files=MAX_DATASET_UPLOAD_FILES, max_fields=20)
        dataset_id = str(form.get("dataset_id", "")).strip()
        dataset_version_id = str(form.get("dataset_version_id", "")).strip()
        if not dataset_id or not dataset_version_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="dataset_id and dataset_version_id are required",
            )
        files = [value for _, value in form.multi_items() if isinstance(value, StarletteUploadFile)]
        normalized_files, validation = _validate_uploaded_imagefolder(files)
        dataset_dir = _uploaded_dataset_destination(
            api.state.imported_dataset_dir,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
        )
        temporary_dir = dataset_dir.parent / f".{dataset_dir.name}.upload-{uuid4().hex[:8]}"
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)
        temporary_dir.mkdir(parents=True, exist_ok=True)
        try:
            for upload_file, relative_path in normalized_files:
                destination = temporary_dir / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as output:
                    shutil.copyfileobj(upload_file.file, output)
            manifest = scan_imagefolder(temporary_dir, dataset_id, dataset_version_id)
            if dataset_dir.exists():
                shutil.rmtree(dataset_dir)
            dataset_dir.parent.mkdir(parents=True, exist_ok=True)
            temporary_dir.rename(dataset_dir)
            manifest = scan_imagefolder(dataset_dir, dataset_id, dataset_version_id)
            store.save_dataset_manifest(manifest)
            return {
                "dataset": store.dataset_detail(manifest.dataset_id),
                "version": store.version_summary(manifest),
                "upload": {
                    **validation,
                    "stored_path": str(dataset_dir),
                },
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        finally:
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir)

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

    @api.get("/api/dataset-versions/{dataset_version_id}/sample-previews")
    def get_dataset_version_sample_previews(dataset_version_id: str, limit: int = Query(default=6, ge=1, le=24)) -> dict[str, object]:
        manifest = store.get_dataset_version(dataset_version_id)
        if manifest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset version not found")
        return {
            "dataset_id": manifest.dataset_id,
            "dataset_version_id": manifest.dataset_version_id,
            "samples": _sample_preview_payloads(manifest, limit=limit),
        }

    @api.get("/api/dataset-versions/{dataset_version_id}/samples/{sample_id}/image")
    def get_dataset_version_sample_image(dataset_version_id: str, sample_id: str) -> FileResponse:
        manifest = store.get_dataset_version(dataset_version_id)
        if manifest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset version not found")
        sample = next((item for item in manifest.samples if item.sample_id == sample_id), None)
        if sample is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample not found")
        image_path = Path(sample.path)
        if not image_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample image file not found")
        return FileResponse(image_path)

    @api.get("/api/dataset-versions/{dataset_version_id}/card")
    def get_dataset_version_card(dataset_version_id: str) -> dict[str, object]:
        card = store.get_dataset_card(dataset_version_id)
        manifest = store.get_dataset_version(dataset_version_id)
        if card is None or manifest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset version not found")
        return {
            "dataset_id": manifest.dataset_id,
            "dataset_version_id": manifest.dataset_version_id,
            "dataset_card": card,
        }

    @api.put("/api/dataset-versions/{dataset_version_id}/card")
    def update_dataset_version_card(dataset_version_id: str, request: DatasetCardRequest) -> dict[str, object]:
        card = store.update_dataset_card(dataset_version_id, request.dataset_card)
        manifest = store.get_dataset_version(dataset_version_id)
        if card is None or manifest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset version not found")
        return {
            "dataset_id": manifest.dataset_id,
            "dataset_version_id": manifest.dataset_version_id,
            "dataset_card": card,
        }

    @api.post("/api/dataset-versions/{dataset_version_id}/card/generate")
    def generate_dataset_version_card(dataset_version_id: str) -> dict[str, object]:
        manifest = store.get_dataset_version(dataset_version_id)
        if manifest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset version not found")
        existing_card = store.get_dataset_card(dataset_version_id) or {}
        try:
            generated = generate_dataset_card_from_llm(manifest=manifest, existing_card=existing_card)
        except LLMConfigurationError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except LLMRequestError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        card = store.update_dataset_card(dataset_version_id, generated)
        if card is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset version not found")
        return {
            "dataset_id": manifest.dataset_id,
            "dataset_version_id": manifest.dataset_version_id,
            "dataset_card": card,
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
        feature_batch_size = request.feature_batch_size or 8
        image_size = request.image_size or (448 if request.extractor in DINOV3_MODEL_PRESETS else None)
        if image_size is not None and image_size % 16 != 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="image_size must be divisible by 16 for DINOv3 patch16 backbones",
            )

        run_id = f"run-{uuid4().hex[:12]}"
        payload = {
            "training_run_id": run_id,
            "dataset_id": manifest.dataset_id,
            "dataset_version_id": manifest.dataset_version_id,
            "backbone_id": backbone_id,
            "extractor": request.extractor,
            "extractor_config": _extractor_config(
                request.extractor,
                backbone_id,
                feature_batch_size,
                image_size,
                request.feature_pool,
            ),
            "batch_size": feature_batch_size,
            "image_size": image_size,
            "feature_pool": request.feature_pool if request.extractor in DINOV3_MODEL_PRESETS else None,
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

    @api.post("/api/training-runs/{run_id}/pause")
    def pause_training_run(run_id: str) -> dict[str, object]:
        training_run = _get_training_run_or_404(training_store, run_id)
        if training_run.status not in {"queued", "running"}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only queued or running training runs can be paused")
        try:
            paused = training_store.pause_training_run(run_id)  # type: ignore[union-attr]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {"training_run": paused.__dict__}

    @api.post("/api/training-runs/{run_id}/resume")
    def resume_training_run(run_id: str) -> dict[str, object]:
        training_run = _get_training_run_or_404(training_store, run_id)
        if training_run.status != "paused":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only paused training runs can be resumed")
        try:
            resumed = training_store.resume_training_run(run_id)  # type: ignore[union-attr]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {"training_run": resumed.__dict__}

    @api.post("/api/training-runs/{run_id}/cancel")
    def cancel_training_run(run_id: str) -> dict[str, object]:
        training_run = _get_training_run_or_404(training_store, run_id)
        if training_run.status not in {"queued", "paused", "running"}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only queued, paused, or running training runs can be cancelled")
        try:
            cancelled = training_store.cancel_training_run(run_id, "Cancelled by user from training queue.")  # type: ignore[union-attr]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {"training_run": cancelled.__dict__}

    @api.get("/api/model-weights")
    def list_model_weights() -> dict[str, object]:
        return inspect_dinov3_weight_cache()

    @api.delete("/api/model-weights/{preset}")
    def delete_model_weight(preset: str) -> dict[str, object]:
        try:
            return delete_dinov3_weight_cache(preset)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @api.delete("/api/training-runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_training_run(run_id: str) -> None:
        _get_training_run_or_404(training_store, run_id)
        try:
            training_store.delete_training_run(run_id)  # type: ignore[union-attr]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @api.post("/api/inference")
    def run_scoped_inference(request: RunInferenceRequest) -> dict[str, object]:
        payload = _run_scoped_inference_payload(api, request)
        inference_run_id = request.inference_run_id or _create_inference_run(api, request, run_type="single")
        _attach_inference_run_payload(payload, inference_run_id)
        _record_review_route(api, request, payload, inference_run_id=inference_run_id)
        _finish_inference_run(api, inference_run_id, summary={"total": 1, "succeeded": 1, "failed": 0}, first_result_payload=payload)
        return {"inference_result": payload}

    @api.post("/api/inference/upload")
    def run_uploaded_image_inference(
        dataset_version_id: str = Form(..., min_length=1),
        model_version_id: str = Form(..., min_length=1),
        image: UploadFile = File(...),
        top_k: int = Form(default=3, ge=1, le=10),
        evidence_k: int = Form(default=3, ge=0, le=10),
        accept_threshold: float | None = Form(default=None, ge=0.0, le=1.0),
        margin_threshold: float | None = Form(default=None, ge=0.0, le=1.0),
        ood_distance_threshold: float | None = Form(default=None, ge=0.0),
    ) -> dict[str, object]:
        uploaded_path = _save_uploaded_image(api.state.upload_dir, image)
        request = RunInferenceRequest(
            dataset_version_id=dataset_version_id,
            model_version_id=model_version_id,
            image_path=str(uploaded_path),
            sample_id=None,
            top_k=top_k,
            evidence_k=evidence_k,
            accept_threshold=accept_threshold,
            margin_threshold=margin_threshold,
            ood_distance_threshold=ood_distance_threshold,
        )
        payload = _run_scoped_inference_payload(
            api,
            request,
            input_overrides={
                "upload_filename": image.filename,
                "uploaded_image_path": str(uploaded_path),
            },
        )
        inference_run_id = _create_inference_run(
            api,
            request,
            run_type="upload",
            request_payload={"upload_filename": image.filename},
        )
        _attach_inference_run_payload(payload, inference_run_id)
        _record_review_route(api, request, payload, inference_run_id=inference_run_id)
        _finish_inference_run(api, inference_run_id, summary={"total": 1, "succeeded": 1, "failed": 0}, first_result_payload=payload)
        return {"inference_result": payload}

    @api.post("/api/inference/upload-folder")
    def run_uploaded_folder_inference(
        dataset_version_id: str = Form(..., min_length=1),
        model_version_id: str = Form(..., min_length=1),
        images: list[UploadFile] = File(...),
        top_k: int = Form(default=3, ge=1, le=10),
        evidence_k: int = Form(default=3, ge=0, le=10),
        accept_threshold: float | None = Form(default=None, ge=0.0, le=1.0),
        margin_threshold: float | None = Form(default=None, ge=0.0, le=1.0),
        ood_distance_threshold: float | None = Form(default=None, ge=0.0),
        route_all_to_review: bool = Form(default=True),
    ) -> dict[str, object]:
        if not images:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one image is required")
        results: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        review_item_ids: list[str] = []
        batch_run_id = _create_inference_run(
            api,
            RunInferenceRequest(
                dataset_version_id=dataset_version_id,
                model_version_id=model_version_id,
                top_k=top_k,
                evidence_k=evidence_k,
                accept_threshold=accept_threshold,
                margin_threshold=margin_threshold,
                ood_distance_threshold=ood_distance_threshold,
            ),
            run_type="upload_folder",
            request_payload={
                "total": len(images),
                "route_all_to_review": route_all_to_review,
            },
        )
        first_successful_payload: dict[str, Any] | None = None
        for image in images:
            try:
                uploaded_path = _save_uploaded_image(api.state.upload_dir, image)
                request = RunInferenceRequest(
                    dataset_version_id=dataset_version_id,
                    model_version_id=model_version_id,
                    image_path=str(uploaded_path),
                    sample_id=None,
                    top_k=top_k,
                    evidence_k=evidence_k,
                    accept_threshold=accept_threshold,
                    margin_threshold=margin_threshold,
                    ood_distance_threshold=ood_distance_threshold,
                )
                payload = _run_scoped_inference_payload(
                    api,
                    request,
                    input_overrides={
                        "upload_filename": image.filename,
                        "uploaded_image_path": str(uploaded_path),
                        "batch_upload": True,
                    },
                )
                _attach_inference_run_payload(payload, batch_run_id)
                _record_review_route(api, request, payload, force_review=route_all_to_review)
                if first_successful_payload is None:
                    first_successful_payload = payload
                if payload.get("review_item_id"):
                    review_item_ids.append(str(payload["review_item_id"]))
                results.append(payload)
            except HTTPException as exc:
                failures.append({"filename": image.filename or "unknown", "error": str(exc.detail)})
            except Exception as exc:
                failures.append({"filename": image.filename or "unknown", "error": str(exc)})
        batch_summary = {
            "inference_run_id": batch_run_id,
            "batch_inference_id": batch_run_id,
            "batch_id": batch_run_id,
            "total": len(images),
            "succeeded": len(results),
            "failed": len(failures),
            "review_item_count": len(review_item_ids),
            "review_item_ids": review_item_ids,
            "route_all_to_review": route_all_to_review,
        }
        _finish_inference_run(
            api,
            batch_run_id,
            summary={**batch_summary, "failures": failures[:20]},
            first_result_payload=first_successful_payload,
        )
        return {
            "batch": batch_summary,
            "results": results,
            "failures": failures,
        }

    @api.get("/api/review-items")
    def list_review_items(
        status_filter: str | None = Query(default="pending", alias="status"),
        dataset_id: str | None = Query(default=None),
        limit: int = 50,
    ) -> dict[str, object]:
        review_store: DatabaseReviewStore | None = api.state.review_store
        if review_store is None:
            return {"review_items": []}
        allowed_statuses = {None, "", "all", "pending", "submitted", "feedbacked", "skipped", "disputed"}
        if status_filter not in allowed_statuses:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported review status filter")
        normalized_status = None if status_filter in {None, "", "all"} else status_filter
        return {
            "review_items": [
                _review_item_payload(item)
                for item in review_store.list_review_items(
                    status=normalized_status,
                    dataset_id=dataset_id,
                    limit=max(1, min(limit, 200)),
                )
            ]
        }

    @api.get("/api/review-items/{review_item_id}")
    def get_review_item(review_item_id: str) -> dict[str, object]:
        review_store: DatabaseReviewStore | None = api.state.review_store
        if review_store is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")
        review_item = review_store.get_review_item(review_item_id)
        if review_item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")
        return {"review_item": _review_item_payload(review_item)}

    @api.post("/api/review-items/{review_item_id}/submit")
    def submit_review_item(review_item_id: str, request: SubmitReviewRequest) -> dict[str, object]:
        review_store: DatabaseReviewStore | None = api.state.review_store
        if review_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Review workflow requires DATABASE_URL-backed persistence",
            )
        try:
            review_item, feedback_item = review_store.complete_review(
                review_id=review_item_id,
                final_outcome=request.final_outcome,
                destination=request.destination,
                final_label=request.final_label,
                reviewer_note=request.reviewer_note,
                reviewer=request.reviewer,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return {
            "review_item": _review_item_payload(review_item),
            "feedback_item": _feedback_item_payload(feedback_item, review_item_id=review_item.review_item_id),
        }

    @api.post("/api/review-items/{review_item_id}/assist")
    def generate_review_item_assistance(review_item_id: str, request: ReviewAssistanceRequest | None = None) -> dict[str, object]:
        review_store: DatabaseReviewStore | None = api.state.review_store
        if review_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Review assistance requires DATABASE_URL-backed persistence",
            )
        review_item = review_store.get_review_item(review_item_id)
        if review_item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")
        if review_item.status != "pending":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="LLM assistance is only generated for pending review items")
        context = _with_dataset_summary(store, _review_assistance_context(review_item, question=request.question if request else None))
        assistance = _call_llm_assistant(task="review_assistance", context=context)
        try:
            updated = review_store.update_review_assistance(review_id=review_item_id, assistance=assistance)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {
            "assistance": assistance,
            "review_item": _review_item_payload(updated),
        }

    @api.post("/api/llm/assist")
    def generate_llm_assistance(request: LLMAssistanceRequest) -> dict[str, object]:
        context = _with_dataset_summary(store, _sanitize_llm_context(request.context))
        return {"assistance": _call_llm_assistant(task=request.task, context=context)}

    @api.get("/api/feedback-items")
    def list_feedback_items(
        destination: str | None = Query(default=None),
        dataset_id: str | None = Query(default=None),
        limit: int = 100,
    ) -> dict[str, object]:
        review_store: DatabaseReviewStore | None = api.state.review_store
        if review_store is None:
            return {"feedback_items": []}
        allowed_destinations = {None, "", "all", "training_candidate", "ood_stress", "bad_image", "taxonomy_dispute", "ignore"}
        if destination not in allowed_destinations:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported feedback destination filter")
        normalized_destination = None if destination in {None, "", "all"} else destination
        return {
            "feedback_items": [
                _feedback_item_payload(item)
                for item in review_store.list_feedback_items(
                    destination=normalized_destination,
                    dataset_id=dataset_id,
                    limit=max(1, min(limit, 300)),
                )
            ]
        }

    @api.post("/api/abstention-policies/propose", status_code=status.HTTP_201_CREATED)
    def propose_abstention_policy(request: ProposeAbstentionPolicyRequest) -> dict[str, object]:
        abstention_store: DatabaseAbstentionStore | None = api.state.abstention_store
        if abstention_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Online abstention policy proposals require DATABASE_URL-backed persistence",
            )
        try:
            policy = abstention_store.propose_policy(
                dataset_version_id=request.dataset_version_id,
                model_version_id=request.model_version_id,
                target_selective_risk=request.target_selective_risk,
                review_cost_per_item=request.review_cost_per_item,
                created_by=request.created_by,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except InsufficientFeedbackError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {"policy": _abstention_policy_payload(policy)}

    @api.get("/api/abstention-policies")
    def list_abstention_policies(
        dataset_version_id: str | None = Query(default=None),
        model_version_id: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        limit: int = 50,
    ) -> dict[str, object]:
        abstention_store: DatabaseAbstentionStore | None = api.state.abstention_store
        if abstention_store is None:
            return {"policies": []}
        allowed_statuses = {None, "", "all", "shadow", "candidate", "active", "superseded", "deactivated", "archived"}
        if status_filter not in allowed_statuses:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported abstention policy status")
        normalized_status = None if status_filter in {None, "", "all"} else status_filter
        return {
            "policies": [
                _abstention_policy_payload(policy)
                for policy in abstention_store.list_policies(
                    dataset_version_id=dataset_version_id,
                    model_version_id=model_version_id,
                    status=normalized_status,
                    limit=max(1, min(limit, 100)),
                )
            ]
        }

    @api.get("/api/abstention-policies/{policy_id}")
    def get_abstention_policy(policy_id: str) -> dict[str, object]:
        abstention_store: DatabaseAbstentionStore | None = api.state.abstention_store
        if abstention_store is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Abstention policy not found")
        policy = abstention_store.get_policy(policy_id)
        if policy is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Abstention policy not found")
        return {"policy": _abstention_policy_payload(policy)}

    @api.post("/api/abstention-policies/{policy_id}/activate")
    def activate_abstention_policy(policy_id: str, request: ActivateAbstentionPolicyRequest) -> dict[str, object]:
        abstention_store: DatabaseAbstentionStore | None = api.state.abstention_store
        if abstention_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Abstention policy activation requires DATABASE_URL-backed persistence",
            )
        try:
            policy = abstention_store.activate_policy(
                policy_id,
                activated_by=request.activated_by,
                activation_reason=request.activation_reason,
                min_feedback_count=request.min_feedback_count,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except AbstentionPolicyGateError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {"policy": _abstention_policy_payload(policy)}

    @api.post("/api/abstention-policies/{policy_id}/deactivate")
    def deactivate_abstention_policy(policy_id: str, request: DeactivateAbstentionPolicyRequest) -> dict[str, object]:
        abstention_store: DatabaseAbstentionStore | None = api.state.abstention_store
        if abstention_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Abstention policy deactivation requires DATABASE_URL-backed persistence",
            )
        try:
            policy = abstention_store.deactivate_policy(
                policy_id,
                deactivated_by=request.deactivated_by,
                deactivation_reason=request.deactivation_reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except AbstentionPolicyGateError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {"policy": _abstention_policy_payload(policy)}

    @api.get("/api/abstention-policies/{policy_id}/shadow-decisions")
    def list_abstention_shadow_decisions(
        policy_id: str,
        diff: str | None = Query(default=None),
        limit: int = 100,
    ) -> dict[str, object]:
        abstention_store: DatabaseAbstentionStore | None = api.state.abstention_store
        if abstention_store is None:
            return {"shadow_decisions": []}
        allowed_diffs = {None, "", "all", "same", "new_accepts_old_abstains", "new_abstains_old_accepts", "new_rejects_ood", "other_change"}
        if diff not in allowed_diffs:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported shadow decision diff")
        if abstention_store.get_policy(policy_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Abstention policy not found")
        normalized_diff = None if diff in {None, "", "all"} else diff
        return {
            "shadow_decisions": [
                _abstention_shadow_decision_payload(item)
                for item in abstention_store.list_shadow_decisions(
                    policy_id=policy_id,
                    diff=normalized_diff,
                    limit=max(1, min(limit, 300)),
                )
            ]
        }

    return api


def _call_llm_assistant(*, task: str, context: dict[str, Any]) -> dict[str, Any]:
    try:
        return generate_assistance(task=task, context=context)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except LLMRequestError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _with_dataset_summary(store: Any, context: dict[str, Any]) -> dict[str, Any]:
    if context.get("dataset_summary"):
        return context
    dataset_version_id = context.get("dataset_version_id")
    if not dataset_version_id:
        return context
    manifest = store.get_dataset_version(str(dataset_version_id))
    if manifest is None:
        return context
    dataset_card = store.get_dataset_card(str(dataset_version_id))
    return {
        **context,
        "dataset_summary": _dataset_summary_for_llm(manifest, dataset_card=dataset_card),
    }


def _dataset_summary_for_llm(manifest: Any, *, dataset_card: dict[str, Any] | None = None) -> dict[str, Any]:
    class_preview_limit = 30
    split_totals = {
        split: sum(class_counts.values())
        for split, class_counts in (manifest.split_counts or {}).items()
        if isinstance(class_counts, dict)
    }
    readiness = dict(manifest.readiness or {})
    issue_fields = ["low_sample_classes", "missing_train_classes", "missing_eval_classes", "missing_split_classes"]
    issues = {
        key: value[:12] if isinstance(value, list) else value
        for key, value in readiness.items()
        if key in issue_fields and value
    }
    classes = list(manifest.classes or [])
    summary = {
        "dataset_id": manifest.dataset_id,
        "dataset_version_id": manifest.dataset_version_id,
        "task": "image_classification",
        "class_count": len(classes),
        "sample_count": len(manifest.samples or []),
        "class_preview": classes[:class_preview_limit],
        "class_preview_truncated": len(classes) > class_preview_limit,
        "split_totals": split_totals,
        "readiness_status": "ready" if readiness.get("ready") is True else "needs_attention",
        "readiness_issues": issues,
        "guidance": (
            "Use only these dataset classes as candidate in-domain labels. "
            "If model evidence conflicts with visible content or the image seems outside this domain, route to human review/OOD rather than inventing a new label."
        ),
    }
    if dataset_card:
        summary.update(
            {
                "task": dataset_card.get("task") or summary["task"],
                "domain": dataset_card.get("domain"),
                "summary": dataset_card.get("summary"),
                "known_confusions": dataset_card.get("known_confusions") or [],
                "ood_policy": dataset_card.get("ood_policy"),
                "review_guidance": dataset_card.get("review_guidance"),
            }
        )
    return summary


def _default_imported_dataset_dir() -> Path:
    configured = os.environ.get("FINEVISION_IMPORTED_DATASET_DIR")
    if configured:
        return Path(configured)
    data_root = Path("/data")
    if data_root.exists() and os.access(data_root, os.W_OK):
        return data_root / "imported-datasets"
    app_data = Path("/app/data")
    if app_data.exists() and os.access(app_data, os.W_OK):
        return app_data / "imported-datasets"
    return Path("data/imported-datasets")


def _uploaded_dataset_destination(base_dir: Path, *, dataset_id: str, dataset_version_id: str) -> Path:
    return base_dir / _safe_path_segment(dataset_id) / _safe_path_segment(dataset_version_id)


def _safe_path_segment(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.@-]+", "_", value.strip())
    normalized = normalized.strip("._")
    if not normalized:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid dataset path segment")
    return normalized[:160]


def _validate_uploaded_imagefolder(files: list[UploadFile]) -> tuple[list[tuple[UploadFile, Path]], dict[str, object]]:
    if not files:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No files were uploaded")

    raw_entries: list[tuple[UploadFile, PurePosixPath]] = []
    ignored_count = 0
    for upload_file in files:
        relative_path = _safe_upload_relative_path(upload_file.filename)
        if _is_ignored_upload_path(relative_path):
            ignored_count += 1
            continue
        if relative_path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported file type in dataset folder: {relative_path.as_posix()}",
            )
        raw_entries.append((upload_file, relative_path))

    if not raw_entries:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No supported image files were uploaded")

    common_prefix = _common_path_prefix([path for _, path in raw_entries])
    best_result: tuple[list[tuple[UploadFile, Path]], dict[str, object]] | None = None
    for prefix_length in range(0, len(common_prefix) + 1):
        candidate_entries: list[tuple[UploadFile, Path]] = []
        for upload_file, relative_path in raw_entries:
            candidate_parts = relative_path.parts[prefix_length:]
            if not candidate_parts:
                candidate_entries = []
                break
            candidate_entries.append((upload_file, Path(*candidate_parts)))
        if not candidate_entries:
            continue
        validation = _validate_imagefolder_relative_paths([path for _, path in candidate_entries])
        if validation is None:
            continue
        validation["ignored_files"] = ignored_count
        best_result = (candidate_entries, validation)

    if best_result is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Invalid ImageFolder structure. Use class/image files or split/class/image files "
                "under train, val, and/or test."
            ),
        )

    normalized_files, validation = best_result
    seen_paths: set[str] = set()
    for _, relative_path in normalized_files:
        path_key = relative_path.as_posix()
        if path_key in seen_paths:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Duplicate uploaded path: {path_key}")
        seen_paths.add(path_key)
    return normalized_files, validation


def _safe_upload_relative_path(filename: str | None) -> PurePosixPath:
    raw_name = (filename or "").replace("\\", "/").strip()
    if not raw_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Uploaded file is missing a relative path")
    relative_path = PurePosixPath(raw_name)
    if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unsafe uploaded path: {raw_name}")
    return relative_path


def _is_ignored_upload_path(path: PurePosixPath) -> bool:
    return any(part == "__MACOSX" or part.startswith(".") for part in path.parts)


def _common_path_prefix(paths: list[PurePosixPath]) -> tuple[str, ...]:
    if not paths:
        return ()
    prefix = list(paths[0].parts)
    for path in paths[1:]:
        next_prefix: list[str] = []
        for left, right in zip(prefix, path.parts, strict=False):
            if left != right:
                break
            next_prefix.append(left)
        prefix = next_prefix
        if not prefix:
            break
    return tuple(prefix)


def _validate_imagefolder_relative_paths(paths: list[Path]) -> dict[str, object] | None:
    parts_list = [path.parts for path in paths]
    if any(len(parts) < 2 for parts in parts_list):
        return None

    top_level = {parts[0] for parts in parts_list}
    split_mode = bool(top_level & set(EXPLICIT_SPLITS)) and top_level <= set(EXPLICIT_SPLITS)
    if split_mode:
        if any(len(parts) < 3 for parts in parts_list):
            return None
        class_labels = sorted({parts[1] for parts in parts_list})
        split_counts = {
            split: sum(1 for parts in parts_list if parts[0] == split)
            for split in EXPLICIT_SPLITS
            if any(parts[0] == split for parts in parts_list)
        }
    else:
        if any(parts[0] in EXPLICIT_SPLITS for parts in parts_list):
            return None
        class_labels = sorted({parts[0] for parts in parts_list})
        split_counts = {}

    if len(class_labels) < 2:
        return None
    return {
        "image_count": len(paths),
        "class_count": len(class_labels),
        "classes": class_labels,
        "format": "split/class/image" if split_mode else "class/image",
        "splits": split_counts,
    }


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


def _sample_preview_payloads(manifest, *, limit: int) -> list[dict[str, object]]:
    split_order = {"val": 0, "test": 1, "train": 2}
    ordered_samples = sorted(
        manifest.samples,
        key=lambda item: (split_order.get(item.split, 99), item.label, item.path),
    )
    selected = []
    seen_labels: set[str] = set()
    for sample in ordered_samples:
        if sample.label in seen_labels:
            continue
        selected.append(sample)
        seen_labels.add(sample.label)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        selected_ids = {sample.sample_id for sample in selected}
        for sample in ordered_samples:
            if sample.sample_id in selected_ids:
                continue
            selected.append(sample)
            if len(selected) >= limit:
                break

    return [
        {
            "sample_id": sample.sample_id,
            "label": sample.label,
            "split": sample.split,
            "image_url": f"/api/dataset-versions/{manifest.dataset_version_id}/samples/{sample.sample_id}/image",
        }
        for sample in selected
    ]


def _run_scoped_inference_payload(
    api: FastAPI,
    request: RunInferenceRequest,
    input_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    effective_request = request
    applied_policy_id: str | None = None
    applied_policy_source = "model_threshold_strategy"
    has_manual_threshold_override = any(
        value is not None
        for value in (request.accept_threshold, request.margin_threshold, request.ood_distance_threshold)
    )
    abstention_store: DatabaseAbstentionStore | None = api.state.abstention_store
    if not has_manual_threshold_override and abstention_store is not None:
        active_policy = abstention_store.get_active_policy(
            dataset_version_id=request.dataset_version_id,
            model_version_id=request.model_version_id,
        )
        if active_policy is not None:
            strategy = replace(
                strategy,
                strategy_id=f"{active_policy.policy_id}:active",
                accept_threshold=active_policy.tau_conf,
                margin_threshold=active_policy.tau_margin,
                target_selective_risk=active_policy.target_selective_risk,
                expected_coverage=float(active_policy.metrics.get("coverage", strategy.expected_coverage)),
                expected_selective_risk=float(active_policy.metrics.get("selective_risk", strategy.expected_selective_risk)),
                review_cost_per_item=float(active_policy.metrics.get("review_cost_per_item", strategy.review_cost_per_item)),
                selection_rule="active_abstention_policy",
                selection_config={
                    **strategy.selection_config,
                    "active_policy_id": active_policy.policy_id,
                    "policy_selection_config": active_policy.selection_config,
                },
            )
            if active_policy.tau_ood is not None:
                effective_request = request.model_copy(update={"ood_distance_threshold": active_policy.tau_ood})
            applied_policy_id = active_policy.policy_id
            applied_policy_source = "active_abstention_policy"
    elif has_manual_threshold_override:
        applied_policy_source = "request_threshold_override"

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
            request=effective_request,
            strategy=strategy,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    input_payload = {
        "image_path": request.image_path,
        "sample_id": request.sample_id,
        **(input_overrides or {}),
    }
    image_url = _review_image_url(
        dataset_version_id=context.dataset_version_id,
        sample_id=request.sample_id,
        input_ref=str(input_payload.get("uploaded_image_path") or input_payload.get("image_path") or ""),
    )
    if image_url:
        input_payload["image_url"] = image_url
        if input_payload.get("uploaded_image_path"):
            input_payload["uploaded_image_url"] = image_url
    return {
        "dataset_id": context.dataset_id,
        "dataset_version_id": context.dataset_version_id,
        "model_version_id": context.model_version_id,
        "model_status": context.model_status,
        "model_artifact_id": context.model_artifact.artifact_id,
        "feature_artifact_id": context.feature_artifact.artifact_id,
        "threshold_strategy_id": result.threshold_strategy_id,
        "applied_policy_id": applied_policy_id,
        "applied_policy_source": applied_policy_source,
        "input": input_payload,
        "result": to_jsonable(result),
    }


def _create_inference_run(
    api: FastAPI,
    request: RunInferenceRequest,
    *,
    run_type: Literal["single", "upload", "upload_folder"],
    request_payload: dict[str, Any] | None = None,
) -> str | None:
    review_store: DatabaseReviewStore | None = api.state.review_store
    if review_store is None:
        return None
    try:
        return review_store.create_inference_run(
            dataset_version_id=request.dataset_version_id,
            model_version_id=request.model_version_id,
            run_type=run_type,
            request_payload={**request.model_dump(), **(request_payload or {})},
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _attach_inference_run_payload(payload: dict[str, Any], inference_run_id: str | None) -> None:
    if not inference_run_id:
        return
    payload["inference_run_id"] = inference_run_id
    payload["batch_id"] = inference_run_id


def _finish_inference_run(
    api: FastAPI,
    inference_run_id: str | None,
    *,
    summary: dict[str, Any],
    first_result_payload: dict[str, Any] | None = None,
) -> None:
    review_store: DatabaseReviewStore | None = api.state.review_store
    if review_store is None:
        return
    review_store.finish_inference_run(
        inference_run_id,
        summary=summary,
        first_result_payload=first_result_payload,
    )


def _record_review_route(
    api: FastAPI,
    request: RunInferenceRequest,
    payload: dict[str, Any],
    *,
    force_review: bool = False,
    inference_run_id: str | None = None,
) -> None:
    review_store: DatabaseReviewStore | None = api.state.review_store
    if review_store is None:
        return
    event, review_item = review_store.record_inference_result(
        request_payload={**request.model_dump(), "force_review": force_review, "inference_run_id": inference_run_id or payload.get("inference_run_id")},
        response_payload=payload,
    )
    payload["inference_event_id"] = event.inference_event_id
    payload["inference_run_id"] = event.inference_run_id
    payload["batch_id"] = event.inference_run_id
    payload["review_item_id"] = review_item.review_item_id if review_item else None
    abstention_store: DatabaseAbstentionStore | None = api.state.abstention_store
    if abstention_store is not None:
        try:
            payload["shadow_policy_count"] = abstention_store.record_shadow_for_inference_event(event.inference_event_id)
        except Exception as exc:
            payload["shadow_policy_count"] = None
            payload["shadow_policy_error"] = {
                "type": exc.__class__.__name__,
                "message": str(exc),
            }


def _review_item_payload(item: Any) -> dict[str, Any]:
    image_url = _review_image_url(
        dataset_version_id=item.dataset_version_id,
        sample_id=item.sample_id,
        input_ref=item.input_ref,
    )
    inference_run_id = getattr(item, "inference_run_id", None)
    return {
        "review_item_id": item.review_item_id,
        "inference_event_id": item.inference_event_id,
        "inference_run_id": inference_run_id,
        "batch_id": inference_run_id,
        "dataset_id": item.dataset_id,
        "dataset_version_id": item.dataset_version_id,
        "model_version_id": item.model_version_id,
        "sample_id": item.sample_id,
        "input_ref": item.input_ref,
        "image_url": image_url,
        "status": item.status,
        "risk_type": item.risk_type,
        "priority": item.priority,
        "reason": item.reason,
        "reason_codes": item.reason_codes,
        "context": _review_context_with_image_url(item.context, image_url),
        "assistance_metadata": item.assistance_metadata,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "submitted_at": item.submitted_at,
        "feedbacked_at": item.feedbacked_at,
        "completed_by": item.completed_by,
        "feedback": _feedback_item_payload(item.feedback, review_item_id=item.review_item_id) if item.feedback else None,
    }


def _review_assistance_context(item: Any, *, question: str | None = None) -> dict[str, Any]:
    context = dict(item.context or {})
    return _sanitize_llm_context(
        {
            "review_item_id": item.review_item_id,
            "inference_run_id": getattr(item, "inference_run_id", None),
            "dataset_id": item.dataset_id,
            "dataset_version_id": item.dataset_version_id,
            "model_version_id": item.model_version_id,
            "sample_id": item.sample_id,
            "status": item.status,
            "risk_type": item.risk_type,
            "priority": item.priority,
            "reason": item.reason,
            "reason_codes": item.reason_codes,
            "decision": context.get("decision") or {},
            "top_k": (context.get("top_k") or [])[:5],
            "nearest_neighbors": (context.get("nearest_neighbors") or [])[:5],
            "operator_question": question,
        }
    )


def _sanitize_llm_context(value: Any, *, max_list: int = 12, max_string: int = 1200) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "image_data_url" and isinstance(item, str) and item.startswith("data:image/"):
                sanitized[key] = item
                continue
            if key in {"image_path", "uploaded_image_path", "input_ref", "root_uri", "uri"}:
                sanitized[key] = _redact_path(item)
                continue
            sanitized[str(key)] = _sanitize_llm_context(item, max_list=max_list, max_string=max_string)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_llm_context(item, max_list=max_list, max_string=max_string) for item in value[:max_list]]
    if isinstance(value, str):
        return _redact_path(value)[:max_string]
    return value


def _redact_path(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if "/" not in text and "\\" not in text:
        return text
    return f".../{Path(text).name}"


def _feedback_item_payload(item: FeedbackItemRecord, *, review_item_id: str | None = None) -> dict[str, object]:
    return {
        "feedback_item_id": item.feedback_item_id,
        "review_item_id": review_item_id or item.review_item_id,
        "inference_event_id": item.inference_event_id,
        "inference_run_id": item.inference_run_id,
        "batch_id": item.inference_run_id,
        "dataset_id": item.dataset_id,
        "dataset_version_id": item.dataset_version_id,
        "model_version_id": item.model_version_id,
        "sample_id": item.sample_id,
        "input_ref": item.input_ref,
        "image_url": _review_image_url(
            dataset_version_id=item.dataset_version_id,
            sample_id=item.sample_id,
            input_ref=item.input_ref,
        ),
        "final_outcome": item.final_outcome,
        "destination": item.destination,
        "final_label": item.final_label,
        "reviewer_note": item.reviewer_note,
        "created_by": item.created_by,
        "created_at": item.created_at,
    }


def _abstention_policy_payload(item: Any) -> dict[str, object]:
    metrics = dict(item.metrics or {})
    return {
        "policy_id": item.policy_id,
        "dataset_id": item.dataset_id,
        "dataset_version_id": item.dataset_version_id,
        "model_version_id": item.model_version_id,
        "status": item.status,
        "target_selective_risk": item.target_selective_risk,
        "tau_conf": item.tau_conf,
        "tau_margin": item.tau_margin,
        "tau_ood": item.tau_ood,
        "source_feedback_count": item.source_feedback_count,
        "estimated_coverage": metrics.get("coverage", 0.0),
        "estimated_selective_risk": metrics.get("selective_risk", 0.0),
        "estimated_review_cost": metrics.get("estimated_review_cost", 0.0),
        "metrics": metrics,
        "selection_config": item.selection_config,
        "created_by": item.created_by,
        "activated_by": item.activated_by,
        "activation_reason": item.activation_reason,
        "activated_at": item.activated_at,
        "deactivated_by": item.deactivated_by,
        "deactivation_reason": item.deactivation_reason,
        "deactivated_at": item.deactivated_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _abstention_shadow_decision_payload(item: Any) -> dict[str, object]:
    return {
        "shadow_decision_id": item.shadow_decision_id,
        "policy_id": item.policy_id,
        "inference_event_id": item.inference_event_id,
        "inference_run_id": getattr(item, "inference_run_id", None),
        "dataset_id": item.dataset_id,
        "dataset_version_id": item.dataset_version_id,
        "model_version_id": item.model_version_id,
        "current_decision": item.current_decision,
        "shadow_decision": item.shadow_decision,
        "decision_diff": item.decision_diff,
        "score_snapshot": item.score_snapshot,
        "created_at": item.created_at,
    }


def _review_context_with_image_url(context: dict[str, Any] | None, image_url: str | None) -> dict[str, Any]:
    payload = dict(context or {})
    if not image_url:
        return payload
    input_payload = dict(payload.get("input") or {})
    input_payload.setdefault("image_url", image_url)
    if input_payload.get("uploaded_image_path"):
        input_payload.setdefault("uploaded_image_url", image_url)
    payload["input"] = input_payload
    return payload


def _review_image_url(
    *,
    dataset_version_id: str | None,
    sample_id: str | None,
    input_ref: str | None,
) -> str | None:
    if sample_id and dataset_version_id:
        return f"/api/dataset-versions/{dataset_version_id}/samples/{sample_id}/image"
    return _uploaded_image_url(input_ref)


def _uploaded_image_url(input_ref: str | None) -> str | None:
    if not input_ref:
        return None
    normalized = input_ref.replace("\\", "/")
    if "/uploads/" not in normalized:
        return None
    filename = Path(normalized).name
    if not filename:
        return None
    return f"/api/uploads/{filename}"


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

    extractor = build_extractor_from_config(
        context.feature_artifact.extractor_config,
        overrides={"device": os.environ.get("FINEVISION_DINOV3_DEVICE", "cpu")},
    )
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


def _save_uploaded_image(upload_dir: Path, image: UploadFile) -> Path:
    filename = image.filename or "query-image"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded image must be jpg, jpeg, png, webp, or bmp",
        )

    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / f"{uuid4().hex}{suffix}"
    with destination.open("wb") as output:
        shutil.copyfileobj(image.file, output)
    return destination


def _get_training_run_or_404(training_store: DatabaseTrainingStore | None, run_id: str):
    if training_store is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training run not found")
    training_run = training_store.get_training_run(run_id)
    if training_run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training run not found")
    return training_run


def _default_backbone_id(extractor: str) -> str:
    if extractor in DINOV3_MODEL_PRESETS:
        return DINOV3_MODEL_PRESETS[extractor]["backbone_id"]
    return "color_stats_v1"


def _extractor_config(
    extractor: str,
    backbone_id: str,
    feature_batch_size: int | None = None,
    image_size: int | None = None,
    feature_pool: str = "cls",
) -> dict[str, Any]:
    if extractor in DINOV3_MODEL_PRESETS:
        config = dinov3_extractor_config(extractor, backbone_id, image_size=image_size, feature_pool=feature_pool)
        config["runtime"] = {"feature_batch_size": feature_batch_size or 8}
        return config
    return {"type": "color_stats", "bins": 8, "backbone_id": backbone_id}


def _validate_training_config(extractor: str, backbone_id: str, head_config: dict[str, Any]) -> None:
    expected_backbone = _default_backbone_id(extractor)
    if backbone_id != expected_backbone:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"backbone_id must be {expected_backbone} for extractor {extractor}",
        )
    head_type = str(head_config.get("head_type", "torch_linear_adam"))
    if head_type not in {"torch_linear_adam", "ridge_linear"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="head_config.head_type must be torch_linear_adam or ridge_linear",
        )
    if head_type == "ridge_linear":
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
        return

    numeric_fields = {
        "learning_rate": (1e-3, True),
        "weight_decay": (1e-4, False),
    }
    for field, (default, must_be_positive) in numeric_fields.items():
        try:
            value = float(head_config.get(field, default))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"head_config.{field} must be numeric",
            ) from exc
        if (must_be_positive and value <= 0) or (not must_be_positive and value < 0):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"head_config.{field} is out of range",
            )
    for field, default in {"epochs": 50, "batch_size": 256}.items():
        try:
            value = int(head_config.get(field, default))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"head_config.{field} must be an integer",
            ) from exc
        if value <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"head_config.{field} must be greater than 0",
            )

app = create_app()
