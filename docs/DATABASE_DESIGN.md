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
dataset_card jsonb not null default '{}'
manifest_artifact_id uuid references artifacts(id)
created_by_job_id uuid references jobs(id)
created_at timestamptz not null
```

Important rule: model versions, feature artifacts, inference events, and review items should reference a dataset version.

`dataset_card` is a compact, editable, version-level context document for advisory LLM workflows. It
describes task, domain, class scope, known confusions, OOD policy, and review guidance. It is not
training data and should not be used as a final-label source. Keeping it on `dataset_versions`
prevents historical review and inference explanations from drifting when dataset-level descriptions
change.

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

```text
id uuid primary key
event_key text not null unique
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
- Implemented: manifest JSON is stored as a `dataset_manifest` artifact in `artifacts.artifact_metadata`.
- Kept intentionally: the JSON store remains as an explicit compatibility adapter for no-database local runs and focused tests.

No new migration was required for this step. The Iteration 1.6 tables already support the 1.7 API and worker boundary.

Deferred to Iteration 2:

- dedicated artifact APIs
- feature/model/training-specific tables
- expired lease retry and requeue policy
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

### Iteration 3-4: Inference, Review, And Feedback

- Implemented: `20260619_0003` adds `inference_events`, `review_items`, and `feedback_items`.
- Implemented: inference records events and routes `abstain` / `reject_ood` to human review.
- Implemented: completed reviews create typed feedback entries, visible through the feedback pool API.
- Deferred: `feature_indexes` table and FAISS index lifecycle; MVP nearest-neighbor evidence scans the feature artifact.
- Deferred: consuming feedback into a new immutable dataset version.

## Non-Goals For Now

- No separate database per service.
- No early microservice-owned schemas.
- No image binaries or model weights inside PostgreSQL.
- No standalone vector database until index scale or online query needs justify it.
- No complex event-sourcing model beyond `job_events` until there is a clear audit requirement.
