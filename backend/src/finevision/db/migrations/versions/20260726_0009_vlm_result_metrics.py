"""persist VLM review runtime metrics

Revision ID: 20260726_0009
Revises: 20260726_0008
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_0009"
down_revision = "20260726_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vlm_review_results", sa.Column("latency_seconds", sa.Float(), nullable=True))
    op.add_column("vlm_review_results", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("vlm_review_results", sa.Column("generated_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("vlm_review_results", "generated_tokens")
    op.drop_column("vlm_review_results", "input_tokens")
    op.drop_column("vlm_review_results", "latency_seconds")
