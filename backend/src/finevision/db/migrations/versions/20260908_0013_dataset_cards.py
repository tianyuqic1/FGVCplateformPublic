"""Versioned dataset cards and durable advisory drafts.

Revision ID: 20260908_0013
Revises: 20260908_0012
"""
from alembic import op

revision = "20260908_0013"
down_revision = "20260908_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE dataset_card_revisions (
      dataset_version_id uuid NOT NULL REFERENCES dataset_versions(id),
      revision integer NOT NULL CHECK (revision > 0),
      card jsonb NOT NULL CHECK (jsonb_typeof(card)='object'),
      draft_id uuid,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(dataset_version_id, revision)
    );
    CREATE TABLE dataset_card_generations (
      id uuid PRIMARY KEY,
      dataset_version_id uuid NOT NULL REFERENCES dataset_versions(id),
      base_revision integer NOT NULL,
      input_sha256 text NOT NULL,
      prompt_version text NOT NULL,
      status text NOT NULL CHECK (status IN ('running','succeeded','failed')),
      result jsonb,
      error_code text,
      applied_revision integer,
      created_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz,
      expires_at timestamptz NOT NULL DEFAULT now() + interval '180 seconds'
    );
    CREATE UNIQUE INDEX dataset_card_one_active_generation
      ON dataset_card_generations(dataset_version_id) WHERE status='running';
    CREATE INDEX dataset_card_generation_history
      ON dataset_card_generations(dataset_version_id,created_at DESC);
    """)


def downgrade():
    op.drop_table("dataset_card_generations")
    op.drop_table("dataset_card_revisions")
