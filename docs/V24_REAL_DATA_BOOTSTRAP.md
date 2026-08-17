# V24 Real Historical Data Bootstrap

Status: IN PROGRESS — first preflight/tooling gate green; real source acquisition still open

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
| Historical BIST universe | Explicit half-open listing/tradability intervals; no survivorship inference | append-only `core.universe_membership_history` + audited ingest | Build from official first-trading, permanent-delisting, and equity-code-change evidence |
| Stock execution prices | Exact first-trading-day OPEN/CLOSE for every active ticker | yfinance stock loader + `core.prices_daily` | Backfill after historical universe is known |
| Minimum wage | Exactly one audited net minimum-wage interval per execution date | immutable schedule registry | Official 2021-2026 candidate is now source/hash locked under `WAGE_TR_NET_CSGB_2021_2026_V1`; persistence to real DB remains open |
| Signal cutoff | One timezone-aware cutoff/execution row for each monthly signal date | immutable cutoff registry | Signal dates come from frozen XU100 calendar; actual cutoff/execution policy must be explicitly sourced/decided before the 60-row profile is created |
| Total Rasyo PIT authority | Latest valid FULL_UNIVERSE run at/before each cutoff with full monthly-universe coverage | V24-E/V24-G authority contracts | Historical PIT replay/backfill required where absent |
| Corporate actions | Holding value must not mistake split/bonus/dividend adjustments for investment loss/gain | raw OHLC + `adj_close` exist; V24-B currently changes neither shares nor dividend cash | Must resolve before real-performance claim |

## Official source direction

For the historical BIST universe, Borsa İstanbul's official Equity Market Data page exposes three required source families:

1. **First Trading Date and Price of the Equities** — candidate listing start evidence.
2. **Companies With Equities De-Listed From The Borsa İstanbul Markets Permanently** — candidate listing end evidence.
3. **Equity Name and Equity Code Changes** — ticker lineage evidence.

The acquisition contract is locked in `data/backtest_sources/historical_universe_borsaistanbul_manifest.json`. Raw files have deliberately **not** been marked acquired and no raw URL, column mapping, delisting-date interpretation, or ticker lineage has been invented. Each raw file must be hashed before canonicalization.

Borsa İstanbul also states that older exchange files moved to DataStore as of 2015. The current bootstrap will use the official page/DataStore evidence rather than reconstructing history from the current company snapshot.

## Minimum-wage source candidate

`data/backtest_sources/minimum_wage_csgb_2021_2026.csv` contains eight contiguous official Ministry evidence intervals under schedule key `WAGE_TR_NET_CSGB_2021_2026_V1`:

- 2021: 2,825.90 TL net;
- 2022 H1: 4,253.40 TL net;
- 2022 H2: 5,500.35 TL net;
- 2023 H1: 8,506.80 TL net;
- 2023 H2: 11,402.32 TL net;
- 2024: 17,002.12 TL net;
- 2025: 22,104.67 TL net;
- 2026: 28,075.50 TL net.

Every row carries an official Ministry source reference plus a reproducible source-evidence SHA256. The existing V24-F schedule loader accepts the file, and tests lock the user's monthly contribution rule as `2 × net minimum wage effective on that month's execution date`.

## Cutoff policy boundary

V24-F's production registry deliberately seeded **no real historical cutoff values**. Its hard contract requires only:

- timezone-aware timestamps,
- `cutoff_at < execution_at`,
- execution timestamp's Europe/Istanbul calendar date equals `signal_date`,
- append-only provenance.

The recurring `MONTHLY_FIRST_OPEN_V1`, previous-day 20:00 cutoff, and first-trading-day 10:00 execution values appear in V24-F tests as `POLICY_FIXTURE`. They are not sufficient evidence to silently create a real 60-row profile. The real cutoff/execution policy therefore remains an explicit open input; no fabricated schedule has been created.

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

## Known blockers discovered in this phase

1. **Real production data are not stored in the repository and the current GitHub connector cannot expose the production PostgreSQL secret/variable configuration.** Therefore this branch can validate tooling and audited source inputs, but it cannot truthfully claim to have run the real 60-month DB inventory from GitHub alone.
2. **Corporate actions:** V24-B currently executes and marks positions using raw `open`/`close`, while the price loader stores `adj_close` separately and does not auto-adjust OHLC. V24-B also has no share-count or dividend-cash corporate-action mechanism. A real five-year performance claim is blocked until this semantic gap is resolved or the diagnostic proves no affected held interval exists.
3. **Historical universe raw files:** the three official Borsa İstanbul datasets are identified, but the raw files/checksums/column semantics are not yet captured in-repo. No current-snapshot fallback is permitted.
4. **Cutoff policy:** the registry contract is known, but real 60-month cutoff/execution timestamps must not be copied from test fixtures without an explicit policy decision/source.

## CI evidence — first green real-data preflight gate

Branch: `v24-real-data-work`

V24 Real Data CI run #4, head `cf9c154f1b24da165d80b68ae17f17ec931cd999`:

- targeted real-data preflight contracts: **14 passed, 4 warnings**;
- full repository regression: **1520 passed, 31 warnings**;
- BANK v4.7 regression: **277 passed, 1 xfailed, 1 warning**;
- workflow conclusion: **success**.

An earlier run exposed a test-isolation defect: the inventory PostgreSQL acceptance fixture committed append-only rows into the shared CI DB and polluted later tests. The fixture was corrected to use unique hashes plus transaction rollback; the succeeding full regression above is the closure evidence for that defect.

## Current GitHub work

Added on `v24-real-data-work`:

- read-only historical input inventory and CLI;
- PostgreSQL inventory acceptance test with rollback-only fixture isolation;
- raw-price corporate-action continuity diagnostic and CLI;
- official Ministry minimum-wage source candidate plus source/hash tests;
- official Borsa İstanbul historical-universe acquisition manifest requiring first-trading, permanent-delisting, and ticker-code-change evidence;
- dedicated Real Data CI workflow.

The production V24-G readiness logic remains unchanged.
