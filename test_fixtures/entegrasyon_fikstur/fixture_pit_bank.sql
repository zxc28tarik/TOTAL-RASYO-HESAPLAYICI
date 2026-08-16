-- ============================================================================
-- Entegrasyon test fiksturu: tek bankada BES tuzak ayni anda
--
-- FIXBNK / 2025-12-31 analiz donemi, takvim yuvalari 2024-Q1 .. 2025-Q4
--
--   2024-Q3  : TAMAMEN EKSIK        -> yuva None kalmali (sikistirma testi)
--   2025-Q1  : iki surum, farkli tarih -> point-in-time secim testi
--   2025-Q2  : iki surum, AYNI tarih   -> tie-break testi
--   2023-Q3/Q4: pencere DISINDA      -> "son 8 kayit" hatasi testi
--
-- HAM ORIGINAL seri (point-in-time UYGULANMAMIS -- referans DEGIL):
--   [0.1560, 0.1898, None, 0.2346, 0.2689, 0.2809, 0.2950, 0.3080]
--
-- DOGRU seri analiz tarihine gore DEGISIR:
--   asof=2026-03-01 -> [0.1560, 0.1898, None, 0.2346, 0.2100, 0.2400, 0.2950, 0.3080]
--                      (2025Q1 ve 2025Q2 RESTATED secilir)
--   asof=2025-10-01 -> [0.1560, 0.1898, None, 0.2346, 0.2689, 0.2400, None,   None]
--                      (2025Q1 RESTATED henuz yayimlanmamis; Q3/Q4 de yok)
-- ============================================================================

DROP TABLE IF EXISTS fixture_financials;
CREATE TABLE fixture_financials (
    record_id        bigserial PRIMARY KEY,
    ticker           text    NOT NULL,
    period_end       date    NOT NULL,
    version_tag      text    NOT NULL,
    version_sequence int     NOT NULL,   -- tie-break icin ZORUNLU alan
    published_at     date    NOT NULL,
    roe_ttm          numeric NOT NULL
);

-- Pencere DISI: "son 8 kayit" yaklasimi bunlari iceri alir
INSERT INTO fixture_financials
    (ticker, period_end, version_tag, version_sequence, published_at, roe_ttm) VALUES
 ('FIXBNK','2023-09-30','ORIGINAL',1,'2023-11-10',0.1180),
 ('FIXBNK','2023-12-31','ORIGINAL',1,'2024-02-12',0.1325),

-- Normal donemler
 ('FIXBNK','2024-03-31','ORIGINAL',1,'2024-05-10',0.1560),
 ('FIXBNK','2024-06-30','ORIGINAL',1,'2024-08-09',0.1898),
-- 2024-09-30 KAYIT YOK  (kasitli bosluk)
 ('FIXBNK','2024-12-31','ORIGINAL',1,'2025-02-14',0.2346),

-- 2025-Q1: iki surum, FARKLI published_at -> analysis_date'e gore secim degisir
 ('FIXBNK','2025-03-31','ORIGINAL',1,'2025-05-09',0.2689),
 ('FIXBNK','2025-03-31','RESTATED',2,'2025-11-20',0.2100),

-- 2025-Q2: iki surum, AYNI published_at -> tie-break sart
 ('FIXBNK','2025-06-30','ORIGINAL',1,'2025-08-08',0.2809),
 ('FIXBNK','2025-06-30','RESTATED',2,'2025-08-08',0.2400),

 ('FIXBNK','2025-09-30','ORIGINAL',1,'2025-11-07',0.2950),
 ('FIXBNK','2025-12-31','ORIGINAL',1,'2026-02-13',0.3080);

-- NOT: Fiziksel sira bagimsizligi TEK tabloyla kanitlanamaz.
-- CLUSTER ... USING pkey tabloyu KARISTIRMAZ, bilakis ekleme sirasina dizer ve
-- tie-break'siz sorgunun sectigi kaydi daha da sabitler.
-- Dogru yontem: fixture_pit_bank_orders.sql ile IKI kurulum (A ve B) kurup
-- ayni sorguyu ikisinde de kosturmak.
