-- ============================================================================
-- URETIM ROL SOZLESMESI — migration rolu ile runtime rolu AYRI.
--
-- NEDEN: satir-seviyesi immutable trigger TEK BASINA guvenlik siniri
-- DEGILDIR. PostgreSQL'de TRUNCATE satir trigger'larini ATLAR ve immutable
-- korumasini asar. Tek savunma trigger olursa, tablo sahibi yetkisine sahip
-- herhangi bir uygulama hatasi plan gecmisini silebilir.
--
-- Bu yuzden koruma IKI KATMANLI:
--   1. trigger  -> UPDATE/DELETE'i satir duzeyinde reddeder
--   2. YETKI    -> runtime rolune UPDATE/DELETE/TRUNCATE HIC VERILMEZ
--
-- ROLLER:
--   total_rasyo_migration : tablo SAHIBI. Yalniz migration calistirir.
--   total_rasyo_runtime   : uygulama rolu. Owner DEGIL. Yalniz SELECT/INSERT.
--   total_rasyo_test      : fikstur temizligi icin. URETIM RUNTIME YETKISIYLE
--                           KARISTIRILMAZ; ayri roldur ve uretimde
--                           olusturulmamalidir.
--
-- Roller cluster genelindedir; bu dosya idempotenttir.
-- ============================================================================

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'total_rasyo_migration') THEN
    CREATE ROLE total_rasyo_migration NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'total_rasyo_runtime') THEN
    CREATE ROLE total_rasyo_runtime NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'total_rasyo_test') THEN
    CREATE ROLE total_rasyo_test NOLOGIN;
  END IF;
END $$;

-- Sema erisimi.
--
-- MIGRATION ROLU DE USAGE ALMALI: tablolarin SAHIBI odur ve yabanci anahtar
-- kontrolleri sahip rolu kimliginde calisir. USAGE verilmezse
-- impact_plan_entry -> impact_plan FK dogrulamasi "permission denied for
-- schema analytics" ile duser -- superuser baglantisinda bile, cunku kontrol
-- cagiran kimlikle degil SAHIP kimligiyle yurutulur. Bu, rol ayrimindan
-- sonra 19 testin kirilmasiyla ortaya cikti.
GRANT USAGE, CREATE ON SCHEMA analytics TO total_rasyo_migration;
GRANT USAGE, CREATE ON SCHEMA core TO total_rasyo_migration;

-- Runtime okuyabilmeli ama yeni nesne YARATAMAMALI.
GRANT USAGE ON SCHEMA analytics TO total_rasyo_runtime, total_rasyo_test;
GRANT USAGE ON SCHEMA core TO total_rasyo_runtime, total_rasyo_test;
REVOKE CREATE ON SCHEMA analytics FROM total_rasyo_runtime;
REVOKE CREATE ON SCHEMA core FROM total_rasyo_runtime;

-- Immutable plan tablolarinin SAHIPLIGI migration rolunde.
ALTER TABLE analytics.impact_plan OWNER TO total_rasyo_migration;
ALTER TABLE analytics.impact_plan_entry OWNER TO total_rasyo_migration;
ALTER TABLE analytics.impact_application_run OWNER TO total_rasyo_migration;

-- ---------------------------------------------------------------- RUNTIME
-- Once HER SEYI geri al, sonra yalniz ihtiyac duyulani ver.
REVOKE ALL ON analytics.impact_plan FROM total_rasyo_runtime;
REVOKE ALL ON analytics.impact_plan_entry FROM total_rasyo_runtime;
REVOKE ALL ON analytics.impact_application_run FROM total_rasyo_runtime;

-- Plan tanimi: OKU ve EKLE. Guncelleme/silme YOK -- immutable kanit.
GRANT SELECT, INSERT ON analytics.impact_plan TO total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.impact_plan_entry TO total_rasyo_runtime;

-- Uygulama kosulari: append-only, ama kosu ilerledikce status/finished_at
-- guncellenebilmeli. DELETE ve TRUNCATE yine YOK.
GRANT SELECT, INSERT, UPDATE ON analytics.impact_application_run
  TO total_rasyo_runtime;

-- TRUNCATE ayri bir yetkidir ve GRANT ALL disinda verilmez; yine de acikca
-- geri alinir ki niyet belgede ve semada gorunur olsun.
REVOKE TRUNCATE ON analytics.impact_plan FROM total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.impact_plan_entry FROM total_rasyo_runtime;
REVOKE TRUNCATE ON analytics.impact_application_run FROM total_rasyo_runtime;

-- Restate ve Total Rasyo sonuc tablolari: runtime yazar ama plan gecmisini
-- silemez.
GRANT SELECT, INSERT, UPDATE, DELETE ON analytics.company_total_rasyo_result
  TO total_rasyo_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON analytics.daily_engine_run
  TO total_rasyo_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON analytics.total_rasyo_run
  TO total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.total_rasyo_restate_runs TO total_rasyo_runtime;
GRANT SELECT, INSERT ON analytics.company_total_rasyo_restate_result
  TO total_rasyo_runtime;

-- ---------------------------------------------------------------- TEST
-- YALNIZ test ortami icin. Uretimde bu rol OLUSTURULMAMALIDIR.
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON analytics.impact_plan,
  analytics.impact_plan_entry, analytics.impact_application_run
  TO total_rasyo_test;

COMMENT ON ROLE total_rasyo_runtime IS
  'Uygulama rolu. impact_plan tablolarinda UPDATE/DELETE/TRUNCATE YOKTUR. '
  'Satir trigger tek basina yeterli degildir cunku TRUNCATE onu atlar.';

COMMENT ON ROLE total_rasyo_migration IS
  'Migration/owner rolu. Runtime bu rol OLMAMALIDIR.';
