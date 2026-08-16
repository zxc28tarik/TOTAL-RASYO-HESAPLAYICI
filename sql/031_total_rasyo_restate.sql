-- ============================================================================
-- CURRENT_KNOWLEDGE_RESTATE — AYRI TABLOLAR.
--
-- NEDEN AYRI TABLO:
--   analytics.company_total_rasyo_result artik TEK ve NET bir anlam tasiyor:
--     "Bu analysis_at aninda GERCEKTEN BILINEN verilerle uretilmis PIT sonucu."
--   Ayni tabloya "bugunku bilgilerle gecmisi yeniden hesapla" sonucunu koymak,
--   bir knowledge_basis sutunu eklense bile, TEK BIR FILTRE UNUTULDUGUNDA
--   look-ahead sizintisi uretir. Ayrica V19'un PK'sini, view'larini ve
--   sorgularini degistirmek gerekirdi.
--
-- IKI FARKLI BILGI URUNU:
--   PIT_HISTORY               -> o gun ne biliniyordu?      (V19 tablosu)
--   CURRENT_KNOWLEDGE_RESTATE -> bugun bildiklerimizle
--                                o donem ne olurdu?         (bu tablolar)
--
-- EN ONEMLI INVARIANT: normal PIT sorgulari ve V19 latest_* view'lari bu
--   tablolara HIC BAKMAZ. Restate sonucuna yalnizca ACIKCA restate isteyen
--   sorgu erisir. Bu dosya V19 view'larina DOKUNMAZ -- dokunmamasi
--   sozlesmenin parcasidir.
--
-- ANAHTAR AYRIMI:
--   target_analysis_at  = hangi TARIHSEL KESIMI yeniden hesapliyoruz?
--   knowledge_cutoff_at = hangi TARIHE KADAR yayimlanmis bilgiyi kullaniyoruz?
--   Ikisi ayni kavram DEGILDIR. Ayni kesim, farkli cutoff'larla birden fazla
--   kez guvenle yeniden hesaplanabilir.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analytics.total_rasyo_restate_runs (
  restate_run_id TEXT PRIMARY KEY
    CHECK (restate_run_id ~ '^[0-9a-f]{64}$'),
  target_analysis_at TIMESTAMPTZ NOT NULL,
  knowledge_cutoff_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL
    CHECK (status IN ('COMPLETE','COMPLETE_NO_RESULTS','PARTIAL','FAILED')),
  impact_plan_id TEXT CHECK (impact_plan_id IS NULL OR impact_plan_id ~ '^[0-9a-f]{64}$'),
  registry_version INT NOT NULL CHECK (registry_version >= 1),
  registry_sha256 TEXT NOT NULL CHECK (registry_sha256 ~ '^[0-9a-f]{64}$'),
  detector_version INT NOT NULL CHECK (detector_version >= 1),
  calculation_profile TEXT NOT NULL,
  calculation_version INT NOT NULL CHECK (calculation_version >= 1),
  company_count INT NOT NULL CHECK (company_count >= 0),
  successful_company_count INT NOT NULL CHECK (successful_company_count >= 0),
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (finished_at >= started_at),
  CHECK (successful_company_count <= company_count),
  -- RESTATE'IN TANIMI: bugunku bilgiyle gecmisi hesaplamak demek, bilgi
  -- kesiminin hedef kesimden SONRA olmasi demektir. knowledge_cutoff_at
  -- target_analysis_at'ten ONCE olsaydi bu bir restate degil, eksik bilgiyle
  -- yapilmis baska bir PIT kosusu olurdu.
  CHECK (knowledge_cutoff_at >= target_analysis_at)
);

CREATE INDEX IF NOT EXISTS idx_restate_runs_target
  ON analytics.total_rasyo_restate_runs (target_analysis_at DESC, knowledge_cutoff_at DESC);

CREATE TABLE IF NOT EXISTS analytics.company_total_rasyo_restate_result (
  restate_run_id TEXT NOT NULL
    REFERENCES analytics.total_rasyo_restate_runs (restate_run_id),
  ticker TEXT NOT NULL CHECK (btrim(ticker) <> '' AND ticker = upper(ticker)),
  target_analysis_at TIMESTAMPTZ NOT NULL,
  knowledge_cutoff_at TIMESTAMPTZ NOT NULL,
  engine_family TEXT NOT NULL
    CHECK (engine_family IN ('BANK','NONFIN','HOLDING','GYO','INSURANCE','FINANCIAL')),

  -- V19 sonuc sozlesmesi mumkun oldugunca KORUNUR.
  m2_score NUMERIC CHECK (m2_score IS NULL OR m2_score BETWEEN 0 AND 1),
  m2_source TEXT,
  m2_source_at TIMESTAMPTZ,
  m2_missing BOOLEAN NOT NULL,
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
  good_count_ge8 INT CHECK (good_count_ge8 IS NULL OR good_count_ge8 >= 0),
  good_count_missing BOOLEAN NOT NULL,
  base_score NUMERIC CHECK (base_score IS NULL OR base_score BETWEEN 0 AND 1),
  final_score NUMERIC CHECK (final_score IS NULL OR final_score BETWEEN 0 AND 1),
  veto_flag BOOLEAN,
  decision TEXT CHECK (decision IS NULL OR decision IN ('AL','IZLE','UZAK')),
  weights_profile TEXT,
  total_rasyo_status TEXT NOT NULL
    CHECK (total_rasyo_status IN ('OK','YETERSIZ_VERI','MOTOR_COKTU',
                                  'CALISTIRILMADI','YONLENDIRME_CAKISMASI')),
  rejection_reason TEXT CHECK (rejection_reason IS NULL OR length(rejection_reason) <= 500),
  insufficiency_reason TEXT,
  missing_modules TEXT,
  impact_plan_id TEXT,
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Ayni tarihsel kesim FARKLI cutoff'larla birden fazla kez hesaplanabilir;
  -- bu yuzden anahtar (restate_run_id, ticker). V19'un (analysis_at, ticker)
  -- anahtari DEGISMEZ.
  PRIMARY KEY (restate_run_id, ticker),

  CHECK (knowledge_cutoff_at >= target_analysis_at),
  CHECK (total_rasyo_status = 'OK' OR rejection_reason IS NOT NULL),
  CHECK ((final_score IS NULL) = (decision IS NULL)),
  CHECK (
    total_rasyo_status <> 'OK' OR (
      m1_missing = false AND m2_missing = false AND m3_missing = false
      AND ek4_missing = false AND ek1_missing = false AND ek9_missing = false
      AND good_count_missing = false
      AND final_score IS NOT NULL AND decision IS NOT NULL
    )
  ),
  -- LOOK-AHEAD KORUMASI: restate sonucunda kullanilan hicbir bilesenin
  -- kaynak zamani knowledge_cutoff_at'i ASAMAZ. "Bugunku bilgi" demek
  -- sinirsiz bilgi demek degildir; cutoff kesindir.
  CHECK (m2_source_at IS NULL OR m2_source_at <= knowledge_cutoff_at),
  CHECK (m1_source_at IS NULL OR m1_source_at <= knowledge_cutoff_at),
  CHECK (m3_source_at IS NULL OR m3_source_at <= knowledge_cutoff_at),
  CHECK (ek4_source_at IS NULL OR ek4_source_at <= knowledge_cutoff_at),
  CHECK (ek1_source_at IS NULL OR ek1_source_at <= knowledge_cutoff_at),
  CHECK (ek9_source_at IS NULL OR ek9_source_at <= knowledge_cutoff_at)
);

CREATE INDEX IF NOT EXISTS idx_restate_result_ticker
  ON analytics.company_total_rasyo_restate_result (ticker, target_analysis_at DESC);

CREATE INDEX IF NOT EXISTS idx_restate_result_plan
  ON analytics.company_total_rasyo_restate_result (impact_plan_id)
  WHERE impact_plan_id IS NOT NULL;

-- Restate sonucuna ACIKCA erisen view. Adi bilerek "latest" ICERMEZ:
-- V19'un latest_* isim ailesine benzemesi, sorgu yazarken karistirilma
-- riskini artirirdi.
CREATE OR REPLACE VIEW analytics.restate_vs_pit_comparison AS
SELECT
  r.restate_run_id,
  r.ticker,
  r.target_analysis_at,
  r.knowledge_cutoff_at,
  r.engine_family,
  p.final_score  AS pit_final_score,
  p.decision     AS pit_decision,
  p.total_rasyo_status AS pit_status,
  r.final_score  AS restate_final_score,
  r.decision     AS restate_decision,
  r.total_rasyo_status AS restate_status,
  (r.final_score - p.final_score) AS score_delta,
  (r.decision IS DISTINCT FROM p.decision) AS decision_changed
FROM analytics.company_total_rasyo_restate_result r
LEFT JOIN analytics.company_total_rasyo_result p
  ON p.ticker = r.ticker
 AND p.analysis_at = r.target_analysis_at;

COMMENT ON TABLE analytics.company_total_rasyo_restate_result IS
  'CURRENT_KNOWLEDGE_RESTATE. PIT gecmisi DEGILDIR. Normal PIT sorgulari ve '
  'latest_* view''lari bu tabloya BAKMAZ; look-ahead sizintisi olurdu.';

COMMENT ON TABLE analytics.total_rasyo_restate_runs IS
  'Restate kosu defteri. target_analysis_at (hangi kesim) ile '
  'knowledge_cutoff_at (hangi tarihe kadarki bilgi) AYRI kavramlardir.';
