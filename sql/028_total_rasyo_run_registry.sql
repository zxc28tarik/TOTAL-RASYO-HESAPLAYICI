-- ============================================================================
-- Total Rasyo orkestratoru — KOSU KAYIT DEFTERI.
--
-- NEDEN AYRI MIGRATION: sql/027 canli veritabaninda uygulandi. Migrationlar
-- append-only'dir; uygulanmis bir dosyayi degistirmek, calistiran ile
-- calistirmayan kurulumlar arasinda sessiz sema farki uretir.
--
-- SOZLESME (RUN KIMLIGI): ayni `run_id` FARKLI icerikle yeniden kullanilamaz.
--   `payload_sha256` kosunun kanonik parmak izidir; ayni run_id ile farkli
--   parmak izi gelirse yazim REDDEDILIR. Aksi halde bir kosunun sonucu baska
--   bir kosunun kimligi altinda sessizce saklanabilirdi.
--
-- SOZLESME (SAYAC DURUSTLUGU): rapor sayaclari burada saklanir ve testte
--   veritabanindan YENIDEN SAYILAN satirlarla karsilastirilir. Sayaci koda
--   guvenerek yazmak, V18'deki "persisted_count=9 ama tabloda 0 satir"
--   hatasinin ta kendisidir.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analytics.total_rasyo_run (
  run_id TEXT PRIMARY KEY CHECK (btrim(run_id) <> '' AND length(run_id) <= 128),
  analysis_at TIMESTAMPTZ NOT NULL,
  payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL,
  overall_status TEXT NOT NULL
    CHECK (overall_status IN ('OK','KISMI','BASARISIZ')),
  engine_error_count INT NOT NULL CHECK (engine_error_count >= 0),
  company_count INT NOT NULL CHECK (company_count >= 0),
  successful_company_count INT NOT NULL CHECK (successful_company_count >= 0),
  insufficient_data_count INT NOT NULL CHECK (insufficient_data_count >= 0),
  engine_failed_company_count INT NOT NULL CHECK (engine_failed_company_count >= 0),
  not_run_company_count INT NOT NULL CHECK (not_run_company_count >= 0),
  routing_conflict_count INT NOT NULL CHECK (routing_conflict_count >= 0),
  missing_m1_count INT NOT NULL CHECK (missing_m1_count >= 0),
  missing_m2_count INT NOT NULL CHECK (missing_m2_count >= 0),
  missing_m3_count INT NOT NULL CHECK (missing_m3_count >= 0),
  missing_ek4_count INT NOT NULL CHECK (missing_ek4_count >= 0),
  missing_ek1_count INT NOT NULL CHECK (missing_ek1_count >= 0),
  missing_ek9_count INT NOT NULL CHECK (missing_ek9_count >= 0),
  missing_good_count INT NOT NULL CHECK (missing_good_count >= 0),
  weights_profile TEXT NOT NULL,
  diagnostics JSONB NOT NULL CHECK (jsonb_typeof(diagnostics) = 'object'),
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (finished_at >= started_at),
  -- Alt sayaclarin toplami sirket sayisini ASAMAZ. Asarsa bir sirket iki
  -- kategoride sayilmis demektir ve rapor kendi icinde tutarsizdir.
  CHECK (successful_company_count + insufficient_data_count
         + engine_failed_company_count + not_run_company_count
         + routing_conflict_count = company_count)
);

CREATE INDEX IF NOT EXISTS idx_total_rasyo_run_analysis_at
  ON analytics.total_rasyo_run (analysis_at DESC);

-- run_id, sonuc tablolarina da yazilir: bir satirin HANGI kosudan geldigi
-- izlenebilir olmalidir.
ALTER TABLE analytics.daily_engine_run
  ADD COLUMN IF NOT EXISTS run_id TEXT;

ALTER TABLE analytics.company_total_rasyo_result
  ADD COLUMN IF NOT EXISTS run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_daily_engine_run_run_id
  ON analytics.daily_engine_run (run_id);

CREATE INDEX IF NOT EXISTS idx_company_total_rasyo_run_id
  ON analytics.company_total_rasyo_result (run_id);
