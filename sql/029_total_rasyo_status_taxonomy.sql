-- ============================================================================
-- Total Rasyo orkestratoru — DURUM AYRISTIRMASI.
--
-- SORUN: `YETERSIZ_VERI` tek basina UC farkli olayi ortuyordu:
--   M2_YOK           -> sektor motoru bu sirket icin M2 uretmedi
--   MODUL_SATIRI_YOK -> analytics.module_scores'ta uygun satir HIC yok
--   EKSIK_BILESEN    -> satir var ama bazi moduller NULL
-- Ucu ayni kefeye konursa "motor bu sirketi reddetti" ile "sirketin gecmis
-- modul verisi hic yok" ayirt edilemez ve teshis imkansizlasir.
--
-- `total_rasyo_status` KABA sonuc olarak kalir (sayaclarin ayrikligi buna
-- dayanir); ayrinti `insufficiency_reason` sutununda tasinir.
--
-- overall_status: OK/KISMI/BASARISIZ yerine deterministik COMPLETE/PARTIAL/
-- FAILED sozlesmesi. Eski degerler de kabul edilir; uygulanmis kurulumlarda
-- yazilmis satirlar gecersiz hale GELMEMELIDIR.
-- ============================================================================

ALTER TABLE analytics.company_total_rasyo_result
  ADD COLUMN IF NOT EXISTS insufficiency_reason TEXT;

DO $$
BEGIN
  ALTER TABLE analytics.company_total_rasyo_result
    ADD CONSTRAINT company_total_rasyo_insufficiency_reason_check
    CHECK (insufficiency_reason IS NULL OR insufficiency_reason IN
      ('M2_YOK','MODUL_SATIRI_YOK','EKSIK_BILESEN','MOTOR_REDDETTI',
       'DEGERLEME_KULLANILAMAZ','HESAP_HATASI'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Ayrinti YALNIZ yetersiz veri durumunda anlamlidir. Basarili satirda
-- yetersizlik nedeni bulunmasi kendi icinde celiskidir.
DO $$
BEGIN
  ALTER TABLE analytics.company_total_rasyo_result
    ADD CONSTRAINT company_total_rasyo_insufficiency_scope_check
    CHECK (insufficiency_reason IS NULL OR total_rasyo_status = 'YETERSIZ_VERI');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Yetersiz veri satiri neden TASIMALIDIR; 'yetersiz' demek yetmez.
DO $$
BEGIN
  ALTER TABLE analytics.company_total_rasyo_result
    ADD CONSTRAINT company_total_rasyo_insufficiency_required_check
    CHECK (total_rasyo_status <> 'YETERSIZ_VERI' OR insufficiency_reason IS NOT NULL);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_company_total_rasyo_insufficiency
  ON analytics.company_total_rasyo_result (analysis_at, insufficiency_reason)
  WHERE insufficiency_reason IS NOT NULL;

ALTER TABLE analytics.total_rasyo_run
  DROP CONSTRAINT IF EXISTS total_rasyo_run_overall_status_check;

ALTER TABLE analytics.total_rasyo_run
  ADD CONSTRAINT total_rasyo_run_overall_status_check
  CHECK (overall_status IN ('COMPLETE','PARTIAL','FAILED','OK','KISMI','BASARISIZ'));

-- Kalicilik hatasi ayri bir sinif olarak izlenir.
ALTER TABLE analytics.total_rasyo_run
  ADD COLUMN IF NOT EXISTS persistence_status TEXT;

DO $$
BEGIN
  ALTER TABLE analytics.total_rasyo_run
    ADD CONSTRAINT total_rasyo_run_persistence_status_check
    CHECK (persistence_status IS NULL OR persistence_status IN ('OK','KALICILIK_HATASI'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
