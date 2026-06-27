"""add paused training queue status

Revision ID: 20260620_0004
Revises: 20260619_0003
Create Date: 2026-06-20
"""

from alembic import op

revision = "20260620_0004"
down_revision = "20260619_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_status",
        "jobs",
        "status in ('queued', 'paused', 'running', 'succeeded', 'failed', 'cancelled')",
    )
    op.drop_constraint("ck_training_runs_status", "training_runs", type_="check")
    op.create_check_constraint(
        "ck_training_runs_status",
        "training_runs",
        "status in ('queued', 'paused', 'running', 'succeeded', 'failed', 'cancelled')",
    )


def downgrade() -> None:
    op.execute("UPDATE training_runs SET status = 'queued' WHERE status = 'paused'")
    op.execute("UPDATE jobs SET status = 'queued' WHERE status = 'paused'")
    op.drop_constraint("ck_training_runs_status", "training_runs", type_="check")
    op.create_check_constraint(
        "ck_training_runs_status",
        "training_runs",
        "status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
    )
    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_status",
        "jobs",
        "status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
    )
