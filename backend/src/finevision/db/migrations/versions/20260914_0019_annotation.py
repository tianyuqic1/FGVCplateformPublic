"""Human-in-the-loop annotation projects, durable jobs and confirmed memory outbox."""
from alembic import op

revision = "20260914_0019"
down_revision = "20260913_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE annotation_projects (
      id uuid PRIMARY KEY, name text NOT NULL, classes jsonb NOT NULL,
      method text NOT NULL CHECK(method IN ('A','B','C','D')),
      domain text NOT NULL CHECK(domain IN ('general','birds','cars')),
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE annotation_tasks (
      id uuid PRIMARY KEY, seq bigserial UNIQUE,
      project_id uuid NOT NULL REFERENCES annotation_projects(id),
      filename text NOT NULL, image jsonb NOT NULL, sha text NOT NULL,
      status text NOT NULL DEFAULT 'pending' CHECK(status IN
        ('pending','queued','running','suggested','failed','unknown','confirmed')),
      result jsonb NOT NULL DEFAULT '{}', label text NOT NULL DEFAULT '',
      actor text NOT NULL DEFAULT '', claim_token uuid, lease_until timestamptz,
      error text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(project_id,sha)
    );
    CREATE INDEX annotation_tasks_queue ON annotation_tasks(status,seq);
    CREATE INDEX annotation_tasks_project ON annotation_tasks(project_id,status,seq);
    CREATE TABLE annotation_memory_outbox (
      task_id uuid PRIMARY KEY REFERENCES annotation_tasks(id),
      delivered_at timestamptz, next_at timestamptz NOT NULL DEFAULT now(),
      error text NOT NULL DEFAULT '', attempts integer NOT NULL DEFAULT 0
    );
    CREATE TABLE annotation_worker_status (
      id text PRIMARY KEY, heartbeat_at timestamptz NOT NULL DEFAULT now(), details jsonb NOT NULL
    );
    """)


def downgrade():
    op.execute("DROP TABLE annotation_worker_status; DROP TABLE annotation_memory_outbox; DROP TABLE annotation_tasks; DROP TABLE annotation_projects;")
