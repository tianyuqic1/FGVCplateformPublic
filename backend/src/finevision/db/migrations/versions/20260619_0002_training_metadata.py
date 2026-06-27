"""add training run and model version metadata

Revision ID: 20260619_0002
Revises: 20260612_0001
Create Date: 2026-06-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260619_0002"
down_revision = "20260612_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_key", sa.Text(), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("backbone_id", sa.Text(), nullable=False),
        sa.Column("extractor_config", postgresql.JSONB(), nullable=False),
        sa.Column("head_config", postgresql.JSONB(), nullable=False),
        sa.Column("feature_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("model_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("report_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("calibration_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("threshold_strategy_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_training_runs_status",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"]),
        sa.ForeignKeyConstraint(["feature_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["model_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["report_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["calibration_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["threshold_strategy_artifact_id"], ["artifacts.id"]),
    )
    op.create_unique_constraint("uq_training_runs_run_key", "training_runs", ["run_key"])
    op.create_index("ix_training_runs_status_created_at", "training_runs", ["status", "created_at"])
    op.create_index("ix_training_runs_dataset_version", "training_runs", ["dataset_version_id", "created_at"])

    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("training_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("model_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("calibration_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("threshold_strategy_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('candidate', 'staging', 'production', 'archived', 'failed')",
            name="ck_model_versions_status",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"]),
        sa.ForeignKeyConstraint(["training_run_id"], ["training_runs.id"]),
        sa.ForeignKeyConstraint(["model_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["calibration_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["threshold_strategy_artifact_id"], ["artifacts.id"]),
    )
    op.create_unique_constraint("uq_model_versions_model_key", "model_versions", ["model_key"])
    op.create_index("ix_model_versions_dataset_version", "model_versions", ["dataset_version_id", "created_at"])
    op.create_index("ix_model_versions_status", "model_versions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_model_versions_status", table_name="model_versions")
    op.drop_index("ix_model_versions_dataset_version", table_name="model_versions")
    op.drop_constraint("uq_model_versions_model_key", "model_versions", type_="unique")
    op.drop_table("model_versions")
    op.drop_index("ix_training_runs_dataset_version", table_name="training_runs")
    op.drop_index("ix_training_runs_status_created_at", table_name="training_runs")
    op.drop_constraint("uq_training_runs_run_key", "training_runs", type_="unique")
    op.drop_table("training_runs")
