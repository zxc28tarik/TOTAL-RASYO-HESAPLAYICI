# V24 Real Historical Data Bootstrap

Status: IN PROGRESS — preflight tooling under validation

Target window: **2021-08 .. 2026-07 (60 months)**

Goal: run the already-locked monthly Total Rasyo portfolio contract against an auditable point-in-time historical BIST input set. This phase must not weaken V24-C/V24-G, infer missing history from today's state, or publish performance from known-invalid raw-price semantics.

## Non-negotiable execution order

1. **Inventory only** — discover what exists in PostgreSQL and which registered wage/cutoff keys are candidates.
2. **Corporate-action price gate** — detect whether raw OPEN/CLOSE can be used across the holding intervals without share/dividend adjustment semantics.
3. **Backfill missing source families** from auditable sources; never synthesize gaps.
4. **Run V24-G readiness** with explicitly selected registered wage/cutoff keys.
5. Only when V24-G is `READY` and the corporate-action gate is resolved, run the locked V24-B monthly portfolio engine.
6. Publish monthly holdings/trades/NAV and XU100 comparison only from that verified run.

## Input families

| Family | Required contract | Current repository capability | Real-data action |
|---|---|---|---|
| XU100 calendar/prices | First observed trading day for each of 60 months; finite positive OPEN/CLOSE | yfinance index loader + PostgreSQL table | Backfill/verify full window |
| Historical BIST universe | Explicit half-open listing/tradability intervals; no survivorship inference | append-only `core.universe_membership_history` + audited ingest | Build from official historical listing/de-listing evidence |
| Stock execution prices | Exact first-trading-day OPEN/CLOSE for every active ticker | yfinance stock loader + `core.prices_daily` | Backfill after historical universe is known |
| Minimum wage | Exactly one audited net minimum-wage interval per execution date | immutable schedule registry | Build and register official 2021-08..2026-07 schedule |
| Signal cutoff | One timezone-aware cutoff/execution row for each monthly signal date | immutable cutoff registry | Build/register 60-row profile after XU100 calendar is frozen |
| Total Rasyo PIT authority | Latest valid FULL_UNIVERSE run at/before each cutoff with full monthly-universe coverage | V24-E/V24-G authority contracts | Historical PIT replay/backfill required where absent |
| Corporate actions | Holding value must not mistake split/bonus/dividend adjustments for investment loss/gain | raw OHLC + `adj_close` exist; V24-B currently changes neither shares nor dividend cash | Must resolve before real-performance claim |

## Official source direction

For historical BIST listing intervals, Borsa İstanbul publishes company-data resources including **First Trading Date and Price of the Equities** and **Companies With Equities De-Listed From The Borsa İstanbul Markets Permanently**. Older exchange data is directed to Borsa İstanbul DataStore. These are preferred over reconstructing membership from the current company list.

Minimum-wage intervals must likewise come from an official Turkish government source and retain source reference/hash metadata when registered.

## Read-only discovery commands

```bash
PYTHONPATH=. python scripts/inventory_historical_backtest_inputs.py \
  --start-month 2021-08 \
  --end-month 2026-07 \
  --expected-months 60 \
  --index-code XU100 \
  --json-out artifacts/v24_real_data_inventory.json
```

The inventory command **does not mean READY**. It only reports broad coverage and candidate registered schedule/profile keys. Final month-by-month authority and input validation belongs to V24-G.

Corporate-action diagnostic:

```bash
PYTHONPATH=. python scripts/audit_historical_price_adjustments.py \
  --start-month 2021-08 \
  --end-month 2026-07 \
  --expected-months 60 \
  --index-code XU100 \
  --json-out artifacts/v24_price_adjustment_audit.json
```

A changed `adj_close / close` factor or missing/unverifiable `adj_close` is a **blocking diagnostic**, not an automatic repair instruction.

## V24-G final gate

After inventory identifies and the operator selects the audited schedule/profile keys:

```bash
PYTHONPATH=. python scripts/audit_historical_backtest_readiness.py \
  --wage-schedule-key <AUDITED_WAGE_KEY> \
  --cutoff-profile-key <AUDITED_CUTOFF_KEY> \
  --start-month 2021-08 \
  --end-month 2026-07 \
  --expected-months 60 \
  --index-code XU100 \
  --json-out artifacts/v24g_real_readiness.json \
  --findings-csv artifacts/v24g_real_findings.csv
```

No backtest result is valid until the returned V24-G status is `READY` and the corporate-action treatment is explicit and tested.

## Known blocker discovered in this phase

V24-B currently executes and marks positions using raw `open`/`close`, while the price loader stores `adj_close` separately and does not auto-adjust OHLC. V24-B also has no share-count or dividend-cash corporate-action mechanism. Therefore a real 5-year performance claim is blocked until this semantic gap is resolved or the diagnostic proves no affected held interval exists.

## Current GitHub work

Branch: `v24-real-data-work`

Added:

- read-only historical input inventory and CLI;
- PostgreSQL inventory acceptance test with rollback-only fixture isolation;
- raw-price corporate-action continuity diagnostic and CLI;
- dedicated Real Data CI workflow.

The production V24-G readiness logic remains unchanged.
