-- ============================================================================
-- GUN ICI LOOK-AHEAD FIKSTURU  (timestamptz riski)
--
-- `published_at date` kullanilirsa ayni gun 10:00 ve 17:00'de yayimlanan iki
-- kayit AYNI tarih sayilir. Saat 12:00'de yapilan tarihsel analizde 17:00
-- verisi GECMISE SIZAR. version_sequence hangi surumun secilecegini cozer ama
-- verinin o saatte BILINIP BILINMEDIGINI cozemez -- ayri bir look-ahead kanali.
--
-- BEKLENEN (2025-06-30 donemi):
--   analysis_at = 2025-08-08 09:00  -> hicbiri gorunmemeli (yuva None)
--   analysis_at = 2025-08-08 12:00  -> ORIGINAL 0.2809   (17:00 verisi SIZMAMALI)
--   analysis_at = 2025-08-08 18:00  -> RESTATED 0.2400
-- ============================================================================

DROP TABLE IF EXISTS fixture_intraday;
CREATE TABLE fixture_intraday (
    record_id        bigserial PRIMARY KEY,
    ticker           text        NOT NULL,
    period_end       date        NOT NULL,
    version_tag      text        NOT NULL,
    version_sequence int         NOT NULL,
    published_at     timestamptz NOT NULL,   -- date DEGIL
    roe_ttm          numeric     NOT NULL
);

INSERT INTO fixture_intraday
    (ticker, period_end, version_tag, version_sequence, published_at, roe_ttm) VALUES
 ('FIXBNK','2025-06-30','ORIGINAL',1,'2025-08-08 10:00:00+03',0.2809),
 ('FIXBNK','2025-06-30','RESTATED',2,'2025-08-08 17:00:00+03',0.2400);

-- Dogru sorgu: analysis_at timestamptz ile karsilastirir
--   WHERE published_at <= :analysis_at::timestamptz
-- YANLIS sorgu: tarihe indirger ve 17:00 verisini 12:00 analizine sizdirir
--   WHERE published_at::date <= :analysis_at::date
