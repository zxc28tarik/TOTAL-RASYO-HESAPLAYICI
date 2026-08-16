-- YANLIS DESEN (b): "son 8 KAYIT" -- eksik donem oldugu icin pencere GERIYE kayar,
-- 2023-Q3/Q4 iceri girer ve hicbir eksik gorunmez (roe_missing_count = 0)
SELECT period_end, roe_ttm, version_tag, published_at FROM (
    SELECT period_end, roe_ttm, version_tag, published_at
    FROM fixture_financials
    WHERE ticker = :ticker AND published_at <= :analysis_date::date
    ORDER BY period_end DESC LIMIT 8) t
ORDER BY period_end;
