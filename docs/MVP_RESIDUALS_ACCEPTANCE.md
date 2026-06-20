# MVP Residuals And Acceptance Checklist

Date: 2026-06-20

This document records the current product/QA view of the remaining MVP hardening work after the
dataset import, DINOv3 feature extraction, classifier training, scoped inference, review workflow,
feedback pool, and advisory LLM slices.

## Current Operational State

- The local Compose stack separates the frontend, API, PostgreSQL, and ML worker processes.
- Training jobs are created through the control-plane API and executed by `ml-worker`.
- DINOv3 ViT-S and ViT-L weights are available in the local Hugging Face cache. ViT-B may still be
  incomplete if the unauthenticated Hugging Face download is interrupted.
- The stopped ViT-B training run should be treated as a local cancellation case, not as evidence that
  running cancellation is fully productized.

## P0 Residuals

P0 issues can block normal MVP use or create serious product misunderstanding.

### Running Training Cancellation

Current status: partial.

The UI/API can pause queued runs, resume paused runs, cancel queued or paused runs, and delete safe
non-running records. Running DINOv3 extraction is not checkpointed and cannot yet be cancelled by a
durable cooperative worker protocol.

Local stop procedure for a blocked run:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml restart ml-worker
curl -s http://localhost:8001/api/training-runs/<run_id> | python -m json.tool
```

Acceptance for productized completion:

- A running training job observes a cancellation request between long stages and batch loops.
- The worker records `training_runs.status = cancelled` and `jobs.status = cancelled`.
- Partial artifacts are either not registered or marked unusable.
- The UI can cancel a running job without asking the user to restart the worker.

### Weight Management

Current status: not productized.

`timm` provides the model definition and triggers pretrained weight resolution through Hugging Face
Hub. The platform currently prepares weights implicitly during training or inference. This can make a
run appear stuck in the `weights` stage when Hugging Face is slow or unauthenticated.

Acceptance for productized completion:

- A weight status API lists each supported backbone, cache state, expected size when known, local path
  or cache key, and whether the cache is complete.
- The UI exposes a pre-download or validate action for ViT-S, ViT-B, and ViT-L.
- The UI distinguishes `cached`, `downloading`, `incomplete`, `missing`, and `failed`.
- Documentation explains that `HF_TOKEN` improves Hugging Face rate limits and is separate from the
  OpenAI-compatible LLM API key.

### Model Registry Boundary

Current status: candidate-only.

Completed training runs create candidate model records, and the model pages read those candidates.
This is not yet a production-grade registry with promote, rollback, archive, release gates, or model
lineage enforcement.

Acceptance for productized completion:

- Model statuses include at least `candidate`, `staging`, `production`, `archived`, and `failed`.
- Promotion writes an auditable event and requires release-gate checks.
- Rollback selects a previous production model and records the reason.
- Inference defaults to promoted models unless the user explicitly selects an experiment model.

### Synchronous Uploaded-Image Inference

Current status: acceptable MVP bridge.

`POST /api/inference/upload` runs scoped inference synchronously. This is fine for manual single-image
experiments but not for high-throughput or long-running inference.

Acceptance for productized completion:

- Batch or high-latency inference moves behind worker jobs.
- Inference jobs expose status, cancellation, result artifacts, and review routing.

## P1 Residuals

P1 issues reduce efficiency or trust and should be addressed soon.

### Training Head Limitation

Current status: `ridge_linear` only.

The classifier head currently uses a ridge/linear closed-form solve. It has `ridge_lambda`, but no
learning rate, epoch count, optimizer, scheduler, or early stopping.

Acceptance for completion:

- Add `torch_linear_adam` with `epochs`, `learning_rate`, `batch_size`, `weight_decay`, and
  `early_stopping_patience`.
- Keep `ridge_linear` as the default fast baseline.
- Training reports record optimizer configuration and per-epoch metrics when an iterative trainer is
  selected.

### Feature Extraction Progress

Current status: stage-level progress.

The training detail page can show weight preparation, feature extraction, head training, calibration,
and threshold stages. DINOv3 feature extraction does not yet update batch-level progress.

Acceptance for completion:

- Feature extraction reports processed samples, total samples, batch count, and ETA when possible.
- The UI shows separate progress for weight preparation and feature extraction.
- A slow Hugging Face download is shown as weight preparation, not as model training.

### Dev/Smoke Options In Production UI

Current status: partially exposed.

`color_stats` remains useful for smoke tests and CPU-safe validation, but it should not be presented
as a normal production training backbone unless dev mode is enabled.

Acceptance for completion:

- The training creation UI separates production backbones from smoke/dev extractors.
- Smoke options are hidden behind an explicit development toggle or environment flag.

### Dataset-Scoped Model Selection

Current status: needs guardrails.

Inference should only offer model versions compatible with the selected dataset version.

Acceptance for completion:

- The frontend filters candidate models by `dataset_version_id`.
- The backend returns a clear 4xx error if dataset/model lineage is incompatible.
- The UI explains why no model is available for a dataset version.

### Artifact Browsing

Current status: metadata visible, artifact API deferred.

Training details show artifact ids, but reports and files are not yet browsable through a dedicated
artifact endpoint.

Acceptance for completion:

- Artifact API can retrieve safe JSON/text reports by id.
- Training details link to training report, calibration report, threshold sweep, and threshold
  strategy.
- Binary or large artifacts remain filesystem/object-store backed, not embedded in PostgreSQL.

## P2 Residuals

P2 issues are experience polish or later platform expansion.

- Replace remaining static image placeholders with real dataset samples where a dataset version is
  selected.
- Turn the pipeline view from job-status readout into durable pipeline templates and pipeline runs.
- Add feedback curation into new immutable dataset versions.
- Add richer dashboard summaries for review backlog, training failures, model coverage, and release
  gate status.

## Verification Commands

Backend and database:

```bash
python -m compileall -q backend/src/finevision
uv run --group dev pytest
FINEVISION_TEST_DATABASE_URL=postgresql+psycopg://finevision:finevision@localhost:5432/finevision uv run --group dev pytest
```

Frontend:

```bash
npm --prefix frontend run smoke:api-client
npm --prefix frontend run smoke:jobs-client
npm --prefix frontend run smoke:training-client
npm --prefix frontend run smoke:inference-client
npm --prefix frontend run smoke:review-client
npm --prefix frontend run smoke:routes
npm --prefix frontend run build
```

Compose:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config
docker compose -f docker-compose.yml -f docker-compose.gpu.yml ps
curl -s http://localhost:8001/api/health | python -m json.tool
curl -s http://localhost:8001/api/training-runs | python -m json.tool
```

Manual MVP walkthrough:

```text
1. Import a local ImageFolder dataset.
2. Confirm readiness is ready.
3. Start a ViT-S training run first because its weight cache is available and fast.
4. Confirm staged progress reaches succeeded and artifacts are registered.
5. Run uploaded-image inference against the candidate model.
6. Confirm accept events are persisted without review by default.
7. Confirm abstain/reject_ood routes to review.
8. Submit a review item and confirm feedback pool entry creation.
9. Confirm feedback does not mutate the source dataset version.
```

## Remaining Risk Summary

- Running cancellation still needs cooperative worker support before it is safe for non-technical users.
- Weight downloads are externally dependent on Hugging Face availability, rate limits, and optional
  `HF_TOKEN` configuration.
- Candidate models can be used for experiments, but release governance is not complete until model
  registry promotion and rollback exist.
- `ridge_linear` is a strong baseline, not a full optimizer-based training stack.
- The feedback pool is visible, but feedback-to-new-dataset-version curation is not implemented yet.
