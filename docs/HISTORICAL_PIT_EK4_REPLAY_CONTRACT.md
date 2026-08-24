# Historical PIT Ek4 Replay Contract

Status: **IMPLEMENTED IN PR — independent audit and merge still required**

This contract prevents the historical Ek4 replay from silently diverging from
the production momentum formula or reading information that was unavailable at
the historical market cutoff.

## Locked production arithmetic

The live database path and the database-free historical replay share
`src.analytics.ek4_momentum.compute_ek4_momentum_point`.

For endpoint prices `P0`, `P1` and routed sector-index closes `S0`, `S1`:

```text
stock_return  = P1 / P0 - 1
sector_return = S1 / S0 - 1
excess_return = stock_return - sector_return
Ek4            = clip((excess_return + 0.20) / 0.40, 0, 1)
```

The sector return is the raw routed sector-index return. Ek4 does not subtract
XU100 from the sector return and does not reuse M3's sector-excess factor.

## Exact window definition

The replay uses **20 trading intervals**, which requires 21 ordered trading-date
positions including both endpoints. If `end` is the last calendar day on or
before `market_asof_date`, then `start` is exactly `calendar[end_position - 20]`.
It is not a 20-calendar-day window and it is not a 20-row inclusive slice.

Both stock and routed sector-index prices must exist at `start` and `end`.
Missing intermediate observations do not alter the endpoint calculation.

## Historical routing and fallback policy

The caller must provide the date-correct `sector_index_code` already resolved
from `HISTORICAL_M3_SOURCE_PACKAGE_V1` for each historical universe member.
The replay does not query the current universe or resolve today's sector.

A blank route or a route equal to the market index (`XU100`) is a hard input
error. If a routed sector endpoint is missing, the ticker receives
`SECTOR_WINDOW_PRICE_MISSING`; XU100 is never substituted. A missing stock
endpoint receives `STOCK_WINDOW_PRICE_MISSING`.

Every historical ticker must appear exactly once in either `ek4_scores` or
`rejections`.

## Knowledge boundary

`asof_date` labels the signal date. `market_asof_date` is the last market date
allowed by the caller's cutoff policy. They remain separate fields.

The replay rejects, rather than silently filters:

- a calendar date after `market_asof_date`;
- a stock or index price after `market_asof_date`;
- a ticker outside the supplied historical universe;
- duplicate ticker/date or index/date observations;
- off-calendar observations;
- invalid, non-finite or non-positive prices.

`analysis_at` must be timezone-aware, and neither signal nor market-as-of dates
may claim knowledge after the local Europe/Istanbul analysis day.

The final real cutoff/execution-time policy remains a separate open project
decision. This replay enforces the boundary it is given; it does not invent that
policy.

## CI proof

The permanent CI gate runs:

- `tests/test_historical_pit_ek4_replay.py`;
- `tests/test_ek4_live_compatibility.py`.

Those tests lock the exact 20-interval endpoints, production formula, raw-sector
subtraction, separate as-of dates, future-data rejection, current-universe
isolation, explicit missing-price rejections and the XU100 fallback prohibition.
