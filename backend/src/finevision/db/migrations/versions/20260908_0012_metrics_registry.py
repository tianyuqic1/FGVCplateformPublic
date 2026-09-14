"""add training metrics and governed model registry

Revision ID: 20260908_0012
Revises: 20260907_0011
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260908_0012"
down_revision = "20260907_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_metric_points",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("training_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_epoch", sa.BigInteger(), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("step", sa.BigInteger(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("step >= 0", name="ck_training_metric_points_step"),
        sa.ForeignKeyConstraint(["training_run_id"], ["training_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attempt_id"], ["job_attempts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "training_run_id",
            "attempt_id",
            "metric_name",
            "step",
            name="uq_training_metric_points_attempt_name_step",
        ),
    )
    op.create_index(
        "ix_training_metric_points_run_cursor",
        "training_metric_points",
        ["training_run_id", "id"],
    )
    op.create_index(
        "ix_training_metric_points_series",
        "training_metric_points",
        ["training_run_id", "attempt_id", "metric_name", "step"],
    )

    for column in (
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
        sa.Column("evaluation_context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    ):
        op.add_column("model_versions", column)

    op.create_table(
        "model_version_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("alias in ('champion', 'challenger')", name="ck_model_version_aliases_alias"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dataset_id", "alias", name="uq_model_version_aliases_dataset_alias"),
    )
    op.create_index("ix_model_version_aliases_version", "model_version_aliases", ["model_version_id"])

    op.create_table(
        "model_version_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text()),
        sa.Column("alias", sa.Text()),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_model_version_events_version_created",
        "model_version_events",
        ["model_version_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_version_events_version_created", table_name="model_version_events")
    op.drop_table("model_version_events")
    op.drop_index("ix_model_version_aliases_version", table_name="model_version_aliases")
    op.drop_table("model_version_aliases")
    for column in (
        "evaluation_context",
        "head_type",
        "pooling",
        "parameter_count",
        "feature_dim",
        "input_size",
        "pretraining_dataset",
        "pretraining_method",
        "architecture",
        "backbone_key",
        "description",
        "name",
    ):
        op.drop_column("model_versions", column)
    op.drop_index("ix_training_metric_points_series", table_name="training_metric_points")
    op.drop_index("ix_training_metric_points_run_cursor", table_name="training_metric_points")
    op.drop_table("training_metric_points")
