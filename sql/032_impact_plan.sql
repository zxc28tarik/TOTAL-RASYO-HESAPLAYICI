-- ============================================================================
-- Change-impact — IMMUTABLE ETKI PLANI + APPEND-ONLY UYGULAMA KOSULARI.
--
-- IMMUTABLE'IN ANLAMI: "bir kez yazilabilir" DEGIL, "bir kez tanimlanan
--   planin ICERIGI degistirilemez". Aksi halde retry, worker yeniden
--   baslatma veya ayni fact revizyonunun tekrar islenmesi gereksiz hata
--   uretirdi.
--
--     ayni impact_plan_id + ayni plan_sha256  -> IDEMPOTENT BASARI
--                                                (yeni satir OLUSMAZ)
--     ayni impact_plan_id + FARKLI plan_sha256 -> SERT HATA, overwrite YOK
--     UPDATE / DELETE                          -> trigger ile YASAK
--
-- IKI AYRI KAVRAM:
--   impact_plan            = hangi hesaplarin etkilenmesi GEREKTIGININ
--                            degismez tanimi
--   impact_application_run = bu plani NE ZAMAN / HANGI orkestratör kosusunda
--                            uygulamaya CALISTIK
--   Bir planin birden fazla uygulama denemesi olabilir; bunlar append-only
--   kayitlardir ve plan satirini KIRLETMEZ.
--
-- KIMLIK: impact_plan_id yalniz kaynak revizyondan uretilmez. Kanonik
--   girdiler: degisen fact kumesi, analysis_at/etki baglami, registry_sha256,
--   detector_version, run_scope ve knowledge_basis. Ayni fact degisikligi
--   PIT_HISTORY ve CURRENT_KNOWLEDGE_RESTATE icin FARKLI plan kimligi uretir;
--   bilgi tabanlari farklidir.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analytics.impact_plan (
  impact_plan_id TEXT PRIMARY KEY CHECK (impact_plan_id ~ '^[0-9a-f]{64}$'),
  -- KIMLIK ile ICERIK ayri tutulur: ayni kimlikle farkli icerik gelirse
  -- catisma ancak boyle tespit edilir.
  plan_sha256 TEXT NOT NULL CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
  knowledge_basis TEXT NOT NULL
    CHECK (knowledge_basis IN ('PIT_HISTORY','CURRENT_KNOWLEDGE_RESTATE')),
  run_scope TEXT NOT NULL CHECK (run_scope IN ('FULL_UNIVERSE','TARGETED')),
  direct_ticker TEXT NOT NULL CHECK (direct_ticker = upper(direct_ticker)),
  source_fact_id TEXT NOT NULL,
  source_statement_id TEXT NOT NULL,
  source_version_id TEXT NOT NULL,
  statement_type TEXT NOT NULL
    CHECK (statement_type IN ('BALANCE_SHEET','INCOME_STATEMENT','CASH_FLOW')),
  fact_key TEXT NOT NULL,
  changed_period_end DATE NOT NULL,
  published_at TIMESTAMPTZ NOT NULL,
  analysis_at TIMESTAMPTZ,
  knowledge_cutoff_at TIMESTAMPTZ,
  registry_version INT NOT NULL CHECK (registry_version >= 1),
  registry_sha256 TEXT NOT NULL CHECK (registry_sha256 ~ '^[0-9a-f]{64}$'),
  detector_version INT NOT NULL CHECK (detector_version >= 1),
  entry_count INT NOT NULL CHECK (entry_count >= 0),
  impacted_ticker_count INT NOT NULL CHECK (impacted_ticker_count >= 0),
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- RESTATE plani bilgi kesimi TASIMAK ZORUNDA; PIT plani tasimaz.
  CHECK (knowledge_basis <> 'CURRENT_KNOWLEDGE_RESTATE'
         OR knowledge_cutoff_at IS NOT NULL),
  CHECK (knowledge_cutoff_at IS NULL OR analysis_at IS NULL
         OR knowledge_cutoff_at >= analysis_at)
);

CREATE TABLE IF NOT EXISTS analytics.impact_plan_entry (
  impact_plan_id TEXT NOT NULL REFERENCES analytics.impact_plan (impact_plan_id),
  entry_seq INT NOT NULL CHECK (entry_seq >= 0),
  direct_ticker TEXT NOT NULL CHECK (direct_ticker = upper(direct_ticker)),
  impacted_ticker TEXT NOT NULL CHECK (impacted_ticker = upper(impacted_ticker)),
  impact_type TEXT NOT NULL
    CHECK (impact_type IN ('DIRECT','PEER_PROPAGATED','MODULE_DEPENDENCY')),
  engine_family TEXT NOT NULL,
  module TEXT NOT NULL
    CHECK (module IN ('M2','M1','M3','Ek1','Ek4','Ek9','GOOD_COUNT')),
  dependency_edge_id TEXT NOT NULL CHECK (dependency_edge_id ~ '^[0-9a-f]{64}$'),
  dependency_group_key TEXT,
  reason_code TEXT NOT NULL,
  -- GERCEKLESEN etkiler. Registry'nin POTANSIYEL failure_mode'undan ayridir.
  actual_effects TEXT[] NOT NULL CHECK (cardinality(actual_effects) >= 1),
  effective_from TIMESTAMPTZ NOT NULL,
  affected_anchor_period_ends DATE[] NOT NULL
    CHECK (cardinality(affected_anchor_period_ends) >= 1),
  eligibility_scope TEXT,
  PRIMARY KEY (impact_plan_id, entry_seq),
  -- Degisen sirket KENDISINE peer etkisi alamaz: cift sayim olurdu.
  CHECK (impact_type <> 'PEER_PROPAGATED' OR impacted_ticker <> direct_ticker)
);

CREATE INDEX IF NOT EXISTS idx_impact_plan_entry_ticker
  ON analytics.impact_plan_entry (impacted_ticker);

CREATE INDEX IF NOT EXISTS idx_impact_plan_source
  ON analytics.impact_plan (source_version_id, changed_period_end);

-- APPEND-ONLY uygulama kosulari. Bir plan birden fazla kez uygulanabilir;
-- bu denemeler plan satirini KIRLETMEZ.
CREATE TABLE IF NOT EXISTS analytics.impact_application_run (
  application_run_id TEXT PRIMARY KEY
    CHECK (btrim(application_run_id) <> '' AND length(application_run_id) <= 128),
  impact_plan_id TEXT NOT NULL REFERENCES analytics.impact_plan (impact_plan_id),
  attempt_no INT NOT NULL CHECK (attempt_no >= 1),
  orchestrator_run_id TEXT,
  analysis_at TIMESTAMPTZ,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL
    CHECK (status IN ('PENDING','APPLIED','FAILED','SKIPPED')),
  targeted_ticker_count INT NOT NULL CHECK (targeted_ticker_count >= 0),
  error_type TEXT,
  error_message TEXT CHECK (error_message IS NULL OR length(error_message) <= 500),
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (impact_plan_id, attempt_no),
  CHECK (status <> 'FAILED' OR error_type IS NOT NULL),
  CHECK (status = 'PENDING' OR finished_at IS NOT NULL),
  CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE INDEX IF NOT EXISTS idx_impact_application_plan
  ON analytics.impact_application_run (impact_plan_id, attempt_no DESC);

-- ---------------------------------------------------------------- IMMUTABLE
-- Plan tanimi TARIHSEL KANITTIR. UPDATE edilirse, gecmis bir orkestratör
-- kosusunun hangi gerekceyle tetiklendigi sonradan aciklanamaz hale gelir.
CREATE OR REPLACE FUNCTION analytics.impact_plan_immutable()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION
    'analytics.% degistirilemez (immutable impact plan): % denendi',
    TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_impact_plan_immutable ON analytics.impact_plan;
CREATE TRIGGER trg_impact_plan_immutable
  BEFORE UPDATE OR DELETE ON analytics.impact_plan
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

DROP TRIGGER IF EXISTS trg_impact_plan_entry_immutable ON analytics.impact_plan_entry;
CREATE TRIGGER trg_impact_plan_entry_immutable
  BEFORE UPDATE OR DELETE ON analytics.impact_plan_entry
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

-- Uygulama kosulari APPEND-ONLY: UPDATE serbest degil, ama DELETE yasak.
-- (Kosu ilerledikce status/finished_at guncellenebilmeli.)
DROP TRIGGER IF EXISTS trg_impact_application_no_delete ON analytics.impact_application_run;
CREATE TRIGGER trg_impact_application_no_delete
  BEFORE DELETE ON analytics.impact_application_run
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

COMMENT ON TABLE analytics.impact_plan IS
  'Hangi hesaplarin etkilenmesi gerektiginin DEGISMEZ tanimi. Ayni kimlik + '
  'ayni plan_sha256 idempotent kabul; farkli icerik SERT HATA.';

COMMENT ON TABLE analytics.impact_application_run IS
  'Planin uygulama denemeleri. Append-only; plan satirini kirletmez.';
