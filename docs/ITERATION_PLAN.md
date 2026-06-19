# FineVision Iteration Plan

This plan turns the current FineVision documents and HTML workbench prototype into a staged implementation path. The guiding principle is to deliver a working vertical slice at each checkpoint, not to build every module in isolation before integration.

## Version Control Policy

FineVision should use git from the start of implementation.

- Initialize the repository before Iteration 0 implementation begins.
- Push every completed, reviewed checkpoint to the remote repository.
- Prefer one commit per coherent checkpoint, with tests or smoke verification noted in the commit message or PR description.
- Do not wait until a full iteration is finished if a smaller checkpoint is already independently useful and verified.
- Avoid pushing broken mainline work; use feature branches for in-progress implementation.
- Tag or mark important milestones such as `iteration-0-shell`, `iteration-0.5-toolkit`, and `iteration-1-dataset-assets` once they are verified.

Recommended branch pattern:

```text
main
feature/iteration-0-shell
feature/iteration-0-5-ml-toolkit
feature/iteration-1-dataset-assets
```

## Service Boundary Strategy

FineVision should not jump directly from a document/prototype workspace into many independent microservices. The safer path is:

```text
Iteration 0.5: module boundaries
Iteration 1: control-plane API
Iteration 1.5: process/container boundaries
MVP later: split true microservices only when scaling or ownership requires it
```

The important architectural split is not "CRUD service versus model service" as separate products. It is:

- **Control plane API**: ordinary product and metadata operations that should stay lightweight and responsive.
- **Compute plane worker**: heavy ML/data work that may need PyTorch, FAISS, CUDA/GPU, long runtimes, retries, and artifact writes.
- **Shared contracts**: schemas, artifact ids, path conventions, job lifecycle states, and validation rules used by both.

The control-plane API can own CRUD-like operations for datasets, taxonomy, training runs, review items, feedback pools, model registry, dashboard summaries, and job creation. It should not execute long-running training, feature extraction, threshold sweeps, or batch inference inside request handlers.

The ML worker should execute queued jobs and update run/artifact metadata. It can live in the same repository and share schema code with the API at first. Docker should isolate it as a separate process/container once job execution begins, primarily to separate heavy dependencies and runtime behavior from the API.

This means FineVision should start as a **modular monolith with a separated worker process**, not a fully distributed microservice system. A true microservice split can happen later if there is a clear need for independent deployment, separate scaling, separate ownership, or stricter reliability boundaries.

## Iteration 0: Project Skeleton And Workbench Shell

Objective: turn the document-only workspace into a runnable project and preserve the HTML prototype as the product reference.

Scope:

- Initialize git and configure the remote repository.
- Create the frontend application skeleton.
- Convert the HTML prototype direction into React/Vite routes for dashboard, datasets, training, inference, review, models, and pipelines.
- Keep the current HTML prototype under `frontend/prototypes/` as the reference artifact.
- Use mock data first.
- Create a minimal backend skeleton if the implementation will use FastAPI or a similar API layer.

Deliverables:

- Runnable frontend workbench shell.
- Routes matching the HTML prototype page set.
- Shared navigation, shell layout, cards, metrics, tables, forms, status chips, and toast patterns.
- Initial mock data shaped close to future API responses.

Acceptance:

- The workbench opens locally.
- All prototype-level pages and detail pages are reachable.
- The UI is recognizably derived from `frontend/prototypes/fine-grained-vision-platform.html`.
- A first push exists for the project skeleton.

Suggested checkpoint pushes:

- `chore: initialize finevision repository`
- `feat: add workbench frontend shell`
- `feat: port prototype routes with mock data`

## Iteration 0.5: ML/Data Toolkit Prototype

Objective: create a small, testable toolkit layer for dataset processing, feature extraction, training, evaluation, threshold sweep, and inference before building higher-level APIs around it.

This is a platform-kernel spike, not a full algorithm research phase. The goal is stable callable boundaries and artifact contracts.

Scope:

- Scan ImageFolder-style datasets into a dataset manifest.
- Preserve existing `train`/`val`/`test` splits when provided.
- Create stratified splits when explicit splits are absent.
- Generate taxonomy and low-sample readiness diagnostics.
- Wrap frozen-backbone feature extraction behind a generic interface such as `backbone_id` plus `extractor_config`.
- Add a DINOv3 ViT-L extractor through `timm`, but keep heavy model downloads out of default tests.
- Add a lightweight deterministic extractor for local smoke tests.
- Persist feature artifacts with sample ids, labels, dimensions, backbone metadata, and dataset-version binding.
- Train at least one lightweight classifier head, starting with a linear head.
- Produce evaluation reports with accuracy, macro F1, per-class metrics, confusion data, and run configuration.
- Produce threshold sweep output with coverage, selective risk, abstention rate, and estimated review cost.
- Return inference results with top-k candidates, confidence, margin, and a first abstention decision.

Preferred artifact flow:

```text
DatasetManifest -> FeatureArtifact -> TrainingRunReport -> ModelArtifact -> InferenceResult
```

Deliverables:

- Toolkit modules or scripts with clear input/output contracts.
- A smoke command or test that runs the full local flow on a small dataset.
- Example artifacts saved under a predictable runtime location.

Acceptance:

- A small ImageFolder dataset can produce a manifest, feature artifact, trained head, evaluation report, and inference result.
- Artifacts include stable ids and metadata.
- The implementation does not hard-code DINOv3 into business logic; DINOv3-style extraction is one configured backbone option.
- The default smoke test runs without downloading DINOv3 weights.
- A documented command can run the DINOv3 ViT-L extractor when optional dependencies and weights are available.
- A checkpoint push records the working toolkit spike.

Suggested checkpoint pushes:

- `feat: add dataset manifest toolkit`
- `feat: add feature extraction artifact contract`
- `feat: add linear head training smoke flow`
- `feat: add evaluation and threshold sweep outputs`

## Iteration 0.6: Calibration And Decision Strategy

Objective: turn raw model scores into a versioned decision contract before wrapping the toolkit with higher-level APIs.

Scope:

- Fit temperature scaling on the validation split.
- Report calibration metrics: ECE, NLL, Brier score, and confidence bins.
- Generate confidence-threshold candidates from validation score distributions instead of hard-coded production thresholds.
- Select a `ThresholdStrategy` from validation sweep results under a target selective-risk policy.
- Estimate a margin threshold from validation margins.
- Persist calibration reports, threshold sweeps, and threshold strategies as model-linked artifacts.
- Require inference to consume `ThresholdStrategy` instead of ad hoc confidence and margin parameters.
- Re-run the CIFAR-10 mini + DINOv3 ViT-L validation path with calibration enabled.

Deliverables:

- `CalibrationReport` schema.
- `ThresholdStrategy` schema.
- Temperature-scaling utility.
- Strategy selection utility.
- Updated smoke flow and tests.
- Validation documentation showing before/after calibration behavior.

Acceptance:

- The default smoke flow writes `calibration_report.json`, `threshold_sweep.json`, and `threshold_strategy.json`.
- Inference results include `threshold_strategy_id`.
- Tests assert that strategy thresholds are selected from validation sweep output.
- Tests assert that inference uses the strategy thresholds.
- The DINOv3 CIFAR-10 mini run demonstrates calibrated confidence and a strategy-backed decision.

Suggested checkpoint pushes:

- `feat: add calibration report artifacts`
- `feat: add threshold strategy selection`
- `test: cover calibrated inference contract`

## Iteration 1: Dataset Assets And Metadata APIs

Objective: make datasets, dataset versions, taxonomy, sample quality, and readiness first-class platform concepts in the control-plane API.

Status: implemented as the first control-plane vertical slice on branch `codex/iteration-1-control-plane`.

Scope:

- Define dataset version metadata.
- Define class taxonomy metadata.
- Define sample quality and feedback outcome enums.
- Create the first backend control-plane API skeleton if it does not already exist.
- Add dataset list API.
- Add dataset detail API.
- Add readiness diagnostics for ImageFolder imports.
- Wire the dataset list and dataset detail pages to API data.

Deliverables:

- Dataset metadata schemas.
- Dataset list/detail API responses.
- Dataset detail views for overview, class governance, samples, feature index, and OOD/abstention sections.
- Local JSON metadata store for Iteration 1 development.
- Workbench dataset pages that use the API when available and fall back to mock data when the API is offline.
- API documentation in `docs/CONTROL_PLANE_API.md`.

Acceptance:

- Multiple datasets can be listed.
- Each dataset has a version, class count, sample count, readiness state, quality summary, and artifact references.
- Dataset detail exposes taxonomy and readiness diagnostics.
- Tests cover dataset version creation, split preservation, low-sample diagnostics, and taxonomy updates.

Implemented acceptance:

- `POST /api/datasets/import-imagefolder` scans an ImageFolder dataset and persists a manifest.
- `GET /api/datasets` returns dataset summaries with latest version, class count, sample count, readiness, and status.
- `GET /api/datasets/{dataset_id}` returns versions, classes, split counts, and readiness.
- `GET /api/dataset-versions/{dataset_version_id}/readiness` returns readiness diagnostics.
- Frontend dataset list/detail pages consume the API through a fallback-safe client.
- Tests cover import/list/detail/readiness and missing-resource errors.

Deferred to later iterations:

- Editable taxonomy governance APIs.
- Sample-level quality state mutation APIs.
- Durable database schema.
- Job queue and worker execution for heavy ML/data operations.

Suggested checkpoint pushes:

- `feat: add dataset metadata schemas`
- `feat: add dataset asset APIs`
- `feat: wire dataset workbench views`

## Iteration 1.5: API/Worker Process Boundary

Objective: introduce the runtime boundary between lightweight API operations and heavy ML/data jobs without prematurely splitting the codebase into independent microservices.

Status: implemented as the first API/worker boundary slice on branch `codex/iteration-1-5-api-worker-boundary`.

Scope:

- Keep one repository and shared schema/artifact contracts.
- Run the control-plane API and ML worker as separate processes.
- Add a job lifecycle for long-running tasks such as dataset import, feature extraction, training, evaluation, threshold sweep, nearest-neighbor index build, and batch inference.
- Add Docker Compose for local development with:
  - frontend
  - api
  - ml-worker
  - database or durable local metadata store
  - artifact volume
- Keep CRUD-like operations in the API:
  - dataset metadata
  - taxonomy updates
  - review queue and completion
  - feedback pools
  - model registry metadata
  - dashboard summaries
  - job creation and status reads
- Keep heavy compute out of API request handlers.

Deliverables:

- API process entrypoint.
- ML worker process entrypoint.
- Job table or queue abstraction.
- Docker Compose configuration.
- Shared schema package/module for API and worker.
- Artifact volume/path conventions.
- Frontend job status panel on the pipelines page.

Acceptance:

- The API can create a job and return a job id immediately.
- The worker can pick up a pending job, mark it running, write or simulate artifacts, and mark it completed or failed.
- The frontend can query job status through the API.
- API dependencies remain lightweight and do not require the ML runtime stack.
- Worker dependencies can include heavy ML packages without polluting the API container.

Implemented acceptance:

- `POST /api/jobs` creates a queued `import_imagefolder` job without scanning the dataset in the request handler.
- `GET /api/jobs` and `GET /api/jobs/{job_id}` expose job status.
- `POST /api/jobs/{job_id}/cancel` cancels queued jobs before the worker executes them.
- `python -m finevision.worker.jobs` runs a worker loop; `--once` executes one queued job.
- Worker jobs persist `running`, `succeeded`, and `failed` transitions with result/error data.
- `docker-compose.yml` runs `frontend`, `api`, and `ml-worker` as separate processes with shared metadata/artifact volumes.
- The pipeline page reads recent jobs from the API, displays queued/running/succeeded/failed/cancelled states, and falls back to preview jobs when the API is offline.

Deferred to later iterations:

- Real queue backend with locking/leases.
- Database-backed metadata.
- Worker jobs for feature extraction, training, calibration, threshold sweep, indexing, and batch inference.
- Job progress streaming or polling intervals beyond simple status reads.

Target database design:

```text
docs/DATABASE_DESIGN.md
```

Suggested checkpoint pushes:

- `feat: add api and worker entrypoints`
- `feat: add job lifecycle abstraction`
- `chore: add docker compose service boundary`

## Iteration 1.6: Database Foundation

Objective: replace the temporary JSON metadata direction with a durable PostgreSQL-backed control-plane schema.

Scope:

- Add PostgreSQL to local Docker Compose.
- Add SQLAlchemy and Alembic.
- Create initial migrations for `datasets`, `dataset_versions`, `jobs`, `job_events`, and `artifacts`.
- Keep artifact files outside PostgreSQL and register them by URI.
- Preserve the current JSON metadata store as a temporary compatibility adapter while repositories are introduced.

Acceptance:

- A fresh database can be migrated from zero.
- API and worker can connect to the same database.
- Job status and dataset metadata schema include leases, events, and artifact references.
- Documentation and schema match `docs/DATABASE_DESIGN.md`.

Suggested checkpoint pushes:

- `chore: add postgres and alembic foundation`
- `feat: add control plane database schema`
- `test: cover database-backed repositories`

## Iteration 1.7: Replace JSON Metadata Store

Objective: move dataset import metadata and job lifecycle from JSON files into PostgreSQL before expanding worker responsibilities.

Status: implemented as the database-backed repository slice on branch `codex/iteration-1-5-api-worker-boundary`.

Scope:

- Replace `MetadataStore` and `JobStore` JSON persistence with repository interfaces backed by PostgreSQL.
- Keep dataset manifest JSON as an artifact registered in the `artifacts` table.
- Add transactional job claiming with lease ownership and lease expiry.
- Preserve API response contracts for dataset and job endpoints.

Acceptance:

- Done: `POST /api/jobs` writes a PostgreSQL job row when `DATABASE_URL` is configured.
- Done: worker claims jobs transactionally with `SELECT ... FOR UPDATE SKIP LOCKED` and lease metadata.
- Done: dataset import writes dataset, dataset version, manifest artifact, job, and job event rows.
- Done: existing JSON-backed tests still pass through the explicit `metadata_dir` compatibility path.
- Done: PostgreSQL integration tests cover the API-to-worker import lifecycle and double-claim protection.

Implementation notes:

- `finevision.api.store` keeps the JSON adapter and exposes a store factory.
- `finevision.api.db_store` implements database-backed dataset and job stores.
- API and worker prefer PostgreSQL when `DATABASE_URL` is present.
- The JSON adapter remains available for focused tests and no-database local runs.

Suggested checkpoint pushes:

- `feat: replace metadata store with postgres repositories`
- `test: cover postgres job lifecycle and claiming`
- `docs: document iteration 1.7 persistence`

## Iteration 2: Training And Evaluation Services

Objective: turn the toolkit training flow into tracked platform operations executed by the worker and observed through the API.

Status: implemented as the first database-backed training/evaluation slice.

Scope:

- Bind feature extraction to dataset versions.
- Reuse feature artifacts for unchanged dataset/backbone combinations.
- Generalize classifier-head training inputs.
- Create training run records through the API.
- Link training outputs to candidate model versions.
- Serve training queue and training detail APIs from control-plane metadata.
- Execute feature extraction, training, evaluation, and threshold sweep in the worker.
- Attach calibration report and threshold strategy artifacts to completed training runs and candidate model versions.
- Show progress, run configuration, metrics, reports, and artifact links in the UI.

Deliverables:

- Training run API.
- Evaluation report model.
- Candidate model version creation.
- Frontend training queue and training detail views connected to real or fallback API data.

Acceptance:

- Done: a training run has a dataset version, feature artifact, backbone, head config, report, metrics, and status.
- Done: completed runs produce candidate model versions.
- Done: reports include accuracy, macro F1, per-class metrics, confusion information, and run configuration through the training report artifact.
- Done: worker execution registers feature matrix, model artifact, training report, calibration report, threshold sweep, and threshold strategy artifacts.
- Done: feature reuse keys include dataset version, backbone, and extractor config hash.
- Done: training queue and detail views read `/api/training-runs`; mock data is only a fallback when the API is unavailable.
- Done: `POST /api/training-runs` rejects dataset versions whose readiness report is not ready.
- Done: training job cancellation synchronizes the business training run to `cancelled`.
- Done: extractor/backbone metadata is canonicalized at the API boundary, so DINOv3 requests record `dinov3_vitl16` instead of the color-stats default.
- Partial: top-k/candidate recall is deferred until model registry and inference evaluation are expanded.

Implementation notes:

- `20260619_0002` adds `training_runs` and `model_versions`.
- `POST /api/training-runs` creates a business training run plus a `train_classifier` worker job.
- `GET /api/training-runs` and `GET /api/training-runs/{run_id}` serve training queue/detail metadata.
- `train_classifier` jobs are intentionally rejected through raw `POST /api/jobs`; they must be created through the training-run API so job and training metadata stay consistent.
- Dataset readiness is a hard gate for training creation. Use `/api/dataset-versions/{dataset_version_id}/readiness` to inspect the report before enqueueing.
- Training services require PostgreSQL-backed persistence. The JSON adapter remains for dataset/job compatibility tests only.
- Remaining hardening before production promotion: atomic job/run creation, expired worker lease recovery, model promotion/rollback invariants, and richer progress events.

Suggested checkpoint pushes:

- `feat: add training run model and APIs`
- `feat: connect training workbench views`
- `test: cover training artifacts and reports`

## Iteration 3: Scoped Inference And Abstention

Objective: make inference dataset-scoped and return decisions, not just labels.

Scope:

- Require dataset version and model version for inference.
- Return top-k candidates capped by dataset class count.
- Return calibrated scores where available.
- Return an abstention decision object with decision, reasons, thresholds, margin, confidence, and OOD/domain signal.
- Add nearest-neighbor evidence lookup from the feature index metadata.
- Connect the inference lab UI to the API.

Deliverables:

- Inference API.
- Abstention decision contract.
- Nearest-neighbor evidence response shape.
- Inference lab showing top-k, decision, reasons, and evidence.

Acceptance:

- High-confidence in-domain samples can return `accept`.
- Low-confidence or low-margin samples return `abstain`.
- Out-of-domain samples can return `reject_ood`.
- Tests cover accept, abstain, low-margin abstain, OOD reject, and nearest-neighbor response shape.

Suggested checkpoint pushes:

- `feat: add scoped inference contract`
- `feat: add abstention decision output`
- `feat: connect inference lab`

## Iteration 4: Human Review And Typed Feedback

Objective: complete the human-in-the-loop feedback path.

Scope:

- Create review items automatically for `abstain` and `reject_ood` decisions when routing is enabled.
- Store review items with dataset id, sample id, priority, reason, model context, nearest neighbors, and assistance metadata.
- Add optional LLM/VLM assistance adapter interface, but keep it advisory only.
- Add review detail API.
- Add review completion API requiring human final outcome, feedback destination, reviewer note, and completion metadata.
- Route completed outcomes into training candidate, OOD/stress, bad-image, dispute, or ignore pools.
- Connect review queue and detail UI.

Deliverables:

- Review queue API.
- Review detail API.
- Review completion API.
- Feedback pool storage.
- UI for final label, feedback destination, reviewer note, and submission.

Acceptance:

- Review items are ordered by risk priority.
- A review cannot complete without a human final outcome.
- LLM/VLM assistance is visibly separate from the final label.
- Completed outcomes enter the correct typed feedback pool.
- Tests cover queue ordering, completion, typed routing, and audit trail retention.

Suggested checkpoint pushes:

- `feat: add review queue and detail APIs`
- `feat: add typed feedback routing`
- `feat: connect review workflow`
- `test: cover review completion audit trail`

## Iteration 5: Model Registry And Release Gates

Objective: make model promotion auditable and safe.

Scope:

- Add model registry API for experiment, staging, production, archived, and failed states.
- Add model detail API with metadata, metrics, threshold strategy, artifacts, and production comparison.
- Evaluate release gates for offline metrics, calibration, OOD/stress performance, review pressure, and rollback availability.
- Add promotion and rollback service functions.
- Connect model registry and detail UI.

Deliverables:

- Model version registry.
- Release gate evaluator.
- Promotion and rollback operations.
- Model version UI with gates and comparison.

Acceptance:

- Every model version is traceable to dataset version, feature artifact, backbone, head, threshold strategy, report, and artifact location.
- Production promotion is blocked when required gates fail.
- Rollback metadata is stored when a new production model is promoted.
- Tests cover registration, gate failure, promotion, and rollback metadata.

Suggested checkpoint pushes:

- `feat: add model registry APIs`
- `feat: add release gate evaluation`
- `feat: connect model registry views`
- `test: cover promotion and rollback`

## Iteration 6: Dashboard, Pipelines, And End-To-End Validation

Objective: connect the workbench into one operational system.

Scope:

- Add dashboard summary API.
- Add pipeline template and pipeline run views backed by real run state or a durable fallback.
- Show priority tasks, review backlog, training status, production coverage, OOD alerts, dataset status, and release gates.
- Update README and architecture documentation.
- Add end-to-end API tests for the main dataset-to-feedback loop.
- Add frontend build verification and smoke interaction checks for key routes.

Deliverables:

- Operational dashboard using real platform state.
- Pipeline run view with progress, logs, artifacts, and retry/pause affordances.
- Updated documentation.
- End-to-end validation suite.

Acceptance:

- The primary loop is demonstrable:

```text
dataset import -> feature extraction -> training -> evaluation -> inference -> abstention -> review -> feedback pool -> candidate model -> release gates
```

- `uv run pytest` passes if a Python backend exists.
- `cd frontend && npm run build` passes if the frontend is present.
- A final milestone push marks the MVP workflow as verified.

Suggested checkpoint pushes:

- `feat: add operational dashboard summaries`
- `feat: add pipeline run views`
- `test: add end-to-end MVP flow coverage`
- `docs: document MVP workflow`

## Current Corrections To The Existing Plan

- Historical correction: the project started from documents and an HTML prototype, then Iteration 0-2 migrated it into a runnable frontend, API, worker, database schema, and training candidate flow.
- The HTML prototype is the UI source of truth for the workbench shape.
- The toolkit prototype should happen before durable dataset APIs, so backend contracts can wrap real callable functionality instead of imagined behavior.
- DINOv3 should be a configured backbone option behind an extractor interface, not a hard-coded platform assumption.
- OOD behavior should be treated as risk scoring and abstention support in the MVP, not as a guaranteed open-set classifier.
- LLM/VLM assistance should remain advisory and can be integrated after the review data model is stable.
- Model version metadata and threshold strategy should be introduced early, even if promotion and rollback are implemented later.
