# FineVision

FineVision is the intended workspace for the fine-grained image classification platform discussed in the migrated conversation.

## Current Artifacts

- `frontend/prototypes/fine-grained-vision-platform.html`: clickable workbench HTML prototype.
- `frontend/prototypes/NOTES.md`: prototype notes and product direction.
- `openspec/changes/build-fine-grained-vision-platform-mvp/`: OpenSpec proposal, design, specs, and task list for the MVP.
- `docs/ITERATION_PLAN.md`: staged implementation plan with checkpoint-based git push guidance.
- `HANDOFF.md`: concise context for continuing this work in a fresh thread.

## Prototype

Open this file directly in a browser:

```text
frontend/prototypes/fine-grained-vision-platform.html
```

Useful routes:

```text
?page=dashboard
?page=datasets
?page=dataset-detail&id=bird&tab=classes
?page=inference
?page=review
?page=models
?page=pipelines
```

## Workbench App

The Iteration 0 React/Vite workbench lives under `frontend/`.

Run locally:

```text
cd frontend
npm install
npm run dev
```

Verify the production build:

```text
cd frontend
npm run build
```

Main routes:

```text
/
/datasets
/datasets/bird?tab=classes
/training
/training/run-042
/inference
/review
/review/sample-0817
/models
/models/bird-cls-v4
/pipelines
/pipelines/pipe-014
```

## ML/Data Toolkit

Iteration 0.5 starts the backend toolkit under `backend/src/finevision/ml_toolkit/`.

Run the lightweight smoke flow:

```text
uv run --group dev python -m finevision.ml_toolkit.smoke --work-dir .finevision-smoke
uv run --group dev pytest
```

The smoke flow generates a tiny ImageFolder-style toy dataset and verifies:

```text
DatasetManifest -> FeatureArtifact -> ModelArtifact -> EvaluationReport -> CalibrationReport -> ThresholdStrategy -> InferenceResult
```

DINOv3 ViT-L is wired through `timm` as an optional extractor. It may download large weights, so it is not used by the default smoke test:

```text
uv run --extra dinov3 --group dev python -m finevision.ml_toolkit.smoke --work-dir .finevision-dinov3 --extractor dinov3_vitl
```

The first real-data DINOv3 validation is documented in:

```text
docs/ML_TOOLKIT_VALIDATION.md
```

## Control-plane API

Iteration 1 starts the FastAPI control plane under `backend/src/finevision/api/`.
It stores lightweight JSON metadata locally and exposes dataset asset endpoints without
running DINOv3, training, or inference jobs.

Run the API locally:

```text
uv run uvicorn finevision.api:app --reload
```

Run the worker locally:

```text
uv run python -m finevision.worker.jobs
```

When the workbench runs through Vite, `/api` is proxied to `http://localhost:8000`.
For other environments or a different API port, set `VITE_API_BASE_URL`.

Control-plane endpoints:

```text
GET  /api/datasets
GET  /api/datasets/{dataset_id}
POST /api/datasets/import-imagefolder
GET  /api/dataset-versions/{dataset_version_id}/readiness
POST /api/jobs
GET  /api/jobs
GET  /api/jobs/{job_id}
```

Run the local service boundary:

```text
docker compose up --build
```

More detail:

```text
docs/CONTROL_PLANE_API.md
```

## Next Step

Start implementation from:

```text
docs/ITERATION_PLAN.md
```

Use the OpenSpec task list as the detailed backlog:

```text
openspec/changes/build-fine-grained-vision-platform-mvp/tasks.md
```
