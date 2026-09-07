"""add Fine-R1 VLM review runs and results

Revision ID: 20260726_0008
Revises: 20260623_0007
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260726_0008"
down_revision = "20260623_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vlm_review_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_key", sa.Text(), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("inference_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["inference_run_id"], ["inference_runs.id"]),
        sa.UniqueConstraint("run_key", name="uq_vlm_review_runs_run_key"),
    )
    op.create_index("ix_vlm_review_runs_status_created_at", "vlm_review_runs", ["status", "created_at"])

    op.create_table(
        "vlm_review_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("result_key", sa.Text(), nullable=False),
        sa.Column("vlm_review_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_item_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed', 'skipped', 'cancelled')",
            name="ck_vlm_review_results_status",
        ),
        sa.ForeignKeyConstraint(["vlm_review_run_id"], ["vlm_review_runs.id"]),
        sa.ForeignKeyConstraint(["review_item_id"], ["review_items.id"]),
        sa.UniqueConstraint("result_key", name="uq_vlm_review_results_result_key"),
        sa.UniqueConstraint("vlm_review_run_id", "review_item_id", name="uq_vlm_review_result_run_item"),
    )
    op.create_index(
        "ix_vlm_review_results_run_status",
        "vlm_review_results",
        ["vlm_review_run_id", "status"],
    )
    op.create_index(
        "ix_vlm_review_results_review_item",
        "vlm_review_results",
        ["review_item_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_vlm_review_results_review_item", table_name="vlm_review_results")
    op.drop_index("ix_vlm_review_results_run_status", table_name="vlm_review_results")
    op.drop_table("vlm_review_results")
    op.drop_index("ix_vlm_review_runs_status_created_at", table_name="vlm_review_runs")
    op.drop_table("vlm_review_runs")
