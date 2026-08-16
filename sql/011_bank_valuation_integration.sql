-- Banka değerleme üretim entegrasyonu.
-- Point-in-time seçim için DATE değil TIMESTAMPTZ kullanılır.

CREATE TABLE IF NOT EXISTS core.bank_metrics_quarterly (
  record_id BIGSERIAL PRIMARY KEY,
  ticker TEXT NOT NULL,
  period_end DATE NOT NULL,
  version_tag TEXT NOT NULL,
  version_sequence INT NOT NULL DEFAULT 0,
  published_at TIMESTAMPTZ NOT NULL,
  source_disclosure_id TEXT,
  roe_ttm NUMERIC,
  bvps NUMERIC,
  payout_sus NUMERIC,
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_bank_metrics_source_disclosure
  ON core.bank_metrics_quarterly(source_disclosure_id)
  WHERE source_disclosure_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_bank_metrics_natural_version
  ON core.bank_metrics_quarterly
     (ticker, period_end, version_tag, version_sequence, published_at);

CREATE INDEX IF NOT EXISTS idx_bank_metrics_pit_lookup
  ON core.bank_metrics_quarterly
     (ticker, period_end, published_at DESC, version_sequence DESC, record_id DESC);

COMMENT ON COLUMN core.bank_metrics_quarterly.published_at IS
  'Kamuya açıklanma anı. Point-in-time analizlerde tarih/saat birlikte korunur.';
COMMENT ON COLUMN core.bank_metrics_quarterly.version_sequence IS
  'Aynı published_at anındaki sürümler için deterministik sıralama.';

CREATE TABLE IF NOT EXISTS analytics.bank_valuation_periods (
  ticker TEXT NOT NULL,
  analysis_at TIMESTAMPTZ NOT NULL,
  anchor_period_end DATE NOT NULL,
  selected_version_tag TEXT,
  selected_published_at TIMESTAMPTZ,

  quarter_slots JSONB NOT NULL,
  roe_series_canonical JSONB NOT NULL,
  selected_versions JSONB NOT NULL,
  selected_publication_times JSONB NOT NULL,
  roe_missing_count INT NOT NULL,

  trend_slope NUMERIC,
  sector_sample_size INT,
  sector_asof_cutoff TIMESTAMPTZ,
  sd_roe_floor NUMERIC,
  floor_source TEXT,
  sd_roe_effective NUMERIC,

  valuation_status TEXT NOT NULL,
  valuation_reason TEXT,
  valuation_method TEXT,
  v_low NUMERIC,
  v_mid NUMERIC,
  v_high NUMERIC,
  justified_pb NUMERIC,
  roe_sus NUMERIC,
  growth_rate NUMERIC,
  implied_payout NUMERIC,
  payout_gap NUMERIC,
  would_be_band_too_wide BOOLEAN,

  payout_factor NUMERIC,
  outlier_conf_penalty NUMERIC,
  corner_conf_penalty NUMERIC,
  tier_cap NUMERIC,
  v_conf NUMERIC,
  confidence_factors JSONB NOT NULL,

  diagnostics JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, analysis_at, anchor_period_end)
);

CREATE INDEX IF NOT EXISTS idx_bank_valuation_latest
  ON analytics.bank_valuation_periods(ticker, analysis_at DESC);

-- Şema düzeyinde ikinci savunma katmanı. Uygulama kapısı atlansa bile bozuk
-- payout/BVPS veya sekizden farklı kanonik seri kalıcılaştırılamaz.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_bank_metrics_version_sequence'
  ) THEN
    ALTER TABLE core.bank_metrics_quarterly
      ADD CONSTRAINT ck_bank_metrics_version_sequence CHECK (version_sequence >= 0);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_bank_metrics_bvps'
  ) THEN
    ALTER TABLE core.bank_metrics_quarterly
      ADD CONSTRAINT ck_bank_metrics_bvps CHECK (bvps IS NULL OR bvps > 0);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_bank_metrics_payout'
  ) THEN
    ALTER TABLE core.bank_metrics_quarterly
      ADD CONSTRAINT ck_bank_metrics_payout CHECK (payout_sus IS NULL OR payout_sus BETWEEN 0 AND 1);
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_bank_valuation_eight_slots'
  ) THEN
    ALTER TABLE analytics.bank_valuation_periods
      ADD CONSTRAINT ck_bank_valuation_eight_slots CHECK (
        jsonb_typeof(quarter_slots) = 'array'
        AND jsonb_array_length(quarter_slots) = 8
        AND jsonb_typeof(roe_series_canonical) = 'array'
        AND jsonb_array_length(roe_series_canonical) = 8
        AND jsonb_typeof(selected_versions) = 'array'
        AND jsonb_array_length(selected_versions) = 8
        AND jsonb_typeof(selected_publication_times) = 'array'
        AND jsonb_array_length(selected_publication_times) = 8
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_bank_valuation_missing_count'
  ) THEN
    ALTER TABLE analytics.bank_valuation_periods
      ADD CONSTRAINT ck_bank_valuation_missing_count CHECK (roe_missing_count BETWEEN 0 AND 8);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_bank_valuation_confidence'
  ) THEN
    ALTER TABLE analytics.bank_valuation_periods
      ADD CONSTRAINT ck_bank_valuation_confidence CHECK (v_conf IS NULL OR v_conf BETWEEN 0 AND 1);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ck_bank_valuation_geometry'
  ) THEN
    ALTER TABLE analytics.bank_valuation_periods
      ADD CONSTRAINT ck_bank_valuation_geometry CHECK (
        valuation_status <> 'OK'
        OR (v_low > 0 AND v_mid > 0 AND v_high > 0 AND v_low <= v_mid AND v_mid <= v_high)
      );
  END IF;
END $$;

-- Tek kaynaklı üretim sorgusu: Python hattı ve PostgreSQL kabul testi aynı
-- fonksiyonu çağırır; iki ayrı SQL kopyasının zamanla ayrışması engellenir.
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
  SELECT *
  FROM ranked
  WHERE rn = 1
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
