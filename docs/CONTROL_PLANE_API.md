# Control-plane API

> Status: Phase 1 contract reference. The authoritative public source is
> `go/api/openapi/finevision.yaml`; the authoritative internal source is `go/api/proto`.
> Sections explicitly labelled “Legacy contract” remain only for route-by-route compatibility work
> and do not describe a deployed FastAPI service.

## Phase 1 Current Interfaces

The Compose public entry point is the Go Control Plane on port `8001`. Its current OpenAPI batch owns
health, Dataset/Job/Training read models, Training lifecycle actions, the approved ViT-S weight
catalog, and generic advisory LLM assistance. The Go process also hosts the internal Training
Lifecycle gRPC server on port `9000`.

Python compute exposes no public HTTP server:

```text
TrainingLifecycle (Go :9000)
  Claim / Heartbeat / ReportProgress / Complete / Fail

InferenceRuntime (Python :9100)
  Predict / EvictCache / Health

RabbitMQ event
  training.job.ready.v1
```

Every compute completion carries Artifact Descriptors. Go verifies object bytes against `sha256` and
`size_bytes` before one transaction registers Artifacts, creates the Model Version, changes Job and
Training Run to `succeeded`, and appends the audit event. Stable errors use:

```json
{"error":{"code":"FENCED","message":"...","details":{},"request_id":"..."}}
```

Regenerate both Go and Python contract code with `scripts/generate-contracts.sh`.

## Legacy contract reference

This document records the Iteration 1 through Iteration 2 control-plane API slices.

## Boundary

The control-plane API owns lightweight product and metadata operations. It does not run DINOv3
feature extraction, model training, calibration, threshold sweeps, or batch inference inside request
handlers. `POST /api/inference/upload` is the current MVP exception: it runs one uploaded-image
inference synchronously for manual laboratory use and should move behind worker jobs before
high-throughput or production use.

Current scope:

- Dataset metadata listing
- ImageFolder manifest import
- Dataset detail and version summaries
- Dataset readiness reads
- Job creation and job status reads
- Worker execution for queued ImageFolder import jobs
- Worker execution for queued training jobs
- PostgreSQL metadata persistence when `DATABASE_URL` is configured
- Frontend dataset pages connected to the API; missing API data is shown as explicit empty/error states
- Frontend pipeline page connected to real job status plus a read-only template explainer
- Frontend training pages connected to training-run status without static training-result fallback

The ML/data toolkit remains the compute kernel. API request handlers create metadata and queued jobs;
the worker executes feature extraction, classifier-head training, calibration, threshold sweeps, and
artifact writes outside the request path.

## Legacy FastAPI run (contract tests only)

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

Dataset-card endpoints are active MVP routes. Import creates a manifest-derived default card and the
card is persisted as a `dataset_card` artifact, avoiding a dedicated migration for now.

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
inference path synchronously inside the API request. That upload route is an MVP bridge for manual
single-image checks. `POST /api/inference/upload-folder` accepts multipart `images` from a browser
folder picker, runs them sequentially through the same scoped inference path, and defaults to
`route_all_to_review=true` so every image becomes a pending review item for human batch review.
These upload routes are still synchronous MVP bridges, not the production shape for high-throughput
or long-running inference. Each database-backed inference request now creates an `inference_run_id`;
folder upload returns the same value as `batch_id` / `batch_inference_id` and attaches it to every
result, review item, and feedback item created from that batch.

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
    "inference_run_id": "infer-run-abc123",
    "batch_id": "infer-run-abc123",
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

Folder upload response shape:

```json
{
  "batch": {
    "inference_run_id": "infer-run-batch001",
    "batch_inference_id": "infer-run-batch001",
    "batch_id": "infer-run-batch001",
    "total": 20,
    "succeeded": 20,
    "failed": 0,
    "review_item_count": 20,
    "review_item_ids": ["review-001"]
  },
  "results": [
    {
      "inference_run_id": "infer-run-batch001",
      "inference_event_id": "inference-001",
      "review_item_id": "review-001"
    }
  ],
  "failures": []
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
      "inference_run_id": "infer-run-batch001",
      "batch_id": "infer-run-batch001",
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

The Docker Compose stack also includes a one-shot migration service:

```bash
docker compose up -d postgres
docker compose run --rm migrate
```

`api` and `ml-worker` depend on that migration service completing successfully, so a normal
`docker compose up --build` applies Alembic migrations before starting the application processes.

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

Default demo smoke is a contract and availability gate, not a complete browser E2E suite. For MVP
release acceptance, run `scripts/smoke-demo.sh --release-acceptance` against a running stack.

```bash
uv run --group dev pytest
cd frontend && npm run smoke:api-client
cd frontend && npm run smoke:jobs-client
cd frontend && npm run smoke:training-client
cd frontend && npm run smoke:inference-client
cd frontend && npm run smoke:abstention-client
cd frontend && npm run smoke:review-client
cd frontend && npm run smoke:llm-client
scripts/smoke-online-abstention-contract.sh
cd frontend && npm run build
cd frontend && npm run smoke:routes
docker compose config
```

`npm run smoke:routes` verifies preview HTTP availability for SPA routes only. It does not execute
browser interactions or prove backend workflow behavior.

Current verified result from the targeted docs/demo smoke update on 2026-06-23:

```text
shell syntax: bash -n scripts/demo-up.sh scripts/smoke-demo.sh scripts/smoke-online-abstention-contract.sh passed
shellcheck: not run; shellcheck is not installed in this environment
online abstention policy unit tests: 7 passed
online abstention API contract tests: 8 passed, 1 StarletteDeprecationWarning
online abstention contract smoke with activation contracts: 15 passed, 1 StarletteDeprecationWarning
inference API tests: 11 passed, 3 warnings
RUN_ABSTENTION_ACTIVATION_CONTRACT_SMOKE=1 scripts/smoke-demo.sh --contracts-only: passed
docker compose config: passed through scripts/smoke-demo.sh
frontend api client: passed
frontend jobs client: passed
frontend training client: passed
frontend inference client: passed
frontend review client: passed
frontend llm client: passed
frontend abstention client: passed
frontend build: passed
frontend route availability: 16 x 200
```

## Review Workflow Status

Iteration 4 now turns `abstain` and `reject_ood` inference decisions into typed human review work:
inference events are persisted, pending review items are created automatically, and human submit
writes typed feedback pool entries. General LLM assistance remains advisory. Automatic
dataset-version curation remains deferred.

## Online Abstention Phase 1

Implemented MVP:

```text
docs/ONLINE_ABSTENTION_PHASE1.md
```

The first online-abstention phase should use a risk-constrained threshold strategy:

```text
accept:
  confidence >= tau_conf
  margin >= tau_margin
  ood_score <= tau_ood

reject_ood:
  ood_score > tau_ood

otherwise:
  abstain
```

The target risk is `target_selective_risk`: the maximum allowed error rate among samples that the
policy chooses to automatically accept. For example, `target_selective_risk = 0.05` means the
auto-accepted subset should be at least roughly `95%` accurate.

Phase 1 runs in shadow mode:

- Do not replace the current inference `decision`.
- Do not mutate model-version threshold artifacts.
- Do not let LLM assistance activate or tune policies.
- Persist candidate policy versions and shadow decisions for audit and comparison.

Shadow policies do not change live inference decisions, review routing, model threshold artifacts,
feedback rows, or dataset versions. Manual activation is implemented as the first operation that
can make a policy affect live inference thresholds.

Implemented endpoints:

```text
POST /api/abstention-policies/propose
GET  /api/abstention-policies
GET  /api/abstention-policies/{policy_key}
POST /api/abstention-policies/{policy_key}/activate
POST /api/abstention-policies/{policy_key}/deactivate
GET  /api/abstention-policies/{policy_key}/shadow-decisions
```

The activation endpoint enforces these gates before a policy can become `active`:

- `source_feedback_count >= min_feedback_count`.
- `metrics.selective_risk <= target_selective_risk`.
- `reason` is non-empty and supplied by a human operator.
- The policy is in the exact dataset-version/model-version scope that inference will use.
- The policy is not archived.
- A partial unique index prevents two active policies in the same scope; activating a new policy
  marks the previous active policy as `superseded`.

An active policy is the first online-abstention state that may affect live inference thresholds.
Inference must prefer the active policy's `tau_conf`, `tau_margin`, and `tau_ood` over the model
version's default threshold artifact for the same dataset/model scope. Inference responses expose
`applied_policy_id` and `applied_policy_source` so decisions remain auditable.

LLM output must remain advisory with respect to abstention-policy management. It may summarize a
policy report or highlight risks, but it must not call activation/deactivation endpoints, tune
thresholds, or auto-fill an activation reason on behalf of the operator.

Activation smoke:

```text
scripts/smoke-online-abstention-contract.sh --with-activation-contracts
RUN_ABSTENTION_ACTIVATION_CONTRACT_SMOKE=1 scripts/smoke-demo.sh --contracts-only
```

`POST /api/abstention-policies/propose` accepts:

```json
{
  "dataset_version_id": "dataset@cub-200-2011-imagenet-001",
  "model_version_id": "cub-200-2011-imagenet-run-abc-candidate",
  "target_selective_risk": 0.05,
  "review_cost_per_item": 1.0,
  "created_by": "local-operator"
}
```

The response returns a `policy` with `tau_conf`, `tau_margin`, optional `tau_ood`,
`source_feedback_count`, estimated coverage/risk/cost, and `selection_config.selection_rule`.
If the selected dataset/model scope has no evaluable feedback item yet, the API returns `409`
instead of persisting an unusable all-abstain policy.

`GET /api/abstention-policies/{policy_key}/shadow-decisions` returns current-vs-shadow decision
diffs. These rows are audit artifacts only; they do not change inference events, review routing, or
model threshold artifacts.

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
    "holistic_analysis": "...",
    "inspection_notes": [],
    "suggested_actions": [],
    "risk_flags": [],
    "model": "gpt-5.5"
  }
}
```

LLM output is not a final label, does not update thresholds, and does not mutate dataset versions.
The default request uses Responses `text.format` with `type=json_schema`, `strict=true`, and a
schema requiring `summary`, `holistic_analysis`, `inspection_notes`, `suggested_actions`,
`risk_flags`, and `confidence`. When `context.dataset_version_id` is present, the backend injects a
compact manifest-derived `dataset_summary` so the assistant can ground its first-pass
`holistic_analysis` before producing checklist items. When the frontend sends
`context.image_input.image_data_url` for an uploaded query image, the backend attaches that image as
a Responses `input_image` and removes the base64 payload from the text JSON context. If a provider
rejects image inputs, the backend falls back to text-only evidence.

## Dataset Card LLM Context API

Iteration 5A adds a version-level dataset card so LLM assistance can reason from explicit dataset
scope instead of guessing from class names and model scores. The active MVP stores cards as
`dataset_card` artifacts and also returns the latest card from `GET /api/datasets/{dataset_id}`.

Read route:

```text
GET /api/dataset-versions/{dataset_version_id}/card
```

Update route:

```text
PUT /api/dataset-versions/{dataset_version_id}/card
```

LLM generation route:

```text
POST /api/dataset-versions/{dataset_version_id}/card/generate
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
    "task": "image classification",
    "domain": "CIFAR-10 benchmark images",
    "summary": "Small 10-class image classification dataset.",
    "class_count": 10,
    "sample_count": 460,
    "class_preview": ["airplane", "automobile", "bird"],
    "known_confusions": ["cat <> dog", "automobile <> truck"],
    "ood_policy": "Inputs outside the configured class list should be reviewed as OOD/uncertain.",
    "review_guidance": "Use the image content as the source of truth."
  }
}
```

`GET /api/datasets/{dataset_id}` includes the latest version card. The backend injects `dataset_card`
as compact `dataset_summary` context into
`POST /api/llm/assist` when the request context includes `dataset_version_id`, and into
`POST /api/review-items/{review_item_id}/assist` using the review item's dataset version.

`POST /api/dataset-versions/{dataset_version_id}/card/generate` reads the manifest class labels,
sample totals, split totals, readiness state, and the current card, then asks the configured LLM to
return a structured card. The generated card is persisted through the same normalization path as
`PUT`. This route is intentionally manual: dataset import remains deterministic and does not block
on LLM network/provider availability.

The card is advisory context only. It must not mutate review status, feedback items, thresholds,
dataset versions, or model versions.
