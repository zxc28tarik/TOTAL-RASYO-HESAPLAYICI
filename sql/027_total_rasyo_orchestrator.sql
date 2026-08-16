-- ============================================================================
-- Total Rasyo ana orkestratoru — kalicilik tablolari.
--
-- SOZLESME 1 (TEK KESIM): her satir TEK bir `analysis_at` kesimine aittir.
--   Bilesenlerin GERCEK kaynak zamani ayrica saklanir (`*_source_at`); kesim
--   esitligi degil, `kaynak_zamani <= analysis_at` kurali uygulanir.
--
-- SOZLESME 2 (OTORITATIFLIK): ayni `analysis_at` yeniden calistirildiginda
--   eski satirlar SILINIR. Birincil anahtar bunu garanti etmez -- silme
--   kalicilik katmaninin sorumlulugudur; buradaki PK yalniz ayni kosuda
--   yinelenen satiri engeller.
--
-- SOZLESME 3 (RETLER KAYBOLMAZ): motoru coken veya verisi eksik sirket de
--   satir olarak YAZILIR. "sonuc yok" ile "calistirilmadi", "motor coktu" ve
--   "yetersiz veri" AYRI durumlardir; bu yuzden status CHECK listesi ayrik.
--
-- SOZLESME 4 (ALTI MODUL): Total Rasyo formulu src/analytics/total_rasyo_score.py
--   icindedir: M2 .40, M1 .18, M3 .12, Ek4 .16, Ek1 .08, Ek9 .06 + good_count
--   vetosu. Burada ikinci bir formul YOKTUR; yalniz sonucu saklanir.
--   Bir modul eksikse skor NULL kalir ve agirlik DAGITILMAZ.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analytics.daily_engine_run (
  analysis_at TIMESTAMPTZ NOT NULL,
  engine TEXT NOT NULL
    CHECK (engine IN ('BANK','NONFIN','HOLDING','GYO','INSURANCE','FINANCIAL')),
  status TEXT NOT NULL
    CHECK (status IN ('OK','FAILED','SKIPPED')),
  result_count INT NOT NULL CHECK (result_count >= 0),
  rejection_count INT NOT NULL CHECK (rejection_count >= 0),
  routed_company_count INT NOT NULL CHECK (routed_company_count >= 0),
  error_type TEXT,
  error_message TEXT CHECK (error_message IS NULL OR length(error_message) <= 500),
  duration_ms INT CHECK (duration_ms IS NULL OR duration_ms >= 0),
  config_sha256 TEXT CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$'),
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (analysis_at, engine),
  -- Coken motor hata tipini TASIMALIDIR; sessiz basarisizlik yasak.
  CHECK (status <> 'FAILED' OR error_type IS NOT NULL),
  -- Basarili motor hata tasimaz.
  CHECK (status <> 'OK' OR error_type IS NULL)
);

CREATE TABLE IF NOT EXISTS analytics.company_total_rasyo_result (
  analysis_at TIMESTAMPTZ NOT NULL,
  ticker TEXT NOT NULL CHECK (btrim(ticker) <> '' AND ticker = upper(ticker)),
  routed_engine TEXT NOT NULL
    CHECK (routed_engine IN ('BANK','NONFIN','HOLDING','GYO','INSURANCE','FINANCIAL')),
  engine_status TEXT NOT NULL
    CHECK (engine_status IN ('OK','MOTOR_COKTU','CALISTIRILMADI','REDDEDILDI')),
  engine_reason TEXT CHECK (engine_reason IS NULL OR length(engine_reason) <= 500),

  -- M2: sektor motorundan gelir (SOZLESME 6: module_scores'taki eski m2 DEGIL).
  m2_score NUMERIC CHECK (m2_score IS NULL OR m2_score BETWEEN 0 AND 1),
  m2_source TEXT,
  m2_source_at TIMESTAMPTZ,
  m2_source_type TEXT,
  m2_missing BOOLEAN NOT NULL,
  valuation_confidence NUMERIC
    CHECK (valuation_confidence IS NULL OR valuation_confidence BETWEEN 0 AND 1),

  -- M1/M3/Ek4/Ek1/Ek9: analytics.module_scores'tan point-in-time okunur.
  m1_score NUMERIC CHECK (m1_score IS NULL OR m1_score BETWEEN 0 AND 1),
  m1_source_at TIMESTAMPTZ,
  m1_missing BOOLEAN NOT NULL,
  m3_score NUMERIC CHECK (m3_score IS NULL OR m3_score BETWEEN 0 AND 1),
  m3_source_at TIMESTAMPTZ,
  m3_missing BOOLEAN NOT NULL,
  ek4_score NUMERIC CHECK (ek4_score IS NULL OR ek4_score BETWEEN 0 AND 1),
  ek4_source_at TIMESTAMPTZ,
  ek4_missing BOOLEAN NOT NULL,
  ek1_score NUMERIC CHECK (ek1_score IS NULL OR ek1_score BETWEEN 0 AND 1),
  ek1_source_at TIMESTAMPTZ,
  ek1_missing BOOLEAN NOT NULL,
  ek9_score NUMERIC CHECK (ek9_score IS NULL OR ek9_score BETWEEN 0 AND 1),
  ek9_source_at TIMESTAMPTZ,
  ek9_missing BOOLEAN NOT NULL,
  module_source_type TEXT,

  -- Veto girdisi ZORUNLU sozlesmenin parcasidir; eksikse sifir VARSAYILMAZ.
  good_count_ge8 INT CHECK (good_count_ge8 IS NULL OR good_count_ge8 >= 0),
  good_count_missing BOOLEAN NOT NULL,

  base_score NUMERIC CHECK (base_score IS NULL OR base_score BETWEEN 0 AND 1),
  final_score NUMERIC CHECK (final_score IS NULL OR final_score BETWEEN 0 AND 1),
  total_rasyo_100 NUMERIC CHECK (total_rasyo_100 IS NULL OR total_rasyo_100 BETWEEN 0 AND 100),
  veto_flag BOOLEAN,
  decision TEXT CHECK (decision IS NULL OR decision IN ('AL','IZLE','UZAK')),
  weights_profile TEXT,

  total_rasyo_status TEXT NOT NULL
    CHECK (total_rasyo_status IN ('OK','YETERSIZ_VERI','MOTOR_COKTU',
                                  'CALISTIRILMADI','YONLENDIRME_CAKISMASI')),
  rejection_reason TEXT CHECK (rejection_reason IS NULL OR length(rejection_reason) <= 500),
  missing_modules TEXT,
  data_confidence NUMERIC CHECK (data_confidence IS NULL OR data_confidence BETWEEN 0 AND 1),
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (analysis_at, ticker),

  -- TEK MOTOR SAHIPLIGI: ayni sirket ayni kesimde tek satir tasir (PK) ve
  -- cakisma tespit edilirse skor URETILMEZ.
  CHECK (total_rasyo_status <> 'YONLENDIRME_CAKISMASI' OR final_score IS NULL),

  -- BASARI = alti modulun ALTISI da dolu + veto girdisi dolu.
  -- Eksik modulle 'OK' yazilmasi engellenir; agirlik dagitimi imkansiz olur.
  CHECK (
    total_rasyo_status <> 'OK' OR (
      m1_missing = false AND m2_missing = false AND m3_missing = false
      AND ek4_missing = false AND ek1_missing = false AND ek9_missing = false
      AND good_count_missing = false
      AND final_score IS NOT NULL AND base_score IS NOT NULL
      AND decision IS NOT NULL AND veto_flag IS NOT NULL
    )
  ),

  -- OK OLMAYAN her satir NEDEN tasimalidir; sessiz kayip yasak.
  CHECK (total_rasyo_status = 'OK' OR rejection_reason IS NOT NULL),

  -- Skor varsa karar da olmalidir.
  CHECK ((final_score IS NULL) = (decision IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_company_total_rasyo_pit
  ON analytics.company_total_rasyo_result (analysis_at, routed_engine);

CREATE INDEX IF NOT EXISTS idx_company_total_rasyo_ticker
  ON analytics.company_total_rasyo_result (ticker, analysis_at DESC);

CREATE INDEX IF NOT EXISTS idx_company_total_rasyo_status
  ON analytics.company_total_rasyo_result (analysis_at, total_rasyo_status);

-- Yalniz en son kesim. Eski kesimleri gizlemez; ayri sorgulanabilir kalir.
CREATE OR REPLACE VIEW analytics.latest_total_rasyo_result AS
WITH son AS (
  SELECT max(analysis_at) AS analysis_at FROM analytics.company_total_rasyo_result
)
SELECT r.*
FROM analytics.company_total_rasyo_result r
JOIN son ON son.analysis_at = r.analysis_at;

CREATE OR REPLACE VIEW analytics.latest_engine_run AS
WITH son AS (
  SELECT max(analysis_at) AS analysis_at FROM analytics.daily_engine_run
)
SELECT e.*
FROM analytics.daily_engine_run e
JOIN son ON son.analysis_at = e.analysis_at;

-- Sektor kapsam ozeti. coverage_ratio paydasi SIFIR olabilir; NULLIF sart.
CREATE OR REPLACE VIEW analytics.total_rasyo_engine_coverage AS
SELECT
  r.analysis_at,
  r.routed_engine AS engine_name,
  count(*) AS routed_company_count,
  count(*) FILTER (WHERE r.total_rasyo_status = 'OK') AS successful_count,
  count(*) FILTER (WHERE r.total_rasyo_status = 'YETERSIZ_VERI') AS insufficient_data_count,
  count(*) FILTER (WHERE r.total_rasyo_status = 'MOTOR_COKTU') AS engine_failed_count,
  count(*) FILTER (WHERE r.total_rasyo_status IN ('CALISTIRILMADI','YONLENDIRME_CAKISMASI')) AS rejected_count,
  round(
    count(*) FILTER (WHERE r.total_rasyo_status = 'OK')::numeric
    / NULLIF(count(*), 0), 6
  ) AS coverage_ratio
FROM analytics.company_total_rasyo_result r
GROUP BY r.analysis_at, r.routed_engine;
