"""Durable, bounded dataset import queue.

Revision ID: 20260910_0017
Revises: 20260910_0016
"""
from alembic import op

revision = "20260910_0017"
down_revision = "20260910_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE dataset_import_jobs (
      id uuid PRIMARY KEY,
      name text NOT NULL,
      request_id uuid NOT NULL,
      archive jsonb NOT NULL,
      status text NOT NULL CHECK (status IN ('queued','running','succeeded','failed')),
      attempt integer NOT NULL DEFAULT 0,
      lease_until timestamptz,
      result jsonb,
      error text NOT NULL DEFAULT '',
      created_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz
    );
    CREATE UNIQUE INDEX dataset_import_one_active_request
      ON dataset_import_jobs(request_id) WHERE status IN ('queued','running');
    CREATE INDEX dataset_import_queue_order ON dataset_import_jobs(status,created_at);
    CREATE INDEX dataset_import_recent_finished ON dataset_import_jobs(created_at DESC)
      WHERE status IN ('succeeded','failed');
    """)


def downgrade():
    op.drop_table("dataset_import_jobs")
