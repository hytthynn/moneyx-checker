-- Run this whole file in Supabase Dashboard -> SQL Editor.
-- It is safe to run again: application tables/data are preserved.

BEGIN;

CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  web_url VARCHAR(255) NOT NULL,
  api_url VARCHAR(255) NOT NULL,
  last_check_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Cleanup for databases initialized by older project versions.
ALTER TABLE settings DROP COLUMN IF EXISTS schedule_minute;
ALTER TABLE settings DROP COLUMN IF EXISTS paused;
ALTER TABLE settings DROP COLUMN IF EXISTS chat_id;
ALTER TABLE settings DROP COLUMN IF EXISTS message_thread_id;
ALTER TABLE settings DROP COLUMN IF EXISTS group_alerted;

CREATE TABLE IF NOT EXISTS secrets (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  token_encrypted TEXT,
  mxi_token_encrypted TEXT,
  token_fingerprint VARCHAR(64),
  auth_valid BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS delivery_runs (
  id BIGSERIAL PRIMARY KEY,
  scheduled_hour VARCHAR(40) NOT NULL,
  chat_id BIGINT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'running',
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  error TEXT,
  CONSTRAINT uq_delivery_hour_chat UNIQUE (scheduled_hour, chat_id)
);

CREATE TABLE IF NOT EXISTS rate_snapshots (
  id BIGSERIAL PRIMARY KEY,
  delivery_run_id BIGINT NOT NULL REFERENCES delivery_runs(id),
  captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  web_url VARCHAR(255) NOT NULL,
  currency VARCHAR(32) NOT NULL,
  network VARCHAR(80) NOT NULL,
  rate NUMERIC(30, 12),
  status VARCHAR(20) NOT NULL,
  error TEXT
);

CREATE INDEX IF NOT EXISTS ix_rate_snapshots_captured_at ON rate_snapshots(captured_at);

CREATE TABLE IF NOT EXISTS admin_alerts (
  key VARCHAR(100) PRIMARY KEY,
  sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pending_actions (
  admin_id BIGINT PRIMARY KEY,
  action VARCHAR(40) NOT NULL,
  payload_encrypted TEXT,
  expires_at TIMESTAMPTZ NOT NULL
);

INSERT INTO settings (id, web_url, api_url)
VALUES (1, 'https://mxc1n.com', 'https://api.mxc1n.com')
ON CONFLICT (id) DO NOTHING;

INSERT INTO secrets (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- The application connects directly as the database user. RLS prevents accidental
-- access to these private tables through Supabase Data API (anon/authenticated roles).
ALTER TABLE settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE secrets ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_actions ENABLE ROW LEVEL SECURITY;

COMMIT;