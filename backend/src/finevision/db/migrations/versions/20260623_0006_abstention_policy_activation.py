"""add abstention policy activation metadata

Revision ID: 20260623_0006
Revises: 20260623_0005
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20260623_0006"
down_revision = "20260623_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("abstention_policy_versions", sa.Column("activated_by", sa.Text()))
    op.add_column("abstention_policy_versions", sa.Column("activation_reason", sa.Text()))
    op.add_column("abstention_policy_versions", sa.Column("activated_at", sa.DateTime(timezone=True)))
    op.add_column("abstention_policy_versions", sa.Column("deactivated_by", sa.Text()))
    op.add_column("abstention_policy_versions", sa.Column("deactivation_reason", sa.Text()))
    op.add_column("abstention_policy_versions", sa.Column("deactivated_at", sa.DateTime(timezone=True)))
    op.drop_constraint("ck_abstention_policy_versions_status", "abstention_policy_versions", type_="check")
    op.create_check_constraint(
        "ck_abstention_policy_versions_status",
        "abstention_policy_versions",
        "status in ('shadow', 'candidate', 'active', 'superseded', 'deactivated', 'archived')",
    )
    op.create_index(
        "uq_abstention_policy_versions_active_scope",
        "abstention_policy_versions",
        ["dataset_version_id", "model_version_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_abstention_policy_versions_active_scope", table_name="abstention_policy_versions")
    op.execute(
        "update abstention_policy_versions set status = 'archived' "
        "where status in ('active', 'superseded', 'deactivated')"
    )
    op.drop_constraint("ck_abstention_policy_versions_status", "abstention_policy_versions", type_="check")
    op.create_check_constraint(
        "ck_abstention_policy_versions_status",
        "abstention_policy_versions",
        "status in ('shadow', 'candidate', 'archived')",
    )
    op.drop_column("abstention_policy_versions", "deactivated_at")
    op.drop_column("abstention_policy_versions", "deactivation_reason")
    op.drop_column("abstention_policy_versions", "deactivated_by")
    op.drop_column("abstention_policy_versions", "activated_at")
    op.drop_column("abstention_policy_versions", "activation_reason")
    op.drop_column("abstention_policy_versions", "activated_by")
