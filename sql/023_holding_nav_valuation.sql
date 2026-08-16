CREATE TABLE IF NOT EXISTS core.holding_nav_snapshots (
  ticker TEXT NOT NULL,
  nav_asof_date DATE NOT NULL,
  published_at TIMESTAMPTZ NOT NULL,
  version_tag TEXT NOT NULL,
  version_sequence INT NOT NULL CHECK (version_sequence > 0),
  nav_total NUMERIC NOT NULL CHECK (nav_total > 0),
  shares_out NUMERIC NOT NULL CHECK (shares_out > 0),
  share_basis TEXT NOT NULL CHECK (btrim(share_basis) <> ''),
  nav_per_share NUMERIC GENERATED ALWAYS AS (nav_total / shares_out) STORED,
  currency TEXT NOT NULL,
  source_confidence NUMERIC NOT NULL CHECK (source_confidence BETWEEN 0 AND 1),
  source_type TEXT NOT NULL,
  source_document_id TEXT NOT NULL,
  source_uri TEXT,
  source_sha256 TEXT NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  nav_profile TEXT NOT NULL,
  nav_version INT NOT NULL CHECK (nav_version > 0),
  lineage JSONB NOT NULL CHECK (jsonb_typeof(lineage) = 'object'),
  canonical_sha256 TEXT NOT NULL CHECK (canonical_sha256 ~ '^[0-9a-f]{64}$'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (
    ticker, nav_asof_date, published_at,
    nav_profile, nav_version, source_document_id
  ),
  CHECK (nav_asof_date <= (published_at AT TIME ZONE 'Europe/Istanbul')::date)
);

CREATE INDEX IF NOT EXISTS idx_holding_nav_pit
  ON core.holding_nav_snapshots (
    ticker, nav_profile, nav_version,
    nav_asof_date DESC, published_at DESC,
    version_sequence DESC, source_document_id DESC, source_sha256 DESC
  );

CREATE OR REPLACE FUNCTION core.reject_holding_nav_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'holding_nav_snapshots immutable';
  END IF;
  IF ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) THEN
    RAISE EXCEPTION 'holding_nav_snapshots immutable';
  END IF;
  RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_holding_nav_immutable ON core.holding_nav_snapshots;
CREATE TRIGGER trg_holding_nav_immutable
BEFORE UPDATE OR DELETE ON core.holding_nav_snapshots
FOR EACH ROW EXECUTE FUNCTION core.reject_holding_nav_mutation();

CREATE TABLE IF NOT EXISTS analytics.holding_valuation_periods (
  ticker TEXT NOT NULL,
  analysis_at TIMESTAMPTZ NOT NULL,
  nav_asof_date DATE NOT NULL,
  peer_group TEXT NOT NULL,
  currency TEXT NOT NULL,
  share_basis TEXT NOT NULL CHECK (btrim(share_basis) <> ''),
  price_trade_date DATE NOT NULL,
  current_price NUMERIC NOT NULL CHECK (current_price > 0),
  nav_published_at TIMESTAMPTZ NOT NULL,
  nav_total NUMERIC NOT NULL CHECK (nav_total > 0),
  shares_out NUMERIC NOT NULL CHECK (shares_out > 0),
  nav_per_share NUMERIC NOT NULL CHECK (nav_per_share > 0),
  source_confidence NUMERIC NOT NULL CHECK (source_confidence BETWEEN 0 AND 1),
  source_document_id TEXT NOT NULL,
  source_sha256 TEXT NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  nav_profile TEXT NOT NULL,
  nav_version INT NOT NULL CHECK (nav_version > 0),
  valuation_profile TEXT NOT NULL,
  valuation_version INT NOT NULL CHECK (valuation_version > 0),
  config_sha256 TEXT NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
  valuation_status TEXT NOT NULL,
  valuation_reason TEXT,
  v_low NUMERIC,
  v_mid NUMERIC,
  v_high NUMERIC,
  target_discount NUMERIC,
  current_discount NUMERIC,
  valuation_score NUMERIC NOT NULL CHECK (valuation_score BETWEEN 0 AND 1),
  z_val NUMERIC,
  v_conf NUMERIC NOT NULL CHECK (v_conf BETWEEN 0 AND 1),
  lower_halfwidth NUMERIC,
  upper_halfwidth NUMERIC,
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, analysis_at, nav_asof_date, valuation_profile, valuation_version),
  CHECK (price_trade_date <= (analysis_at AT TIME ZONE 'Europe/Istanbul')::date),
  CHECK (nav_published_at <= analysis_at),
  CHECK (
    valuation_status <> 'OK'
    OR (v_low > 0 AND v_low <= v_mid AND v_mid <= v_high)
  )
);

CREATE INDEX IF NOT EXISTS idx_holding_valuation_latest
  ON analytics.holding_valuation_periods(ticker, analysis_at DESC, nav_asof_date DESC);
CREATE INDEX IF NOT EXISTS idx_holding_valuation_group
  ON analytics.holding_valuation_periods(peer_group, analysis_at DESC, nav_asof_date DESC);


CREATE TABLE IF NOT EXISTS analytics.holding_valuation_rejections (
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

CREATE INDEX IF NOT EXISTS idx_holding_rejections_latest
  ON analytics.holding_valuation_rejections(analysis_at DESC, ticker);

CREATE TABLE IF NOT EXISTS analytics.holding_m2_scores (
  ticker TEXT NOT NULL,
  analysis_at TIMESTAMPTZ NOT NULL,
  asof_date DATE GENERATED ALWAYS AS ((analysis_at AT TIME ZONE 'Europe/Istanbul')::date) STORED,
  nav_asof_date DATE NOT NULL,
  m2_score NUMERIC NOT NULL CHECK (m2_score BETWEEN 0 AND 1),
  m2_source TEXT NOT NULL,
  valuation_usable BOOLEAN NOT NULL,
  score_inputs JSONB NOT NULL CHECK (jsonb_typeof(score_inputs) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, analysis_at, nav_asof_date)
);

CREATE INDEX IF NOT EXISTS idx_holding_m2_latest
  ON analytics.holding_m2_scores(ticker, analysis_at DESC, nav_asof_date DESC);

CREATE OR REPLACE VIEW analytics.latest_holding_m2_scores AS
SELECT DISTINCT ON (ticker)
       ticker, analysis_at, asof_date, nav_asof_date,
       m2_score, m2_source, valuation_usable, score_inputs
FROM analytics.holding_m2_scores
ORDER BY ticker, analysis_at DESC, nav_asof_date DESC;
