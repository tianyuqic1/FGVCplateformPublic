"""Immutable annotation release batches and source reservations."""
from alembic import op

revision = "20260914_0020"
down_revision = "20260914_0019"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE annotation_publications (
      id uuid PRIMARY KEY, status text NOT NULL CHECK(status IN
        ('preview','queued','building','registering','published','failed','cancelled')),
      fingerprint text NOT NULL, plan jsonb NOT NULL, preview jsonb NOT NULL,
      result jsonb NOT NULL DEFAULT '{}', error text NOT NULL DEFAULT '',
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE annotation_publication_members (
      source text NOT NULL CHECK(source IN ('annotation','feedback')),
      source_id uuid NOT NULL,
      publication_id uuid NOT NULL REFERENCES annotation_publications(id),
      PRIMARY KEY(source,source_id)
    );
    CREATE INDEX annotation_publications_queue ON annotation_publications(status,created_at);
    CREATE INDEX annotation_publication_members_batch ON annotation_publication_members(publication_id);
    """)


def downgrade():
    op.execute("DROP TABLE annotation_publication_members; DROP TABLE annotation_publications;")
