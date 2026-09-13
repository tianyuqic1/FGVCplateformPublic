"""Hardware-specific deployments and their isolated transactional build outbox."""
from alembic import op

revision = "20260913_0018"
down_revision = "20260910_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE model_deployments (
      id uuid PRIMARY KEY,
      model_version_id uuid NOT NULL REFERENCES model_versions(id),
      source_artifact_id uuid NOT NULL REFERENCES artifacts(id),
      compiled_artifact_id uuid REFERENCES artifacts(id),
      runtime text NOT NULL CHECK (runtime IN ('tensorrt','ascend_acl')),
      precision text NOT NULL CHECK (precision IN ('FP32','FP16')),
      target_profile text NOT NULL,
      max_batch integer NOT NULL CHECK (max_batch BETWEEN 1 AND 32),
      status text NOT NULL CHECK (status IN ('queued','building','ready','failed')),
      source_descriptor jsonb NOT NULL,
      compiled_descriptor jsonb,
      validation jsonb NOT NULL DEFAULT '{}',
      build_token uuid NOT NULL,
      worker_id text,
      lease_expires_at timestamptz,
      error text NOT NULL DEFAULT '',
      actor text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(model_version_id,source_artifact_id,runtime,precision,target_profile,max_batch)
    );
    CREATE INDEX model_deployments_model ON model_deployments(model_version_id,created_at);
    CREATE TABLE deployment_outbox (
      id uuid PRIMARY KEY,
      deployment_id uuid NOT NULL REFERENCES model_deployments(id),
      build_token uuid NOT NULL,
      runtime text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      published_at timestamptz,
      UNIQUE(deployment_id,build_token)
    );
    ALTER TABLE inference_events ADD COLUMN deployment_id text,
      ADD COLUMN runtime_metadata jsonb NOT NULL DEFAULT '{}';
    """)


def downgrade():
    op.execute("""ALTER TABLE inference_events DROP COLUMN runtime_metadata, DROP COLUMN deployment_id;
    DROP TABLE deployment_outbox; DROP TABLE model_deployments;""")
