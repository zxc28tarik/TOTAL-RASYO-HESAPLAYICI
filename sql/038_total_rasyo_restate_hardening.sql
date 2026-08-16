-- ============================================================================
-- V23-A — RESTATE URETIM TEMELI: sertlestirme + tuketim-ani snapshot.
--
-- sql/031'i DEGISTIRMEZ; mevcut tablolara EKLEME/ALTER yapar.
--
-- KIMLIK/ICERIK AYRIMI: restate_run_id KIMLIK, inputs_sha256+results_sha256
-- ICERIK. Kimlik yalniz sonucu etkileyen girdilerden (restate_contract_
-- version + reader_version -- detector_version/registry_sha256 DEGIL,
-- RESTATE uretiminde change-impact detector'i kullanilmiyor) turer. Bu
-- ikisi bu yuzden NULLABLE'a cevrilir (yalniz impact-tetiklemesi
-- baglaminda opsiyonel provenance).
--
-- TUKETIM-ANI SNAPSHOT (V22-A'nin RESTATE eshdegeri): M2 icin
-- identity_known HER ZAMAN false.
--
-- IMMUTABLE + ROL AYRIMI: RESTATE'in uretim yolu ILK KEZ kuruluyor.
-- ============================================================================

ALTER TABLE analytics.total_rasyo_restate_runs
  ALTER COLUMN registry_version DROP NOT NULL,
  ALTER COLUMN registry_sha256 DROP NOT NULL,
  ALTER COLUMN detector_version DROP NOT NULL;

ALTER TABLE analytics.total_rasyo_restate_runs
  ADD COLUMN IF NOT EXISTS restate_contract_version INT,
  ADD COLUMN IF NOT EXISTS reader_version INT,
  ADD COLUMN IF NOT EXISTS inputs_sha256 TEXT,
  ADD COLUMN IF NOT EXISTS results_sha256 TEXT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_schema = 'analytics' AND table_name = 'total_rasyo_restate_runs'
      AND constraint_name = 'ck_restate_runs_contract_version'
  ) THEN
    ALTER TABLE analytics.total_rasyo_restate_runs
      ADD CONSTRAINT ck_restate_runs_contract_version
        CHECK (restate_contract_version IS NOT NULL AND restate_contract_version >= 1),
      ADD CONSTRAINT ck_restate_runs_reader_version
        CHECK (reader_version IS NOT NULL AND reader_version >= 1),
      ADD CONSTRAINT ck_restate_runs_inputs_sha
        CHECK (inputs_sha256 IS NOT NULL AND inputs_sha256 ~ '^[0-9a-f]{64}$'),
      ADD CONSTRAINT ck_restate_runs_results_sha
        CHECK (results_sha256 IS NOT NULL AND results_sha256 ~ '^[0-9a-f]{64}$');
  END IF;
END $$;

COMMENT ON COLUMN analytics.total_rasyo_restate_runs.restate_run_id IS
  'KIMLIK. Yalniz sonucu etkileyen girdilerden turer.';
COMMENT ON COLUMN analytics.total_rasyo_restate_runs.inputs_sha256 IS
  'ICERIK -- tuketilen total_rasyo_restate_module_input satirlarinin ozeti.';
COMMENT ON COLUMN analytics.total_rasyo_restate_runs.results_sha256 IS
  'ICERIK -- uretilen company_total_rasyo_restate_result satirlarinin ozeti.';
COMMENT ON COLUMN analytics.total_rasyo_restate_runs.registry_sha256 IS
  'OPSIYONEL provenance: yalniz impact-tetiklemeli restate icin anlamli. '
  'restate_run_id KIMLIGINE DAHIL DEGILDIR.';

CREATE TABLE IF NOT EXISTS analytics.total_rasyo_restate_module_input (
  restate_run_id TEXT NOT NULL
    REFERENCES analytics.total_rasyo_restate_runs (restate_run_id),
  ticker TEXT NOT NULL CHECK (btrim(ticker) <> '' AND ticker = upper(ticker)),
  module TEXT NOT NULL CHECK (module IN ('M1','M2','M3','Ek1','Ek4','Ek9')),
  module_score NUMERIC CHECK (module_score IS NULL OR module_score BETWEEN 0 AND 1),
  module_missing BOOLEAN NOT NULL,
  module_source_at TIMESTAMPTZ,
  module_source_run_key TEXT,
  identity_known BOOLEAN NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (restate_run_id, ticker, module),
  CHECK (NOT identity_known OR module_source_run_key IS NOT NULL),
  CHECK (module <> 'M2' OR NOT identity_known),
  CHECK (module <> 'M2' OR module_missing)
);

CREATE INDEX IF NOT EXISTS idx_restate_module_input_ticker
  ON analytics.total_rasyo_restate_module_input (ticker, module);

DROP TRIGGER IF EXISTS trg_total_rasyo_restate_runs_immutable
  ON analytics.total_rasyo_restate_runs;
CREATE TRIGGER trg_total_rasyo_restate_runs_immutable
  BEFORE UPDATE OR DELETE ON analytics.total_rasyo_restate_runs
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

DROP TRIGGER IF EXISTS trg_company_total_rasyo_restate_result_immutable
  ON analytics.company_total_rasyo_restate_result;
CREATE TRIGGER trg_company_total_rasyo_restate_result_immutable
  BEFORE UPDATE OR DELETE ON analytics.company_total_rasyo_restate_result
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

DROP TRIGGER IF EXISTS trg_total_rasyo_restate_module_input_immutable
  ON analytics.total_rasyo_restate_module_input;
CREATE TRIGGER trg_total_rasyo_restate_module_input_immutable
  BEFORE UPDATE OR DELETE ON analytics.total_rasyo_restate_module_input
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

ALTER TABLE analytics.total_rasyo_restate_runs OWNER TO total_rasyo_migration;
ALTER TABLE analytics.company_total_rasyo_restate_result OWNER TO total_rasyo_migration;
ALTER TABLE analytics.total_rasyo_restate_module_input OWNER TO total_rasyo_migration;

REVOKE ALL ON analytics.total_rasyo_restate_runs FROM total_rasyo_runtime;
REVOKE ALL ON analytics.company_total_rasyo_restate_result FROM total_rasyo_runtime;
REVOKE ALL ON analytics.total_rasyo_restate_module_input FROM total_rasyo_runtime;

GRANT SELECT, INSERT ON analytics.total_rasyo_restate_runs TO total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.company_total_rasyo_restate_result TO total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.total_rasyo_restate_module_input TO total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.total_rasyo_restate_runs FROM total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.company_total_rasyo_restate_result FROM total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.total_rasyo_restate_module_input FROM total_rasyo_runtime;

GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
  ON analytics.total_rasyo_restate_runs, analytics.company_total_rasyo_restate_result,
     analytics.total_rasyo_restate_module_input
  TO total_rasyo_test;

COMMENT ON TABLE analytics.total_rasyo_restate_module_input IS
  'V23-A: RESTATE hesaplanirken TUKETILEN modul girdilerinin tuketim-ani '
  'kaniti. M2 icin identity_known HER ZAMAN false.';
