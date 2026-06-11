# Control-plane API

This document records the Iteration 1 and 1.5 control-plane API slices.

## Boundary

The control-plane API owns lightweight product and metadata operations. It does not run DINOv3 extraction, model training, calibration, threshold sweeps, or batch inference inside request handlers.

Current scope:

- Dataset metadata listing
- ImageFolder manifest import
- Dataset detail and version summaries
- Dataset readiness reads
- Job creation and job status reads
- Worker execution for queued ImageFolder import jobs
- Local JSON metadata persistence
- Frontend dataset pages connected to the API with mock fallback
- Frontend pipeline page connected to job status with mock fallback

The ML/data toolkit remains the compute kernel. Iteration 1.5 introduces a worker process that calls toolkit functions outside the request path.

## Run

Start the API:

```bash
uv run uvicorn finevision.api:app --reload
```

Start the worker loop in another terminal:

```bash
uv run python -m finevision.worker.jobs
```

Run at most one queued job:

```bash
uv run python -m finevision.worker.jobs --once
```

If port `8000` is already occupied:

```bash
uv run uvicorn finevision.api:app --reload --port 8001
```

Start the workbench:

```bash
cd frontend
npm run dev
```

The Vite dev server proxies `/api` to `http://localhost:8000`. If the API runs on another port or in a non-dev deployment, set:

```bash
VITE_API_BASE_URL=http://localhost:8000
```

For example, with the API on port `8001`:

```bash
VITE_API_BASE_URL=http://localhost:8001 npm run dev
```

## Endpoints

```text
GET  /api/health
GET  /api/datasets
GET  /api/datasets/{dataset_id}
POST /api/datasets/import-imagefolder
GET  /api/dataset-versions/{dataset_version_id}/readiness
POST /api/jobs
GET  /api/jobs
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/cancel
```

Import request:

```json
{
  "path": "data/test/cifar10-mini-imagefolder",
  "dataset_id": "cifar10-mini",
  "dataset_version_id": "dataset@cifar10-mini-001"
}
```

The synchronous `POST /api/datasets/import-imagefolder` endpoint is retained for Iteration 1 compatibility. New long-running imports should use the job API:

```json
{
  "type": "import_imagefolder",
  "payload": {
    "path": "data/test/cifar10-mini-imagefolder",
    "dataset_id": "cifar10-mini",
    "dataset_version_id": "dataset@cifar10-mini-001"
  }
}
```

Job response shape:

```json
{
  "job": {
    "job_id": "job-abc123",
    "type": "import_imagefolder",
    "status": "queued",
    "payload": {
      "path": "data/test/cifar10-mini-imagefolder",
      "dataset_id": "cifar10-mini",
      "dataset_version_id": "dataset@cifar10-mini-001"
    },
    "result": null,
    "error": null
  }
}
```

Dataset summary response shape:

```json
{
  "dataset_id": "cifar10-mini",
  "latest_version_id": "dataset@cifar10-mini-001",
  "dataset_version_id": "dataset@cifar10-mini-001",
  "version_count": 1,
  "classes": ["airplane", "automobile"],
  "class_count": 10,
  "sample_count": 460,
  "status": "ready",
  "readiness": {
    "ready": true,
    "provided_splits": true
  }
}
```

## Persistence

By default, API metadata is written under:

```text
.finevision-api/metadata
```

Override it with:

```bash
FINEVISION_METADATA_DIR=/path/to/metadata
```

The current store is intentionally simple JSON. It is enough for Iteration 1 and keeps the API contract testable before introducing database tables or a job queue.

Jobs are stored as JSON under:

```text
.finevision-api/metadata/jobs
```

Dataset manifests are stored under:

```text
.finevision-api/metadata/datasets
```

## Docker Compose

Run the local service boundary:

```bash
docker compose up --build
```

Services:

```text
frontend   Vite workbench
api        FastAPI control plane
ml-worker  worker loop consuming queued jobs
```

The `api` and `ml-worker` services share metadata and artifact volumes. This validates the process boundary without introducing distributed infrastructure yet.

## Verification

```bash
uv run --group dev pytest
cd frontend && npm run smoke:api-client
cd frontend && npm run smoke:jobs-client
cd frontend && npm run build
cd frontend && npm run smoke:routes
docker compose config
```

Current verified result:

```text
backend: 7 passed
frontend api client: passed
frontend jobs client: passed
frontend build: passed
frontend routes: 16 x 200
docker compose config: passed
```

## Next

Iteration 2 should move feature extraction, training, evaluation, calibration, and threshold strategy generation behind the worker job lifecycle. The API should continue to create jobs and expose metadata/status; the worker should own compute execution and artifact writes.
