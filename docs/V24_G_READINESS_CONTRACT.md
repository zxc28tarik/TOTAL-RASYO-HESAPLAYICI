# V24-G Historical Backtest Readiness Contract

Status: PART 1 — CONTRACT LOCKED, IMPLEMENTATION NOT YET CLOSED

V24-G is a report-only preflight layer for the locked 2021-08 .. 2026-07 monthly historical backtest. It must never repair, infer, seed, or fabricate historical data.

## Primary invariant

`READY` is permitted only when the same input set is expected to pass the already-locked V24-C/V24-E/V24-F input contracts. V24-G may be stricter only when that strictness is an explicit production-integrity requirement; it must not invent a different portfolio or signal-selection contract.

## Audit categories

- `BENCHMARK`: first observed XU100 trading day and finite positive execution/benchmark prices.
- `WAGE`: exactly one wage interval per execution day plus the structural interval rules already enforced by V24-C.
- `CUTOFF`: exactly one cutoff per signal day, timezone-aware timestamps, cutoff before execution, Istanbul execution-day agreement when `execution_at` is supplied.
- `UNIVERSE`: non-empty then-tradable historical universe plus the structural membership interval rules already enforced by V24-C.
- `PRICE`: exact execution-day finite positive prices for every then-tradable ticker.
- `TOTAL_RASYO`: authoritative FULL_UNIVERSE run identity/registry integrity and execution-universe coverage at or before cutoff.

## Locked semantic details

1. V24-G is report-only. It does not call UPDATE/DELETE/INSERT as a repair mechanism.
2. Missing data is a finding, never a fallback.
3. All target months are scanned when their execution date can be resolved; one bad month must not hide later-month findings.
4. `checked_months == expected_months` is necessary but not sufficient for `READY`.
5. A Total Rasyo run must cover every ticker in that month's execution universe. Extra authoritative-run tickers do **not** by themselves invalidate readiness, matching V24-C signal selection.
6. A `total_rasyo_status != OK` row is valid audit data and yields a no-action state; it does not by itself make the dataset unready.
7. An `OK` row with invalid/missing decision or non-finite/missing score is malformed and must block `READY`, matching V24-C.
8. Wage gaps/overlaps are structural failures even when no monthly signal date lands inside the bad interval.
9. Historical membership overlaps/invalid intervals are structural failures even when no monthly signal date lands inside the bad interval.
10. If `execution_at` is supplied by the registered cutoff schedule, it must be timezone-aware; invalid/naive execution timestamps may not be silently ignored.

## Part-1 contract tests

`tests/test_historical_backtest_readiness_contract.py` locks the high-risk parity cases above before the production implementation is changed.

## Mutation obligations for later parts

At minimum, the closing mutation set must demonstrate that tests fail if any of these protections is removed:

- execution-universe missing ticker check,
- malformed `OK` Total Rasyo row check,
- wage structural gap/overlap check,
- membership structural overlap check,
- timezone-aware execution timestamp check,
- `checked_months == expected_months` readiness requirement.

The permanent V20 rule applies: every protection test must answer "which mutation does this test break?"
