"""add phase-one lifecycle/outbox/artifact integrity and remove Fine-R1

Revision ID: 20260907_0011
Revises: 20260726_0010
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260907_0011"
down_revision = "20260726_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fine-R1 is removed by a forward migration so existing migration history remains valid.
    op.drop_table("vlm_review_results")
    op.drop_table("vlm_review_runs")

    op.add_column("jobs", sa.Column("dispatch_generation", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("jobs", sa.Column("execution_epoch", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("jobs", sa.Column("active_attempt_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("jobs", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "job_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_attempts_number"),
        sa.UniqueConstraint("job_id", "execution_epoch", name="uq_job_attempts_epoch"),
        sa.UniqueConstraint("job_id", "completion_key", name="uq_job_attempts_completion_key"),
    )
    op.create_index("ix_job_attempts_job_status", "job_attempts", ["job_id", "status"])
    op.create_index("ix_job_attempts_lease", "job_attempts", ["status", "lease_expires_at"])
    op.create_foreign_key(
        "fk_jobs_active_attempt_id",
        "jobs",
        "job_attempts",
        ["active_attempt_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_jobs_dispatchable", "jobs", ["status", "available_at", "priority", "queued_at"])

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", sa.Text(), nullable=False),
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
        sa.UniqueConstraint("message_id", name="uq_outbox_events_message_id"),
    )
    op.create_index(
        "ix_outbox_events_pending",
        "outbox_events",
        ["published_at", "available_at", "created_at"],
    )

    op.add_column("artifacts", sa.Column("storage_version", sa.Text(), nullable=False, server_default="s3-v1"))
    op.add_column("artifacts", sa.Column("producer", sa.Text(), nullable=False, server_default="legacy"))
    op.add_column("artifacts", sa.Column("training_run_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("artifacts", sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("artifacts", sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("artifacts", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_artifacts_training_run_id",
        "artifacts",
        "training_runs",
        ["training_run_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_artifacts_attempt_id",
        "artifacts",
        "job_attempts",
        ["attempt_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_artifacts_ready_integrity",
        "artifacts",
        "checksum is null or (char_length(checksum) = 64 and size_bytes >= 0)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_artifacts_ready_integrity", "artifacts", type_="check")
    op.drop_constraint("fk_artifacts_attempt_id", "artifacts", type_="foreignkey")
    op.drop_constraint("fk_artifacts_training_run_id", "artifacts", type_="foreignkey")
    for column in ("verified_at", "schema_version", "attempt_id", "training_run_id", "producer", "storage_version"):
        op.drop_column("artifacts", column)

    op.drop_index("ix_outbox_events_pending", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_jobs_dispatchable", table_name="jobs")
    op.drop_constraint("fk_jobs_active_attempt_id", "jobs", type_="foreignkey")
    op.drop_index("ix_job_attempts_lease", table_name="job_attempts")
    op.drop_index("ix_job_attempts_job_status", table_name="job_attempts")
    op.drop_table("job_attempts")
    for column in ("last_heartbeat_at", "active_attempt_id", "available_at", "execution_epoch", "dispatch_generation"):
        op.drop_column("jobs", column)

    op.create_table(
        "vlm_review_runs",
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
        sa.Column("fallback_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("model_revision", sa.Text()),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "vlm_review_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("result_key", sa.Text(), nullable=False, unique=True),
        sa.Column("vlm_review_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vlm_review_runs.id"), nullable=False),
        sa.Column("review_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("review_items.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("candidate_labels", postgresql.JSONB(), nullable=False),
        sa.Column("suggested_label", sa.Text()),
        sa.Column("reasoning", sa.Text()),
        sa.Column("raw_output", sa.Text()),
        sa.Column("image_sha256", sa.Text()),
        sa.Column("model_revision", sa.Text()),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("auto_submit_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("gate_report", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_message", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_seconds", sa.Float()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("generated_tokens", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("vlm_review_run_id", "review_item_id", name="uq_vlm_review_result_run_item"),
    )
