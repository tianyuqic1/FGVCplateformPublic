"""create control-plane foundation tables

Revision ID: 20260612_0001
Revises:
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260612_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("domain", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('draft', 'ready', 'archived')", name="ck_datasets_status"),
    )
    op.create_unique_constraint("uq_datasets_dataset_key", "datasets", ["dataset_key"])

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_key", sa.Text(), nullable=False),
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
            "status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
    )
    op.create_unique_constraint("uq_jobs_job_key", "jobs", ["job_key"])
    op.create_index("ix_jobs_status_priority_queued_at", "jobs", ["status", "priority", "queued_at"])
    op.create_index("ix_jobs_lease_expires_at", "jobs", ["lease_expires_at"])

    op.create_table(
        "dataset_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_key", sa.Text(), nullable=False),
        sa.Column("root_uri", sa.Text(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("class_count", sa.Integer(), nullable=False),
        sa.Column("split_summary", postgresql.JSONB(), nullable=False),
        sa.Column("readiness_status", sa.Text(), nullable=False),
        sa.Column("readiness_report", postgresql.JSONB(), nullable=False),
        sa.Column("manifest_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_by_job_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["created_by_job_id"], ["jobs.id"]),
    )
    op.create_unique_constraint("uq_dataset_versions_version_key", "dataset_versions", ["version_key"])
    op.create_index("ix_dataset_versions_dataset_created_at", "dataset_versions", ["dataset_id", "created_at"])

    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("artifact_key", sa.Text(), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("job_id", postgresql.UUID(as_uuid=True)),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("checksum", sa.Text()),
        sa.Column("content_type", sa.Text()),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("artifact_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
    )
    op.create_unique_constraint("uq_artifacts_artifact_key", "artifacts", ["artifact_key"])
    op.create_index("ix_artifacts_dataset_version_type", "artifacts", ["dataset_version_id", "artifact_type"])

    op.create_foreign_key(
        "fk_dataset_versions_manifest_artifact_id",
        "dataset_versions",
        "artifacts",
        ["manifest_artifact_id"],
        ["id"],
    )

    op.create_table(
        "job_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
    )
    op.create_index("ix_job_events_job_created_at", "job_events", ["job_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_job_events_job_created_at", table_name="job_events")
    op.drop_table("job_events")
    op.drop_constraint("fk_dataset_versions_manifest_artifact_id", "dataset_versions", type_="foreignkey")
    op.drop_index("ix_artifacts_dataset_version_type", table_name="artifacts")
    op.drop_constraint("uq_artifacts_artifact_key", "artifacts", type_="unique")
    op.drop_table("artifacts")
    op.drop_index("ix_dataset_versions_dataset_created_at", table_name="dataset_versions")
    op.drop_constraint("uq_dataset_versions_version_key", "dataset_versions", type_="unique")
    op.drop_table("dataset_versions")
    op.drop_index("ix_jobs_lease_expires_at", table_name="jobs")
    op.drop_index("ix_jobs_status_priority_queued_at", table_name="jobs")
    op.drop_constraint("uq_jobs_job_key", "jobs", type_="unique")
    op.drop_table("jobs")
    op.drop_constraint("uq_datasets_dataset_key", "datasets", type_="unique")
    op.drop_table("datasets")
