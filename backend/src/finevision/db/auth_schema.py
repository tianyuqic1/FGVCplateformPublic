"""SQLAlchemy mirror for the Go-owned authentication tables."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg


def register(metadata: sa.MetaData) -> None:
    users = sa.Table(
        "app_users", metadata,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="business"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("role IN ('admin','annotator','business')", name="app_users_role"),
        sa.CheckConstraint("status IN ('pending','active','disabled')", name="app_users_status"),
        sa.CheckConstraint("failed_logins >= 0", name="app_users_failed_logins"),
    )
    sessions = sa.Table(
        "app_sessions", metadata,
        sa.Column("token_hash", pg.BYTEA(), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey(users.c.id, ondelete="CASCADE"), nullable=False),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    sa.Index("app_sessions_user", sessions.c.user_id)
    sa.Index("app_sessions_expiry", sessions.c.expires_at)
    events = sa.Table(
        "app_user_events", metadata,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey(users.c.id), nullable=False),
        sa.Column("actor_id", pg.UUID(as_uuid=True), sa.ForeignKey(users.c.id)),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("from_role", sa.Text()),
        sa.Column("to_role", sa.Text()),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text()),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    sa.Index("app_user_events_user_time", events.c.user_id, events.c.created_at.desc())
