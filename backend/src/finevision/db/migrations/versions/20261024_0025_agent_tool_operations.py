"""Platform-owned durable receipts for external Agent tool operations."""
from alembic import op

revision = "20261024_0025"
down_revision = "20260926_0024"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_tool_operations (
      actor_id uuid NOT NULL REFERENCES app_users(id),
      operation_id uuid NOT NULL,
      tool text NOT NULL,
      arguments_sha256 text NOT NULL,
      status text NOT NULL CHECK (status IN ('running','completed')),
      http_status integer NOT NULL DEFAULT 0,
      response jsonb,
      updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (actor_id, operation_id)
    );
    CREATE INDEX agent_tool_operations_updated ON agent_tool_operations(updated_at);
    CREATE UNIQUE INDEX jobs_agent_operation ON jobs ((payload->>'agent_operation_key'))
      WHERE payload->>'agent_operation_key' IS NOT NULL;
    CREATE UNIQUE INDEX inference_events_agent_operation ON inference_events ((request_payload->>'agent_operation_key'))
      WHERE request_payload->>'agent_operation_key' IS NOT NULL;
    CREATE INDEX job_events_agent_operation ON job_events ((payload->>'agent_operation_key'))
      WHERE payload->>'agent_operation_key' IS NOT NULL;
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS job_events_agent_operation; DROP INDEX IF EXISTS inference_events_agent_operation; DROP INDEX IF EXISTS jobs_agent_operation;")
    op.drop_table("agent_tool_operations")
