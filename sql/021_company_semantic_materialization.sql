CREATE TABLE IF NOT EXISTS core.company_metrics_quarterly (
  ticker TEXT NOT NULL,
  sector_family TEXT NOT NULL CHECK (sector_family IN ('NONFIN','HOLDING','GYO','INSURANCE','FINANCIAL')),
  period_end DATE NOT NULL,
  version_tag TEXT NOT NULL,
  version_sequence INT NOT NULL CHECK (version_sequence >= 0),
  published_at TIMESTAMPTZ NOT NULL,
  source_disclosure_id TEXT NOT NULL UNIQUE,
  lineage_sha256 TEXT NOT NULL CHECK (lineage_sha256 ~ '^[0-9a-f]{64}$'),
  source_lineage JSONB NOT NULL CHECK (jsonb_typeof(source_lineage) = 'array'),
  derivation_profile TEXT NOT NULL,
  derivation_version INT NOT NULL CHECK (derivation_version > 0),
  is_complete BOOLEAN NOT NULL,
  derivation_diagnostics JSONB NOT NULL CHECK (jsonb_typeof(derivation_diagnostics) = 'object'),

  revenue NUMERIC,
  cogs NUMERIC,
  gross_profit NUMERIC,
  ebit NUMERIC,
  net_income NUMERIC,
  interest_exp NUMERIC,
  total_assets NUMERIC,
  total_equity NUMERIC,
  current_assets NUMERIC,
  current_liabilities NUMERIC,
  cash_and_eq NUMERIC,
  st_investments NUMERIC,
  receivables NUMERIC,
  inventory NUMERIC,
  debt_st NUMERIC,
  debt_lt NUMERIC,
  cfo NUMERIC,
  capex NUMERIC,
  shares_out NUMERIC CHECK (shares_out IS NULL OR shares_out > 0),
  shares_diluted NUMERIC CHECK (shares_diluted IS NULL OR shares_diluted > 0),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (period_end <= published_at::date),
  PRIMARY KEY (ticker, period_end, derivation_profile, derivation_version, lineage_sha256)
);

CREATE INDEX IF NOT EXISTS idx_company_metrics_pit
  ON core.company_metrics_quarterly (
    ticker, period_end, published_at DESC, version_sequence DESC, lineage_sha256 DESC
  );
CREATE INDEX IF NOT EXISTS idx_company_metrics_sector_pit
  ON core.company_metrics_quarterly (
    sector_family, period_end, published_at DESC
  );

CREATE TABLE IF NOT EXISTS core.company_metric_derivation_rejections (
  ticker TEXT NOT NULL,
  analysis_at TIMESTAMPTZ NOT NULL,
  anchor_period_end DATE NOT NULL,
  derivation_profile TEXT NOT NULL,
  derivation_version INT NOT NULL CHECK (derivation_version > 0),
  reason TEXT NOT NULL,
  first_rejected_at TIMESTAMPTZ NOT NULL,
  last_rejected_at TIMESTAMPTZ NOT NULL,
  attempts INT NOT NULL CHECK (attempts > 0),
  PRIMARY KEY (
    ticker, analysis_at, anchor_period_end, derivation_profile, derivation_version
  )
);

CREATE OR REPLACE FUNCTION core.prevent_company_metric_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF ROW(OLD.*) IS DISTINCT FROM ROW(NEW.*) THEN
    IF NEW.inserted_at = OLD.inserted_at THEN
      RAISE EXCEPTION 'company_metrics_quarterly immutable';
    END IF;
    -- The idempotent writer may only move inserted_at forward.
    NEW := OLD;
    NEW.inserted_at := GREATEST(OLD.inserted_at, NEW.inserted_at);
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_company_metric_immutable ON core.company_metrics_quarterly;
CREATE TRIGGER trg_company_metric_immutable
BEFORE UPDATE ON core.company_metrics_quarterly
FOR EACH ROW EXECUTE FUNCTION core.prevent_company_metric_mutation();

CREATE OR REPLACE VIEW core.company_metrics_latest AS
SELECT DISTINCT ON (ticker, period_end, derivation_profile, derivation_version)
       *
FROM core.company_metrics_quarterly
ORDER BY ticker, period_end, derivation_profile, derivation_version,
         published_at DESC, version_sequence DESC, lineage_sha256 DESC;
