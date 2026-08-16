-- ============================================================================
-- Total Rasyo — KOSU DURUMU ve KOSU KAPSAMI sozlesmesi.
--
-- SORUN 1: `FAILED` iki farkli olayi ortuyordu:
--   (a) orkestrasyon/motor calismasi KULLANILAMAZ  -> gercek basarisizlik
--   (b) motorlar saglikli calisti ama hicbir sirket skor alamadi (hepsi
--       YETERSIZ_VERI) -> calisma teknik olarak TAMAMLANDI, veri yetersiz
-- Ikisi ayni statuye konursa "sistem bozuk" ile "veri yok" ayirt edilemez
-- ve operator yanlis yere bakar.
--
--   COMPLETE            -> motorlar saglikli, en az bir sirket skor aldi
--   COMPLETE_NO_RESULTS -> motorlar saglikli, hicbir sirket skor almadi
--   PARTIAL             -> bazi motorlar coktu/atlandi ama digerleri tamam
--   FAILED              -> hicbir motor kullanilabilir durumda degil,
--                          veya kalicilik basarisiz
--
-- SORUN 2: "evren" ile "bu kosunun HEDEFLEDIGI sirketler" ayni sey degildir.
--   Gunluk tam kosuda ikisi cakisir. Change-impact kosusunda ise yalniz
--   birkac sirket hedeflenir; o zaman otoritatif silme YALNIZ hedeflenen
--   kumeyi kapsamalidir. Ikisini yapistirmak, kismi kosunun butun kesimi
--   yeniden yazmasina yol acardi.
-- ============================================================================

ALTER TABLE analytics.total_rasyo_run
  DROP CONSTRAINT IF EXISTS total_rasyo_run_overall_status_check;

ALTER TABLE analytics.total_rasyo_run
  ADD CONSTRAINT total_rasyo_run_overall_status_check
  CHECK (overall_status IN ('COMPLETE','COMPLETE_NO_RESULTS','PARTIAL','FAILED',
                            'OK','KISMI','BASARISIZ'));

-- Kosu kapsami: tam evren mi, hedefli alt kume mi.
ALTER TABLE analytics.total_rasyo_run
  ADD COLUMN IF NOT EXISTS run_scope TEXT;

DO $$
BEGIN
  ALTER TABLE analytics.total_rasyo_run
    ADD CONSTRAINT total_rasyo_run_scope_check
    CHECK (run_scope IS NULL OR run_scope IN ('FULL_UNIVERSE','TARGETED'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Evrende KAC sirket vardi ve bu kosu KACINI hedefledi. Hedeflenen sayi
-- company_count ile ayni olmalidir; evren daha buyuk olabilir.
ALTER TABLE analytics.total_rasyo_run
  ADD COLUMN IF NOT EXISTS universe_company_count INT;

DO $$
BEGIN
  ALTER TABLE analytics.total_rasyo_run
    ADD CONSTRAINT total_rasyo_run_universe_check
    CHECK (universe_company_count IS NULL
           OR universe_company_count >= company_count);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Calistirilmayan motorun eski sonucuna ne yapildigi KAYDA GECER.
-- Sozlesmenin sessiz kalmasi, bayat skorun guncel sanilmasina yol acar.
ALTER TABLE analytics.total_rasyo_run
  ADD COLUMN IF NOT EXISTS not_run_policy TEXT;

DO $$
BEGIN
  ALTER TABLE analytics.total_rasyo_run
    ADD CONSTRAINT total_rasyo_run_not_run_policy_check
    CHECK (not_run_policy IS NULL
           OR not_run_policy IN ('OVERWRITE','PRESERVE'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
