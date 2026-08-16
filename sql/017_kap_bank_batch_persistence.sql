-- KAP BANK batch sonucunun idempotent ve izlenebilir kalıcılığı.
-- 011, 013 ve 016 migration'larından sonra çalıştırılır.

CREATE TABLE IF NOT EXISTS analytics.kap_bank_batch_runs (
  run_key TEXT PRIMARY KEY,
  analysis_at TIMESTAMPTZ NOT NULL,
  asof_date DATE NOT NULL,
  anchor_period_end DATE NOT NULL,
  horizon_days INT NOT NULL,
  pipeline_version TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL,
  requested_count INT NOT NULL,
  prepared_count INT NOT NULL,
  result_count INT NOT NULL,
  rejected_count INT NOT NULL,
  sector_scale_eligible_count INT NOT NULL,
  valuation_ok_count INT NOT NULL,
  report_sha256 TEXT NOT NULL,
  config_lineage JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_kap_bank_batch_run UNIQUE
    (analysis_at, anchor_period_end, horizon_days, pipeline_version),
  CONSTRAINT ck_kap_bank_batch_run_key CHECK (run_key ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_kap_bank_batch_report_sha CHECK (report_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_kap_bank_batch_status CHECK (status IN ('COMPLETE','PARTIAL','FAILED')),
  CONSTRAINT ck_kap_bank_batch_counts CHECK (
    horizon_days > 0
    AND requested_count > 0
    AND prepared_count >= 0
    AND result_count >= 0
    AND rejected_count >= 0
    AND sector_scale_eligible_count >= 0
    AND valuation_ok_count >= 0
    AND result_count + rejected_count = requested_count
    AND prepared_count >= result_count
    AND prepared_count <= requested_count
    AND sector_scale_eligible_count <= prepared_count
    AND valuation_ok_count <= result_count
    AND (
      (status = 'COMPLETE' AND result_count = requested_count)
      OR (status = 'PARTIAL' AND result_count > 0 AND result_count < requested_count)
      OR (status = 'FAILED' AND result_count = 0)
    )
  ),
  CONSTRAINT ck_kap_bank_batch_local_asof CHECK (
    asof_date = (analysis_at AT TIME ZONE 'Europe/Istanbul')::date
  ),
  CONSTRAINT ck_kap_bank_batch_text CHECK (
    btrim(pipeline_version) <> '' AND btrim(source) <> ''
  ),
  CONSTRAINT ck_kap_bank_batch_lineage CHECK (jsonb_typeof(config_lineage) = 'object')
);

CREATE TABLE IF NOT EXISTS analytics.kap_bank_batch_rankings (
  run_key TEXT NOT NULL REFERENCES analytics.kap_bank_batch_runs(run_key) ON DELETE CASCADE,
  ticker TEXT NOT NULL,
  rank INT NOT NULL,
  total_rasyo_100 NUMERIC NOT NULL,
  decision TEXT NOT NULL,
  m2_score NUMERIC NOT NULL,
  v_conf NUMERIC,
  valuation_status TEXT NOT NULL,
  result_payload JSONB NOT NULL,
  lineage JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_key, ticker),
  CONSTRAINT uq_kap_bank_batch_rank UNIQUE (run_key, rank),
  CONSTRAINT ck_kap_bank_batch_rank_values CHECK (
    rank > 0
    AND total_rasyo_100 BETWEEN 0 AND 100
    AND m2_score BETWEEN 0 AND 1
    AND (v_conf IS NULL OR v_conf BETWEEN 0 AND 1)
  ),
  CONSTRAINT ck_kap_bank_batch_rank_decision CHECK (decision IN ('AL','IZLE','UZAK')),
  CONSTRAINT ck_kap_bank_batch_rank_json CHECK (
    jsonb_typeof(result_payload) = 'object'
    AND jsonb_typeof(lineage) = 'object'
  )
);

CREATE INDEX IF NOT EXISTS idx_kap_bank_batch_rank_latest
  ON analytics.kap_bank_batch_rankings(run_key, rank);

CREATE TABLE IF NOT EXISTS analytics.kap_bank_batch_rejections (
  run_key TEXT NOT NULL REFERENCES analytics.kap_bank_batch_runs(run_key) ON DELETE CASCADE,
  ticker TEXT NOT NULL,
  stage TEXT NOT NULL DEFAULT 'EVALUATION',
  reason TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_key, ticker),
  CONSTRAINT ck_kap_bank_batch_rejection_text CHECK (
    btrim(ticker) <> '' AND btrim(stage) <> '' AND btrim(reason) <> ''
  )
);

ALTER TABLE analytics.module_scores
  ADD COLUMN IF NOT EXISTS analysis_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS source_run_key TEXT;

CREATE INDEX IF NOT EXISTS idx_module_scores_source_run
  ON analytics.module_scores(source_run_key)
  WHERE source_run_key IS NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'ck_module_scores_analysis_asof'
      AND conrelid = 'analytics.module_scores'::regclass
  ) THEN
    ALTER TABLE analytics.module_scores
      ADD CONSTRAINT ck_module_scores_analysis_asof CHECK (
        analysis_at IS NULL
        OR asof_date = (analysis_at AT TIME ZONE 'Europe/Istanbul')::date
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_module_scores_source_run'
      AND conrelid = 'analytics.module_scores'::regclass
  ) THEN
    ALTER TABLE analytics.module_scores
      ADD CONSTRAINT fk_module_scores_source_run
      FOREIGN KEY (source_run_key)
      REFERENCES analytics.kap_bank_batch_runs(run_key)
      ON DELETE SET NULL;
  END IF;
END $$;

CREATE OR REPLACE VIEW analytics.latest_kap_bank_batch_rankings AS
WITH latest_runs AS (
  SELECT DISTINCT ON (asof_date, horizon_days)
    run_key, analysis_at, asof_date, anchor_period_end, horizon_days,
    pipeline_version, source, status, updated_at
  FROM analytics.kap_bank_batch_runs
  WHERE status IN ('COMPLETE','PARTIAL')
  ORDER BY asof_date, horizon_days, analysis_at DESC, updated_at DESC, pipeline_version DESC
)
SELECT
  r.analysis_at, r.asof_date, r.anchor_period_end, r.horizon_days,
  r.pipeline_version, r.source, r.status,
  k.rank, k.ticker, k.total_rasyo_100, k.decision,
  k.m2_score, k.v_conf, k.valuation_status, k.result_payload, k.lineage
FROM latest_runs r
JOIN analytics.kap_bank_batch_rankings k USING (run_key);
