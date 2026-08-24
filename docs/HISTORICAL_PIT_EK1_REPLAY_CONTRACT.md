# Historical PIT Ek1 and Good-Count Replay Contract

Status: **IMPLEMENTED IN PR — independent audit and merge still required**

This contract keeps Ek1 and the Total Rasyo veto input on one point-in-time RSC
lineage. It prevents a score from one financial period being combined with a
`good_count_ge8` value from another period or from today's database state.

## Locked source chain

```text
HistoricalPitRatioReplayResult
  -> HistoricalPitRscReplayResult.rsc_summary
  -> HistoricalPitM1ReplayResult.period_comparison
  -> HistoricalPitEk1ReplayResult
```

Production M1 and Ek1 both read the same `analytics.period_8q_comparison` row.
The historical adapter therefore consumes `HistoricalPitM1ReplayResult`; it does
not independently choose a latest RSC period and it has no database connection.

For every scored ticker, `m1_scores` and `period_comparison` must agree exactly
on:

- ticker;
- latest financial `period_end`;
- `good_count_ge8` / `good_count_latest`.

A mismatch is a hard lineage error.

## Locked Ek1 arithmetic

The live path and historical adapter share
`src.analytics.ek1_quality.compute_ek1_score_from_good_count`:

```text
Ek1 = clip(good_count_ge8 / 18, 0, 1)
```

Counts above 18 saturate Ek1 at 1.0 but remain unchanged as the veto input.
Historical good-count values must be finite, non-negative integers; booleans,
fractions, missing values and infinities are rejected.

The live SQL compatibility path retains its pre-refactor coercion of an internal
missing database value to zero. Historical replay does not copy that fallback:
the upstream closed PIT chain must provide a real good-count value.

## Missing coverage

Every historical ticker appears exactly once in either `ek1_scores` or
`rejections`. A ticker with no PIT RSC/M1 period receives
`PIT_RSC_PERIOD_UNAVAILABLE`; it is not assigned `Ek1=0` or
`good_count_ge8=0`.

Current-universe contamination, duplicate tickers, a future financial period, a
future signal date and disagreement between the two M1 output frames are hard
errors.

## Veto boundary

Ek1 replay publishes the exact `good_count_ge8` beside its score. It does not
reimplement the veto. Final historical assembly must pass that value to the
locked production function `total_rasyo_score.compute_total_rasyo`:

```text
veto_flag  = good_count_ge8 < 5
final_score = base_score * 0.60 when veto_flag else base_score
```

Tests cover both sides of the boundary: count 4 applies the 0.60 factor; count 5
does not.

## CI proof

The permanent CI gate runs:

- `tests/test_historical_pit_ek1_replay.py`;
- `tests/test_ek1_live_compatibility.py`.

Those tests lock the denominator and clipping, live compatibility, single-period
lineage, explicit missing-ticker rejection, input validation, future-data
rejection, exhaustive coverage and the real Total Rasyo veto consumer.
