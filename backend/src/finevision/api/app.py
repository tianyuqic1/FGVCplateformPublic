from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import shutil
from typing import Any, Literal
from uuid import uuid4

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from finevision.api.inference_store import DatabaseInferenceStore, InferenceContext
from finevision.api.review_store import DatabaseReviewStore, FeedbackItemRecord
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
    route_to_review: bool = True


class SubmitReviewRequest(BaseModel):
    final_outcome: Literal["confirmed_label", "corrected_label", "ood", "bad_image", "uncertain", "ignore"]
    destination: Literal["training_candidate", "ood_stress", "bad_image", "taxonomy_dispute", "ignore"]
    final_label: str | None = Field(default=None)
    reviewer_note: str | None = Field(default=None)
    reviewer: str | None = Field(default="local-reviewer")


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
    api.state.upload_dir = Path(os.environ.get("FINEVISION_UPLOAD_DIR", ".finevision-api/uploads"))
    api.state.upload_dir.mkdir(parents=True, exist_ok=True)
    api.mount("/api/uploads", StaticFiles(directory=str(api.state.upload_dir)), name="uploads")

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
        payload = _run_scoped_inference_payload(api, request)
        _record_review_route(api, request, payload)
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
        _record_review_route(api, request, payload)
        return {"inference_result": payload}

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

    input_payload = {
        "image_path": request.image_path,
        "sample_id": request.sample_id,
        **(input_overrides or {}),
    }
    return {
        "dataset_id": context.dataset_id,
        "dataset_version_id": context.dataset_version_id,
        "model_version_id": context.model_version_id,
        "model_status": context.model_status,
        "model_artifact_id": context.model_artifact.artifact_id,
        "feature_artifact_id": context.feature_artifact.artifact_id,
        "threshold_strategy_id": result.threshold_strategy_id,
        "input": input_payload,
        "result": to_jsonable(result),
    }


def _record_review_route(api: FastAPI, request: RunInferenceRequest, payload: dict[str, Any]) -> None:
    if not request.route_to_review:
        return
    review_store: DatabaseReviewStore | None = api.state.review_store
    if review_store is None:
        return
    event, review_item = review_store.record_inference_result(
        request_payload=request.model_dump(),
        response_payload=payload,
    )
    payload["inference_event_id"] = event.inference_event_id
    payload["review_item_id"] = review_item.review_item_id if review_item else None


def _review_item_payload(item: Any) -> dict[str, Any]:
    return {
        "review_item_id": item.review_item_id,
        "inference_event_id": item.inference_event_id,
        "dataset_id": item.dataset_id,
        "dataset_version_id": item.dataset_version_id,
        "model_version_id": item.model_version_id,
        "sample_id": item.sample_id,
        "input_ref": item.input_ref,
        "image_url": _uploaded_image_url(item.input_ref),
        "status": item.status,
        "risk_type": item.risk_type,
        "priority": item.priority,
        "reason": item.reason,
        "reason_codes": item.reason_codes,
        "context": item.context,
        "assistance_metadata": item.assistance_metadata,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "submitted_at": item.submitted_at,
        "feedbacked_at": item.feedbacked_at,
        "completed_by": item.completed_by,
        "feedback": _feedback_item_payload(item.feedback, review_item_id=item.review_item_id) if item.feedback else None,
    }


def _feedback_item_payload(item: FeedbackItemRecord, *, review_item_id: str | None = None) -> dict[str, object]:
    return {
        "feedback_item_id": item.feedback_item_id,
        "review_item_id": review_item_id or item.review_item_id,
        "final_outcome": item.final_outcome,
        "destination": item.destination,
        "final_label": item.final_label,
        "reviewer_note": item.reviewer_note,
        "created_by": item.created_by,
        "created_at": item.created_at,
    }


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
