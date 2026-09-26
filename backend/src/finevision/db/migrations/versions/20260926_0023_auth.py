"""Add local user accounts, revocable sessions and account audit events."""

from alembic import op

revision = "20260926_0023"
down_revision = "20260920_0022"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE app_users (
      id uuid PRIMARY KEY,
      email text NOT NULL UNIQUE,
      display_name text NOT NULL,
      password_hash text NOT NULL,
      role text NOT NULL DEFAULT 'business',
      status text NOT NULL DEFAULT 'pending',
      failed_logins integer NOT NULL DEFAULT 0,
      locked_until timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT app_users_role CHECK (role IN ('admin','annotator','business')),
      CONSTRAINT app_users_status CHECK (status IN ('pending','active','disabled')),
      CONSTRAINT app_users_failed_logins CHECK (failed_logins >= 0)
    );
    CREATE TABLE app_sessions (
      token_hash bytea PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
      csrf_token text NOT NULL,
      expires_at timestamptz NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX app_sessions_user ON app_sessions(user_id);
    CREATE INDEX app_sessions_expiry ON app_sessions(expires_at);
    CREATE TABLE app_user_events (
      id uuid PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES app_users(id),
      actor_id uuid REFERENCES app_users(id),
      event_type text NOT NULL,
      from_role text,
      to_role text,
      from_status text,
      to_status text,
      reason text NOT NULL DEFAULT '',
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX app_user_events_user_time ON app_user_events(user_id, created_at DESC);
    """)


def downgrade():
    op.execute("""
    DROP TABLE app_user_events;
    DROP TABLE app_sessions;
    DROP TABLE app_users;
    """)
