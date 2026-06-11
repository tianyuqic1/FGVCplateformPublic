from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from finevision.api.store import MetadataStore
from finevision.ml_toolkit.datasets import scan_imagefolder


class ImportImageFolderRequest(BaseModel):
    path: str = Field(..., min_length=1)
    dataset_id: str = Field(..., min_length=1)
    dataset_version_id: str = Field(..., min_length=1)


def create_app(metadata_dir: str | Path | None = None) -> FastAPI:
    store = MetadataStore(metadata_dir or os.environ.get("FINEVISION_METADATA_DIR", ".finevision-api/metadata"))
    api = FastAPI(title="FineVision Control Plane API", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api.state.metadata_store = store

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

    return api


app = create_app()
