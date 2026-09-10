"""Dataset snapshots, automatic numbering and reviewed training additions."""
from alembic import op

revision = "20260910_0014"
down_revision = "20260908_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE dataset_versions
      ADD COLUMN version_number integer,
      ADD COLUMN parent_version_id uuid REFERENCES dataset_versions(id),
      ADD COLUMN source_type text NOT NULL DEFAULT 'initial_import',
      ADD COLUMN change_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
      ADD COLUMN import_request_id uuid UNIQUE,
      ADD COLUMN import_fingerprint text;
    WITH numbered AS (
      SELECT id, row_number() OVER (PARTITION BY dataset_id ORDER BY created_at, id) AS n
      FROM dataset_versions
    ) UPDATE dataset_versions v SET version_number=n.n FROM numbered n WHERE v.id=n.id;
    ALTER TABLE dataset_versions
      ALTER COLUMN version_number SET NOT NULL,
      ALTER COLUMN version_number SET DEFAULT 1,
      ADD CONSTRAINT dataset_version_number_positive CHECK (version_number > 0),
      ADD CONSTRAINT dataset_version_number_unique UNIQUE(dataset_id, version_number),
      ADD CONSTRAINT dataset_version_source_type CHECK (source_type IN ('initial_import','manual_expansion','review_feedback','mixed_expansion'));
    CREATE TABLE dataset_version_feedback (
      dataset_id uuid NOT NULL REFERENCES datasets(id),
      feedback_item_id uuid NOT NULL REFERENCES feedback_items(id),
      dataset_version_id uuid NOT NULL REFERENCES dataset_versions(id),
      PRIMARY KEY(dataset_id, feedback_item_id)
    );
    """)


def downgrade():
    op.drop_table("dataset_version_feedback")
    op.execute("""
    ALTER TABLE dataset_versions
      DROP COLUMN import_fingerprint, DROP COLUMN import_request_id,
      DROP COLUMN change_summary, DROP COLUMN source_type,
      DROP COLUMN parent_version_id, DROP COLUMN version_number;
    """)
