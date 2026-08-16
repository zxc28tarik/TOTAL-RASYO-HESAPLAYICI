-- BANK batch valuation + point-in-time assumptions + M2 bridge.
-- Requires sql/011_bank_valuation_integration.sql.

CREATE TABLE IF NOT EXISTS analytics.bank_valuation_assumptions (
  scope_type TEXT NOT NULL,
  scope_code TEXT NOT NULL,
  effective_at TIMESTAMPTZ NOT NULL,
  coe NUMERIC NOT NULL,
  macro_cap NUMERIC NOT NULL,
  risk_free_rate NUMERIC,
  tier_cap NUMERIC NOT NULL DEFAULT 0.80,
  payout_missing_factor NUMERIC NOT NULL DEFAULT 0.70,
  band_width_shadow_mode BOOLEAN NOT NULL DEFAULT TRUE,
  max_halfwidth NUMERIC NOT NULL DEFAULT 0.80,
  source TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (scope_type, scope_code, effective_at),
  CONSTRAINT ck_bank_assumption_scope CHECK (
    (scope_type = 'BANK' AND scope_code = 'BANK')
    OR (scope_type = 'TICKER' AND scope_code <> '')
  ),
  CONSTRAINT ck_bank_assumption_coe CHECK (coe > 0),
  CONSTRAINT ck_bank_assumption_macro_cap CHECK (macro_cap >= 0),
  CONSTRAINT ck_bank_assumption_rf CHECK (risk_free_rate IS NULL OR risk_free_rate >= 0),
  CONSTRAINT ck_bank_assumption_tier CHECK (tier_cap BETWEEN 0 AND 1),
  CONSTRAINT ck_bank_assumption_payout_factor CHECK (payout_missing_factor BETWEEN 0 AND 1),
  CONSTRAINT ck_bank_assumption_halfwidth CHECK (max_halfwidth > 0)
);

CREATE INDEX IF NOT EXISTS idx_bank_assumption_pit
  ON analytics.bank_valuation_assumptions
     (scope_type, scope_code, effective_at DESC);

COMMENT ON TABLE analytics.bank_valuation_assumptions IS
  'COE ve makro büyüme tavanının point-in-time kaynağı. TICKER kaydı BANK varsayımını ezer.';

CREATE TABLE IF NOT EXISTS analytics.bank_m2_scores (
  ticker TEXT NOT NULL,
  asof_date DATE NOT NULL,
  analysis_at TIMESTAMPTZ NOT NULL,
  anchor_period_end DATE NOT NULL,
  current_price NUMERIC,
  price_trade_date DATE,
  price_source TEXT NOT NULL DEFAULT 'DAILY_CLOSE',
  valuation_status TEXT NOT NULL,
  valuation_reason TEXT,
  v_conf NUMERIC,
  z_val NUMERIC,
  s_valuation NUMERIC,
  s_val_effective NUMERIC NOT NULL,
  s_lag_effective NUMERIC NOT NULL,
  lag_active BOOLEAN NOT NULL,
  lag_source TEXT NOT NULL,
  valuation_usable BOOLEAN NOT NULL,
  m2_score NUMERIC NOT NULL,
  score_inputs JSONB NOT NULL,
  diagnostics JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, analysis_at, anchor_period_end),
  CONSTRAINT ck_bank_m2_v_conf CHECK (v_conf IS NULL OR v_conf BETWEEN 0 AND 1),
  CONSTRAINT ck_bank_m2_scores CHECK (
    s_val_effective BETWEEN 0 AND 1
    AND s_lag_effective BETWEEN 0 AND 1
    AND m2_score BETWEEN 0 AND 1
  )
);

CREATE INDEX IF NOT EXISTS idx_bank_m2_latest
  ON analytics.bank_m2_scores(ticker, asof_date, analysis_at DESC);

ALTER TABLE analytics.bank_valuation_periods
  ADD COLUMN IF NOT EXISTS lower_halfwidth NUMERIC,
  ADD COLUMN IF NOT EXISTS upper_halfwidth NUMERIC,
  ADD COLUMN IF NOT EXISTS assumption_effective_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS assumption_source TEXT,
  ADD COLUMN IF NOT EXISTS coe NUMERIC,
  ADD COLUMN IF NOT EXISTS macro_cap NUMERIC,
  ADD COLUMN IF NOT EXISTS risk_free_rate NUMERIC;

ALTER TABLE analytics.bank_valuation_assumptions
  ADD COLUMN IF NOT EXISTS risk_free_rate NUMERIC;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'ck_bank_assumption_rf'
      AND conrelid = 'analytics.bank_valuation_assumptions'::regclass
  ) THEN
    ALTER TABLE analytics.bank_valuation_assumptions
      ADD CONSTRAINT ck_bank_assumption_rf CHECK (
        risk_free_rate IS NULL OR risk_free_rate >= 0
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'ck_bank_assumption_payload'
      AND conrelid = 'analytics.bank_valuation_assumptions'::regclass
  ) THEN
    ALTER TABLE analytics.bank_valuation_assumptions
      ADD CONSTRAINT ck_bank_assumption_payload CHECK (
        btrim(source) <> ''
        AND jsonb_typeof(metadata) = 'object'
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'ck_bank_metrics_period_before_publish'
      AND conrelid = 'core.bank_metrics_quarterly'::regclass
  ) THEN
    ALTER TABLE core.bank_metrics_quarterly
      ADD CONSTRAINT ck_bank_metrics_period_before_publish CHECK (
        period_end <= (published_at AT TIME ZONE 'Europe/Istanbul')::date
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'ck_bank_valuation_halfwidths'
      AND conrelid = 'analytics.bank_valuation_periods'::regclass
  ) THEN
    ALTER TABLE analytics.bank_valuation_periods
      ADD CONSTRAINT ck_bank_valuation_halfwidths CHECK (
        valuation_status <> 'OK'
        OR (lower_halfwidth > 0 AND upper_halfwidth > 0)
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'ck_bank_valuation_assumption_trace'
      AND conrelid = 'analytics.bank_valuation_periods'::regclass
  ) THEN
    ALTER TABLE analytics.bank_valuation_periods
      ADD CONSTRAINT ck_bank_valuation_assumption_trace CHECK (
        (assumption_effective_at IS NULL OR assumption_effective_at <= analysis_at)
        AND (coe IS NULL OR coe > 0)
        AND (macro_cap IS NULL OR macro_cap >= 0)
        AND (risk_free_rate IS NULL OR risk_free_rate >= 0)
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'ck_bank_valuation_point_in_time_trace'
      AND conrelid = 'analytics.bank_valuation_periods'::regclass
  ) THEN
    ALTER TABLE analytics.bank_valuation_periods
      ADD CONSTRAINT ck_bank_valuation_point_in_time_trace CHECK (
        (selected_published_at IS NULL OR selected_published_at <= analysis_at)
        AND (sector_asof_cutoff IS NULL OR sector_asof_cutoff <= analysis_at)
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'ck_bank_valuation_json_payloads'
      AND conrelid = 'analytics.bank_valuation_periods'::regclass
  ) THEN
    ALTER TABLE analytics.bank_valuation_periods
      ADD CONSTRAINT ck_bank_valuation_json_payloads CHECK (
        jsonb_typeof(confidence_factors) = 'object'
        AND jsonb_typeof(diagnostics) = 'object'
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'ck_bank_m2_local_asof'
      AND conrelid = 'analytics.bank_m2_scores'::regclass
  ) THEN
    ALTER TABLE analytics.bank_m2_scores
      ADD CONSTRAINT ck_bank_m2_local_asof CHECK (
        asof_date = (analysis_at AT TIME ZONE 'Europe/Istanbul')::date
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'ck_bank_m2_daily_close_cutoff'
      AND conrelid = 'analytics.bank_m2_scores'::regclass
  ) THEN
    ALTER TABLE analytics.bank_m2_scores
      ADD CONSTRAINT ck_bank_m2_daily_close_cutoff CHECK (
        price_trade_date IS NULL
        OR price_trade_date < (analysis_at AT TIME ZONE 'Europe/Istanbul')::date
        OR (
          price_trade_date = (analysis_at AT TIME ZONE 'Europe/Istanbul')::date
          AND (analysis_at AT TIME ZONE 'Europe/Istanbul')::time >= TIME '18:30:00'
        )
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'ck_bank_m2_json_payloads'
      AND conrelid = 'analytics.bank_m2_scores'::regclass
  ) THEN
    ALTER TABLE analytics.bank_m2_scores
      ADD CONSTRAINT ck_bank_m2_json_payloads CHECK (
        jsonb_typeof(score_inputs) = 'object'
        AND jsonb_typeof(diagnostics) = 'object'
      );
  END IF;
END $$;

ALTER TABLE analytics.module_scores
  ADD COLUMN IF NOT EXISTS m2_source TEXT,
  ADD COLUMN IF NOT EXISTS m2_score_inputs JSONB;
