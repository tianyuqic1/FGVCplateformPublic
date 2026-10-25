CREATE TABLE IF NOT EXISTS watches(
 id uuid PRIMARY KEY,conversation_id uuid NOT NULL REFERENCES conversations(id),owner_id uuid NOT NULL,training_run_id uuid NOT NULL,
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','waiting_auth','completed','cancelled','expired')),
 grant_token text NOT NULL,observed_status text NOT NULL DEFAULT '',result jsonb NOT NULL DEFAULT '{}',error text NOT NULL DEFAULT '',
 epoch bigint NOT NULL DEFAULT 0,lease_until timestamptz,next_poll timestamptz NOT NULL DEFAULT now(),expires_at timestamptz NOT NULL DEFAULT now()+interval '24 hours',
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS watches_active_resource ON watches(owner_id,conversation_id,training_run_id) WHERE status IN ('active','waiting_auth');
CREATE INDEX IF NOT EXISTS watches_queue ON watches(next_poll) WHERE status='active';
INSERT INTO agent_schema_migrations(version) VALUES(2) ON CONFLICT DO NOTHING;
