"""add inference run traceability

Revision ID: 20260623_0007
Revises: 20260623_0006
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260623_0007"
down_revision = "20260623_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inference_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_key", sa.Text(), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.CheckConstraint("run_type in ('single', 'upload', 'upload_folder')", name="ck_inference_runs_run_type"),
        sa.CheckConstraint(
            "status in ('running', 'succeeded', 'partial_failed', 'failed')",
            name="ck_inference_runs_status",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"]),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
    )
    op.create_unique_constraint("uq_inference_runs_run_key", "inference_runs", ["run_key"])
    op.create_index(
        "ix_inference_runs_scope_created_at",
        "inference_runs",
        ["dataset_version_id", "model_version_id", "created_at"],
    )

    op.add_column("inference_events", sa.Column("inference_run_id", postgresql.UUID(as_uuid=True)))
    op.add_column("review_items", sa.Column("inference_run_id", postgresql.UUID(as_uuid=True)))
    op.add_column("feedback_items", sa.Column("inference_run_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_inference_events_inference_run_id",
        "inference_events",
        "inference_runs",
        ["inference_run_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_review_items_inference_run_id",
        "review_items",
        "inference_runs",
        ["inference_run_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_feedback_items_inference_run_id",
        "feedback_items",
        "inference_runs",
        ["inference_run_id"],
        ["id"],
    )
    op.create_index("ix_inference_events_run_created_at", "inference_events", ["inference_run_id", "created_at"])
    op.create_index("ix_review_items_run_status", "review_items", ["inference_run_id", "status"])
    op.create_index("ix_feedback_items_run_created_at", "feedback_items", ["inference_run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_feedback_items_run_created_at", table_name="feedback_items")
    op.drop_index("ix_review_items_run_status", table_name="review_items")
    op.drop_index("ix_inference_events_run_created_at", table_name="inference_events")
    op.drop_constraint("fk_feedback_items_inference_run_id", "feedback_items", type_="foreignkey")
    op.drop_constraint("fk_review_items_inference_run_id", "review_items", type_="foreignkey")
    op.drop_constraint("fk_inference_events_inference_run_id", "inference_events", type_="foreignkey")
    op.drop_column("feedback_items", "inference_run_id")
    op.drop_column("review_items", "inference_run_id")
    op.drop_column("inference_events", "inference_run_id")

    op.drop_index("ix_inference_runs_scope_created_at", table_name="inference_runs")
    op.drop_constraint("uq_inference_runs_run_key", "inference_runs", type_="unique")
    op.drop_table("inference_runs")
