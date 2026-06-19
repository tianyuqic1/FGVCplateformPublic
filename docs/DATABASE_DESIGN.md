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

Important rule: model versions, feature artifacts, inference requests, and review items should reference a dataset version.

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

These tables can be introduced after training/model registry, but the design should reserve them early.

### inference_requests

```text
id uuid primary key
dataset_version_id uuid not null references dataset_versions(id)
model_version_id uuid not null references model_versions(id)
input_uri text
decision text not null
confidence double precision
margin double precision
threshold_strategy_artifact_id uuid references artifacts(id)
result jsonb not null
created_at timestamptz not null
```

### review_items

```text
id uuid primary key
inference_request_id uuid references inference_requests(id)
dataset_version_id uuid not null references dataset_versions(id)
sample_id uuid references samples(id)
status text not null
risk_type text not null
priority integer not null default 100
assigned_to text
context jsonb not null default '{}'
created_at timestamptz not null
resolved_at timestamptz
```

### feedback_items

```text
id uuid primary key
review_item_id uuid references review_items(id)
dataset_version_id uuid not null references dataset_versions(id)
sample_id uuid references samples(id)
final_label text
feedback_type text not null
destination text not null
comment text
created_at timestamptz not null
```

Feedback destinations:

```text
training_candidate
ood_stress
bad_image
taxonomy_dispute
ignore
```

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

- Add `feature_artifacts`, `feature_indexes`, `training_runs`, and `model_versions`.
- Convert feature extraction, training, evaluation, calibration, and threshold strategy generation into worker jobs.
- Register every output file in `artifacts`.

### Iteration 3: Inference And Review

- Add `inference_requests`, `review_items`, and `feedback_items`.
- Use `feature_indexes` for nearest-neighbor evidence and OOD decisions.
- Feed reviewed outcomes back into future dataset versions.

## Non-Goals For Now

- No separate database per service.
- No early microservice-owned schemas.
- No image binaries or model weights inside PostgreSQL.
- No standalone vector database until index scale or online query needs justify it.
- No complex event-sourcing model beyond `job_events` until there is a clear audit requirement.
