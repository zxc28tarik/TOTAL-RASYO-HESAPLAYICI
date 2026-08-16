-- Resmi MKK API Portal / KAP veri alimi icin kayipsiz ham katman.
-- Uc nokta ve alan adlari portal urun dokumanindan config ile gelir; burada
-- belgelenmemis bir KAP endpoint'i sabitlenmez.

CREATE SCHEMA IF NOT EXISTS raw;

ALTER TABLE core.universe_stocks
  ADD COLUMN IF NOT EXISTS kap_company_id TEXT,
  ADD COLUMN IF NOT EXISTS source_url TEXT,
  ADD COLUMN IF NOT EXISTS universe_source TEXT,
  ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_universe_kap_company_id
  ON core.universe_stocks(kap_company_id)
  WHERE kap_company_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS raw.kap_disclosures (
  source TEXT NOT NULL,
  disclosure_id TEXT NOT NULL,
  published_at TIMESTAMPTZ NOT NULL,
  ticker TEXT,
  company_id TEXT,
  notification_type TEXT,
  subject TEXT,
  source_url TEXT,
  payload JSONB NOT NULL,
  payload_sha256 CHAR(64) NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source, disclosure_id),
  CONSTRAINT ck_kap_disclosure_payload_object
    CHECK (jsonb_typeof(payload) = 'object'),
  CONSTRAINT ck_kap_disclosure_sha256
    CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_kap_disclosure_timezone_future
    CHECK (published_at <= fetched_at + interval '5 minutes'),
  CONSTRAINT ck_kap_disclosure_seen_order
    CHECK (first_seen_at <= last_seen_at)
);

CREATE INDEX IF NOT EXISTS idx_kap_disclosures_published
  ON raw.kap_disclosures(published_at, disclosure_id);
CREATE INDEX IF NOT EXISTS idx_kap_disclosures_ticker_published
  ON raw.kap_disclosures(ticker, published_at)
  WHERE ticker IS NOT NULL;

CREATE OR REPLACE FUNCTION raw.reject_kap_payload_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.payload_sha256 <> NEW.payload_sha256 THEN
    RAISE EXCEPTION
      'KAP disclosure payload mutation rejected for source=% id=% old_sha=% new_sha=%',
      OLD.source, OLD.disclosure_id, OLD.payload_sha256, NEW.payload_sha256;
  END IF;
  IF OLD.published_at <> NEW.published_at THEN
    RAISE EXCEPTION
      'KAP disclosure publication time mutation rejected for source=% id=% old=% new=%',
      OLD.source, OLD.disclosure_id, OLD.published_at, NEW.published_at;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_reject_kap_payload_mutation ON raw.kap_disclosures;
CREATE TRIGGER trg_reject_kap_payload_mutation
BEFORE UPDATE OF payload_sha256, payload, published_at
ON raw.kap_disclosures
FOR EACH ROW
EXECUTE FUNCTION raw.reject_kap_payload_mutation();

CREATE TABLE IF NOT EXISTS raw.kap_sync_state (
  source TEXT NOT NULL,
  stream_name TEXT NOT NULL,
  cursor_value TEXT,
  window_start TIMESTAMPTZ NOT NULL,
  window_end TIMESTAMPTZ NOT NULL,
  last_success_at TIMESTAMPTZ NOT NULL,
  rows_seen INT NOT NULL,
  pages_fetched INT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (source, stream_name),
  CONSTRAINT ck_kap_sync_window CHECK (window_start <= window_end),
  CONSTRAINT ck_kap_sync_counts CHECK (rows_seen >= 0 AND pages_fetched >= 0)
);

CREATE TABLE IF NOT EXISTS raw.kap_financial_facts (
  source TEXT NOT NULL,
  disclosure_id TEXT NOT NULL,
  mapping_profile TEXT NOT NULL,
  mapping_version INT NOT NULL,
  fact_key CHAR(64) NOT NULL,
  ticker TEXT,
  published_at TIMESTAMPTZ NOT NULL,
  version_tag TEXT NOT NULL,
  version_sequence INT NOT NULL,
  fact_code TEXT NOT NULL,
  period_start DATE,
  period_end DATE NOT NULL,
  currency TEXT,
  unit_scale BIGINT NOT NULL,
  raw_value_text TEXT NOT NULL,
  normalized_value NUMERIC NOT NULL,
  scaled_value NUMERIC NOT NULL,
  statement_scope TEXT,
  dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
  extracted_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (source, disclosure_id, mapping_profile, mapping_version, fact_key),
  FOREIGN KEY (source, disclosure_id)
    REFERENCES raw.kap_disclosures(source, disclosure_id)
    ON DELETE RESTRICT,
  CONSTRAINT ck_kap_fact_mapping_version CHECK (mapping_version > 0),
  CONSTRAINT ck_kap_fact_publication_time
    CHECK (published_at <= extracted_at + interval '5 minutes'),
  CONSTRAINT ck_kap_fact_key CHECK (fact_key ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_kap_fact_version_sequence CHECK (version_sequence >= 0),
  CONSTRAINT ck_kap_fact_unit_scale CHECK (unit_scale > 0 AND unit_scale <= 1000000000000),
  CONSTRAINT ck_kap_fact_numeric_bounds CHECK (
    abs(normalized_value) <= 1e100::numeric AND abs(scaled_value) <= 1e100::numeric
  ),
  CONSTRAINT ck_kap_fact_period CHECK (period_start IS NULL OR period_start <= period_end),
  CONSTRAINT ck_kap_fact_dimensions CHECK (
    jsonb_typeof(dimensions) = 'object'
    AND octet_length(dimensions::text) <= 65536
  )
);

CREATE INDEX IF NOT EXISTS idx_kap_facts_ticker_period_code
  ON raw.kap_financial_facts(ticker, period_end, fact_code, published_at);

CREATE OR REPLACE FUNCTION raw.reject_kap_fact_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.ticker IS DISTINCT FROM NEW.ticker
     OR OLD.published_at <> NEW.published_at
     OR OLD.version_tag <> NEW.version_tag
     OR OLD.version_sequence <> NEW.version_sequence
     OR OLD.fact_code <> NEW.fact_code
     OR OLD.period_start IS DISTINCT FROM NEW.period_start
     OR OLD.period_end <> NEW.period_end
     OR OLD.currency IS DISTINCT FROM NEW.currency
     OR OLD.unit_scale <> NEW.unit_scale
     OR OLD.raw_value_text <> NEW.raw_value_text
     OR OLD.normalized_value <> NEW.normalized_value
     OR OLD.scaled_value <> NEW.scaled_value
     OR OLD.statement_scope IS DISTINCT FROM NEW.statement_scope
     OR OLD.dimensions <> NEW.dimensions THEN
    RAISE EXCEPTION
      'KAP financial fact mutation rejected for source=% id=% profile=% version=% fact_key=%',
      OLD.source, OLD.disclosure_id, OLD.mapping_profile, OLD.mapping_version, OLD.fact_key;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_reject_kap_fact_mutation ON raw.kap_financial_facts;
CREATE TRIGGER trg_reject_kap_fact_mutation
BEFORE UPDATE ON raw.kap_financial_facts
FOR EACH ROW
EXECUTE FUNCTION raw.reject_kap_fact_mutation();

CREATE TABLE IF NOT EXISTS raw.kap_fact_extraction_rejections (
  source TEXT NOT NULL,
  disclosure_id TEXT NOT NULL,
  mapping_profile TEXT NOT NULL,
  mapping_version INT NOT NULL,
  payload_sha256 CHAR(64) NOT NULL,
  reason TEXT NOT NULL,
  first_rejected_at TIMESTAMPTZ NOT NULL,
  last_rejected_at TIMESTAMPTZ NOT NULL,
  attempts INT NOT NULL DEFAULT 1,
  PRIMARY KEY (source, disclosure_id, mapping_profile, mapping_version),
  FOREIGN KEY (source, disclosure_id)
    REFERENCES raw.kap_disclosures(source, disclosure_id)
    ON DELETE RESTRICT,
  CONSTRAINT ck_kap_fact_rejection_version CHECK (mapping_version > 0),
  CONSTRAINT ck_kap_fact_rejection_attempts CHECK (attempts > 0),
  CONSTRAINT ck_kap_fact_rejection_time CHECK (first_rejected_at <= last_rejected_at)
);
