"""Persistent node snapshots and 24-hour hardware history."""
from alembic import op

revision = "20260910_0014"
down_revision = "20260908_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE hardware_nodes (
      node_id text PRIMARY KEY,
      sampled_at timestamptz NOT NULL,
      received_at timestamptz NOT NULL,
      snapshot jsonb NOT NULL
    );
    CREATE TABLE hardware_samples (
      node_id text NOT NULL REFERENCES hardware_nodes(node_id) ON DELETE CASCADE,
      received_at timestamptz NOT NULL,
      snapshot jsonb NOT NULL,
      PRIMARY KEY (node_id, received_at)
    );
    CREATE INDEX hardware_samples_retention ON hardware_samples(received_at);
    """)


def downgrade():
    op.drop_table("hardware_samples")
    op.drop_table("hardware_nodes")
