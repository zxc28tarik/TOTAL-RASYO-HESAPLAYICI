-- ============================================================================
-- IZOLE TIE-BREAK TESTI
--
-- sorgu_dogru.sql ile TEK farki: ORDER BY'da version_sequence/record_id YOK.
-- Takvim yuvasi, sekiz donem penceresi ve LEFT JOIN aynen korunur.
-- Boylece test basarisiz olursa sebebin KESINLIKLE tie-break oldugu bilinir.
-- ============================================================================
WITH slots AS (
    SELECT (date_trunc('quarter', :son_donem::date)
            - (n || ' months')::interval
            + interval '3 months' - interval '1 day')::date AS period_end
    FROM generate_series(0, 21, 3) AS n
),
pit AS (
    SELECT DISTINCT ON (f.period_end)
           f.period_end, f.version_tag, f.published_at, f.roe_ttm
    FROM fixture_financials f
    WHERE f.ticker = :ticker
      AND f.published_at <= :analysis_date::date
    ORDER BY f.period_end,
             f.published_at DESC        -- <-- TEK FARK: tie-break yok
)
SELECT s.period_end, p.roe_ttm, p.version_tag AS selected_version_tag
FROM slots s
LEFT JOIN pit p USING (period_end)
ORDER BY s.period_end;
