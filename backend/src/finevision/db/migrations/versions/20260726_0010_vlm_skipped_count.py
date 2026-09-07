"""track skipped VLM review results

Revision ID: 20260726_0010
Revises: 20260726_0009
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_0010"
down_revision = "20260726_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vlm_review_runs",
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("vlm_review_runs", "skipped_count")
