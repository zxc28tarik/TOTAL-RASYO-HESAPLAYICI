CREATE TABLE IF NOT EXISTS raw.mkk_suite_sync_runs (
  run_key CHAR(64) PRIMARY KEY,
  suite_name TEXT NOT NULL,
  suite_version INTEGER NOT NULL CHECK (suite_version > 0),
  started_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('COMPLETE', 'PARTIAL', 'FAILED')),
  requested_start TIMESTAMPTZ NULL,
  requested_end TIMESTAMPTZ NOT NULL,
  resume BOOLEAN NOT NULL,
  continue_on_error BOOLEAN NOT NULL,
  max_windows_per_product INTEGER NOT NULL CHECK (max_windows_per_product > 0),
  max_product_attempts INTEGER NOT NULL CHECK (max_product_attempts > 0),
  total_rows_persisted INTEGER NOT NULL CHECK (total_rows_persisted >= 0),
  total_quarantined INTEGER NOT NULL CHECK (total_quarantined >= 0),
  report JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_mkk_suite_run_sha CHECK (run_key ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_mkk_suite_run_time CHECK (completed_at >= started_at),
  CONSTRAINT ck_mkk_suite_run_window CHECK (requested_start IS NULL OR requested_end >= requested_start)
);

CREATE TABLE IF NOT EXISTS raw.mkk_suite_product_runs (
  run_key CHAR(64) NOT NULL REFERENCES raw.mkk_suite_sync_runs(run_key) ON DELETE CASCADE,
  product_name TEXT NOT NULL,
  source_name TEXT NOT NULL,
  stream_name TEXT NOT NULL,
  config_sha256 CHAR(64) NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('COMPLETE', 'PARTIAL', 'UP_TO_DATE', 'QUARANTINED', 'FAILED', 'NOT_RUN')),
  windows_completed INTEGER NOT NULL CHECK (windows_completed >= 0),
  attempts INTEGER NOT NULL CHECK (attempts >= 0),
  rows_persisted INTEGER NOT NULL CHECK (rows_persisted >= 0),
  pages_fetched INTEGER NOT NULL CHECK (pages_fetched >= 0),
  quarantined_count INTEGER NOT NULL CHECK (quarantined_count >= 0),
  requested_end TIMESTAMPTZ NOT NULL,
  last_window_start TIMESTAMPTZ NULL,
  last_window_end TIMESTAMPTZ NULL,
  checkpoint_window_end TIMESTAMPTZ NULL,
  error TEXT NULL,
  details JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_key, product_name),
  CONSTRAINT ck_mkk_suite_product_sha CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_mkk_suite_product_window_pair CHECK (
    (last_window_start IS NULL AND last_window_end IS NULL)
    OR (last_window_start IS NOT NULL AND last_window_end IS NOT NULL AND last_window_end >= last_window_start)
  ),
  CONSTRAINT ck_mkk_suite_product_error CHECK (
    (status = 'FAILED' AND error IS NOT NULL)
    OR (status <> 'FAILED' AND error IS NULL)
  ),
  CONSTRAINT ck_mkk_suite_product_quarantine CHECK (
    status <> 'QUARANTINED' OR quarantined_count > 0
  )
);

CREATE INDEX IF NOT EXISTS idx_mkk_suite_runs_time
  ON raw.mkk_suite_sync_runs (suite_name, completed_at DESC);

CREATE INDEX IF NOT EXISTS idx_mkk_suite_product_status
  ON raw.mkk_suite_product_runs (source_name, stream_name, status, created_at DESC);
