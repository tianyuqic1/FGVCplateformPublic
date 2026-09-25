"""Persist async trace context and append-only annotation/deployment audit timelines."""
from alembic import op

revision = "20260920_0022"
down_revision = "20260914_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE outbox_events ADD COLUMN trace_context jsonb NOT NULL DEFAULT '{}';
    ALTER TABLE deployment_outbox ADD COLUMN trace_context jsonb NOT NULL DEFAULT '{}';

    CREATE TABLE annotation_task_events (
      id uuid PRIMARY KEY,
      task_id uuid NOT NULL REFERENCES annotation_tasks(id) ON DELETE CASCADE,
      event_type text NOT NULL,
      actor text NOT NULL DEFAULT 'system',
      from_status text,
      to_status text,
      payload jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX annotation_task_events_timeline ON annotation_task_events(task_id, created_at);

    CREATE TABLE model_deployment_events (
      id uuid PRIMARY KEY,
      deployment_id uuid NOT NULL REFERENCES model_deployments(id) ON DELETE CASCADE,
      event_type text NOT NULL,
      actor text NOT NULL DEFAULT 'system',
      from_status text,
      to_status text,
      payload jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX model_deployment_events_timeline ON model_deployment_events(deployment_id, created_at);
    """)


def downgrade():
    op.execute("""
    DROP TABLE model_deployment_events;
    DROP TABLE annotation_task_events;
    ALTER TABLE deployment_outbox DROP COLUMN trace_context;
    ALTER TABLE outbox_events DROP COLUMN trace_context;
    """)
