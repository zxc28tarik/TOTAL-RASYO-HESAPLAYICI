-- ============================================================================
-- MODUL URETIM SOY KUTUGU — readiness bariyerinin kanit tabani.
--
-- SORUN: `analytics.module_scores` satirinin VAR OLMASI, o satirin BU impact
-- planina uygun YENI kaynak surumunden uretildigini KANITLAMAZ. Satir aylar
-- once eski bir bilancoyla hesaplanmis olabilir; sorgu yine bir satir doner.
--
-- "Satir var" ile "dogru girdiden tazelendi" ayni sey DEGILDIR. Readiness
-- bariyeri ikincisini ister; bu tablo onu kanitlanabilir kilar.
--
-- Kayit her modul URETIMINDE yazilir ve hangi kaynak surumunden, hangi
-- hesaplama profiliyle, ne zaman uretildigini tasir.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analytics.module_production_lineage (
  ticker TEXT NOT NULL CHECK (btrim(ticker) <> '' AND ticker = upper(ticker)),
  module TEXT NOT NULL
    CHECK (module IN ('M2','M1','M3','Ek1','Ek4','Ek9','GOOD_COUNT')),
  analysis_at TIMESTAMPTZ NOT NULL,
  engine_family TEXT
    CHECK (engine_family IS NULL OR engine_family IN
           ('BANK','NONFIN','HOLDING','GYO','INSURANCE','FINANCIAL',
            'MODULE_PIPELINE')),
  -- Hangi finansal kaynak surumunden uretildi. Readiness bunu plandaki
  -- source_version_id ile karsilastirir.
  source_version_id TEXT,
  source_period_end DATE,
  produced_at TIMESTAMPTZ NOT NULL,
  calculation_profile TEXT NOT NULL,
  calculation_version INT NOT NULL CHECK (calculation_version >= 1),
  -- Hangi impact plani bu uretimi tetikledi (varsa).
  impact_plan_id TEXT,
  upstream_fingerprint TEXT,
  diagnostics JSONB NOT NULL DEFAULT '{}'
    CHECK (jsonb_typeof(diagnostics) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, module, analysis_at)
);

CREATE INDEX IF NOT EXISTS idx_module_lineage_plan
  ON analytics.module_production_lineage (impact_plan_id)
  WHERE impact_plan_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_module_lineage_source
  ON analytics.module_production_lineage (ticker, module, source_version_id);

GRANT SELECT, INSERT, UPDATE ON analytics.module_production_lineage
  TO total_rasyo_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
  ON analytics.module_production_lineage TO total_rasyo_test;

COMMENT ON TABLE analytics.module_production_lineage IS
  'Modul uretim soy kutugu. "Satir var" ile "dogru girdiden tazelendi" ayni '
  'sey degildir; readiness bariyeri ikincisini bu tablodan kanitlar.';
