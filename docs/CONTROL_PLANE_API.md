# Control-plane API

This document records the Iteration 1 through Iteration 2 control-plane API slices.

## Boundary

The control-plane API owns lightweight product and metadata operations. It does not run DINOv3 extraction, model training, calibration, threshold sweeps, or batch inference inside request handlers.

Current scope:

- Dataset metadata listing
- ImageFolder manifest import
- Dataset detail and version summaries
- Dataset readiness reads
- Job creation and job status reads
- Worker execution for queued ImageFolder import jobs
- Worker execution for queued training jobs
- PostgreSQL metadata persistence when `DATABASE_URL` is configured
- Frontend dataset pages connected to the API with mock fallback
- Frontend pipeline page connected to job status with mock fallback
- Frontend training pages connected to training-run status with mock fallback

The ML/data toolkit remains the compute kernel. API request handlers create metadata and queued jobs; the worker executes feature extraction, classifier-head training, calibration, threshold sweeps, and artifact writes outside the request path.

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

Job status values:

```text
queued, paused, running, succeeded, failed, cancelled
```

Training run request:

```json
{
  "dataset_version_id": "dataset@cifar10-mini-001",
  "backbone_id": "dinov3_vitb16",
  "extractor": "dinov3_vitb",
  "feature_batch_size": 8,
  "image_size": 448,
  "feature_pool": "cls",
  "head_config": {
    "head_type": "torch_linear_adam",
    "learning_rate": 0.001,
    "epochs": 100,
    "batch_size": 256,
    "weight_decay": 0.0001
  },
  "target_selective_risk": 0.01,
  "review_cost_per_item": 1.0
}
```

Training run response shape:

```json
{
  "training_run": {
    "run_id": "run-abc123",
    "status": "queued",
    "job_id": "job-def456",
    "dataset_id": "cifar10-mini",
    "dataset_version_id": "dataset@cifar10-mini-001",
    "backbone_id": "dinov3_vitb16",
    "extractor_config": {
      "type": "timm_dinov3",
      "preset": "dinov3_vitb",
      "model_name": "vit_base_patch16_dinov3",
      "backbone_id": "dinov3_vitb16",
      "feature_pool": "cls",
      "image_size": 448,
      "runtime": {
        "feature_batch_size": 8
      }
    },
    "head_config": {
      "head_type": "torch_linear_adam",
      "learning_rate": 0.001,
      "epochs": 100,
      "batch_size": 256,
      "weight_decay": 0.0001
    },
    "feature_artifact_id": null,
    "model_artifact_id": null,
    "model_version_id": null,
    "report_artifact_id": null,
    "calibration_artifact_id": null,
    "threshold_strategy_artifact_id": null,
    "metrics": {
      "training_progress": {
        "current_stage": "queued",
        "overall_percent": 0,
        "stages": []
      }
    },
    "error": null,
    "created_at": "2026-06-22T00:00:00Z",
    "updated_at": "2026-06-22T00:00:00Z",
    "started_at": null,
    "finished_at": null
  },
  "job": {
    "job_id": "job-def456",
    "type": "train_classifier",
    "status": "queued"
  }
}
```

Training jobs must be created through `POST /api/training-runs`, not raw `POST /api/jobs`, so the job row and `training_runs` row remain consistent. The API rejects dataset versions whose readiness report is not ready, canonicalizes `backbone_id` from the selected extractor when omitted, and synchronizes queued training-run cancellation through `POST /api/jobs/{job_id}/cancel`.

Frontend model selection uses the same response shape: succeeded DINOv3 runs with
`extractor_config.feature_pool=cls`, `head_config.head_type=torch_linear_adam`, and a
`model_version_id` are treated as the current CLS baseline. Older DINOv3 runs without
`feature_pool` are displayed as legacy/model-output runs and should not be auto-selected ahead of
new CLS runs.

Supported extractors:

```text
color_stats   -> color_stats_v1
dinov3_vits   -> dinov3_vits16, timm vit_small_patch16_dinov3
dinov3_vitb   -> dinov3_vitb16, timm vit_base_patch16_dinov3
dinov3_vitl   -> dinov3_vitl16, timm vit_large_patch16_dinov3
```

`feature_batch_size` defaults to `8` and only affects DINOv3 feature extraction runtime memory and
throughput. `image_size` defaults to `448` for DINOv3 patch16 backbones, must be divisible by `16`,
and is part of the feature cache identity because it changes the extracted embeddings. `feature_pool`
defaults to `cls` for new DINOv3 runs, so the worker stores the ViT CLS token instead of timm's
legacy `model(tensor)` pooled output. Older DINOv3 feature artifacts without `feature_pool` are treated
as `model` pooling for compatibility and must not be mixed with new CLS feature caches. The Adam
classifier head has a separate `head_config.batch_size`.

Training queue controls:

```text
pause   queued/running -> paused
resume  paused -> queued
cancel  queued/paused/running -> cancelled
delete  removes queued/paused/cancelled/failed runs only when no artifacts or model version exist
```

Running DINOv3 extraction is still not checkpointed in the MVP, but the API can now mark a running
training run as `cancelled` or `paused`. The worker checks that control state at stage boundaries,
before and after weight preparation, during feature extraction progress callbacks, and before later
training/calibration/threshold steps. A paused run can be resumed as a queued run.

Current cancellation boundary:

```text
implemented: queued/running pause, paused resume, queued/paused/running cancel, safe non-running delete
implemented: worker cooperative stop checks between training stages and feature extraction batches
remaining limitation: Hugging Face/timm weight downloads are not preempted mid-request; the worker observes cancellation after the blocking download call returns
```

The default classifier head is `torch_linear_adam`, a `torch.nn.Linear` head trained with
cross-entropy and Adam. The current baseline defaults are `learning_rate=0.001`, `epochs=100`,
`batch_size=256`, and `weight_decay=0.0001`. It records optimizer configuration, device, solver,
and per-epoch loss/accuracy in the training report. `ridge_linear` remains supported as a fast
compatibility baseline for old runs and local smoke checks.

DINOv3 pretrained weights are resolved by `timm` through Hugging Face Hub.

```text
GET /api/model-weights
DELETE /api/model-weights/{preset}
```

`GET /api/model-weights` returns ViT-S/B/L cache status (`cached`, `partial`, or `missing`),
complete/incomplete cache sizes, Hugging Face cache paths, model descriptions, the Hugging Face cache
root, and whether an `HF_TOKEN`/`HUGGING_FACE_HUB_TOKEN` is configured. `DELETE
/api/model-weights/{preset}` removes only the local Hugging Face repo cache for a known preset
(`dinov3_vits`, `dinov3_vitb`, or `dinov3_vitl`). It does not delete dataset manifests,
`features.npz`, trained linear heads, calibration reports, or threshold strategies. A run can still
appear to sit in the `weights` stage while Hugging Face downloads or resumes a model cache.

Scoped inference request:

```json
{
  "dataset_version_id": "dataset@cifar10-mini-001",
  "model_version_id": "cifar10-mini-run-abc123-candidate",
  "image_path": "/absolute/path/to/query.png",
  "sample_id": null,
  "top_k": 3,
  "evidence_k": 3,
  "ood_distance_threshold": 1.5,
  "route_to_review": true
}
```

`POST /api/inference` requires PostgreSQL-backed model metadata and uses the `model_versions`
row produced by Iteration 2. Input can be either `image_path` or `sample_id`. `sample_id`
reuses the stored feature matrix; `image_path` restores the extractor from the feature artifact
metadata and extracts a single query feature. `POST /api/inference/upload` accepts a multipart
`image` file and stores it under the configured upload directory before running the same scoped
inference path. Batch inference is deferred.

Scoped inference response shape:

```json
{
  "inference_result": {
    "dataset_id": "cifar10-mini",
    "dataset_version_id": "dataset@cifar10-mini-001",
    "model_version_id": "cifar10-mini-run-abc123-candidate",
    "model_status": "candidate",
    "model_artifact_id": "dataset@cifar10-mini-001-run-abc123-linear-head",
    "feature_artifact_id": "dataset@cifar10-mini-001-color_stats_v1-cff1350237",
    "threshold_strategy_id": "dataset@cifar10-mini-001-run-abc123-threshold-strategy",
    "inference_event_id": "inference-abc123",
    "review_item_id": null,
    "input": {
      "image_path": "/absolute/path/to/query.png",
      "sample_id": null
    },
    "result": {
      "top_k": [
        { "label": "airplane", "score": 0.92 }
      ],
      "decision": {
        "decision": "accept",
        "reasons": ["meets_acceptance_thresholds"],
        "thresholds": {
          "confidence": 0.8,
          "margin": 0.15,
          "ood_distance": 1.5
        },
        "margin": 0.31,
        "confidence": 0.92,
        "ood_score": 0.4
      },
      "nearest_neighbors": [
        { "sample_id": "sample-456", "label": "airplane", "distance": 0.4 }
      ]
    }
  }
}
```

The `decision.decision` value is one of `accept`, `abstain`, or `reject_ood`. `top_k` is capped by
the model class count. Nearest-neighbor evidence is an exact scan over the feature artifact for MVP;
ANN/vector index serving is deferred.

When `route_to_review` is true, every inference is persisted as an `inference_event`. `accept`
decisions are recorded for traceability only. `abstain` and `reject_ood` decisions create a pending
review item; `reject_ood` is treated as an OOD candidate until a human confirms it.

Review queue endpoints:

```text
GET  /api/review-items?status=pending&limit=50
GET  /api/review-items/{review_item_id}
POST /api/review-items/{review_item_id}/submit
GET  /api/feedback-items?destination=training_candidate&dataset_id=cifar10-mini&limit=100
```

Review submit request:

```json
{
  "final_outcome": "corrected_label",
  "destination": "training_candidate",
  "final_label": "red_square",
  "reviewer_note": "Nearest-neighbor evidence supports red_square.",
  "reviewer": "local-reviewer"
}
```

`final_outcome` is one of `confirmed_label`, `corrected_label`, `ood`, `bad_image`, `uncertain`, or
`ignore`. `destination` is one of `training_candidate`, `ood_stress`, `bad_image`,
`taxonomy_dispute`, or `ignore`. The submit operation creates a typed feedback item and marks the
review as `feedbacked` in one database transaction. Feedback pool entries do not rewrite dataset
versions or trigger retraining.

Feedback pool responses are read-only MVP curation inputs:

```json
{
  "feedback_items": [
    {
      "feedback_item_id": "feedback-001",
      "review_item_id": "review-001",
      "inference_event_id": "inference-001",
      "dataset_id": "cifar10-mini",
      "dataset_version_id": "dataset@cifar10-mini-001",
      "model_version_id": "cifar10-mini-run-001-candidate",
      "sample_id": null,
      "input_ref": "/data/uploads/query.jpg",
      "image_url": "/api/uploads/query.jpg",
      "final_outcome": "corrected_label",
      "destination": "training_candidate",
      "final_label": "deer",
      "reviewer_note": "human correction",
      "created_by": "local-reviewer",
      "created_at": "2026-06-19T00:00:00Z"
    }
  ]
}
```

`GET /api/feedback-items` supports `destination=all|training_candidate|ood_stress|bad_image|taxonomy_dispute|ignore`,
`dataset_id`, and `limit`. The next platform slice should add a dataset curation job that consumes
selected feedback into a new immutable dataset version.

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

Iteration 1.7 makes PostgreSQL the default persistence layer when `DATABASE_URL` is configured.
The API and worker use the same control-plane tables:

```text
datasets
dataset_versions
artifacts
jobs
job_events
training_runs
model_versions
```

Dataset manifests are kept as `dataset_manifest` artifacts. The full manifest JSON is stored in
`artifacts.artifact_metadata`, while `dataset_versions.manifest_artifact_id` points to the current
manifest artifact row. This preserves the existing dataset list/detail/readiness API contract while
keeping large future artifacts outside the database.

Jobs are stored in `jobs`. Job lifecycle transitions append rows to `job_events` so the UI can later
show durable pipeline history without parsing worker stdout.

Training runs are stored in `training_runs`. Completed runs register feature, model, report,
calibration, threshold sweep, and threshold strategy artifacts in `artifacts`, then create a
candidate row in `model_versions`. Feature reuse uses an artifact key containing dataset version,
backbone, and an extractor config hash.

Run migrations before starting API and worker against a fresh database:

```bash
DATABASE_URL=postgresql+psycopg://finevision:finevision@localhost:5432/finevision uv run alembic upgrade head
```

The old JSON store remains as a compatibility adapter. It is used when tests or local tools pass
`metadata_dir` explicitly, or when no `DATABASE_URL` is configured. In that mode:

```text
.finevision-api/metadata/datasets
.finevision-api/metadata/jobs
```

The durable schema design is recorded in:

```text
docs/DATABASE_DESIGN.md
```

The current MVP residuals and acceptance checklist are recorded in:

```text
docs/MVP_RESIDUALS_ACCEPTANCE.md
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

The `api` and `ml-worker` services share the PostgreSQL control-plane database. They still share
metadata/artifact volumes for compatibility and future file artifacts, but job and dataset metadata
now flows through PostgreSQL by default.

The `ml-worker` service uses `Dockerfile.worker`, which installs the `dinov3` optional dependencies
(`torch`, `torchvision`, and `timm`) and mounts the host model caches for DINOv3 feature extraction.
The API image currently installs the same optional extractor dependencies because uploaded-image
inference is still a synchronous MVP endpoint. Long-running or high-throughput image inference
should move behind worker jobs before production hardening.

## Verification

```bash
uv run --group dev pytest
cd frontend && npm run smoke:api-client
cd frontend && npm run smoke:jobs-client
cd frontend && npm run smoke:training-client
cd frontend && npm run smoke:inference-client
cd frontend && npm run smoke:review-client
cd frontend && npm run build
cd frontend && npm run smoke:routes
docker compose config
```

Current verified result:

```text
backend: 10 passed, 14 db integration tests skipped unless FINEVISION_TEST_DATABASE_URL is set
db integration: 14 passed against local PostgreSQL
frontend api client: passed
frontend jobs client: passed
frontend training client: passed
frontend inference client: passed
frontend build: passed
frontend routes: 16 x 200
docker compose config: passed
worker --once CLI smoke: passed
```

## Review Workflow Status

Iteration 4 now turns `abstain` and `reject_ood` inference decisions into typed human review work:
inference events are persisted, pending review items are created automatically, and human submit
writes typed feedback pool entries. LLM/VLM assistance, online abstention updates, and automatic
dataset-version curation remain deferred.

## LLM Assistant API

Iteration 5 adds advisory-only LLM assistance. The backend calls an OpenAI-compatible Responses
provider using env configuration such as:

```text
OPENAI_API_KEY=...
FINEVISION_LLM_BASE_URL=https://mikuapi.org/v1
FINEVISION_LLM_MODEL=gpt-5.5
FINEVISION_LLM_REVIEW_MODEL=gpt-5.5
FINEVISION_LLM_REASONING_EFFORT=high
FINEVISION_LLM_DISABLE_RESPONSE_STORAGE=true
FINEVISION_LLM_WIRE_API=responses
FINEVISION_LLM_STRUCTURED_OUTPUTS=true
```

Review-specific assistance:

```text
POST /api/review-items/{review_item_id}/assist
```

The endpoint reads the stored review context, generates an advisory payload, writes it to
`review_items.assistance_metadata.llm_assistance`, and returns the updated review item. It only
works for `pending` review items and never creates `feedback_items`.

Generic on-demand assistance:

```text
POST /api/llm/assist
```

Request:

```json
{
  "task": "inference_explanation | review_assistance | training_diagnosis | feedback_curation",
  "context": {}
}
```

Response:

```json
{
  "assistance": {
    "advisory_only": true,
    "summary": "...",
    "inspection_notes": [],
    "suggested_actions": [],
    "risk_flags": [],
    "model": "gpt-5.5"
  }
}
```

LLM output is not a final label, does not update thresholds, and does not mutate dataset versions.
The default request uses Responses `text.format` with `type=json_schema`, `strict=true`, and a
schema requiring `summary`, `inspection_notes`, `suggested_actions`, `risk_flags`, and
`confidence`.

## Dataset Card LLM Context API

Iteration 5A adds a version-level dataset card so LLM assistance can reason from explicit dataset
scope instead of guessing from class names and model scores.

Read the card for a dataset version:

```text
GET /api/dataset-versions/{dataset_version_id}/card
```

Update the card:

```text
PUT /api/dataset-versions/{dataset_version_id}/card
```

Request:

```json
{
  "dataset_card": {
    "task": "image classification",
    "domain": "CIFAR-10 benchmark images",
    "summary": "Small 10-class image classification dataset.",
    "known_confusions": ["cat <> dog", "automobile <> truck"],
    "ood_policy": "Inputs outside the configured class list should be reviewed as OOD/uncertain.",
    "review_guidance": "Use the image content as the source of truth."
  }
}
```

Response:

```json
{
  "dataset_version_id": "dataset@cifar10-mini-001",
  "dataset_card": {
    "schema_version": 1,
    "dataset_version_id": "dataset@cifar10-mini-001",
    "task": "image classification",
    "domain": "CIFAR-10 benchmark images",
    "classes": ["airplane", "automobile", "bird"],
    "source": "manual_update",
    "updated_at": "2026-06-20T00:00:00Z"
  }
}
```

`GET /api/datasets/{dataset_id}` should include the latest version card and may include per-version
card summaries. The backend injects `dataset_card` into `POST /api/llm/assist` when the request
context includes `dataset_version_id`, and into `POST /api/review-items/{review_item_id}/assist`
using the review item's dataset version.

The card is advisory context only. It must not mutate review status, feedback items, thresholds,
dataset versions, or model versions.
