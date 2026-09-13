"""Allow annotation provenance without relaxing immutable dataset constraints."""
from alembic import op

revision = "20260914_0021"
down_revision = "20260914_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE dataset_versions DROP CONSTRAINT dataset_version_source_type;
    ALTER TABLE dataset_versions ADD CONSTRAINT dataset_version_source_type CHECK
    (source_type IN ('initial_import','manual_expansion','review_feedback','mixed_expansion','annotation_publish','annotation_append'));""")


def downgrade():
    # Deliberately fails when annotated releases exist: do not rewrite provenance.
    op.execute("""ALTER TABLE dataset_versions DROP CONSTRAINT dataset_version_source_type;
    ALTER TABLE dataset_versions ADD CONSTRAINT dataset_version_source_type CHECK
    (source_type IN ('initial_import','manual_expansion','review_feedback','mixed_expansion'));""")
