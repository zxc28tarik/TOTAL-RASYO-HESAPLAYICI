-- Surumlu semantic finansal kalem katmani ve BANK turetilmis metrik lineage'i.
-- raw.kap_financial_facts kayipsiz kaynak; bu katman sektor motorlarinin ortak
-- canonical alan adlarini ve hangi ham kalemden uretildigini saklar.

CREATE TABLE IF NOT EXISTS core.semantic_financial_facts (
  source TEXT NOT NULL,
  disclosure_id TEXT NOT NULL,
  semantic_profile TEXT NOT NULL,
  semantic_version INT NOT NULL,
  canonical_field TEXT NOT NULL,
  lineage_sha256 CHAR(64) NOT NULL,
  ticker TEXT NOT NULL,
  sector_family TEXT NOT NULL,
  published_at TIMESTAMPTZ NOT NULL,
  version_tag TEXT NOT NULL,
  version_sequence INT NOT NULL,
  nature TEXT NOT NULL,
  period_start DATE,
  period_end DATE NOT NULL,
  currency TEXT,
  statement_scope TEXT,
  value NUMERIC NOT NULL,
  source_fact_code TEXT NOT NULL,
  source_fact_key CHAR(64) NOT NULL,
  source_mapping_profile TEXT NOT NULL,
  source_mapping_version INT NOT NULL,
  dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
  mapped_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (
    source, disclosure_id, semantic_profile, semantic_version,
    canonical_field, lineage_sha256
  ),
  FOREIGN KEY (
    source, disclosure_id, source_mapping_profile,
    source_mapping_version, source_fact_key
  ) REFERENCES raw.kap_financial_facts (
    source, disclosure_id, mapping_profile, mapping_version, fact_key
  ) ON DELETE RESTRICT,
  CONSTRAINT ck_semantic_fact_versions CHECK (
    semantic_version > 0 AND source_mapping_version > 0 AND version_sequence >= 0
  ),
  CONSTRAINT ck_semantic_fact_nature CHECK (
    nature IN ('INSTANT', 'YTD', 'QUARTER', 'TTM', 'RATIO')
  ),
  CONSTRAINT ck_semantic_fact_period CHECK (
    period_start IS NULL OR period_start <= period_end
  ),
  CONSTRAINT ck_semantic_fact_duration_start CHECK (
    nature NOT IN ('YTD', 'QUARTER') OR period_start IS NOT NULL
  ),
  CONSTRAINT ck_semantic_fact_publication CHECK (
    published_at <= mapped_at
  ),
  CONSTRAINT ck_semantic_fact_lineage CHECK (
    lineage_sha256 ~ '^[0-9a-f]{64}$' AND source_fact_key ~ '^[0-9a-f]{64}$'
  ),
  CONSTRAINT ck_semantic_fact_value_bounds CHECK (abs(value) <= 1e100::numeric),
  CONSTRAINT ck_semantic_fact_dimensions CHECK (
    jsonb_typeof(dimensions) = 'object'
    AND octet_length(dimensions::text) <= 65536
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_semantic_fact_lineage
  ON core.semantic_financial_facts(lineage_sha256);

CREATE INDEX IF NOT EXISTS idx_semantic_fact_pit
  ON core.semantic_financial_facts(
    ticker, sector_family, canonical_field, period_end,
    published_at DESC, version_sequence DESC, lineage_sha256 DESC
  );

CREATE OR REPLACE FUNCTION core.reject_semantic_fact_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.source <> NEW.source
     OR OLD.disclosure_id <> NEW.disclosure_id
     OR OLD.semantic_profile <> NEW.semantic_profile
     OR OLD.semantic_version <> NEW.semantic_version
     OR OLD.canonical_field <> NEW.canonical_field
     OR OLD.lineage_sha256 <> NEW.lineage_sha256
     OR OLD.ticker <> NEW.ticker
     OR OLD.sector_family <> NEW.sector_family
     OR OLD.published_at <> NEW.published_at
     OR OLD.version_tag <> NEW.version_tag
     OR OLD.version_sequence <> NEW.version_sequence
     OR OLD.nature <> NEW.nature
     OR OLD.period_start IS DISTINCT FROM NEW.period_start
     OR OLD.period_end <> NEW.period_end
     OR OLD.currency IS DISTINCT FROM NEW.currency
     OR OLD.statement_scope IS DISTINCT FROM NEW.statement_scope
     OR OLD.value <> NEW.value
     OR OLD.source_fact_code <> NEW.source_fact_code
     OR OLD.source_fact_key <> NEW.source_fact_key
     OR OLD.source_mapping_profile <> NEW.source_mapping_profile
     OR OLD.source_mapping_version <> NEW.source_mapping_version
     OR OLD.dimensions <> NEW.dimensions THEN
    RAISE EXCEPTION
      'semantic fact mutation rejected source=% disclosure=% profile=% version=% field=% lineage=%',
      OLD.source, OLD.disclosure_id, OLD.semantic_profile,
      OLD.semantic_version, OLD.canonical_field, OLD.lineage_sha256;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_reject_semantic_fact_mutation
  ON core.semantic_financial_facts;
CREATE TRIGGER trg_reject_semantic_fact_mutation
BEFORE UPDATE ON core.semantic_financial_facts
FOR EACH ROW EXECUTE FUNCTION core.reject_semantic_fact_mutation();

ALTER TABLE core.bank_metrics_quarterly
  ADD COLUMN IF NOT EXISTS lineage_sha256 CHAR(64),
  ADD COLUMN IF NOT EXISTS source_lineage JSONB,
  ADD COLUMN IF NOT EXISTS derivation_profile TEXT,
  ADD COLUMN IF NOT EXISTS derivation_version INT,
  ADD COLUMN IF NOT EXISTS derivation_diagnostics JSONB;

CREATE UNIQUE INDEX IF NOT EXISTS uq_bank_metrics_lineage
  ON core.bank_metrics_quarterly(lineage_sha256)
  WHERE lineage_sha256 IS NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'ck_bank_metrics_derived_lineage'
      AND conrelid = 'core.bank_metrics_quarterly'::regclass
  ) THEN
    ALTER TABLE core.bank_metrics_quarterly
      ADD CONSTRAINT ck_bank_metrics_derived_lineage CHECK (
        lineage_sha256 IS NULL
        OR (
          lineage_sha256 ~ '^[0-9a-f]{64}$'
          AND source_lineage IS NOT NULL
          AND jsonb_typeof(source_lineage) = 'array'
          AND derivation_profile IS NOT NULL
          AND btrim(derivation_profile) <> ''
          AND derivation_version > 0
          AND derivation_diagnostics IS NOT NULL
          AND jsonb_typeof(derivation_diagnostics) = 'object'
        )
      );
  END IF;
END $$;

CREATE OR REPLACE FUNCTION core.reject_derived_bank_metric_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.lineage_sha256 IS NOT NULL AND (
       OLD.ticker <> NEW.ticker
       OR OLD.period_end <> NEW.period_end
       OR OLD.version_tag <> NEW.version_tag
       OR OLD.version_sequence <> NEW.version_sequence
       OR OLD.published_at <> NEW.published_at
       OR OLD.source_disclosure_id IS DISTINCT FROM NEW.source_disclosure_id
       OR OLD.roe_ttm IS DISTINCT FROM NEW.roe_ttm
       OR OLD.bvps IS DISTINCT FROM NEW.bvps
       OR OLD.payout_sus IS DISTINCT FROM NEW.payout_sus
       OR OLD.lineage_sha256 IS DISTINCT FROM NEW.lineage_sha256
       OR OLD.source_lineage IS DISTINCT FROM NEW.source_lineage
       OR OLD.derivation_profile IS DISTINCT FROM NEW.derivation_profile
       OR OLD.derivation_version IS DISTINCT FROM NEW.derivation_version
       OR OLD.derivation_diagnostics IS DISTINCT FROM NEW.derivation_diagnostics
  ) THEN
    RAISE EXCEPTION
      'derived BANK metric mutation rejected record_id=% lineage=%',
      OLD.record_id, OLD.lineage_sha256;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_reject_derived_bank_metric_mutation
  ON core.bank_metrics_quarterly;
CREATE TRIGGER trg_reject_derived_bank_metric_mutation
BEFORE UPDATE ON core.bank_metrics_quarterly
FOR EACH ROW EXECUTE FUNCTION core.reject_derived_bank_metric_mutation();


-- Ayni published_at/version_sequence aninda turetilmis iki mantiksal snapshot
-- varsa tam lineage hash deterministik tie-break olur. Eski manuel satirlarda
-- lineage NULL kalabilir; record_id son savunma olarak korunur.
CREATE OR REPLACE FUNCTION analytics.bank_point_in_time_slots(
  p_ticker TEXT,
  p_analysis_at TIMESTAMPTZ,
  p_anchor_period_end DATE
)
RETURNS TABLE (
  period_end DATE,
  record_id BIGINT,
  selected_version_tag TEXT,
  selected_version_sequence INT,
  selected_published_at TIMESTAMPTZ,
  roe_ttm NUMERIC,
  bvps NUMERIC,
  payout_sus NUMERIC
)
LANGUAGE sql
STABLE
AS $$
WITH params AS (
  SELECT
    p_ticker AS ticker,
    p_analysis_at AS analysis_at,
    (
      date_trunc('quarter', p_anchor_period_end::timestamp)
      + interval '3 months' - interval '1 day'
    )::date AS anchor_period_end
),
quarter_slots AS (
  SELECT
    (
      date_trunc('quarter', p.anchor_period_end::timestamp)
      - (g.offset_q * interval '3 months')
      + interval '3 months' - interval '1 day'
    )::date AS period_end
  FROM params p
  CROSS JOIN LATERAL generate_series(7, 0, -1) AS g(offset_q)
),
ranked AS (
  SELECT
    m.record_id,
    m.ticker,
    m.period_end,
    m.version_tag,
    m.version_sequence,
    m.published_at,
    m.roe_ttm,
    m.bvps,
    m.payout_sus,
    row_number() OVER (
      PARTITION BY m.ticker, m.period_end
      ORDER BY
        m.published_at DESC,
        m.version_sequence DESC,
        m.lineage_sha256 DESC NULLS LAST,
        m.record_id DESC
    ) AS rn
  FROM core.bank_metrics_quarterly m
  CROSS JOIN params p
  WHERE m.ticker = p.ticker
    AND m.published_at <= p.analysis_at
    AND m.period_end BETWEEN
      (SELECT min(period_end) FROM quarter_slots)
      AND
      (SELECT max(period_end) FROM quarter_slots)
),
selected AS (
  SELECT * FROM ranked WHERE rn = 1
)
SELECT
  s.period_end,
  x.record_id,
  x.version_tag AS selected_version_tag,
  x.version_sequence AS selected_version_sequence,
  x.published_at AS selected_published_at,
  x.roe_ttm,
  x.bvps,
  x.payout_sus
FROM quarter_slots s
LEFT JOIN selected x USING (period_end)
ORDER BY s.period_end;
$$;

CREATE TABLE IF NOT EXISTS core.semantic_mapping_rejections (
  source TEXT NOT NULL,
  disclosure_id TEXT NOT NULL,
  semantic_profile TEXT NOT NULL,
  semantic_version INT NOT NULL,
  source_payload_sha256 CHAR(64) NOT NULL,
  reason TEXT NOT NULL,
  first_rejected_at TIMESTAMPTZ NOT NULL,
  last_rejected_at TIMESTAMPTZ NOT NULL,
  attempts INT NOT NULL DEFAULT 1,
  PRIMARY KEY (source, disclosure_id, semantic_profile, semantic_version),
  FOREIGN KEY (source, disclosure_id)
    REFERENCES raw.kap_disclosures(source, disclosure_id)
    ON DELETE RESTRICT,
  CONSTRAINT ck_semantic_rejection_version CHECK (semantic_version > 0),
  CONSTRAINT ck_semantic_rejection_sha CHECK (source_payload_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_semantic_rejection_attempts CHECK (attempts > 0),
  CONSTRAINT ck_semantic_rejection_time CHECK (first_rejected_at <= last_rejected_at)
);

CREATE TABLE IF NOT EXISTS core.bank_metric_derivation_rejections (
  ticker TEXT NOT NULL,
  analysis_at TIMESTAMPTZ NOT NULL,
  anchor_period_end DATE NOT NULL,
  derivation_profile TEXT NOT NULL,
  derivation_version INT NOT NULL,
  reason TEXT NOT NULL,
  first_rejected_at TIMESTAMPTZ NOT NULL,
  last_rejected_at TIMESTAMPTZ NOT NULL,
  attempts INT NOT NULL DEFAULT 1,
  PRIMARY KEY (
    ticker, analysis_at, anchor_period_end,
    derivation_profile, derivation_version
  ),
  CONSTRAINT ck_bank_derivation_rejection_version CHECK (derivation_version > 0),
  CONSTRAINT ck_bank_derivation_rejection_attempts CHECK (attempts > 0),
  CONSTRAINT ck_bank_derivation_rejection_time CHECK (first_rejected_at <= last_rejected_at)
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'ck_kap_fact_period_before_publish'
      AND conrelid = 'raw.kap_financial_facts'::regclass
  ) THEN
    ALTER TABLE raw.kap_financial_facts
      ADD CONSTRAINT ck_kap_fact_period_before_publish CHECK (
        period_end <= (published_at AT TIME ZONE 'Europe/Istanbul')::date
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'ck_semantic_fact_period_before_publish'
      AND conrelid = 'core.semantic_financial_facts'::regclass
  ) THEN
    ALTER TABLE core.semantic_financial_facts
      ADD CONSTRAINT ck_semantic_fact_period_before_publish CHECK (
        period_end <= (published_at AT TIME ZONE 'Europe/Istanbul')::date
      );
  END IF;
END $$;
