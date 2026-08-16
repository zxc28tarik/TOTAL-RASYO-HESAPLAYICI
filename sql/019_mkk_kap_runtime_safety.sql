-- MKK/KAP canli servis calisma guvenligi:
-- - her sync denemesinin izlenmesi
-- - bozuk item karantinasi
-- - karantina varsa ileri checkpoint'in ilerlememesi uygulama katmaninda zorunludur

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.kap_sync_runs (
  run_id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL,
  stream_name TEXT NOT NULL,
  window_start TIMESTAMPTZ NOT NULL,
  window_end TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL,
  rows_seen INT NOT NULL,
  quarantined_count INT NOT NULL,
  pages_fetched INT NOT NULL,
  next_cursor TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_kap_sync_run_window CHECK (window_start <= window_end),
  CONSTRAINT ck_kap_sync_run_status CHECK (status IN ('COMPLETE', 'QUARANTINED')),
  CONSTRAINT ck_kap_sync_run_counts CHECK (
    rows_seen >= 0 AND quarantined_count >= 0 AND pages_fetched >= 0
  ),
  CONSTRAINT ck_kap_sync_run_status_count CHECK (
    (status = 'COMPLETE' AND quarantined_count = 0)
    OR (status = 'QUARANTINED' AND quarantined_count > 0)
  ),
  CONSTRAINT ck_kap_sync_run_metadata CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_kap_sync_runs_stream_completed
  ON raw.kap_sync_runs(source, stream_name, completed_at DESC);

CREATE TABLE IF NOT EXISTS raw.kap_api_quarantine (
  source TEXT NOT NULL,
  stream_name TEXT NOT NULL,
  window_start TIMESTAMPTZ NOT NULL,
  window_end TIMESTAMPTZ NOT NULL,
  page_number INT NOT NULL,
  item_index INT NOT NULL,
  cursor_value TEXT,
  reason TEXT NOT NULL,
  payload JSONB NOT NULL,
  payload_sha256 CHAR(64) NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL,
  first_seen_at TIMESTAMPTZ NOT NULL,
  last_seen_at TIMESTAMPTZ NOT NULL,
  attempts INT NOT NULL DEFAULT 1,
  PRIMARY KEY (
    source, stream_name, window_start, window_end,
    page_number, item_index, payload_sha256
  ),
  CONSTRAINT ck_kap_quarantine_window CHECK (window_start <= window_end),
  CONSTRAINT ck_kap_quarantine_position CHECK (page_number > 0 AND item_index >= 0),
  CONSTRAINT ck_kap_quarantine_reason CHECK (
    length(btrim(reason)) > 0 AND octet_length(reason) <= 8192
  ),
  CONSTRAINT ck_kap_quarantine_sha CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_kap_quarantine_seen CHECK (
    first_seen_at <= last_seen_at AND fetched_at <= last_seen_at
  ),
  CONSTRAINT ck_kap_quarantine_attempts CHECK (attempts > 0)
);

CREATE INDEX IF NOT EXISTS idx_kap_quarantine_stream_seen
  ON raw.kap_api_quarantine(source, stream_name, last_seen_at DESC);
