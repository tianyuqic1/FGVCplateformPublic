"""Dataset-scoped immutable publication versions; historical releases stay unnumbered."""
from alembic import op

revision = "20260910_0016"
down_revision = "20260910_0015"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
    ALTER TABLE model_versions ADD COLUMN release_version text,
      ADD COLUMN release_sequence bigint,
      ADD COLUMN release_reason text,
      ADD COLUMN release_signature text;
    CREATE UNIQUE INDEX model_release_number_unique ON model_versions(dataset_id, release_version);
    CREATE UNIQUE INDEX model_release_sequence_unique ON model_versions(dataset_id, release_sequence);
    """)

def downgrade():
    op.execute("""ALTER TABLE model_versions DROP COLUMN release_version,
      DROP COLUMN release_sequence, DROP COLUMN release_reason, DROP COLUMN release_signature""")
