# V24-G Historical Backtest Readiness Contract

Status: PART 5 — CLOSED

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

## Delivery record

- **Part 1 — contract lock:** high-risk V24-C/V24-E/V24-F parity rules were frozen before production implementation changes.
- **Part 2 — compatibility/alignment:** pandas 3 fixture compatibility and contract-alignment gaps were closed.
- **Part 3 — PostgreSQL bridge:** report-only database snapshot/readiness integration was added and exercised against the locked monthly backtest inputs.
- **Part 4 — operator interface:** readiness report/CLI was added with fail-closed operator exit semantics.
- **Part 5 — closing proof:** the remaining V20 mutation obligations were mapped one-to-one to permanent tests; an explicit `checked_months == expected_months` guard test was added. Part 5 changes do not alter production readiness logic.

## Closing mutation obligations

The closing mutation set must demonstrate that tests fail if any of these protections is removed:

1. execution-universe missing ticker check,
2. malformed `OK` Total Rasyo row check,
3. wage structural gap/overlap check,
4. membership structural overlap check,
5. timezone-aware execution timestamp check,
6. `checked_months == expected_months` readiness requirement.

The permanent V20 rule applies: every protection test must answer "which mutation does this test break?"

## Part-5 closure evidence

### Work-branch proof

Validated on branch `v24g-part5-work`, head `9bc2d46a5aa51931e49c016e0f8ac3c864acff4d`, V24G CI run #10:

- targeted V24-G suite: **24 passed**;
- closing mutation set: **11/11 mutations killed** — the six obligations above plus five retained guards from Parts 1-4;
- full repository regression: **1506 passed**;
- BANK v4.7 regression: **277 passed, 1 xfailed**;
- workflow conclusion: **success**.

### `v24g-ci` integration proof

The verified Part-5 chain was fast-forwarded to `v24g-ci` without force. V24G CI run #11 completed successfully after integration.

The temporary `v24g-part5-work` workflow trigger was then removed. The resulting clean `v24g-ci` workflow was validated at commit `8d0abe89d7e16706aaf737d94154f413e408cdb5` by V24G CI run #12:

- targeted V24-G suite: **24 passed, 6 warnings**;
- closing mutation set: **11/11 mutations killed**;
- full repository regression: **1506 passed, 27 warnings**;
- BANK v4.7 regression: **277 passed, 1 xfailed, 1 warning**;
- workflow conclusion: **success**.

V24-G Part 5 is therefore closed. The readiness layer remains report-only and fail-closed; no historical repair/fallback path was introduced by the closing proof work.
