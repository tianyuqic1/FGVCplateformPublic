"""add online abstention shadow policy tables

Revision ID: 20260623_0005
Revises: 20260620_0004
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260623_0005"
down_revision = "20260620_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "abstention_policy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_key", sa.Text(), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("target_selective_risk", sa.Float(), nullable=False),
        sa.Column("tau_conf", sa.Float(), nullable=False),
        sa.Column("tau_margin", sa.Float(), nullable=False),
        sa.Column("tau_ood", sa.Float()),
        sa.Column("source_feedback_count", sa.Integer(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("selection_config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('shadow', 'candidate', 'archived')", name="ck_abstention_policy_versions_status"),
        sa.CheckConstraint("target_selective_risk >= 0 and target_selective_risk <= 1", name="ck_abstention_policy_target_risk"),
        sa.CheckConstraint("tau_conf >= 0 and tau_conf <= 1", name="ck_abstention_policy_tau_conf"),
        sa.CheckConstraint("tau_margin >= 0 and tau_margin <= 1", name="ck_abstention_policy_tau_margin"),
        sa.CheckConstraint("tau_ood is null or tau_ood >= 0", name="ck_abstention_policy_tau_ood"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"]),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
    )
    op.create_unique_constraint("uq_abstention_policy_versions_policy_key", "abstention_policy_versions", ["policy_key"])
    op.create_index(
        "ix_abstention_policy_versions_scope_created_at",
        "abstention_policy_versions",
        ["dataset_version_id", "model_version_id", "created_at"],
    )
    op.create_index(
        "ix_abstention_policy_versions_status_created_at",
        "abstention_policy_versions",
        ["status", "created_at"],
    )

    op.create_table(
        "abstention_shadow_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inference_event_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(["policy_version_id"], ["abstention_policy_versions.id"]),
        sa.ForeignKeyConstraint(["inference_event_id"], ["inference_events.id"]),
    )
    op.create_unique_constraint(
        "uq_abstention_shadow_policy_inference",
        "abstention_shadow_decisions",
        ["policy_version_id", "inference_event_id"],
    )
    op.create_index(
        "ix_abstention_shadow_policy_diff_created_at",
        "abstention_shadow_decisions",
        ["policy_version_id", "decision_diff", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_abstention_shadow_policy_diff_created_at", table_name="abstention_shadow_decisions")
    op.drop_constraint("uq_abstention_shadow_policy_inference", "abstention_shadow_decisions", type_="unique")
    op.drop_table("abstention_shadow_decisions")
    op.drop_index("ix_abstention_policy_versions_status_created_at", table_name="abstention_policy_versions")
    op.drop_index("ix_abstention_policy_versions_scope_created_at", table_name="abstention_policy_versions")
    op.drop_constraint("uq_abstention_policy_versions_policy_key", "abstention_policy_versions", type_="unique")
    op.drop_table("abstention_policy_versions")
