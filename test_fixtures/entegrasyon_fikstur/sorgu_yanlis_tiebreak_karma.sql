-- YANLIS DESEN (c): tie-break YOK + takvim yuvasi YOK + pencere sinirsiz.
-- Izole tie-break testi icin sorgu_tiebreak_izole.sql kullanilmali.
SELECT period_end, roe_ttm, version_tag, published_at FROM (
    SELECT DISTINCT ON (period_end) period_end, roe_ttm, version_tag, published_at
    FROM fixture_financials
    WHERE ticker = :ticker AND published_at <= :analysis_date::date
    ORDER BY period_end, published_at DESC) x
ORDER BY period_end;
