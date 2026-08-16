-- YANLIS DESEN (a): eksik ceyrek yok sayilir, takvim yuvasi kaybolur
SELECT period_end, roe_ttm, version_tag, published_at
FROM fixture_financials
WHERE ticker = :ticker AND published_at <= :analysis_date::date
ORDER BY period_end;
