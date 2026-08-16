-- ============================================================================
-- V23-B — PIT <-> RESTATE reconciliation.
--
-- REPORT-ONLY. Otomatik duzeltici kosu BASLATMAZ.
--
-- KRITIK SOZLESME: hicbir ticker GERCEKTEN karsilastirilamadiysa (compared_
-- count=0) status PASS OLAMAZ, mismatch_count=0 olsa bile. Bu, mevcut V23-A
-- gercekliginde (M2 nedeniyle her RESTATE sonucu YETERSIZ_VERI) status'un
-- INCOMPLETE olmasini GARANTI eder -- "kanit yok ama PASS" sinifi
-- (V21/V22-B'de kapatilan hata) burada YENIDEN ACILMAZ.
--
-- mevcut restate_vs_pit_comparison view'i (sql/031) HUKUM KAYNAGI
-- YAPILMAZ: LEFT JOIN + IS DISTINCT FROM, RESTATE her zaman eksikken
-- decision_changed=TRUE dondurur (sahte fark). Bu migration o view'e
-- DOKUNMAZ; reconciliation ALTTAKI iki tabloyu (company_total_rasyo_result,
-- company_total_rasyo_restate_result) DOGRUDAN sorgular.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analytics.reconciliation_restate_run (
  reconciliation_run_id TEXT PRIMARY KEY
    CHECK (reconciliation_run_id ~ '^[0-9a-f]{64}$'),
  reconciliation_sha256 TEXT NOT NULL CHECK (reconciliation_sha256 ~ '^[0-9a-f]{64}$'),
  reconciliation_type TEXT NOT NULL CHECK (reconciliation_type IN ('PIT_VS_RESTATE')),
  reconciler_version INT NOT NULL CHECK (reconciler_version >= 1),
  restate_run_id TEXT NOT NULL
    REFERENCES analytics.total_rasyo_restate_runs (restate_run_id),
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PASS','MISMATCH','INCOMPLETE','ERROR')),
  fully_verified BOOLEAN NOT NULL,
  ticker_count INT NOT NULL CHECK (ticker_count >= 0),
  compared_count INT NOT NULL CHECK (compared_count >= 0),
  mismatch_count INT NOT NULL CHECK (mismatch_count >= 0),
  pit_missing_count INT NOT NULL CHECK (pit_missing_count >= 0),
  restate_incomplete_count INT NOT NULL CHECK (restate_incomplete_count >= 0),
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (finished_at >= started_at),
  CHECK (compared_count <= ticker_count),
  -- KRITIK SOZLESME veritabani duzeyinde de KORUNUR: compared_count=0 iken
  -- status PASS/MISMATCH OLAMAZ.
  CHECK (compared_count > 0 OR status IN ('INCOMPLETE','ERROR')),
  CHECK (status <> 'PASS' OR (compared_count > 0 AND mismatch_count = 0)),
  CHECK (status <> 'MISMATCH' OR (compared_count > 0 AND mismatch_count > 0)),
  -- fully_verified=true ANCAK status=PASS VE her ticker karsilastirilmissa.
  CHECK (NOT fully_verified OR (status = 'PASS' AND compared_count = ticker_count))
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_restate_run_restate
  ON analytics.reconciliation_restate_run (restate_run_id);

CREATE TABLE IF NOT EXISTS analytics.reconciliation_restate_finding (
  reconciliation_run_id TEXT NOT NULL
    REFERENCES analytics.reconciliation_restate_run (reconciliation_run_id),
  ticker TEXT NOT NULL CHECK (btrim(ticker) <> '' AND ticker = upper(ticker)),
  compared BOOLEAN NOT NULL,
  finding_type TEXT NOT NULL
    CHECK (finding_type IN ('PIT_MISSING','RESTATE_INCOMPLETE',
                            'VALUE_CHANGED','DECISION_CHANGED')),
  pit_run_id TEXT,
  pit_final_score NUMERIC,
  restate_final_score NUMERIC,
  pit_decision TEXT,
  restate_decision TEXT,
  restate_status TEXT,
  PRIMARY KEY (reconciliation_run_id, ticker, finding_type),
  -- Bu tabloda YALNIZ GERCEK bulgu satirlari bulunur (V21 deseniyle AYNI --
  -- "temiz" karsilastirilmis bir ticker HIC SATIR almaz). Karsilastirilmamis
  -- bir ticker'in bulgusu YALNIZ PIT_MISSING/RESTATE_INCOMPLETE olabilir;
  -- karsilastirilmis bir ticker'in bulgusu YALNIZ VALUE_CHANGED/
  -- DECISION_CHANGED olabilir.
  CHECK (compared OR finding_type IN ('PIT_MISSING','RESTATE_INCOMPLETE')),
  CHECK (NOT compared OR finding_type IN ('VALUE_CHANGED','DECISION_CHANGED'))
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_restate_finding_type
  ON analytics.reconciliation_restate_finding (finding_type);

DROP TRIGGER IF EXISTS trg_reconciliation_restate_run_immutable
  ON analytics.reconciliation_restate_run;
CREATE TRIGGER trg_reconciliation_restate_run_immutable
  BEFORE UPDATE OR DELETE ON analytics.reconciliation_restate_run
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

DROP TRIGGER IF EXISTS trg_reconciliation_restate_finding_immutable
  ON analytics.reconciliation_restate_finding;
CREATE TRIGGER trg_reconciliation_restate_finding_immutable
  BEFORE UPDATE OR DELETE ON analytics.reconciliation_restate_finding
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

ALTER TABLE analytics.reconciliation_restate_run OWNER TO total_rasyo_migration;
ALTER TABLE analytics.reconciliation_restate_finding OWNER TO total_rasyo_migration;
REVOKE ALL ON analytics.reconciliation_restate_run FROM total_rasyo_runtime;
REVOKE ALL ON analytics.reconciliation_restate_finding FROM total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.reconciliation_restate_run TO total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.reconciliation_restate_finding TO total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.reconciliation_restate_run FROM total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.reconciliation_restate_finding FROM total_rasyo_runtime;

GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
  ON analytics.reconciliation_restate_run, analytics.reconciliation_restate_finding
  TO total_rasyo_test;

COMMENT ON TABLE analytics.reconciliation_restate_run IS
  'V23-B: PIT <-> RESTATE reconciliation. REPORT-ONLY. compared_count=0 '
  'iken status ASLA PASS/MISMATCH olamaz -- yalniz INCOMPLETE/ERROR.';

COMMENT ON TABLE analytics.reconciliation_restate_finding IS
  'Ticker-bazinda bulgu. PIT_MISSING/RESTATE_INCOMPLETE ile VALUE_CHANGED/'
  'DECISION_CHANGED birbirinden AYRIKTIR; M2 nedenli INCOMPLETE bir RESTATE '
  'asla sahte VALUE/DECISION_CHANGED uretmez.';
