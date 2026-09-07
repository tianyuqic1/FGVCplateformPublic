# FineVision Context

FineVision is a fine-grained image classification platform that governs versioned datasets, compute work, model artifacts, inference decisions, human review, and feedback. This glossary fixes the domain terms used during the Phase 1 and Phase 2 refactors.

## Platform Modules

**Control Plane**:
The Go module that owns FineVision's public HTTP interface, PostgreSQL business state, lifecycle transactions, and orchestration decisions.
_Avoid_: API service, backend

**LLMApplication**:
The Go Control Plane module that owns FineVision prompts, context selection, redaction, advisory invariants, and persistence of LLM Assistance.
_Avoid_: LLM transport, provider client

**LLMGateway**:
The independently deployed Go container that owns external provider adapters, credentials, timeouts, retries, fallback, and structured-output transport without owning FineVision business state.
_Avoid_: Fine-R1 service, LLMApplication

**Compute Plane**:
The Python modules that perform dataset scanning, feature extraction, training, calibration, threshold computation, and inference without owning control-plane state.
_Avoid_: Python backend

**Inference Runtime**:
The long-lived Python Compute Plane implementation that materializes verified Model Artifacts and performs numerical inference for the Go Control Plane.
_Avoid_: Inference API, Model Version

**Training Lifecycle**:
The authoritative progression of a training job and all of its attempts, leases, state transitions, events, artifacts, and terminal result.
_Avoid_: Queue status, worker status

**Training Job**:
A schedulable and retryable intent to execute one training configuration for one Dataset Version.
_Avoid_: Training Run, queue message

**Training Run**:
The product-facing record that preserves training configuration, lineage, metrics, Artifacts, and resulting Model Version.
_Avoid_: Training Job, Job Attempt

**Training Metric Point**:
One append-only named numeric observation reported at a step for a specific Training Run and Job Attempt, ordered by a server-issued cursor for incremental reads.
_Avoid_: latest progress blob, chart event, RabbitMQ metric

**Job Attempt**:
One actual execution of a Training Job by one worker under one fencing epoch.
_Avoid_: Retry message, Training Run

**Dispatch Message**:
An at-least-once RabbitMQ notification that a specific Training Job generation is ready to claim; it is never the Job itself.
_Avoid_: Task, Job record

**Worker Lease**:
The time-limited right of one Job Attempt to execute a Training Job and report progress or completion.
_Avoid_: Lock, queue ownership

**Fencing Token**:
The monotonically increasing execution epoch used to reject updates and results from obsolete Job Attempts.
_Avoid_: Worker ID, message ID

**Transactional Outbox**:
The committed dispatch intent written in the same PostgreSQL transaction as a Training Job state change.
_Avoid_: Job Event, RabbitMQ queue

**Outbox Relay**:
The Go implementation that publishes committed Outbox Events to RabbitMQ with publisher confirms and records delivery metadata.
_Avoid_: Scheduler, Training Lifecycle

**LLM Assistance**:
Advisory output from an external large model used for dataset cards, inference explanations, training diagnosis, review assistance, or feedback curation; it cannot make final governance decisions.
_Avoid_: Auto review, automatic label

**ArtifactStore**:
The deep storage module for immutable object bytes, exposed through an S3-compatible interface and backed by a selected MinIO-compatible deployment in Phase 1.
_Avoid_: Shared volume, database blob, MinIO-specific business interface

## Data And Model Assets

**Dataset**:
A business-level fine-grained classification asset with its own taxonomy and governance history.
_Avoid_: Image folder

**Dataset Version**:
An immutable snapshot of a dataset used by feature extraction, training, evaluation, inference, and review.
_Avoid_: Dataset copy, latest dataset

**Artifact Object**:
The immutable bytes stored in the S3-compatible object store for a Dataset, feature matrix, model, report, or inference input.
_Avoid_: Artifact Record, local file

**Artifact Record**:
The PostgreSQL metadata and relationships that register one Artifact Object by canonical URI, SHA-256, size, and content type.
_Avoid_: Artifact Object, object bytes

**Artifact Descriptor**:
The language-neutral identity and integrity metadata of an Artifact Object exchanged between the Control Plane and Compute Plane.
_Avoid_: Path, file reference

**Artifact Integrity Descriptor**:
The canonical SHA-256 and byte size that must match before an Artifact Object can be registered or loaded.
_Avoid_: ETag, SHA

**Pretrained Weight**:
An approved frozen backbone weight imported from an upstream source and bound to a revision, license, SHA-256, and size.
_Avoid_: Model version, training output

**Model Version**:
A governed model candidate or release tied to one Dataset Version, one Training Lifecycle result, and a complete set of verified inference Artifacts.
_Avoid_: Weight file, checkpoint

**Model Alias**:
A Dataset-scoped mutable pointer such as `champion` or `challenger` that resolves to one immutable Model Version and changes only through an audited Control Plane transaction.
_Avoid_: Model Version status, automatic best model, tag

**Backbone Spec**:
The stable catalog definition binding a `backbone_key` to architecture, preprocessing, pooling, feature dimension, Pretrained Weight identity, and extractor semantic version.
_Avoid_: arbitrary timm model name, download URL, model display label

**Trained Model Artifact**:
The model bytes produced by a Training Run and stored as an Artifact Object.
_Avoid_: Pretrained Weight, Model Version

**Candidate Model**:
A Model Version produced by a successful Training Run but not yet promoted through release governance.
_Avoid_: Production model, weight file

**Exact Scope**:
The Dataset Version and Model Version pair within which inference, thresholds, review, and policy decisions are valid.
_Avoid_: Dataset scope, global model

## Review And Feedback

**Review Item**:
An inference item routed to a human because the model abstained, detected out-of-domain input, or otherwise required judgment.
_Avoid_: VLM task, uncertain record

**Feedback Item**:
A typed and auditable human outcome derived from a completed Review Item and routed to an appropriate feedback pool.
_Avoid_: LLM label, review result

**Abstention Policy**:
A dataset-and-model-scoped policy that decides whether inference is accepted, abstained, or rejected as out of domain.
_Avoid_: Global threshold

## Ambiguities To Avoid

- “任务”必须具体写成 Training Job、Training Run、Job Attempt 或 Dispatch Message。
- “权重”必须具体写成 Pretrained Weight 或 Trained Model Artifact。
- “模型”必须具体写成 backbone、Trained Model Artifact、Model Version 或外部 LLM。
- “指标”必须具体写成 Training Metric Point、Training Run 摘要指标或评估报告指标。
- “数据集”必须具体写成 Dataset、Dataset Version、manifest 或 Artifact Object 集合。
- 产品和代码统一使用官方拼写 **MinIO**，不使用 “MiniIO”。
- 完整性字段统一使用 **SHA-256**，不能用含糊的 “SHA”，也不能与 ETag 混用。
