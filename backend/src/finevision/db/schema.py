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
        "status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
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
