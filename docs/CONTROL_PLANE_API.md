# Control-plane API

This document records the Iteration 1 control-plane API slice.

## Boundary

The control-plane API owns lightweight product and metadata operations. It does not run DINOv3 extraction, model training, calibration, threshold sweeps, or batch inference inside request handlers.

Current scope:

- Dataset metadata listing
- ImageFolder manifest import
- Dataset detail and version summaries
- Dataset readiness reads
- Local JSON metadata persistence
- Frontend dataset pages connected to the API with mock fallback

The ML/data toolkit remains the compute kernel. A later worker process will call those toolkit functions outside the request path.

## Run

Start the API:

```bash
uv run uvicorn finevision.api:app --reload
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
```

Import request:

```json
{
  "path": "data/test/cifar10-mini-imagefolder",
  "dataset_id": "cifar10-mini",
  "dataset_version_id": "dataset@cifar10-mini-001"
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

## Verification

```bash
uv run --group dev pytest
cd frontend && npm run smoke:api-client
cd frontend && npm run build
cd frontend && npm run smoke:routes
```

Current verified result:

```text
backend: 5 passed
frontend api client: passed
frontend build: passed
frontend routes: 16 x 200
```

## Next

Iteration 1.5 should add an API/worker process boundary and a job lifecycle. The API should create jobs and return ids immediately; the worker should execute ImageFolder import, feature extraction, training, calibration, and inference jobs against the shared artifact contracts.
