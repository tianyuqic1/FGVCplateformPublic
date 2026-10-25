CREATE TABLE IF NOT EXISTS agent_schema_migrations(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS conversations (
 id uuid PRIMARY KEY,owner_id uuid NOT NULL,title text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(owner_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS runs (
 id uuid PRIMARY KEY,conversation_id uuid NOT NULL REFERENCES conversations(id),owner_id uuid NOT NULL,request_key uuid NOT NULL,
 input text NOT NULL,status text NOT NULL CHECK(status IN ('queued','running','waiting_confirmation','waiting_auth','unknown','succeeded','failed','cancelled')),
 state jsonb NOT NULL DEFAULT '{}',grant_token text NOT NULL DEFAULT '',error text NOT NULL DEFAULT '',epoch bigint NOT NULL DEFAULT 0,
 lease_until timestamptz,cancel_requested boolean NOT NULL DEFAULT false,created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,request_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS runs_conversation_active ON runs(conversation_id) WHERE status IN ('queued','running','waiting_confirmation','waiting_auth');
CREATE INDEX IF NOT EXISTS runs_queue ON runs(status,created_at);
CREATE TABLE IF NOT EXISTS events(id bigserial PRIMARY KEY,run_id uuid NOT NULL REFERENCES runs(id),kind text NOT NULL,data jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS events_run_cursor ON events(run_id,id);
INSERT INTO agent_schema_migrations(version) VALUES (1) ON CONFLICT DO NOTHING;
