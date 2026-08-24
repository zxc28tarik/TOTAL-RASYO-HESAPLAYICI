# Historical PIT Ek9 Replay Contract

Status: **IMPLEMENTED IN PR — independent audit and merge still required**

This contract reproduces the production Ek9 daily-return volatility score without
a database read, current-universe lookup, price fallback or post-cutoff market
observation.

## Locked production arithmetic

The live database path keeps its existing price query, pivot, `pct_change()` call,
row-count guard and 63-row tail unchanged. Only the arithmetic after that tail is
moved to the shared pure function
`src.analytics.ek9_volatility.compute_ek9_volatility_scores`.

For each ticker's return window:

```text
volatility = std(daily_returns, ddof=1)
volatility = replace(+/-inf, NaN); fill NaN with 0.0
Ek9        = 1 - clip(volatility / 0.06, 0, 1)
```

There is no annualization and the denominator is exactly `0.06`.

The historical adapter reuses this arithmetic but does **not** copy the live
path's permissive missing-data preprocessing. The legacy live behavior remains
unchanged for compatibility; the historical path validates a complete PIT window
before it computes returns.

## Exact 63-return window

Production Ek9 uses `lookback=63`. The historical contract therefore fixes
`lookback_days=63`; another value is rejected.

A 63-return window requires **64 consecutive XU100 trading-calendar price
positions**. To preserve the live path's existing global guard, the supplied
calendar must contain at least `lookback + 2 = 65` eligible rows. The score uses
the final 64 positions ending at the last calendar day on or before
`market_asof_date`.

For every scored ticker, all 64 stock prices must exist. The adapter then calls
`pct_change(fill_method=None)` and requires exactly 63 finite returns. The
explicit `fill_method=None` is safe because completeness was already proven and
prevents a missing historical observation from becoming a synthetic zero return.

A missing price produces `STOCK_WINDOW_PRICE_MISSING`; it is never forward-filled
or borrowed from another series.

## XU100 and fallback policy

The V24 historical backtest uses the XU100 trading calendar as the market-date
authority. `market_index` is therefore locked to `XU100` for this replay.

Ek9 itself has **no index-price input**. XU100 is a calendar authority only. A
missing stock price can never be replaced by an XU100 close, sector close,
current ticker price or any other fallback. This is structural: the replay API
accepts only historical `stock_prices` plus the supplied trading calendar.

The historical universe is also explicit input. A stock-price row for a ticker
outside that universe is a hard error.

## Knowledge boundary and fail-closed rules

`asof_date` labels the signal. `market_asof_date` is the latest market date the
caller has authorized under the cutoff policy. They remain separate fields.

The replay rejects rather than silently filters:

- timezone-naive `analysis_at`;
- `market_asof_date > asof_date`;
- signal or market dates after the Europe/Istanbul analysis day;
- a calendar or stock price after `market_asof_date`;
- duplicate ticker/date observations;
- off-calendar stock observations;
- historical-universe contamination;
- non-numeric, non-finite or non-positive selected stock prices;
- a non-63 lookback or a non-XU100 calendar authority.

Every historical ticker appears exactly once in either `ek9_scores` or
`rejections`, in deterministic ticker order.

The final real cutoff/execution-time policy remains a separate project decision.
This adapter enforces the boundary supplied by that future policy and does not
invent one.

## CI proof

The permanent CI gate runs:

- `tests/test_historical_pit_ek9_replay.py`;
- `tests/test_ek9_live_compatibility.py`.

The tests lock DB-free structure, no index-price fallback path, 64-price/63-return
windowing, `ddof=1`, the 0.06 cap, no annualization, live legacy `pct_change()`
preprocessing, explicit missing-price rejection, future-data rejection,
current-universe isolation, deterministic exhaustive coverage and pandas 2.2.3
compatibility.
