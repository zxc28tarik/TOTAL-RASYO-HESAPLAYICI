-- ============================================================================
-- V21 RECONCILIATION-1 — Impact Plan ↔ Orchestrator Actual Set (report-only).
--
-- KATMANLAMA (bilerek dar tutuldu):
--   V21 (bu dosya) : (b) beklenen etki kumesi <-> gercekten yeniden hesaplanan
--                     kume. En kritik operasyonel reconciliation.
--   V22 (sonra)     : (c) modul hatti <-> Total Rasyo tazelik tutarliligi.
--   V23 (sonra)     : (a) PIT <-> CURRENT_KNOWLEDGE_RESTATE. Bilgi-zamani/
--                     audit dogrulugu; operasyonel zincirin ilk guvenlik
--                     kapisi OLMAMALI, bu yuzden en sona birakildi.
--
-- REPORT-ONLY: bu surum yalniz BULGU URETIR. Otomatik duzeltici kosu
--   BASLATMAZ (detect -> repair YOK). Reconciliation mekanizmasinin
--   kendisini reconciliation edecek bir guven katmani henuz yok; ilk
--   turlarda yanlis pozitifleri gormeden kendi kendine yeniden hesaplama
--   baslatmak YENI BIR HATA SINIFI yaratirdi.
--
-- BOSLUK: impact_application_run.targeted_ticker_count yalniz bir SAYI
--   tutuyor; evaluate_readiness() sonucu (HANGI ticker'lar hedeflenmesi
--   GEREKTIGI) hicbir yerde kalici degildi. Bu migration onu ACIKCA
--   kalicilastirir -- reconciliation'in "beklenen kume" tarafi bunsuz
--   kanitlanamaz. V20 tablolarina DOKUNULMADI; yalniz yeni tablo eklendi.
-- ============================================================================

-- Bir uygulama denemesinin HEDEFLEDIGI ticker kumesi. Append-only: bu bir
-- karar KAYDIDIR, geriye donuk degistirilemez.
CREATE TABLE IF NOT EXISTS analytics.impact_application_target (
  application_run_id TEXT NOT NULL
    REFERENCES analytics.impact_application_run (application_run_id),
  ticker TEXT NOT NULL CHECK (btrim(ticker) <> '' AND ticker = upper(ticker)),
  readiness_status TEXT NOT NULL CHECK (readiness_status IN ('READY')),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (application_run_id, ticker)
);

-- ---------------------------------------------------------------- RECONCILE
CREATE TABLE IF NOT EXISTS analytics.reconciliation_run (
  reconciliation_run_id TEXT PRIMARY KEY
    CHECK (reconciliation_run_id ~ '^[0-9a-f]{64}$'),
  reconciliation_sha256 TEXT NOT NULL CHECK (reconciliation_sha256 ~ '^[0-9a-f]{64}$'),
  -- V21'de tek tur var; alan ileride V22/V23 icin ayrik listeye genisler.
  reconciliation_type TEXT NOT NULL
    CHECK (reconciliation_type IN ('IMPACT_PLAN_VS_ORCHESTRATOR_ACTUAL')),
  reconciler_version INT NOT NULL CHECK (reconciler_version >= 1),
  application_run_id TEXT NOT NULL
    REFERENCES analytics.impact_application_run (application_run_id),
  impact_plan_id TEXT NOT NULL REFERENCES analytics.impact_plan (impact_plan_id),
  analysis_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PASS','MISMATCH','INCOMPLETE','ERROR')),
  expected_count INT NOT NULL CHECK (expected_count >= 0),
  actual_count INT NOT NULL CHECK (actual_count >= 0),
  missing_count INT NOT NULL CHECK (missing_count >= 0),
  unexpected_count INT NOT NULL CHECK (unexpected_count >= 0),
  stale_count INT NOT NULL CHECK (stale_count >= 0),
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (finished_at >= started_at),
  -- PASS ANCAK uc kume de bossa olabilir. Sessizce PASS denip altinda
  -- bulgu birakmak, reconciliation'in tum amacini yok ederdi.
  CHECK (status <> 'PASS' OR (missing_count = 0 AND unexpected_count = 0
                              AND stale_count = 0)),
  CHECK (status <> 'MISMATCH' OR (missing_count > 0 OR unexpected_count > 0
                                  OR stale_count > 0))
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_run_application
  ON analytics.reconciliation_run (application_run_id);

CREATE TABLE IF NOT EXISTS analytics.reconciliation_finding (
  reconciliation_run_id TEXT NOT NULL
    REFERENCES analytics.reconciliation_run (reconciliation_run_id),
  finding_seq INT NOT NULL CHECK (finding_seq >= 0),
  ticker TEXT NOT NULL CHECK (ticker = upper(ticker)),
  finding_type TEXT NOT NULL CHECK (finding_type IN ('MISSING','UNEXPECTED','STALE')),
  detail TEXT CHECK (detail IS NULL OR length(detail) <= 500),
  PRIMARY KEY (reconciliation_run_id, finding_seq)
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_finding_ticker
  ON analytics.reconciliation_finding (ticker, finding_type);

-- ---------------------------------------------------------------- IMMUTABLE
-- Reconciliation kaydi TARIHSEL KANITTIR (V20 ilkesinin dogal uzantisi).
-- UPDATE edilirse "o an ne bulunmustu" sorusu sonradan aciklanamaz olur.
DROP TRIGGER IF EXISTS trg_impact_application_target_immutable
  ON analytics.impact_application_target;
CREATE TRIGGER trg_impact_application_target_immutable
  BEFORE UPDATE OR DELETE ON analytics.impact_application_target
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

DROP TRIGGER IF EXISTS trg_reconciliation_run_immutable
  ON analytics.reconciliation_run;
CREATE TRIGGER trg_reconciliation_run_immutable
  BEFORE UPDATE OR DELETE ON analytics.reconciliation_run
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

DROP TRIGGER IF EXISTS trg_reconciliation_finding_immutable
  ON analytics.reconciliation_finding;
CREATE TRIGGER trg_reconciliation_finding_immutable
  BEFORE UPDATE OR DELETE ON analytics.reconciliation_finding
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

-- ---------------------------------------------------------------- SAHIPLIK
ALTER TABLE analytics.impact_application_target OWNER TO total_rasyo_migration;
ALTER TABLE analytics.reconciliation_run OWNER TO total_rasyo_migration;
ALTER TABLE analytics.reconciliation_finding OWNER TO total_rasyo_migration;

REVOKE ALL ON analytics.impact_application_target FROM total_rasyo_runtime;
REVOKE ALL ON analytics.reconciliation_run FROM total_rasyo_runtime;
REVOKE ALL ON analytics.reconciliation_finding FROM total_rasyo_runtime;

GRANT SELECT, INSERT ON analytics.impact_application_target TO total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.reconciliation_run TO total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.reconciliation_finding TO total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.impact_application_target FROM total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.reconciliation_run FROM total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.reconciliation_finding FROM total_rasyo_runtime;

GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
  ON analytics.impact_application_target, analytics.reconciliation_run,
     analytics.reconciliation_finding
  TO total_rasyo_test;

COMMENT ON TABLE analytics.impact_application_target IS
  'Bir uygulama denemesinin HEDEFLEDIGI ticker kumesi (readiness bariyerini '
  'gecmis). Reconciliation''in "beklenen kume" kaynagi. Append-only.';

COMMENT ON TABLE analytics.reconciliation_run IS
  'V21 Reconciliation-1: impact plan hedefi ile orkestratör gercek '
  'kalicilastirdigi kume arasindaki fark. REPORT-ONLY -- otomatik duzeltici '
  'kosu BASLATMAZ.';
