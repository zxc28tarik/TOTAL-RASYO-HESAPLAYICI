-- ============================================================================
-- V22-B — Total Rasyo <-> modul hatti tazelik/lineage reconciliation.
--
-- REPORT-ONLY. Otomatik duzeltici kosu BASLATMAZ.
--
-- HALEF KURALI (kod yazilmadan ONCE kilitlendi):
--
--   TOTAL_STALE (M1,M3,Ek1,Ek4,Ek9):
--     module_production_lineage'de
--       R.ticker=tuketilen.ticker AND R.module=tuketilen.module
--       AND R.analysis_at <= total_rasyo.analysis_at   -- ASLA look-ahead DEGIL
--       AND R.analysis_at >  tuketilen.module_analysis_at
--     bir satir VARSA.
--
--   TOTAL_STALE (M2, ZAYIF PROXY -- acikca belgelenir):
--     M2 icin ayri bir producer-lineage tablosu YOK (V22-A bulgusu: sektor
--     motorlerinin kendi uretim kimligini tutan bir tablo yok). Proxy:
--     company_total_rasyo_result'in AYNI (ticker, analysis_at) icin GUNCEL
--     kanonik satirindaki m2_source_at, tuketilen module_source_at'tan
--     YENIYSE. Bu yalniz "daha SONRAKI bir resmi Total Rasyo kosusu M2'yi
--     tazeledi mi" sorusunu cevaplar; HAM sektor motoru verisinin tazeligini
--     DEGIL.
--
--   MODULE_LINEAGE_STALE (yalniz identity_known=true oldugunda):
--     module_production_lineage'de AYNI (ticker, module,
--     analysis_at=tuketilen.module_analysis_at) ETIKETI icin GUNCEL
--     source_version_id, tuketilen module_source_run_key'den FARKLIYSA.
--     M2 icin identity_known HER ZAMAN false'tur (V22-A); bu yuzden M2
--     lineage kontrolu HICBIR ZAMAN yapilmaz.
--
-- "lineage satiri var" != "source identity biliniyor" (V22-A dersi):
-- identity_known=false olan bir modul icin MODULE_LINEAGE_STALE HUKUM
-- VERMEZ -- ne temiz ne stale sayilir; lineage_performed=false ile
-- KONTROLUN UYGULANAMADIGI acikca gorunur.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analytics.reconciliation_module_run (
  reconciliation_run_id TEXT PRIMARY KEY
    CHECK (reconciliation_run_id ~ '^[0-9a-f]{64}$'),
  reconciliation_sha256 TEXT NOT NULL CHECK (reconciliation_sha256 ~ '^[0-9a-f]{64}$'),
  reconciliation_type TEXT NOT NULL
    CHECK (reconciliation_type IN ('TOTAL_RASYO_MODULE_FRESHNESS')),
  reconciler_version INT NOT NULL CHECK (reconciler_version >= 1),
  total_rasyo_run_id TEXT NOT NULL
    REFERENCES analytics.total_rasyo_run (run_id),
  ticker TEXT NOT NULL CHECK (btrim(ticker) <> '' AND ticker = upper(ticker)),
  analysis_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PASS','MISMATCH','INCOMPLETE','ERROR')),
  -- fully_verified, status'tan BAGIMSIZDIR: "sorun bulundu mu" degil
  -- "beklenen butun kontroller GERCEKTEN yapildi mi" sorusuna cevaptir.
  -- INCOMPLETE/ERROR'da anlamsiz oldugu icin NULL kalir.
  fully_verified BOOLEAN,
  expected_module_count INT NOT NULL CHECK (expected_module_count >= 0),
  missing_count INT NOT NULL CHECK (missing_count >= 0),
  total_stale_count INT NOT NULL CHECK (total_stale_count >= 0),
  lineage_stale_count INT NOT NULL CHECK (lineage_stale_count >= 0),
  freshness_performed_count INT NOT NULL CHECK (freshness_performed_count >= 0),
  lineage_performed_count INT NOT NULL CHECK (lineage_performed_count >= 0),
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (finished_at >= started_at),
  CHECK (status <> 'PASS' OR (missing_count = 0 AND total_stale_count = 0
                              AND lineage_stale_count = 0)),
  CHECK (status <> 'MISMATCH' OR (missing_count > 0 OR total_stale_count > 0
                                  OR lineage_stale_count > 0)),
  CHECK (status IN ('PASS','MISMATCH') OR fully_verified IS NULL),
  CHECK (status NOT IN ('PASS','MISMATCH') OR fully_verified IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_module_run_total_rasyo
  ON analytics.reconciliation_module_run (total_rasyo_run_id);

CREATE TABLE IF NOT EXISTS analytics.reconciliation_module_check (
  reconciliation_run_id TEXT NOT NULL
    REFERENCES analytics.reconciliation_module_run (reconciliation_run_id),
  module TEXT NOT NULL CHECK (module IN ('M1','M2','M3','Ek1','Ek4','Ek9')),
  module_missing BOOLEAN NOT NULL,
  freshness_performed BOOLEAN NOT NULL,
  freshness_reason TEXT,
  total_stale BOOLEAN,
  lineage_performed BOOLEAN NOT NULL,
  lineage_reason TEXT,
  lineage_stale BOOLEAN,
  PRIMARY KEY (reconciliation_run_id, module),
  CHECK (freshness_performed OR total_stale IS NULL),
  CHECK (NOT freshness_performed OR total_stale IS NOT NULL),
  CHECK (lineage_performed OR lineage_stale IS NULL),
  CHECK (NOT lineage_performed OR lineage_stale IS NOT NULL),
  CHECK (NOT module_missing OR NOT freshness_performed),
  CHECK (NOT module_missing OR NOT lineage_performed),
  CHECK (module <> 'M2' OR NOT lineage_performed)
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_module_check_module
  ON analytics.reconciliation_module_check (module, lineage_stale)
  WHERE lineage_stale IS TRUE;

DROP TRIGGER IF EXISTS trg_reconciliation_module_run_immutable
  ON analytics.reconciliation_module_run;
CREATE TRIGGER trg_reconciliation_module_run_immutable
  BEFORE UPDATE OR DELETE ON analytics.reconciliation_module_run
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

DROP TRIGGER IF EXISTS trg_reconciliation_module_check_immutable
  ON analytics.reconciliation_module_check;
CREATE TRIGGER trg_reconciliation_module_check_immutable
  BEFORE UPDATE OR DELETE ON analytics.reconciliation_module_check
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

ALTER TABLE analytics.reconciliation_module_run OWNER TO total_rasyo_migration;
ALTER TABLE analytics.reconciliation_module_check OWNER TO total_rasyo_migration;
REVOKE ALL ON analytics.reconciliation_module_run FROM total_rasyo_runtime;
REVOKE ALL ON analytics.reconciliation_module_check FROM total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.reconciliation_module_run TO total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.reconciliation_module_check TO total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.reconciliation_module_run FROM total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.reconciliation_module_check FROM total_rasyo_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
  ON analytics.reconciliation_module_run, analytics.reconciliation_module_check
  TO total_rasyo_test;

COMMENT ON TABLE analytics.reconciliation_module_run IS
  'V22-B: Total Rasyo tuketim snapshotu ile modul hattinin tazeligi/lineage''i '
  'arasindaki fark. REPORT-ONLY. fully_verified, status''tan bagimsizdir.';

COMMENT ON TABLE analytics.reconciliation_module_check IS
  'Modul-bazinda kanit kapsami. performed=false ise sonuc alani NULL kalir; '
  '"kontrol edilmedi" ile "kontrol edildi ve temiz" hicbir zaman ayni deger '
  'ile karistirilmaz.';
