# FineVision Agent Guide

This file governs work in the entire repository. Phase 1 uses a Go Control Plane and independent Go LLMGateway while retaining Python only for ML/data compute. Phase 2 adds the approved A-style research workbench, metric history, governed Model Version comparison, and two ImageNet pretrained backbones without changing that ownership boundary. The Compose release surface no longer starts FastAPI. Historical Python control-plane modules remain only as migration-contract fixtures and must not receive new features or writes.

## Read Before Changing Code

Read the following in order:

1. [`CONTEXT.md`](CONTEXT.md) for canonical domain terms.
2. [`docs/TECHNICAL_ARCHITECTURE.md`](docs/TECHNICAL_ARCHITECTURE.md) for the target stack and engineering rules.
3. [`docs/REFACTOR_PHASE1_PLAN.md`](docs/REFACTOR_PHASE1_PLAN.md) for migration order, lifecycle semantics, gates, and acceptance criteria.
4. [`docs/REFACTOR_PHASE2_PLAN.md`](docs/REFACTOR_PHASE2_PLAN.md) for the approved frontend direction, metric history, Model Version, and pretrained backbone scope.
5. The relevant current-state document, especially [`docs/CONTROL_PLANE_API.md`](docs/CONTROL_PLANE_API.md) or [`docs/DATABASE_DESIGN.md`](docs/DATABASE_DESIGN.md).

When documents conflict, user instructions take precedence, followed by this file, the target technical architecture, the applicable Phase plan, and then current-state documents. Preserve explicit “current” versus “target” labels.

## Architecture Invariants

- Go Control Plane owns the public HTTP interface, PostgreSQL business state, CRUD, transactions, Training Lifecycle, Artifact registry, Model Version, Review/Feedback, and orchestration.
- Python Compute Plane owns Dataset scanning, feature extraction, training, evaluation, calibration, threshold computation, and numerical inference.
- Python must not add new direct writes to control-plane tables. Historical FastAPI tests document pre-cutover contracts; never use them as a second runtime writer.
- PostgreSQL is the Training Lifecycle source of truth. RabbitMQ is an at-least-once dispatch adapter, not job state storage.
- ArtifactStore owns large bytes. PostgreSQL stores canonical URI, SHA-256, `size_bytes`, content type, identity, status, and relationships.
- Fine-R1 has been removed from runtime, routes, dependencies, UI, scripts, and the current schema. Do not reintroduce Fine-R1 features or compatibility layers. Historical Alembic revisions remain immutable; the Phase 1 forward migration removes their tables.
- External LLM calls belong in the independent Go LLMGateway. LLM Assistance remains advisory and cannot set final labels, submit human reviews, activate policies, or trigger training.
- Phase 2 metric history is append-only PostgreSQL state exposed by the Go Control Plane. RabbitMQ dispatches Training Jobs; it does not stream chart data or own metric history.
- FineVision remains the sole Training Run and Model Version fact source. Do not add MLflow Tracking Server, MLflow Model Registry, TensorBoard, or another registry/tracking backend in Phase 2.

## Go Rules

The accepted Phase 1 stack is:

- `net/http` + `chi/v5`
- OpenAPI 3.1 + `oapi-codegen/v2` strict server
- `pgx/v5` + `sqlc`
- Protobuf + gRPC for Go/Python interfaces
- `rabbitmq/amqp091-go`
- AWS SDK for Go v2 S3 client behind ArtifactStore
- `log/slog`, OpenTelemetry, and Prometheus
- manual constructor wiring

Do not introduce Gin, Fiber, Echo, GORM, LangChainGo, Redis/Asynq, Go auto-migrate, or a dependency-injection framework without an accepted ADR.

Go implementation rules:

- Place executables under `go/cmd/{control-plane,outbox-relay,llm-gateway}`. `main.go` wires configuration and adapters; it does not contain domain rules.
- Keep domain Module interfaces independent of HTTP, gRPC, pgx, sqlc, RabbitMQ, AWS, and provider SDK types.
- HTTP handlers only decode, call an Application Module, and map stable errors.
- Generated OpenAPI, Protobuf, and sqlc files are not edited manually. Pin generators and dependencies in `go.mod`/`go.sum`; do not use floating `@latest` in source or CI.
- Propagate `context.Context`, deadlines, request ID, trace context, Job Attempt, and execution epoch.
- Prefer standard library packages. Add a dependency only when it materially deepens a Module or provides a required adapter.
- Do not create generic `utils`, `helpers`, `common`, or pass-through repository packages. Name Modules with terms from `CONTEXT.md`.

## Database And Training Lifecycle

- Alembic is the only Phase 1 schema migration owner. Go may verify schema compatibility but must not auto-create or alter tables.
- Write Job, Training Run, Job Event, and Outbox Event in one PostgreSQL transaction.
- Use explicit SQL and locking for lifecycle changes. Avoid unlocked read-then-write state transitions.
- Every attempt receives `attempt_id` and a monotonically increasing `execution_epoch` fencing token.
- `heartbeat`, `progress`, `complete`, and `fail` must carry the current attempt and epoch. Reject stale workers with `FENCED`.
- Completion must atomically verify the fence, register Artifacts, create the Model Version, update lifecycle rows, and append the Job Event.
- RabbitMQ messages may be duplicated. Claim and completion must be idempotent; never claim exactly-once delivery.
- ACK a Dispatch Message after a successful/obsolete claim decision, not after the full training run. Lease recovery and the reaper own post-ACK worker failure.

## Artifact Rules

- Never pass a local absolute path across Go/Python interfaces. Use an Artifact Descriptor with canonical URI, SHA-256, `size_bytes`, content type, schema version, producer, and lineage IDs.
- Never use S3/MinIO ETag as the content SHA-256.
- Upload to an attempt-scoped staging or immutable content-addressed key. Register or promote only after verification.
- On cache miss, download to a temporary file, stream-verify SHA-256 and size, then atomically rename into the SHA cache.
- Integrity mismatch fails closed and must not create a succeeded Job or Model Version.
- Keep both LocalFilesystem and S3 ArtifactStore adapters passing the same interface contract tests. Production new writes use S3-compatible storage.
- Git LFS is only for approved immutable pretrained weights. Dataset files, features, trained heads, reports, and uploaded images belong in ArtifactStore.
- The approved Phase 1 weight set contains DINOv3 ViT-S only. Phase 2 may add only ImageNet ViT-S and ImageNet ResNet-50 as defined in the Phase 2 plan. Keep `weights/manifest.json`, every LFS pointer, vendored upstream licenses, and MinIO promotion checksums consistent.

## Phase 2 Frontend And Model Rules

- The approved visual direction is A “Research Experiment Workbench”: light gray-blue canvas, white panels, restrained borders/shadows, indigo primary actions, and semantic status colors.
- Keep React, Vite, and React Router. Use Apache ECharts only as a renderer; Training Metric Points come from the Go Control Plane API.
- Production pages and components must not import `frontend/src/prototypes`. Migrate the approved shape into semantic tokens, a shared Shell, design-system components, and domain feature modules, then remove the prototype route after visual regression handoff.
- Split route pages from the legacy monolithic `pages.jsx` by feature. DTO-to-view-model conversion belongs to the owning feature, not to generic UI components.
- The training selector exposes exactly DINOv3 ViT-S, ImageNet ViT-S, and ImageNet ResNet-50. Do not add ViT-B/L, ResNet-18/101, ConvNeXt, Swin, or full-backbone fine-tuning in Phase 2.
- Dynamic charts use cursor-based incremental polling and stop at terminal state. Do not put per-epoch metrics on RabbitMQ or introduce WebSocket/SSE without a revised plan or ADR.
- Model comparisons must surface comparability and N/A explicitly. Never rank metrics across incompatible Dataset Versions or silently replace missing values with zero.
- `champion` and `challenger` are Dataset-scoped mutable Model Aliases. Promotion is explicit, transactional, audited, and never based on accuracy alone.

## HTTP, gRPC, And Message Contracts

- Preserve existing public `/api/*` methods, paths, snake_case fields, enums, status codes, and response envelopes while migrating.
- OpenAPI is the public HTTP contract source. Compare each Go route batch against FastAPI golden responses before cutover.
- Protobuf is the internal Go/Python RPC contract. Add fields compatibly and never reuse removed field numbers.
- RabbitMQ Dispatch Messages contain only stable identifiers, `schema_version`, and `dispatch_generation`; fetch canonical payload after claim.
- Do not put image bytes, weights, feature matrices, provider credentials, or full training configs in RabbitMQ messages.
- Public request fields cannot override provider model, base URL, credential, object-store endpoint, or internal routing.

## Verification

Run checks proportional to the changed area. When the target Go workspace exists:

```bash
cd go
go test ./...
go vet ./...
```

Python and ML toolkit:

```bash
uv run --group dev pytest
uv run --extra dinov3 --group dev pytest backend/tests/test_dinov3_vits_training_integration.py
uv run --group dev python -m finevision.ml_toolkit.smoke --work-dir .finevision-smoke
```

Frontend:

```bash
cd frontend
npm run build
npm run smoke:api-client
npm run smoke:routes
```

For Phase 2 frontend work, also cover incremental metric adapters, terminal polling, comparison warnings/N/A, stable `backbone_key` submission, keyboard navigation, responsive layout, and approved A-style visual regression.

For lifecycle, messaging, storage, or concurrency changes, also run integration/fault tests covering duplicate dispatch, relay crash, worker lease expiry, stale epoch, cancel/complete races, idempotent completion, and corrupted Artifact bytes.

## Change Discipline

- Preserve unrelated user changes in a dirty worktree.
- Implement Phase 1 as route-level or vertical tracer slices; do not replace the entire backend in one change.
- Do not keep permanent Go/Python dual-write or dual-state paths as a migration shortcut.
- Update `CONTEXT.md` when a new domain concept becomes canonical. Use Module, interface, implementation, seam, adapter, depth, leverage, and locality consistently in architecture documents.
- Update OpenAPI/Protobuf/sqlc source contracts and generated outputs in the same change.
- Update technical documentation when a target decision changes. Use an ADR for changes to language ownership, queue, persistence, storage, or framework choices.
- Never commit secrets, model-provider keys, real private datasets, generated caches, or unapproved model weights.

## Definition Of Done

A change is complete only when:

- ownership and current/target status are unambiguous;
- the smallest relevant Module interface is tested;
- public and internal contracts remain compatible or are explicitly versioned;
- lifecycle operations are transactional, idempotent, and fenced where applicable;
- Artifact integrity and lineage are preserved;
- logs and errors contain correlation IDs without secrets or raw image bytes;
- relevant Go, Python, frontend, integration, and documentation checks pass.
