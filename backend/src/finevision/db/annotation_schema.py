"""Annotation metadata matches migration 0019; kept separate from training."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg


def register(metadata):
    publications = sa.Table("annotation_publications", metadata,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("plan", pg.JSONB(), nullable=False),
        sa.Column("preview", pg.JSONB(), nullable=False),
        sa.Column("result", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('preview','queued','building','registering','published','failed','cancelled')"))
    sa.Index("annotation_publications_queue", publications.c.status, publications.c.created_at)
    members = sa.Table("annotation_publication_members", metadata,
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("source_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("publication_id", pg.UUID(as_uuid=True), sa.ForeignKey(publications.c.id), nullable=False),
        sa.CheckConstraint("source IN ('annotation','feedback')"))
    sa.Index("annotation_publication_members_batch", members.c.publication_id)
    projects = sa.Table("annotation_projects", metadata,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("classes", pg.JSONB(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("method IN ('A','B','C','D')"),
        sa.CheckConstraint("domain IN ('general','birds','cars')"))
    sequence = sa.Sequence("annotation_tasks_seq_seq")
    tasks = sa.Table("annotation_tasks", metadata,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sequence, server_default=sequence.next_value(), nullable=False, unique=True),
        sa.Column("project_id", pg.UUID(as_uuid=True), sa.ForeignKey(projects.c.id), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("image", pg.JSONB(), nullable=False),
        sa.Column("sha", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("result", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("label", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.Text(), nullable=False, server_default=""),
        sa.Column("claim_token", pg.UUID(as_uuid=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "sha"),
        sa.CheckConstraint("status IN ('pending','queued','running','suggested','failed','unknown','confirmed')"))
    sa.Index("annotation_tasks_queue", tasks.c.status, tasks.c.seq)
    sa.Index("annotation_tasks_project", tasks.c.project_id, tasks.c.status, tasks.c.seq)
    sa.Table("annotation_memory_outbox", metadata,
        sa.Column("task_id", pg.UUID(as_uuid=True), sa.ForeignKey(tasks.c.id), primary_key=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    sa.Table("annotation_worker_status", metadata,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("details", pg.JSONB(), nullable=False))
