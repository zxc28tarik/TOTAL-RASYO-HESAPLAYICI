-- ============================================================================
-- FIZIKSEL SIRA BAGIMSIZLIGI TESTI
--
-- Ayni mantiksal veri, IKI farkli fiziksel ekleme sirasi:
--   Kurulum A: 2025-Q2 icin ORIGINAL once, RESTATED sonra
--   Kurulum B: 2025-Q2 icin RESTATED once, ORIGINAL sonra
--
-- BEKLENEN:
--   dogru sorgu (uclu tie-break)  -> A ve B'de AYNI sonuc  <- KARARLI CI SARTI
--   tie-break'siz sorgu           -> A ve B'de farklilasABILIR; sonucu TANIMSIZDIR
--                                    ve CI sarti YAPILMAZ. Tablo yeniden kurulunca
--                                    ikisinin ayni sonucu verdigi gozlendi.
-- ============================================================================

DROP TABLE IF EXISTS fixture_order_a;
DROP TABLE IF EXISTS fixture_order_b;

-- LIKE ... INCLUDING ALL sequence PAYLASTIRIR ve yeniden kurulumda
-- "cannot drop table ... other objects depend on it" hatasi verir.
-- Iki tablo da ACIKCA tanimlanir, her biri kendi sequence'ini alir.
CREATE TABLE fixture_order_a (
    record_id bigserial PRIMARY KEY, ticker text NOT NULL, period_end date NOT NULL,
    version_tag text NOT NULL, version_sequence int NOT NULL,
    published_at date NOT NULL, roe_ttm numeric NOT NULL);
CREATE TABLE fixture_order_b (
    record_id bigserial PRIMARY KEY, ticker text NOT NULL, period_end date NOT NULL,
    version_tag text NOT NULL, version_sequence int NOT NULL,
    published_at date NOT NULL, roe_ttm numeric NOT NULL);

-- Kurulum A: ORIGINAL once
INSERT INTO fixture_order_a
    (ticker, period_end, version_tag, version_sequence, published_at, roe_ttm) VALUES
 ('FIXBNK','2025-06-30','ORIGINAL',1,'2025-08-08',0.2809),
 ('FIXBNK','2025-06-30','RESTATED',2,'2025-08-08',0.2400);

-- Kurulum B: RESTATED once (ayni mantiksal veri, ters fiziksel sira)
INSERT INTO fixture_order_b
    (ticker, period_end, version_tag, version_sequence, published_at, roe_ttm) VALUES
 ('FIXBNK','2025-06-30','RESTATED',2,'2025-08-08',0.2400),
 ('FIXBNK','2025-06-30','ORIGINAL',1,'2025-08-08',0.2809);
