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
/feedback
/models
/weights
/pipelines
/pipelines?job_id=<job_id>
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

DINOv3 ViT-S/ViT-B/ViT-L are wired through `timm` as optional extractors. They may download
large weights, so they are not used by the default smoke test:

```text
uv run --extra dinov3 --group dev python -m finevision.ml_toolkit.smoke --work-dir .finevision-dinov3 --extractor dinov3_vits --batch-size 8
```

`--batch-size` only controls DINOv3 feature extraction throughput and memory use. New DINOv3 training
runs extract the ViT CLS token by default (`feature_pool=cls`), matching the fine-grained
classification baseline. The MVP classifier head defaults to `torch_linear_adam`; its
`head_config.batch_size` controls Adam mini-batches separately from feature extraction. Feature cache
identity is based on dataset version, DINOv3 model, pretrained flag, backbone id, image size, and
feature pooling, not the runtime batch size.

In Docker Compose, `ml-worker` is built from `Dockerfile.worker` with the `dinov3` optional
dependencies installed. The API image also installs the same optional extractor dependencies for
the current synchronous uploaded-image inference endpoint; this is an MVP bridge until image
inference is moved behind worker jobs. Both services mount the host Hugging Face and Torch caches so
DINOv3 weights can be reused across container rebuilds.

`timm` supplies the DINOv3 model definitions and resolves pretrained weights through Hugging Face
Hub. A slow or incomplete ViT-B download is therefore a weight-cache/Hugging Face issue, not a
missing `timm` model. Configure `HF_TOKEN` for better Hugging Face rate limits when large weights
need to be downloaded reliably.

The workbench includes a weight-management page at `/weights`. It lists the supported DINOv3
pretrained backbones, explains when to use ViT-S/B/L, shows the local Hugging Face cache path and
size, and can delete a known preset cache. Deleting a pretrained weight cache only affects future
weight loading/downloads; it does not delete dataset versions, CLS feature artifacts, trained
classifier heads, calibration reports, or threshold strategies.

For local iteration, Compose bind-mounts `./backend/src` into the API and worker containers and the
frontend source into the Vite container. Python source changes therefore take effect after a service
restart, and frontend source changes flow through Vite, without rebuilding the images. Dependency,
Dockerfile, or system package changes still require `docker compose build`.

GPU execution is supported through the optional Compose override:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d api ml-worker
```

The override sets DINOv3 feature extraction and the ridge/linear head solver to `cuda`, and requests
the NVIDIA GPU through Docker CDI (`nvidia.com/gpu=all`). This is a two-layer setup: the FineVision
images provide the Python/CUDA user-space dependencies (`torch`, `timm`, extractor code, caches), while
the host NVIDIA Container Toolkit/CDI exposes the real GPU device and driver libraries into the
containers. If the host runtime is not configured, Compose fails before the service starts. The base
`docker-compose.yml` remains CPU-safe so the workbench can still boot on machines without a configured
container GPU runtime.

The first real-data DINOv3 validation is documented in:

```text
docs/ML_TOOLKIT_VALIDATION.md
```

The current MVP hardening checklist and remaining P0/P1/P2 risks are tracked in:

```text
docs/MVP_RESIDUALS_ACCEPTANCE.md
```

## Control-plane API

Iteration 1 starts the FastAPI control plane under `backend/src/finevision/api/`.
Iteration 1.7 uses PostgreSQL for control-plane metadata when `DATABASE_URL` is configured.
The JSON metadata store remains available for explicit no-database local runs and focused tests.
The API exposes dataset asset and job endpoints without running DINOv3 extraction, training, or
batch inference work inside request handlers. `POST /api/inference/upload` remains a synchronous
single-image MVP bridge for manual inference checks.

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
GET  /api/health
GET  /api/datasets
GET  /api/datasets/{dataset_id}
POST /api/datasets/import-imagefolder
POST /api/datasets/upload-imagefolder
GET  /api/dataset-versions/{dataset_version_id}/readiness
GET  /api/dataset-versions/{dataset_version_id}/sample-previews
GET  /api/dataset-versions/{dataset_version_id}/samples/{sample_id}/image
POST /api/jobs
GET  /api/jobs
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/cancel
POST /api/training-runs
GET  /api/training-runs
GET  /api/training-runs/{run_id}
POST /api/training-runs/{run_id}/pause
POST /api/training-runs/{run_id}/resume
POST /api/training-runs/{run_id}/cancel
DELETE /api/training-runs/{run_id}
GET  /api/model-weights
DELETE /api/model-weights/{preset}
POST /api/inference
POST /api/inference/upload
POST /api/inference/upload-folder
GET  /api/dataset-versions/{dataset_version_id}/card
PUT  /api/dataset-versions/{dataset_version_id}/card
POST /api/dataset-versions/{dataset_version_id}/card/generate
GET  /api/review-items
GET  /api/review-items/{review_item_id}
POST /api/review-items/{review_item_id}/assist
POST /api/review-items/{review_item_id}/submit
GET  /api/feedback-items
POST /api/llm/assist
```

Inference requests are persisted as review-auditable events when routing is enabled. `abstain` and
`reject_ood` decisions create pending review items; `accept` decisions are recorded but do not enter
the human queue by default. `POST /api/inference/upload` is an MVP synchronous bridge for manual
single-image inference. `POST /api/inference/upload-folder` accepts a local folder upload from the
frontend, runs each image through the same scoped inference path, and defaults to queueing every
result for human review so an operator can process the batch one image at a time. High-throughput or
long-running batch inference should move behind worker jobs. Review submission writes typed feedback
pool entries and does not mutate the immutable source dataset version.

Dataset cards are active version-level context documents for advisory LLM assistance. Import creates
a deterministic card from the manifest; `GET`/`PUT /api/dataset-versions/{dataset_version_id}/card`
read and update the editable summary. `POST /api/dataset-versions/{dataset_version_id}/card/generate`
lets the configured LLM read class labels and write a domain-aware draft, for example bird species,
plant disease classes, or CIFAR-10 general objects. LLM assistance receives the card when a request
references a dataset version.

For a fresh local database, start PostgreSQL first and apply migrations before
starting the API/worker containers:

```text
docker compose up -d postgres
DATABASE_URL=postgresql+psycopg://finevision:finevision@localhost:5432/finevision uv run alembic upgrade head
docker compose up -d --build
```

This starts:

```text
frontend: http://localhost:5173
api:      http://localhost:8001
adminer:  http://localhost:8081
postgres: localhost:5432
```

Re-apply migrations after schema changes:

```text
DATABASE_URL=postgresql+psycopg://finevision:finevision@localhost:5432/finevision uv run alembic upgrade head
```

Run PostgreSQL-backed repository tests:

```text
docker compose stop ml-worker
FINEVISION_TEST_DATABASE_URL=postgresql+psycopg://finevision:finevision@localhost:5432/finevision uv run pytest backend/tests/test_db_stores.py backend/tests/test_api_inference.py
```

Run frontend API client smoke checks:

```text
cd frontend
npm run smoke:api-client
npm run smoke:jobs-client
npm run smoke:training-client
npm run smoke:inference-client
npm run smoke:review-client
npm run smoke:llm-client
npm run smoke:routes
npm run build
```

More detail:

```text
docs/CONTROL_PLANE_API.md
docs/DATABASE_DESIGN.md
```

## Next Step

Current MVP closeout keeps the model lifecycle candidate-only: no LoRA, no production promotion,
and no automatic feedback-to-training mutation. The next valuable step is to keep the CLS baseline
stable while turning feedback into curated dataset-version candidates:

```text
CLS baseline -> review/feedback pool -> curated dataset version -> retraining gate
```

Use the OpenSpec task list as the detailed backlog:

```text
openspec/changes/build-fine-grained-vision-platform-mvp/tasks.md
```
