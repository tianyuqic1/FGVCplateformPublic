from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()

from .annotation_schema import register as register_annotation
register_annotation(metadata)

dataset_card_revisions = sa.Table(
    "dataset_card_revisions", metadata,
    sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id"), primary_key=True),
    sa.Column("revision", sa.Integer(), primary_key=True),
    sa.Column("card", postgresql.JSONB(), nullable=False),
    sa.Column("draft_id", postgresql.UUID(as_uuid=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.CheckConstraint("revision > 0"),
    sa.CheckConstraint("jsonb_typeof(card) = 'object'"),
)
dataset_card_generations = sa.Table(
    "dataset_card_generations", metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id"), nullable=False),
    sa.Column("base_revision", sa.Integer(), nullable=False),
    sa.Column("input_sha256", sa.Text(), nullable=False),
    sa.Column("prompt_version", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("result", postgresql.JSONB()),
    sa.Column("error_code", sa.Text()),
    sa.Column("applied_revision", sa.Integer()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now() + interval '180 seconds'")),
    sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')"),
)
sa.Index("dataset_card_one_active_generation", dataset_card_generations.c.dataset_version_id, unique=True, postgresql_where=sa.text("status = 'running'"))
sa.Index("dataset_card_generation_history", dataset_card_generations.c.dataset_version_id, dataset_card_generations.c.created_at.desc())

datasets = sa.Table(
    "datasets",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("dataset_key", sa.Text(), nullable=False, unique=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column("domain", sa.Text()),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status in ('draft', 'ready', 'archived')", name="ck_datasets_status"),
)

jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("job_key", sa.Text(), nullable=False, unique=True),
    sa.Column("job_type", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("payload", postgresql.JSONB(), nullable=False),
    sa.Column("result", postgresql.JSONB()),
    sa.Column("error_message", sa.Text()),
    sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
    sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    sa.Column("lease_owner", sa.Text()),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    sa.Column("dispatch_generation", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("execution_epoch", sa.BigInteger(), nullable=False, server_default="0"),
    sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("active_attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_attempts.id")),
    sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "status in ('queued', 'paused', 'running', 'succeeded', 'failed', 'cancelled')",
        name="ck_jobs_status",
    ),
)

job_attempts = sa.Table(
    "job_attempts",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("attempt_number", sa.Integer(), nullable=False),
    sa.Column("execution_epoch", sa.BigInteger(), nullable=False),
    sa.Column("worker_id", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("error_code", sa.Text()),
    sa.Column("error_message", sa.Text()),
    sa.Column("completion_key", sa.Text()),
    sa.Column("result_digest", sa.Text()),
    sa.Column("result", postgresql.JSONB()),
    sa.CheckConstraint(
        "status in ('running', 'succeeded', 'failed', 'fenced', 'cancelled', 'expired')",
        name="ck_job_attempts_status",
    ),
    sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_attempts_number"),
    sa.UniqueConstraint("job_id", "execution_epoch", name="uq_job_attempts_epoch"),
    sa.UniqueConstraint("job_id", "completion_key", name="uq_job_attempts_completion_key"),
)

outbox_events = sa.Table(
    "outbox_events",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("message_id", sa.Text(), nullable=False, unique=True),
    sa.Column("aggregate_type", sa.Text(), nullable=False),
    sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("aggregate_version", sa.BigInteger(), nullable=False),
    sa.Column("event_type", sa.Text(), nullable=False),
    sa.Column("schema_version", sa.Integer(), nullable=False),
    sa.Column("payload", postgresql.JSONB(), nullable=False),
    sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True)),
    sa.Column("lock_owner", sa.Text()),
    sa.Column("lock_expires_at", sa.DateTime(timezone=True)),
    sa.Column("publish_attempts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("last_error", sa.Text()),
)

sa.Index("ix_job_attempts_job_status", job_attempts.c.job_id, job_attempts.c.status)
sa.Index("ix_job_attempts_lease", job_attempts.c.status, job_attempts.c.lease_expires_at)
sa.Index("ix_jobs_dispatchable", jobs.c.status, jobs.c.available_at, jobs.c.priority, jobs.c.queued_at)
sa.Index(
    "ix_outbox_events_pending",
    outbox_events.c.published_at,
    outbox_events.c.available_at,
    outbox_events.c.created_at,
)

dataset_versions = sa.Table(
    "dataset_versions",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id"), nullable=False),
    sa.Column("version_key", sa.Text(), nullable=False, unique=True),
    sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id")),
    sa.Column("source_type", sa.Text(), nullable=False, server_default="initial_import"),
    sa.Column("change_summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("import_request_id", postgresql.UUID(as_uuid=True), unique=True),
    sa.Column("import_fingerprint", sa.Text()),
    sa.UniqueConstraint("dataset_id", "version_number", name="dataset_version_number_unique"),
    sa.CheckConstraint("version_number > 0", name="dataset_version_number_positive"),
    sa.CheckConstraint("source_type IN ('initial_import','manual_expansion','review_feedback','mixed_expansion','annotation_publish','annotation_append')", name="dataset_version_source_type"),
    sa.Column("root_uri", sa.Text(), nullable=False),
    sa.Column("sample_count", sa.Integer(), nullable=False),
    sa.Column("class_count", sa.Integer(), nullable=False),
    sa.Column("split_summary", postgresql.JSONB(), nullable=False),
    sa.Column("readiness_status", sa.Text(), nullable=False),
    sa.Column("readiness_report", postgresql.JSONB(), nullable=False),
    sa.Column("manifest_artifact_id", postgresql.UUID(as_uuid=True)),
    sa.Column("created_by_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

artifacts = sa.Table(
    "artifacts",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("artifact_key", sa.Text(), nullable=False, unique=True),
    sa.Column("artifact_type", sa.Text(), nullable=False),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id")),
    sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id")),
    sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id")),
    sa.Column("uri", sa.Text(), nullable=False),
    sa.Column("checksum", sa.Text()),
    sa.Column("content_type", sa.Text()),
    sa.Column("size_bytes", sa.BigInteger()),
    sa.Column("storage_version", sa.Text(), nullable=False, server_default="s3-v1"),
    sa.Column("producer", sa.Text(), nullable=False, server_default="legacy"),
    sa.Column("training_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("training_runs.id")),
    sa.Column("attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_attempts.id")),
    sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("verified_at", sa.DateTime(timezone=True)),
    sa.Column("artifact_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "checksum is null or (char_length(checksum) = 64 and size_bytes >= 0)",
        name="ck_artifacts_ready_integrity",
    ),
)

job_events = sa.Table(
    "job_events",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
    sa.Column("event_type", sa.Text(), nullable=False),
    sa.Column("message", sa.Text()),
    sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

training_runs = sa.Table(
    "training_runs",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("run_key", sa.Text(), nullable=False, unique=True),
    sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id"), nullable=False),
    sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id"), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("backbone_id", sa.Text(), nullable=False),
    sa.Column("extractor_config", postgresql.JSONB(), nullable=False),
    sa.Column("head_config", postgresql.JSONB(), nullable=False),
    sa.Column("feature_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
    sa.Column("model_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
    sa.Column("report_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
    sa.Column("calibration_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
    sa.Column("threshold_strategy_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
    sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("error_message", sa.Text()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "status in ('queued', 'paused', 'running', 'succeeded', 'failed', 'cancelled')",
        name="ck_training_runs_status",
    ),
)

model_versions = sa.Table(
    "model_versions",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("model_key", sa.Text(), nullable=False, unique=True),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id"), nullable=False),
    sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id"), nullable=False),
    sa.Column("training_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("training_runs.id"), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("model_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id"), nullable=False),
    sa.Column("calibration_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
    sa.Column("threshold_strategy_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
    sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("name", sa.Text()),
    sa.Column("description", sa.Text()),
    sa.Column("backbone_key", sa.Text()),
    sa.Column("architecture", sa.Text()),
    sa.Column("pretraining_method", sa.Text()),
    sa.Column("pretraining_dataset", sa.Text()),
    sa.Column("input_size", sa.Integer()),
    sa.Column("feature_dim", sa.Integer()),
    sa.Column("parameter_count", sa.BigInteger()),
    sa.Column("pooling", sa.Text()),
    sa.Column("head_type", sa.Text()),
    sa.Column("release_version", sa.Text()),
    sa.Column("release_sequence", sa.BigInteger()),
    sa.Column("release_reason", sa.Text()),
    sa.Column("release_signature", sa.Text()),
    sa.UniqueConstraint("dataset_id", "release_version", name="model_release_number_unique"),
    sa.UniqueConstraint("dataset_id", "release_sequence", name="model_release_sequence_unique"),
    sa.Column("evaluation_context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "status in ('candidate', 'staging', 'production', 'archived', 'failed')",
        name="ck_model_versions_status",
    ),
)

training_metric_points = sa.Table(
    "training_metric_points",
    metadata,
    sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
    sa.Column(
        "training_run_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("training_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "attempt_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("job_attempts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("execution_epoch", sa.BigInteger(), nullable=False),
    sa.Column("metric_name", sa.Text(), nullable=False),
    sa.Column("step", sa.BigInteger(), nullable=False),
    sa.Column("value", sa.Float(), nullable=False),
    sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.CheckConstraint("step >= 0", name="ck_training_metric_points_step"),
    sa.UniqueConstraint(
        "training_run_id",
        "attempt_id",
        "metric_name",
        "step",
        name="uq_training_metric_points_attempt_name_step",
    ),
)

model_version_aliases = sa.Table(
    "model_version_aliases",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    sa.Column(
        "model_version_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("model_versions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("alias", sa.Text(), nullable=False),
    sa.Column("updated_by", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("alias in ('champion', 'challenger')", name="ck_model_version_aliases_alias"),
    sa.UniqueConstraint("dataset_id", "alias", name="uq_model_version_aliases_dataset_alias"),
)

model_version_events = sa.Table(
    "model_version_events",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "model_version_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("model_versions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("event_type", sa.Text(), nullable=False),
    sa.Column("from_status", sa.Text()),
    sa.Column("to_status", sa.Text()),
    sa.Column("alias", sa.Text()),
    sa.Column("actor", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text()),
    sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

inference_runs = sa.Table(
    "inference_runs",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("run_key", sa.Text(), nullable=False, unique=True),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id"), nullable=False),
    sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id"), nullable=False),
    sa.Column("model_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_versions.id"), nullable=False),
    sa.Column("run_type", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("review_item_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("applied_policy_key", sa.Text()),
    sa.Column("applied_policy_source", sa.Text()),
    sa.Column("threshold_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("request_payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint(
        "run_type in ('single', 'upload', 'upload_folder')",
        name="ck_inference_runs_run_type",
    ),
    sa.CheckConstraint(
        "status in ('running', 'succeeded', 'partial_failed', 'failed')",
        name="ck_inference_runs_status",
    ),
)

inference_events = sa.Table(
    "inference_events",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("event_key", sa.Text(), nullable=False, unique=True),
    sa.Column("inference_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inference_runs.id")),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id"), nullable=False),
    sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id"), nullable=False),
    sa.Column("model_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_versions.id"), nullable=False),
    sa.Column("model_status", sa.Text(), nullable=False),
    sa.Column("deployment_id", sa.Text()),
    sa.Column("runtime_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("model_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
    sa.Column("feature_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
    sa.Column("threshold_strategy_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
    sa.Column("input_type", sa.Text(), nullable=False),
    sa.Column("input_ref", sa.Text()),
    sa.Column("sample_id", sa.Text()),
    sa.Column("decision", sa.Text(), nullable=False),
    sa.Column("confidence", sa.Float()),
    sa.Column("margin", sa.Float()),
    sa.Column("ood_score", sa.Float()),
    sa.Column("reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("request_payload", postgresql.JSONB(), nullable=False),
    sa.Column("result_payload", postgresql.JSONB(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("input_type in ('sample', 'image_path', 'upload')", name="ck_inference_events_input_type"),
    sa.CheckConstraint("decision in ('accept', 'abstain', 'reject_ood')", name="ck_inference_events_decision"),
)

review_items = sa.Table(
    "review_items",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("review_key", sa.Text(), nullable=False, unique=True),
    sa.Column(
        "inference_event_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("inference_events.id"),
        nullable=False,
        unique=True,
    ),
    sa.Column("inference_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inference_runs.id")),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id"), nullable=False),
    sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id"), nullable=False),
    sa.Column("model_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_versions.id"), nullable=False),
    sa.Column("sample_id", sa.Text()),
    sa.Column("input_ref", sa.Text()),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("risk_type", sa.Text(), nullable=False),
    sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
    sa.Column("reason", sa.Text(), nullable=False),
    sa.Column("reason_codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("assistance_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("assigned_to", sa.Text()),
    sa.Column("submitted_at", sa.DateTime(timezone=True)),
    sa.Column("feedbacked_at", sa.DateTime(timezone=True)),
    sa.Column("completed_by", sa.Text()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "status in ('pending', 'submitted', 'feedbacked', 'skipped', 'disputed')",
        name="ck_review_items_status",
    ),
    sa.CheckConstraint(
        "risk_type in ('low_confidence', 'low_margin', 'ood_candidate', 'mixed')",
        name="ck_review_items_risk_type",
    ),
)

feedback_items = sa.Table(
    "feedback_items",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("feedback_key", sa.Text(), nullable=False, unique=True),
    sa.Column(
        "review_item_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("review_items.id"),
        nullable=False,
        unique=True,
    ),
    sa.Column("inference_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inference_events.id"), nullable=False),
    sa.Column("inference_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inference_runs.id")),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id"), nullable=False),
    sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id"), nullable=False),
    sa.Column("model_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_versions.id"), nullable=False),
    sa.Column("sample_id", sa.Text()),
    sa.Column("final_label", sa.Text()),
    sa.Column("final_outcome", sa.Text(), nullable=False),
    sa.Column("destination", sa.Text(), nullable=False),
    sa.Column("reviewer_note", sa.Text()),
    sa.Column("feedback_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_by", sa.Text()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "final_outcome in ('confirmed_label', 'corrected_label', 'ood', 'bad_image', 'uncertain', 'ignore')",
        name="ck_feedback_items_final_outcome",
    ),
    sa.CheckConstraint(
        "destination in ('training_candidate', 'ood_stress', 'bad_image', 'taxonomy_dispute', 'ignore')",
        name="ck_feedback_items_destination",
    ),
)

sa.Index(
    "ix_inference_runs_scope_created_at",
    inference_runs.c.dataset_version_id,
    inference_runs.c.model_version_id,
    inference_runs.c.created_at,
)
sa.Index(
    "ix_training_metric_points_run_cursor",
    training_metric_points.c.training_run_id,
    training_metric_points.c.id,
)
sa.Index(
    "ix_training_metric_points_series",
    training_metric_points.c.training_run_id,
    training_metric_points.c.attempt_id,
    training_metric_points.c.metric_name,
    training_metric_points.c.step,
)
sa.Index("ix_model_version_aliases_version", model_version_aliases.c.model_version_id)
sa.Index(
    "ix_model_version_events_version_created",
    model_version_events.c.model_version_id,
    model_version_events.c.created_at,
)
sa.Index(
    "ix_inference_events_run_created_at",
    inference_events.c.inference_run_id,
    inference_events.c.created_at,
)
sa.Index(
    "ix_review_items_run_status",
    review_items.c.inference_run_id,
    review_items.c.status,
)
sa.Index(
    "ix_feedback_items_run_created_at",
    feedback_items.c.inference_run_id,
    feedback_items.c.created_at,
)

abstention_policy_versions = sa.Table(
    "abstention_policy_versions",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("policy_key", sa.Text(), nullable=False, unique=True),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id"), nullable=False),
    sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id"), nullable=False),
    sa.Column("model_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_versions.id"), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("target_selective_risk", sa.Float(), nullable=False),
    sa.Column("tau_conf", sa.Float(), nullable=False),
    sa.Column("tau_margin", sa.Float(), nullable=False),
    sa.Column("tau_ood", sa.Float()),
    sa.Column("source_feedback_count", sa.Integer(), nullable=False),
    sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("selection_config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_by", sa.Text()),
    sa.Column("activated_by", sa.Text()),
    sa.Column("activation_reason", sa.Text()),
    sa.Column("activated_at", sa.DateTime(timezone=True)),
    sa.Column("deactivated_by", sa.Text()),
    sa.Column("deactivation_reason", sa.Text()),
    sa.Column("deactivated_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status in ('shadow', 'candidate', 'active', 'superseded', 'deactivated', 'archived')", name="ck_abstention_policy_versions_status"),
    sa.CheckConstraint("target_selective_risk >= 0 and target_selective_risk <= 1", name="ck_abstention_policy_target_risk"),
    sa.CheckConstraint("tau_conf >= 0 and tau_conf <= 1", name="ck_abstention_policy_tau_conf"),
    sa.CheckConstraint("tau_margin >= 0 and tau_margin <= 1", name="ck_abstention_policy_tau_margin"),
    sa.CheckConstraint("tau_ood is null or tau_ood >= 0", name="ck_abstention_policy_tau_ood"),
)

sa.Index(
    "ix_abstention_policy_versions_scope_created_at",
    abstention_policy_versions.c.dataset_version_id,
    abstention_policy_versions.c.model_version_id,
    abstention_policy_versions.c.created_at,
)
sa.Index(
    "ix_abstention_policy_versions_status_created_at",
    abstention_policy_versions.c.status,
    abstention_policy_versions.c.created_at,
)
sa.Index(
    "uq_abstention_policy_versions_active_scope",
    abstention_policy_versions.c.dataset_version_id,
    abstention_policy_versions.c.model_version_id,
    unique=True,
    postgresql_where=abstention_policy_versions.c.status == "active",
)

abstention_shadow_decisions = sa.Table(
    "abstention_shadow_decisions",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "policy_version_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("abstention_policy_versions.id"),
        nullable=False,
    ),
    sa.Column("inference_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inference_events.id"), nullable=False),
    sa.Column("current_decision", sa.Text(), nullable=False),
    sa.Column("shadow_decision", sa.Text(), nullable=False),
    sa.Column("decision_diff", sa.Text(), nullable=False),
    sa.Column("score_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "current_decision in ('accept', 'abstain', 'reject_ood')",
        name="ck_abstention_shadow_current_decision",
    ),
    sa.CheckConstraint(
        "shadow_decision in ('accept', 'abstain', 'reject_ood')",
        name="ck_abstention_shadow_shadow_decision",
    ),
    sa.CheckConstraint(
        "decision_diff in ('same', 'new_accepts_old_abstains', 'new_abstains_old_accepts', 'new_rejects_ood', 'other_change')",
        name="ck_abstention_shadow_decision_diff",
    ),
    sa.UniqueConstraint("policy_version_id", "inference_event_id", name="uq_abstention_shadow_policy_inference"),
)

sa.Index(
    "ix_abstention_shadow_policy_diff_created_at",
    abstention_shadow_decisions.c.policy_version_id,
    abstention_shadow_decisions.c.decision_diff,
    abstention_shadow_decisions.c.created_at,
)


dataset_version_feedback = sa.Table(
    "dataset_version_feedback", metadata,
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id"), primary_key=True),
    sa.Column("feedback_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("feedback_items.id"), primary_key=True),
    sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataset_versions.id"), nullable=False),
)

hardware_nodes = sa.Table(
    "hardware_nodes", metadata,
    sa.Column("node_id", sa.Text(), primary_key=True),
    sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("snapshot", postgresql.JSONB(), nullable=False),
)
hardware_samples = sa.Table(
    "hardware_samples", metadata,
    sa.Column("node_id", sa.Text(), sa.ForeignKey("hardware_nodes.node_id", ondelete="CASCADE"), primary_key=True),
    sa.Column("received_at", sa.DateTime(timezone=True), primary_key=True),
    sa.Column("snapshot", postgresql.JSONB(), nullable=False),
)
sa.Index("hardware_samples_retention", hardware_samples.c.received_at)

model_deployments = sa.Table(
    "model_deployments", metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("model_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_versions.id"), nullable=False),
    sa.Column("source_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id"), nullable=False),
    sa.Column("compiled_artifact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifacts.id")),
    sa.Column("runtime", sa.Text(), nullable=False),
    sa.Column("precision", sa.Text(), nullable=False),
    sa.Column("target_profile", sa.Text(), nullable=False),
    sa.Column("max_batch", sa.Integer(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("source_descriptor", postgresql.JSONB(), nullable=False),
    sa.Column("compiled_descriptor", postgresql.JSONB()),
    sa.Column("validation", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("build_token", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("worker_id", sa.Text()),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    sa.Column("error", sa.Text(), nullable=False, server_default=""),
    sa.Column("actor", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.UniqueConstraint("model_version_id", "source_artifact_id", "runtime", "precision", "target_profile", "max_batch"),
    sa.CheckConstraint("runtime IN ('tensorrt','ascend_acl')"),
    sa.CheckConstraint("precision IN ('FP32','FP16')"),
    sa.CheckConstraint("max_batch BETWEEN 1 AND 32"),
    sa.CheckConstraint("status IN ('queued','building','ready','failed')"),
)
sa.Index("model_deployments_model", model_deployments.c.model_version_id, model_deployments.c.created_at)
deployment_outbox = sa.Table(
    "deployment_outbox", metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("deployment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_deployments.id"), nullable=False),
    sa.Column("build_token", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("runtime", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("published_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint("deployment_id", "build_token"),
)
