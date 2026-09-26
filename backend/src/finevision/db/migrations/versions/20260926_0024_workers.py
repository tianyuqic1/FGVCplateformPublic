"""Persistent worker inventory; independent of job leases."""
from alembic import op

revision = "20260926_0024"
down_revision = "20260926_0023"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE worker_instances (
      id text PRIMARY KEY, session_id uuid NOT NULL, sequence bigint NOT NULL DEFAULT 0,
      registered_at timestamptz NOT NULL DEFAULT now(),
      received_at timestamptz NOT NULL DEFAULT now(), snapshot jsonb NOT NULL
    );
    CREATE TABLE worker_sessions (
      session_id uuid PRIMARY KEY, worker_id text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX worker_sessions_worker ON worker_sessions(worker_id);
    """)


def downgrade():
    op.execute("DROP TABLE worker_sessions; DROP TABLE worker_instances;")
