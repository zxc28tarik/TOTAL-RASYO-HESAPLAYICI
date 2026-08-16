-- ============================================================================
-- DOGRU SORGU: takvim yuvasi + point-in-time surum + deterministik tie-break
-- Parametreler: :ticker, :analysis_date, :son_donem
-- ============================================================================
WITH slots AS (                      -- 1) SEKIZ TAKVIM YUVASI (kayittan bagimsiz)
    SELECT (date_trunc('quarter', :son_donem::date)
            - (n || ' months')::interval
            + interval '3 months' - interval '1 day')::date AS period_end
    FROM generate_series(0, 21, 3) AS n
),
pit AS (                             -- 2) POINT-IN-TIME + TIE-BREAK
    SELECT DISTINCT ON (f.period_end)
           f.period_end, f.version_tag, f.published_at, f.roe_ttm
    FROM fixture_financials f
    WHERE f.ticker = :ticker
      AND f.published_at <= :analysis_date::date
    ORDER BY f.period_end,
             f.published_at   DESC,   -- en son yayimlanan
             f.version_sequence DESC, -- esitse: yuksek surum
             f.record_id      DESC    -- yine esitse: deterministik
)
SELECT s.period_end,                  -- 3) EKSIKLER None OLARAK KORUNUR
       p.roe_ttm       AS roe_ttm,
       p.version_tag   AS selected_version_tag,
       p.published_at  AS selected_published_at
FROM slots s
LEFT JOIN pit p USING (period_end)
ORDER BY s.period_end;                -- 4) ZAMAN SIRASI GARANTILI
