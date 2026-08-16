CREATE TABLE IF NOT EXISTS analytics.nonfin_valuation_periods (
  ticker TEXT NOT NULL,
  analysis_at TIMESTAMPTZ NOT NULL,
  anchor_period_end DATE NOT NULL,
  sector_code TEXT NOT NULL,
  price_trade_date DATE NOT NULL,
  current_price NUMERIC NOT NULL CHECK (current_price > 0),
  valuation_profile TEXT NOT NULL,
  valuation_version INT NOT NULL CHECK (valuation_version > 0),
  source_derivation_profile TEXT NOT NULL,
  source_derivation_version INT NOT NULL CHECK (source_derivation_version > 0),
  config_sha256 TEXT NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
  valuation_status TEXT NOT NULL,
  valuation_reason TEXT,
  v_low NUMERIC,
  v_mid NUMERIC,
  v_high NUMERIC,
  valuation_score NUMERIC NOT NULL CHECK (valuation_score BETWEEN 0 AND 1),
  z_val NUMERIC,
  v_conf NUMERIC NOT NULL CHECK (v_conf BETWEEN 0 AND 1),
  coverage_weight NUMERIC NOT NULL CHECK (coverage_weight BETWEEN 0 AND 1),
  lower_halfwidth NUMERIC,
  upper_halfwidth NUMERIC,
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, analysis_at, anchor_period_end, valuation_profile, valuation_version),
  CHECK (price_trade_date <= (analysis_at AT TIME ZONE 'Europe/Istanbul')::date),
  CHECK (
    valuation_status <> 'OK'
    OR (v_low > 0 AND v_low <= v_mid AND v_mid <= v_high)
  )
);

CREATE INDEX IF NOT EXISTS idx_nonfin_valuation_latest
  ON analytics.nonfin_valuation_periods(ticker, analysis_at DESC, anchor_period_end DESC);
CREATE INDEX IF NOT EXISTS idx_nonfin_valuation_sector
  ON analytics.nonfin_valuation_periods(sector_code, analysis_at DESC, anchor_period_end DESC);

CREATE TABLE IF NOT EXISTS analytics.nonfin_m2_scores (
  ticker TEXT NOT NULL,
  analysis_at TIMESTAMPTZ NOT NULL,
  asof_date DATE GENERATED ALWAYS AS ((analysis_at AT TIME ZONE 'Europe/Istanbul')::date) STORED,
  anchor_period_end DATE NOT NULL,
  m2_score NUMERIC NOT NULL CHECK (m2_score BETWEEN 0 AND 1),
  m2_source TEXT NOT NULL,
  valuation_usable BOOLEAN NOT NULL,
  score_inputs JSONB NOT NULL CHECK (jsonb_typeof(score_inputs) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, analysis_at, anchor_period_end)
);

CREATE INDEX IF NOT EXISTS idx_nonfin_m2_latest
  ON analytics.nonfin_m2_scores(ticker, analysis_at DESC, anchor_period_end DESC);

CREATE OR REPLACE VIEW analytics.latest_nonfin_m2_scores AS
SELECT DISTINCT ON (ticker)
       ticker, analysis_at, asof_date, anchor_period_end,
       m2_score, m2_source, valuation_usable, score_inputs
FROM analytics.nonfin_m2_scores
ORDER BY ticker, analysis_at DESC, anchor_period_end DESC;
