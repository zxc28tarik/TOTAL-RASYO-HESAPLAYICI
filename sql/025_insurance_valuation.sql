CREATE TABLE IF NOT EXISTS core.insurance_metrics_snapshots (
  metrics_id TEXT PRIMARY KEY CHECK (metrics_id ~ '^[0-9a-f]{64}$'),
  ticker TEXT NOT NULL,
  period_end DATE NOT NULL,
  published_at TIMESTAMPTZ NOT NULL,
  version_tag TEXT NOT NULL,
  version_sequence INT NOT NULL CHECK (version_sequence > 0),
  business_type TEXT NOT NULL CHECK (business_type IN ('NON_LIFE','LIFE_PENSION')),
  accounting_profile TEXT NOT NULL CHECK (btrim(accounting_profile) <> ''),
  accounting_version INT NOT NULL CHECK (accounting_version > 0),
  currency TEXT NOT NULL CHECK (btrim(currency) <> ''),
  shares_out NUMERIC NOT NULL CHECK (shares_out > 0),
  share_basis TEXT NOT NULL CHECK (btrim(share_basis) <> ''),
  total_equity NUMERIC NOT NULL CHECK (total_equity > 0),
  net_income_ttm NUMERIC NOT NULL,
  written_premiums_ttm NUMERIC NOT NULL CHECK (written_premiums_ttm > 0),
  technical_result_ttm NUMERIC NOT NULL,
  investment_income_ttm NUMERIC NOT NULL,
  earned_premiums_ttm NUMERIC CHECK (earned_premiums_ttm > 0),
  net_claims_ttm NUMERIC CHECK (net_claims_ttm >= 0),
  operating_expenses_ttm NUMERIC CHECK (operating_expenses_ttm >= 0),
  solvency_ratio NUMERIC CHECK (solvency_ratio >= 0),
  source_confidence NUMERIC NOT NULL CHECK (source_confidence BETWEEN 0 AND 1),
  source_type TEXT NOT NULL,
  source_document_id TEXT NOT NULL,
  source_uri TEXT,
  source_sha256 TEXT NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  metrics_profile TEXT NOT NULL,
  metrics_version INT NOT NULL CHECK (metrics_version > 0),
  lineage JSONB NOT NULL CHECK (jsonb_typeof(lineage) = 'object'),
  canonical_sha256 TEXT NOT NULL CHECK (canonical_sha256 ~ '^[0-9a-f]{64}$'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (period_end <= (published_at AT TIME ZONE 'Europe/Istanbul')::date),
  CHECK ((extract(month FROM period_end), extract(day FROM period_end)) IN ((3,31),(6,30),(9,30),(12,31))),
  CHECK (
    (business_type = 'NON_LIFE' AND (
      (earned_premiums_ttm IS NULL AND net_claims_ttm IS NULL AND operating_expenses_ttm IS NULL)
      OR
      (earned_premiums_ttm IS NOT NULL AND net_claims_ttm IS NOT NULL AND operating_expenses_ttm IS NOT NULL)
    ))
    OR
    (business_type = 'LIFE_PENSION' AND earned_premiums_ttm IS NULL AND net_claims_ttm IS NULL AND operating_expenses_ttm IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_insurance_metrics_pit
  ON core.insurance_metrics_snapshots (
    ticker, metrics_profile, metrics_version, accounting_profile, accounting_version,
    period_end DESC, published_at DESC, version_sequence DESC, source_document_id DESC
  );

CREATE OR REPLACE FUNCTION core.guard_insurance_metrics_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  requested_inserted_at TIMESTAMPTZ;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'insurance_metrics_snapshots immutable';
  END IF;
  requested_inserted_at := NEW.inserted_at;
  NEW.inserted_at := OLD.inserted_at;
  IF ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) THEN
    RAISE EXCEPTION 'insurance_metrics_snapshots immutable';
  END IF;
  NEW.inserted_at := GREATEST(OLD.inserted_at, requested_inserted_at);
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_insurance_metrics_immutable ON core.insurance_metrics_snapshots;
CREATE TRIGGER trg_insurance_metrics_immutable
BEFORE UPDATE OR DELETE ON core.insurance_metrics_snapshots
FOR EACH ROW EXECUTE FUNCTION core.guard_insurance_metrics_mutation();

CREATE TABLE IF NOT EXISTS analytics.insurance_valuation_periods (
  ticker TEXT NOT NULL,
  analysis_at TIMESTAMPTZ NOT NULL,
  period_end DATE NOT NULL,
  business_type TEXT NOT NULL CHECK (business_type IN ('NON_LIFE','LIFE_PENSION')),
  currency TEXT NOT NULL,
  share_basis TEXT NOT NULL,
  price_trade_date DATE NOT NULL,
  current_price NUMERIC NOT NULL CHECK (current_price > 0),
  published_at TIMESTAMPTZ NOT NULL,
  total_equity NUMERIC NOT NULL CHECK (total_equity > 0),
  net_income_ttm NUMERIC NOT NULL,
  written_premiums_ttm NUMERIC NOT NULL CHECK (written_premiums_ttm > 0),
  technical_result_ttm NUMERIC NOT NULL,
  investment_income_ttm NUMERIC NOT NULL,
  shares_out NUMERIC NOT NULL CHECK (shares_out > 0),
  earned_premiums_ttm NUMERIC,
  net_claims_ttm NUMERIC,
  operating_expenses_ttm NUMERIC,
  solvency_ratio NUMERIC,
  source_confidence NUMERIC NOT NULL CHECK (source_confidence BETWEEN 0 AND 1),
  source_document_id TEXT NOT NULL,
  source_sha256 TEXT NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  metrics_profile TEXT NOT NULL,
  metrics_version INT NOT NULL CHECK (metrics_version > 0),
  accounting_profile TEXT NOT NULL,
  accounting_version INT NOT NULL CHECK (accounting_version > 0),
  valuation_profile TEXT NOT NULL,
  valuation_version INT NOT NULL CHECK (valuation_version > 0),
  config_sha256 TEXT NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
  valuation_status TEXT NOT NULL,
  valuation_reason TEXT,
  v_low NUMERIC,
  v_mid NUMERIC,
  v_high NUMERIC,
  target_pb NUMERIC,
  target_pe NUMERIC,
  method_count INT NOT NULL CHECK (method_count BETWEEN 0 AND 2),
  technical_margin NUMERIC NOT NULL,
  combined_ratio NUMERIC,
  roe_ttm NUMERIC NOT NULL,
  investment_dependency NUMERIC NOT NULL CHECK (investment_dependency >= 0),
  valuation_score NUMERIC NOT NULL CHECK (valuation_score BETWEEN 0 AND 1),
  z_val NUMERIC,
  v_conf NUMERIC NOT NULL CHECK (v_conf BETWEEN 0 AND 1),
  lower_halfwidth NUMERIC,
  upper_halfwidth NUMERIC,
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, analysis_at, period_end, valuation_profile, valuation_version),
  CHECK (price_trade_date <= (analysis_at AT TIME ZONE 'Europe/Istanbul')::date),
  CHECK (published_at <= analysis_at),
  CHECK (valuation_status <> 'OK' OR (v_low > 0 AND v_low <= v_mid AND v_mid <= v_high))
);

CREATE INDEX IF NOT EXISTS idx_insurance_valuation_latest
  ON analytics.insurance_valuation_periods(ticker, analysis_at DESC, period_end DESC);
CREATE INDEX IF NOT EXISTS idx_insurance_valuation_group
  ON analytics.insurance_valuation_periods(business_type, accounting_profile, analysis_at DESC, period_end DESC);

CREATE TABLE IF NOT EXISTS analytics.insurance_valuation_rejections (
  ticker TEXT NOT NULL,
  analysis_at TIMESTAMPTZ NOT NULL,
  valuation_profile TEXT NOT NULL,
  valuation_version INT NOT NULL CHECK (valuation_version > 0),
  reason TEXT NOT NULL,
  attempts INT NOT NULL DEFAULT 1 CHECK (attempts > 0),
  first_rejected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_rejected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, analysis_at, valuation_profile, valuation_version)
);

CREATE INDEX IF NOT EXISTS idx_insurance_rejections_latest
  ON analytics.insurance_valuation_rejections(analysis_at DESC, ticker);

CREATE TABLE IF NOT EXISTS analytics.insurance_m2_scores (
  ticker TEXT NOT NULL,
  analysis_at TIMESTAMPTZ NOT NULL,
  asof_date DATE GENERATED ALWAYS AS ((analysis_at AT TIME ZONE 'Europe/Istanbul')::date) STORED,
  period_end DATE NOT NULL,
  m2_score NUMERIC NOT NULL CHECK (m2_score BETWEEN 0 AND 1),
  m2_source TEXT NOT NULL,
  valuation_usable BOOLEAN NOT NULL,
  score_inputs JSONB NOT NULL CHECK (jsonb_typeof(score_inputs) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, analysis_at, period_end)
);

CREATE INDEX IF NOT EXISTS idx_insurance_m2_latest
  ON analytics.insurance_m2_scores(ticker, analysis_at DESC, period_end DESC);

CREATE OR REPLACE VIEW analytics.latest_insurance_m2_scores AS
SELECT DISTINCT ON (ticker)
       ticker, analysis_at, asof_date, period_end,
       m2_score, m2_source, valuation_usable, score_inputs
FROM analytics.insurance_m2_scores
ORDER BY ticker, analysis_at DESC, period_end DESC;
