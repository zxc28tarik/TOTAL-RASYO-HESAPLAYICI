-- Parameters:
--   %(tickers)s            -> text[]
--   %(analysis_at)s        -> timezone-aware timestamp
--   %(anchor_period_end)s  -> date
-- One client round-trip; the canonical slot function remains the single source of truth.
SELECT
  t.ticker,
  s.period_end,
  s.record_id,
  s.selected_version_tag,
  s.selected_version_sequence,
  s.selected_published_at,
  s.roe_ttm,
  s.bvps,
  s.payout_sus
FROM unnest(%(tickers)s::text[]) AS t(ticker)
CROSS JOIN LATERAL analytics.bank_point_in_time_slots(
  t.ticker,
  %(analysis_at)s::timestamptz,
  %(anchor_period_end)s::date
) AS s
ORDER BY t.ticker, s.period_end;
