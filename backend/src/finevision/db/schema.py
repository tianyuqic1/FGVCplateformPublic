from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()

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

dataset_versions = sa.Table(
    "dataset_versions",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id"), nullable=False),
    sa.Column("version_key", sa.Text(), nullable=False, unique=True),
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
    sa.Column("artifact_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
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
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "status in ('candidate', 'staging', 'production', 'archived', 'failed')",
        name="ck_model_versions_status",
    ),
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

vlm_review_runs = sa.Table(
    "vlm_review_runs",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("run_key", sa.Text(), nullable=False, unique=True),
    sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("datasets.id")),
    sa.Column("inference_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inference_runs.id")),
    sa.Column("mode", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("requested_limit", sa.Integer(), nullable=False),
    sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("succeeded_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("fallback_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("model_id", sa.Text(), nullable=False),
    sa.Column("model_revision", sa.Text()),
    sa.Column("prompt_version", sa.Text(), nullable=False),
    sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_by", sa.Text()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("mode in ('assisted', 'auto')", name="ck_vlm_review_runs_mode"),
    sa.CheckConstraint(
        "status in ('queued', 'running', 'succeeded', 'partial_failed', 'failed', 'cancelled')",
        name="ck_vlm_review_runs_status",
    ),
)

vlm_review_results = sa.Table(
    "vlm_review_results",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("result_key", sa.Text(), nullable=False, unique=True),
    sa.Column(
        "vlm_review_run_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("vlm_review_runs.id"),
        nullable=False,
    ),
    sa.Column("review_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("review_items.id"), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("candidate_labels", postgresql.JSONB(), nullable=False),
    sa.Column("suggested_label", sa.Text()),
    sa.Column("reasoning", sa.Text()),
    sa.Column("raw_output", sa.Text()),
    sa.Column("image_sha256", sa.Text()),
    sa.Column("model_revision", sa.Text()),
    sa.Column("prompt_version", sa.Text(), nullable=False),
    sa.Column("latency_seconds", sa.Float()),
    sa.Column("input_tokens", sa.Integer()),
    sa.Column("generated_tokens", sa.Integer()),
    sa.Column("auto_submit_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("gate_report", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("error_message", sa.Text()),
    sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "status in ('queued', 'running', 'succeeded', 'failed', 'skipped', 'cancelled')",
        name="ck_vlm_review_results_status",
    ),
    sa.UniqueConstraint("vlm_review_run_id", "review_item_id", name="uq_vlm_review_result_run_item"),
)

sa.Index(
    "ix_vlm_review_runs_status_created_at",
    vlm_review_runs.c.status,
    vlm_review_runs.c.created_at,
)
sa.Index(
    "ix_vlm_review_results_run_status",
    vlm_review_results.c.vlm_review_run_id,
    vlm_review_results.c.status,
)
sa.Index(
    "ix_vlm_review_results_review_item",
    vlm_review_results.c.review_item_id,
    vlm_review_results.c.created_at,
)

sa.Index(
    "ix_inference_runs_scope_created_at",
    inference_runs.c.dataset_version_id,
    inference_runs.c.model_version_id,
    inference_runs.c.created_at,
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
