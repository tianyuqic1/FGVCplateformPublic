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
- Running training cancellation is now productized as cooperative worker checks, but a blocking
  Hugging Face/timm weight-download request is only observed after that request returns.

## P0 Residuals

P0 issues can block normal MVP use or create serious product misunderstanding.

### Running Training Cancellation

Current status: MVP complete with one external-download limitation.

The UI/API can pause queued/running runs, resume paused runs, cancel queued/paused/running runs, and
delete safe non-running records. The worker checks run status at stage boundaries, before and after
weight preparation, during feature extraction progress callbacks, and before later training,
calibration, and threshold steps.

Operational caveat:

```text
Hugging Face/timm weight downloads are blocking library calls. A cancellation request marks the run
and job as cancelled immediately, but the worker process may only observe that state after the
download call returns.
```

Acceptance for productized completion:

- Done: a running training job observes cancellation/pause requests between long stages and feature
  extraction batch loops.
- Done: the API records `training_runs.status = cancelled` and `jobs.status = cancelled`.
- Done: partial artifacts are not registered after cancellation is observed.
- Done: the UI can request cancellation for a running job without asking the user to restart the worker.

### Weight Management

Current status: MVP visible.

`timm` provides the model definition and triggers pretrained weight resolution through Hugging Face
Hub. `/api/model-weights` reports supported DINOv3 ViT-S/B/L cache state, complete/incomplete cache
bytes, cache root, and HF token configuration. The training UI shows those states before the user
starts a run. `/weights` is the dedicated browser for these pretrained caches and supports deleting
known local DINOv3 cache directories.

Acceptance for productized completion:

- Done: a weight status API lists each supported backbone, cache state, local cache key/path, and
  whether the cache is complete.
- Done: the UI distinguishes `cached`, `partial`, and `missing`.
- Done: a dedicated weight page explains ViT-S/B/L usage and deletes known Hugging Face cache entries
  without touching dataset, feature, classifier-head, calibration, or threshold artifacts.
- Remaining: a pre-download action and explicit failed/download-in-progress states are not yet added.
- Done: documentation explains that `HF_TOKEN` improves Hugging Face rate limits and is separate from the
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

### Training Head

Current status: Adam default.

The classifier head now defaults to `torch_linear_adam`, implemented as a `torch.nn.Linear` layer
trained with cross-entropy and Adam. It records learning rate, epoch count, batch size, weight decay,
device, solver, and per-epoch metrics in the model artifact and training report. `ridge_linear`
remains available for backward compatibility and fast smoke checks.

June 21 diagnosis found a more important feature-semantics issue than the optimizer alone:
FineVision had been using timm's `model(tensor)` output, which resolves to `global_pool=avg` for
`vit_*_patch16_dinov3`, while the stronger FGVC baseline uses the ViT CLS token from
`forward_features(... )[:, 0]`. New DINOv3 runs now default to `feature_pool=cls`, include that value
in the feature cache identity, and keep older artifacts without `feature_pool` compatible as legacy
`model` pooled features. A CUB rerun with ViT-S/16, 448px, CLS pooling, Adam `learning_rate=0.0005`,
`batch_size=256`, and `epochs=100` reached 88.23% accuracy / 88.26% macro F1, compared with roughly
70.8% for the old ViT-S avg-pooled cache under the same dataset and head family.

Acceptance for completion:

- Done: add `torch_linear_adam` with `epochs`, `learning_rate`, `batch_size`, and `weight_decay`.
- Done: use Adam as the default training head from the UI/API.
- Done: training reports record optimizer configuration and per-epoch metrics.
- Done: expose DINOv3 `image_size` as a semantic feature-cache parameter.
- Done: expose DINOv3 `feature_pool=cls` as the default semantic feature-cache parameter.
- Remaining: early stopping and scheduler support can be added after the MVP baseline is stable.

### Feature Extraction Progress

Current status: batch progress for compatible extractors.

The training detail page can show weight preparation, feature extraction, head training, calibration,
and threshold stages. DINOv3 feature extraction now reports processed samples through a progress
callback.

Acceptance for completion:

- Done: feature extraction reports processed samples and total samples.
- Done: the UI shows separate progress for weight preparation and feature extraction.
- Done: a slow Hugging Face download is shown as weight preparation, not as model training.
- Remaining: ETA and batch-count display are deferred.

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
FINEVISION_TEST_DATABASE_URL=postgresql+psycopg://finevision:finevision@localhost:5432/finevision_test uv run --group dev pytest
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

- Running cancellation is cooperative and does not preempt a blocking Hugging Face/timm download call mid-request.
- Weight downloads are externally dependent on Hugging Face availability, rate limits, and optional
  `HF_TOKEN` configuration.
- Candidate models can be used for experiments, but release governance is not complete until model
  registry promotion and rollback exist.
- `torch_linear_adam` is now the default training stack; scheduler and early stopping are still deferred.
- The feedback pool is visible, but feedback-to-new-dataset-version curation is not implemented yet.
