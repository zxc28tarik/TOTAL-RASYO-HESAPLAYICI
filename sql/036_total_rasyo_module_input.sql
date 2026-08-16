-- ============================================================================
-- V22-A — TUKETIM-ANI LINEAGE SNAPSHOT.
--
-- SORU FARKI: "su anda hangi modul kaydi mevcut?" DEGIL,
--   "Bu Total Rasyo sonucu hesaplanirken TAM OLARAK hangi alti modul
--   kaydi TUKETILDI?"
--
-- Bunu tuketim aninda KAYDETMEZSEK, esZAMANLI veya hemen ardindan gelen
-- baska bir modul kosusu kaniti BULANIKLASTIRABILIR -- analytics.module_scores
-- PK'si (ticker, asof_date, horizon_days) UZERINE UPSERT edilir (yerinde
-- ezilir), TARIHSEL VERSIYON TUTMAZ. Bugunden geriye donuk "o an ne
-- kullanildi" sorusu module_scores'tan CEVAPLANAMAZ.
--
-- Lineage SONRADAN YENIDEN TURETILMEZ; TUKETIM ANINDA KANITLANIR.
--
-- KAPSAM SINIRI (bilerek dar): yalniz ALTI Total Rasyo modulu
-- (M1,M2,M3,Ek1,Ek4,Ek9). GOOD_COUNT/veto bu tabloya DAHIL DEGIL.
-- UNEXPECTED_MODULE V22-1'den CIKARILDI: mevcut sozlesmede beklenen kume
-- sabit {M1,M2,M3,Ek1,Ek4,Ek9}; uretim hatti zaten alti-modul
-- sozlesmesiyle calisiyor, simetri icin gercek sistemin URETEMEDIGI bir
-- hata sinifi eklenmedi.
--
-- M2 KIMLIK SINIRI: M2 module_scores'tan DEGIL sektor motorundan gelir
-- (cift sayim yasagi -- V19). Sektor motorlerinin kendi uretim kimligini
-- tutan bir tablo YOK. Bu yuzden M2 icin identity_known HER ZAMAN false
-- yazilir; deger + kaynak zamani kaydedilir ama gercek kimlik
-- KANITLANAMAZ. Bu sessizce gizlenmez.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analytics.total_rasyo_module_input (
  total_rasyo_run_id TEXT NOT NULL
    REFERENCES analytics.total_rasyo_run (run_id),
  ticker TEXT NOT NULL CHECK (btrim(ticker) <> '' AND ticker = upper(ticker)),
  module TEXT NOT NULL CHECK (module IN ('M1','M2','M3','Ek1','Ek4','Ek9')),
  module_score NUMERIC CHECK (module_score IS NULL OR module_score BETWEEN 0 AND 1),
  module_missing BOOLEAN NOT NULL,
  module_source_at TIMESTAMPTZ,
  -- Uretim tarafi kimligi. Yalniz BAGIMSIZ dogrulamayla (asagida) bulunursa
  -- doludur; TOCTOU riskini azaltmak icin module_source_at ile ESLESMESI
  -- ZORUNLUDUR -- eslesmezse identity_known=false, alanlar NULL kalir.
  module_analysis_at TIMESTAMPTZ,
  module_asof_date DATE,
  module_source_run_key TEXT,
  -- GORUNUR SOZLESME (V21 dersi): identity_known=false ise bu modul icin
  -- LINEAGE hicbir zaman kanitlanamaz; reconciliation bunu PASS altinda
  -- sessizce gizleyemez.
  identity_known BOOLEAN NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (total_rasyo_run_id, ticker, module),
  -- identity_known=true ise en az bir kimlik alani DOLU olmalidir; aksi
  -- halde "kimlik biliniyor" iddiasi icerik tasimazdi.
  CHECK (NOT identity_known OR module_analysis_at IS NOT NULL
         OR module_source_run_key IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_total_rasyo_module_input_ticker
  ON analytics.total_rasyo_module_input (ticker, module);

-- ---------------------------------------------------------------- IMMUTABLE
-- Tuketim-ani kaniti TARIHSEL KANITTIR (V20/V21 ilkesinin dogal uzantisi).
DROP TRIGGER IF EXISTS trg_total_rasyo_module_input_immutable
  ON analytics.total_rasyo_module_input;
CREATE TRIGGER trg_total_rasyo_module_input_immutable
  BEFORE UPDATE OR DELETE ON analytics.total_rasyo_module_input
  FOR EACH ROW EXECUTE FUNCTION analytics.impact_plan_immutable();

ALTER TABLE analytics.total_rasyo_module_input OWNER TO total_rasyo_migration;
REVOKE ALL ON analytics.total_rasyo_module_input FROM total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.total_rasyo_module_input TO total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.total_rasyo_module_input FROM total_rasyo_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
  ON analytics.total_rasyo_module_input TO total_rasyo_test;

COMMENT ON TABLE analytics.total_rasyo_module_input IS
  'V22-A: Total Rasyo hesaplanirken TUKETILEN alti modul girdisinin '
  'TUKETIM ANINDA snapshot''i. Sonradan yeniden turetilmez. identity_known '
  '=false ise o modul icin lineage KANITLANAMAZ (M2 icin daima false).';

-- ============================================================================
-- URETICI TARAFI: module_production_lineage'i GUNLUK PIPELINE'a da yazdir.
--
-- V20/V21'de bu tablo yalniz change-impact akisi tarafindan yaziliyordu;
-- SIRADAN gunluk kosularda BOS kaliyordu. Bu, V22-A'nin tuketim-ani
-- kanitini DOGRULAYACAK bir uretici kaydinin coğu zaman OLMAMASI demekti.
-- sql/034'teki tablo semasi degismiyor; yalniz YAZAN taraf genisliyor.
-- ============================================================================

COMMENT ON TABLE analytics.module_production_lineage IS
  'Modul uretim soy kutugu. "Satir var" ile "dogru girdiden tazelendi" ayni '
  'sey degildir; readiness bariyeri ikincisini bu tablodan kanitlar. '
  'V22''den itibaren gunluk pipeline (run_daily_pipeline.py) de M1/M3/Ek1/'
  'Ek4/Ek9 icin buraya yazar (M2 HARIC -- sektor motorunden gelir).';
