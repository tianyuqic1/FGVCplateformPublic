# Database Design

This document records the recommended durable database design for FineVision after Iteration 1.5.

## Decision

Use PostgreSQL as the single control-plane database for the MVP.

Do not store images, feature matrices, vector indexes, model weights, or large reports directly in PostgreSQL. Store those as artifacts on a filesystem or object store, and keep only metadata, state, relationships, and URIs in the database.

Recommended storage split:

```text
PostgreSQL
  metadata, lifecycle state, relationships, audit events, artifact registry

Artifact storage
  images, dataset manifests, features.npz, faiss.index, model weights, reports

Vector index layer
  start with FAISS index artifacts; add pgvector, Qdrant, or Milvus only when scale requires it
```

The current JSON metadata store is useful for Iteration 1 and 1.5, but it should be treated as a temporary adapter. It is not suitable for durable multi-worker execution, filtering, audit, leases, or production history.

## Principles

- Bind all ML work to `dataset_version_id`, not just `dataset_id`.
- Keep artifact files outside PostgreSQL and register them in an `artifacts` table.
- Keep job state in the database and write append-only job events for diagnostics.
- Keep one database and one cohesive control-plane schema during the MVP.
- Do not split databases by future microservice boundaries yet.
- Do not introduce an external vector database until nearest-neighbor scale or online filtering requires it.
- Use JSONB for flexible configs and reports, but keep core identifiers, states, counts, and relationships as typed columns.

## Core Schema

### datasets

Business-level dataset asset.

```text
id uuid primary key
dataset_key text unique not null
name text not null
description text
domain text
status text not null
created_at timestamptz not null
updated_at timestamptz not null
```

Example statuses:

```text
draft
ready
archived
```

### dataset_versions

Immutable dataset snapshot used by training, evaluation, inference, and review.

```text
id uuid primary key
dataset_id uuid not null references datasets(id)
version_key text unique not null
root_uri text not null
sample_count integer not null
class_count integer not null
split_summary jsonb not null
readiness_status text not null
readiness_report jsonb not null
manifest_artifact_id uuid references artifacts(id)
created_by_job_id uuid references jobs(id)
created_at timestamptz not null
```

Important rule: model versions, feature artifacts, inference events, and review items should reference a dataset version.

Dataset Card MVP: the active implementation stores compact, editable, version-level dataset cards as
`dataset_card` artifacts rather than as a `dataset_versions` column. The card describes task, domain,
class scope, known confusions, OOD policy, and review guidance for advisory LLM workflows. It is not
training data and must not be used as a final-label source. A future migration may move card content
to `dataset_versions.dataset_card` JSONB only if querying, diffing, or governance needs justify the
schema change.

### dataset_classes

Class taxonomy and governance metadata.

```text
id uuid primary key
dataset_id uuid not null references datasets(id)
dataset_version_id uuid references dataset_versions(id)
label text not null
display_name text
description text
parent_class_id uuid references dataset_classes(id)
sample_count integer not null default 0
risk_level text not null default 'normal'
metadata jsonb not null default '{}'
created_at timestamptz not null
updated_at timestamptz not null
```

Use this table instead of hiding the class taxonomy only inside dataset JSON. Fine-grained classification needs class definitions, aliases, hierarchy, and risk metadata.

### samples

Sample metadata. The image binary stays in artifact storage.

```text
id uuid primary key
dataset_version_id uuid not null references dataset_versions(id)
sample_key text not null
uri text not null
label text not null
split text not null
quality_state text not null default 'accepted'
width integer
height integer
checksum text
metadata jsonb not null default '{}'
created_at timestamptz not null
```

Recommended constraints:

```text
unique(dataset_version_id, sample_key)
check(split in ('train', 'val', 'test', 'stress', 'unassigned'))
```

## Job And Worker Schema

### jobs

Durable job lifecycle owned by the control plane and executed by workers.

```text
id uuid primary key
job_key text unique not null
job_type text not null
status text not null
payload jsonb not null
result jsonb
error_message text
priority integer not null default 100
attempt_count integer not null default 0
max_attempts integer not null default 3
lease_owner text
lease_expires_at timestamptz
created_at timestamptz not null
queued_at timestamptz not null
started_at timestamptz
finished_at timestamptz
updated_at timestamptz not null
```

Required statuses:

```text
queued
running
succeeded
failed
cancelled
```

Add `lease_owner` and `lease_expires_at` before running multiple workers. A worker should claim queued jobs with a transactional `SELECT ... FOR UPDATE SKIP LOCKED` pattern.

### job_events

Append-only job timeline and debug log.

```text
id uuid primary key
job_id uuid not null references jobs(id)
event_type text not null
message text
payload jsonb not null default '{}'
created_at timestamptz not null
```

Examples:

```text
created
claimed
started
progress
artifact_written
succeeded
failed
cancelled
retried
```

The frontend pipeline log should read from `job_events`, not parse worker stdout.

## Artifact Registry

### artifacts

Unified registry for files produced by imports, feature extraction, training, calibration, inference, and review workflows.

```text
id uuid primary key
artifact_key text unique not null
artifact_type text not null
dataset_id uuid references datasets(id)
dataset_version_id uuid references dataset_versions(id)
job_id uuid references jobs(id)
uri text not null
checksum text
content_type text
size_bytes bigint
metadata jsonb not null default '{}'
created_at timestamptz not null
```

Recommended artifact types:

```text
dataset_manifest
feature_matrix
feature_index
model_weights
training_report
calibration_report
threshold_sweep
threshold_strategy
inference_result
ood_stress_set
```

## Training And Model Registry

### feature_artifacts

Feature matrix metadata. The actual matrix should stay in artifact storage.

```text
id uuid primary key
dataset_version_id uuid not null references dataset_versions(id)
artifact_id uuid not null references artifacts(id)
backbone_id text not null
feature_dim integer not null
extractor_config jsonb not null
sample_count integer not null
created_by_job_id uuid references jobs(id)
created_at timestamptz not null
```

Recommended unique key:

```text
unique(dataset_version_id, backbone_id, extractor_config_hash)
```

### feature_indexes

Nearest-neighbor index metadata.

```text
id uuid primary key
dataset_version_id uuid not null references dataset_versions(id)
feature_artifact_id uuid not null references feature_artifacts(id)
artifact_id uuid not null references artifacts(id)
backbone_id text not null
dimension integer not null
sample_count integer not null
index_type text not null
status text not null
created_by_job_id uuid references jobs(id)
metadata jsonb not null default '{}'
created_at timestamptz not null
```

Start with:

```text
index_type = 'faiss_file'
artifact uri = artifacts/faiss.index
```

Later options:

```text
pgvector
qdrant
milvus
elasticsearch_vector
```

### training_runs

Tracked model-training operation.

```text
id uuid primary key
run_key text unique not null
dataset_version_id uuid not null references dataset_versions(id)
job_id uuid references jobs(id)
feature_artifact_id uuid references feature_artifacts(id)
backbone_id text not null
head_type text not null
config jsonb not null
status text not null
metrics jsonb
created_at timestamptz not null
started_at timestamptz
finished_at timestamptz
updated_at timestamptz not null
```

### model_versions

Candidate, staging, and production model registry.

```text
id uuid primary key
model_key text unique not null
dataset_id uuid not null references datasets(id)
dataset_version_id uuid not null references dataset_versions(id)
training_run_id uuid references training_runs(id)
model_artifact_id uuid references artifacts(id)
evaluation_artifact_id uuid references artifacts(id)
calibration_artifact_id uuid references artifacts(id)
threshold_strategy_artifact_id uuid references artifacts(id)
state text not null
metrics jsonb not null default '{}'
created_at timestamptz not null
promoted_at timestamptz
archived_at timestamptz
```

Recommended states:

```text
candidate
staging
production
archived
rejected
```

Production constraint: only one `production` model per dataset should be active. Enforce this with a partial unique index.

## Inference, Review, And Feedback

Implemented in Iteration 4. These tables preserve the inference-to-review-to-feedback audit trail
without mutating dataset versions or training assets.

### inference_events

### inference_runs

```text
id uuid primary key
run_key text not null unique
dataset_id uuid not null references datasets(id)
dataset_version_id uuid not null references dataset_versions(id)
model_version_id uuid not null references model_versions(id)
run_type text not null
status text not null
item_count integer not null default 0
review_item_count integer not null default 0
applied_policy_key text
applied_policy_source text
threshold_snapshot jsonb not null default '{}'
request_payload jsonb not null default '{}'
summary jsonb not null default '{}'
created_at timestamptz not null
updated_at timestamptz not null
finished_at timestamptz
```

`run_key` is exposed as `inference_run_id`. Browser folder inference also aliases the same value as
`batch_id` / `batch_inference_id`; internally these are the same trace object. A run groups one
single-image inference or a browser-folder batch so review queues, feedback history, and threshold
policy analysis can answer: "which inference batch produced these human-reviewed samples?"

`run_type` is `single`, `upload`, or `upload_folder`. `status` is `running`, `succeeded`,
`partial_failed`, or `failed`. Historical inference events from before this migration may have no
run; new database-backed inference paths create a run before persisting events.

### inference_events

```text
id uuid primary key
event_key text not null unique
inference_run_id uuid references inference_runs(id)
dataset_id uuid not null references datasets(id)
dataset_version_id uuid not null references dataset_versions(id)
model_version_id uuid not null references model_versions(id)
model_status text not null
model_artifact_id uuid references artifacts(id)
feature_artifact_id uuid references artifacts(id)
threshold_strategy_artifact_id uuid references artifacts(id)
input_type text not null
input_ref text
sample_id text
decision text not null
confidence double precision
margin double precision
ood_score double precision
reasons jsonb not null
request_payload jsonb not null
result_payload jsonb not null
created_at timestamptz not null
```

`input_type` is `sample`, `image_path`, or `upload`. `decision` is `accept`, `abstain`, or
`reject_ood`. `accept` is persisted for traceability but does not create a review item by default.

### review_items

```text
id uuid primary key
review_key text not null unique
inference_event_id uuid not null unique references inference_events(id)
inference_run_id uuid references inference_runs(id)
dataset_id uuid not null references datasets(id)
dataset_version_id uuid not null references dataset_versions(id)
model_version_id uuid not null references model_versions(id)
sample_id text
input_ref text
status text not null
risk_type text not null
priority integer not null default 100
reason text not null
reason_codes jsonb not null
context jsonb not null default '{}'
assistance_metadata jsonb not null default '{}'
assigned_to text
submitted_at timestamptz
feedbacked_at timestamptz
completed_by text
created_at timestamptz not null
updated_at timestamptz not null
```

`status` is `pending`, `submitted`, `feedbacked`, `skipped`, or `disputed`. The MVP completes
reviews directly from `pending` to `feedbacked` in one transaction after the human submit.
`risk_type` is `low_confidence`, `low_margin`, `ood_candidate`, or `mixed`. `reject_ood` is treated
as an OOD candidate until a human confirms it.

`assistance_metadata` stores optional assistant output. Iteration 5 uses:

```json
{
  "llm_assistance": {
    "task": "review_assistance",
    "advisory_only": true,
    "provider": "OpenAI",
    "model": "gpt-5.5",
    "reasoning_effort": "high",
    "created_at": "...",
    "summary": "...",
    "inspection_notes": [],
    "suggested_actions": [],
    "risk_flags": [],
    "confidence": "low|medium|high"
  }
}
```

This metadata is advisory. It must not set `final_label`, change `status`, create feedback, or
change dataset/model artifacts.

### feedback_items

```text
id uuid primary key
feedback_key text not null unique
review_item_id uuid not null unique references review_items(id)
inference_event_id uuid not null references inference_events(id)
inference_run_id uuid references inference_runs(id)
dataset_id uuid not null references datasets(id)
dataset_version_id uuid not null references dataset_versions(id)
model_version_id uuid not null references model_versions(id)
sample_id text
final_label text
final_outcome text not null
destination text not null
reviewer_note text
feedback_metadata jsonb not null default '{}'
created_by text
created_at timestamptz not null
```

`final_outcome` is `confirmed_label`, `corrected_label`, `ood`, `bad_image`, `uncertain`, or
`ignore`.

Feedback destinations:

```text
training_candidate
ood_stress
bad_image
taxonomy_dispute
ignore
```

Feedback pools are candidate inputs to later dataset curation/versioning. They must not directly
rewrite the immutable source dataset version or trigger retraining by themselves.

The MVP exposes these pools through `GET /api/feedback-items`. This makes completed review outcomes
visible by `destination` and `dataset_id`, but it does not yet mark entries as consumed. The next
database hardening slice should add a curation state such as `new`, `accepted_for_next_dataset`,
`rejected`, and `included_in_dataset_version`, plus a reference to the derived dataset version that
consumed the feedback.

## Vector Database Position

Do not introduce a standalone vector database in the MVP.

The current platform first needs stable dataset versions, feature artifacts, model versions, jobs, and review feedback. A vector database adds deployment and consistency complexity before those contracts are stable.

Use this path:

```text
Iteration 2:
  feature_artifacts table + feature matrix artifact

Iteration 2.5:
  feature_indexes table + FAISS index artifact

Iteration 3:
  inference API uses the feature index for nearest-neighbor evidence and OOD distance

Later:
  replace FAISS artifact with pgvector, Qdrant, or Milvus if scale requires it
```

Use a vector database only when one of these becomes true:

```text
sample count reaches the million scale
online nearest-neighbor queries become latency-sensitive
metadata filtering plus vector search is required
multiple API/worker processes need concurrent index reads and updates
incremental vector updates are required
multi-tenant isolation becomes important
```

## Indexes And Constraints

Recommended early indexes:

```text
datasets(dataset_key)
dataset_versions(dataset_id, created_at desc)
dataset_versions(version_key)
dataset_classes(dataset_version_id, label)
samples(dataset_version_id, split)
samples(dataset_version_id, label)
samples(dataset_version_id, sample_key)
jobs(status, priority, queued_at)
jobs(lease_expires_at)
job_events(job_id, created_at)
artifacts(dataset_version_id, artifact_type)
feature_artifacts(dataset_version_id, backbone_id)
training_runs(dataset_version_id, created_at desc)
model_versions(dataset_id, state)
review_items(status, priority, created_at)
```

Recommended checks:

```text
job status in queued/running/succeeded/failed/cancelled
model state in candidate/staging/production/archived/rejected
sample split in train/val/test/stress/unassigned
```

## Migration Plan

### Iteration 1.6: Database Foundation

- Add PostgreSQL to Docker Compose.
- Add SQLAlchemy and Alembic.
- Create initial migrations for:
  - `datasets`
  - `dataset_versions`
  - `jobs`
  - `job_events`
  - `artifacts`
- Keep the existing JSON store as a compatibility adapter until API tests move to repositories.

Current foundation status:

```text
docker-compose.yml includes postgres and adminer
alembic.ini is configured
20260612_0001 creates datasets, dataset_versions, jobs, job_events, and artifacts
```

Run migrations:

```bash
DATABASE_URL=postgresql+psycopg://finevision:finevision@localhost:5432/finevision uv run alembic upgrade head
```

### Iteration 1.7: Replace JSON Store

- Implemented: dataset import metadata moves into PostgreSQL when `DATABASE_URL` is configured.
- Implemented: job lifecycle moves into PostgreSQL when `DATABASE_URL` is configured.
- Implemented: worker job claiming uses transactional row locking with `FOR UPDATE SKIP LOCKED`, `lease_owner`, and `lease_expires_at`.
- Implemented: expired running leases are recovered before the next claim; jobs under `max_attempts`
  are requeued, while exhausted jobs fail and release their lease.
- Implemented: manifest JSON is stored as a `dataset_manifest` artifact in `artifacts.artifact_metadata`.
- Kept intentionally: the JSON store remains as an explicit compatibility adapter for no-database local runs and focused tests.

No new migration was required for this step. The Iteration 1.6 tables already support the 1.7 API and worker boundary.

Deferred to Iteration 2:

- dedicated artifact APIs
- feature/model/training-specific tables
- sample-level query tables beyond the manifest artifact JSON

### Iteration 2: Training Metadata

- Implemented: `20260619_0002` adds `training_runs` and `model_versions`.
- Implemented: `train_classifier` worker jobs run feature extraction, linear-head training, evaluation, calibration, threshold sweep, and threshold strategy selection.
- Implemented: feature matrix, model artifact, training report, calibration report, threshold sweep, and threshold strategy outputs are registered in `artifacts`.
- Implemented: completed training runs create `model_versions.status = candidate`.
- Implemented: feature reuse keys include dataset version, backbone, and extractor config hash.

Current deliberate simplification:

- Feature artifacts are represented in the generic `artifacts` table as `artifact_type = feature_matrix`; a dedicated `feature_artifacts` table is deferred until feature search/index lifecycle needs richer query semantics.
- Feature indexes are deferred to Iteration 3, where inference needs nearest-neighbor evidence and OOD distance.
- DINOv3 pretrained weight cache state is not stored in PostgreSQL yet. Weight files remain in the
  host Hugging Face cache; a future weight-management slice should add metadata for backbone id,
  provider, cache state, expected size, checksum if available, and last validation time.
- Model versions are currently candidate records produced by training runs. Production promotion,
  rollback, archived states, and release-gate audit events remain a model-registry follow-up.

### Iteration 3-4: Inference, Review, And Feedback

- Implemented: `20260619_0003` adds `inference_events`, `review_items`, and `feedback_items`.
- Implemented: `20260623_0007` adds `inference_runs` and nullable run links on inference,
  review, and feedback rows for batch-level traceability.
- Implemented: inference records events and routes `abstain` / `reject_ood` to human review.
- Implemented: completed reviews create typed feedback entries, visible through the feedback pool API.
- Deferred: `feature_indexes` table and FAISS index lifecycle; MVP nearest-neighbor evidence scans the feature artifact.
- Deferred: consuming feedback into a new immutable dataset version.

### Online Abstention Phase 1: Shadow Policy Tables

Detailed design:

```text
docs/ONLINE_ABSTENTION_PHASE1.md
```

Phase 1 keeps existing `threshold_strategy` artifacts immutable. Feedback-backed abstention
policies are stored as separate strategy versions. They start in shadow mode and can affect live
inference only after a manual activation gate succeeds.

Implemented by migration `20260623_0005`:

```text
abstention_policy_versions
id uuid primary key
policy_key text unique not null
dataset_id uuid not null references datasets(id)
dataset_version_id uuid not null references dataset_versions(id)
model_version_id uuid not null references model_versions(id)
status text not null
target_selective_risk double precision not null
tau_conf double precision not null
tau_margin double precision not null
tau_ood double precision
metrics jsonb not null default '{}'
source_feedback_count integer not null
selection_config jsonb not null default '{}'
created_by text
activated_by text
activation_reason text
activated_at timestamptz
deactivated_by text
deactivation_reason text
deactivated_at timestamptz
created_at timestamptz not null
updated_at timestamptz not null
```

`status` is `shadow`, `candidate`, `active`, `superseded`, `deactivated`, or `archived`.
`active` is controlled by a manual gate. A partial unique index enforces at most one active policy
per `dataset_version_id + model_version_id` scope. Activating a new policy supersedes the previous
active policy in that scope; deactivated and superseded policies can be manually reactivated for
rollback after passing the gate again.

Implemented table:

```text
abstention_shadow_decisions
id uuid primary key
policy_version_id uuid not null references abstention_policy_versions(id)
inference_event_id uuid not null references inference_events(id)
current_decision text not null
shadow_decision text not null
score_snapshot jsonb not null default '{}'
decision_diff text not null
created_at timestamptz not null
```

These rows must not mutate `inference_events.decision`, `review_items.status`, `feedback_items`, or
model-version threshold artifacts. They only support audit and candidate-policy comparison. Active
policy application happens at inference time by reading the active policy threshold snapshot; it
does not rewrite historical shadow decisions or model artifacts.

### Fine-R1 VLM Review Tables

Implemented by migrations `20260726_0008` through `20260726_0010`.

```text
vlm_review_runs
id uuid primary key
run_key text unique not null
dataset_id uuid references datasets(id)
inference_run_id uuid references inference_runs(id)
mode text not null
status text not null
requested_limit integer not null
total_count integer not null
succeeded_count integer not null
failed_count integer not null
skipped_count integer not null
fallback_count integer not null
model_id text not null
model_revision text
prompt_version text not null
config jsonb not null
created_by text
created_at / started_at / finished_at / updated_at timestamptz
```

```text
vlm_review_results
id uuid primary key
result_key text unique not null
vlm_review_run_id uuid not null references vlm_review_runs(id)
review_item_id uuid not null references review_items(id)
status text not null
candidate_labels jsonb not null
suggested_label text
reasoning text
raw_output text
image_sha256 text
model_revision text
prompt_version text not null
latency_seconds double precision
input_tokens integer
generated_tokens integer
auto_submit_eligible boolean not null
gate_report jsonb not null
error_message text
attempt_count integer not null
created_at / started_at / finished_at / updated_at timestamptz
```

`vlm_review_results` is unique per `(vlm_review_run_id, review_item_id)`. Application-level selection
also excludes review items assigned to another active run. Workers claim rows with
`FOR UPDATE SKIP LOCKED`; stale running rows are requeued by a bounded lease policy. Cancelling a
run marks both queued and running results cancelled, and automatic review completion locks and
revalidates the VLM result before writing feedback.

Fine-R1 automatic feedback is distinguishable through
`feedback_items.feedback_metadata.source=vlm_auto`. Human feedback continues to use
`human_review_mvp`.

## Non-Goals For Now

- No separate database per service.
- No early microservice-owned schemas.
- No image binaries or model weights inside PostgreSQL.
- No standalone vector database until index scale or online query needs justify it.
- No complex event-sourcing model beyond `job_events` until there is a clear audit requirement.

## MVP Acceptance Reference

The product/QA acceptance checklist for running cancellation, weight management, training-head
limits, model registry boundaries, pipeline boundaries, and remaining risk is maintained in:

```text
docs/MVP_RESIDUALS_ACCEPTANCE.md
```
