# V24 Real Historical Data Bootstrap

Status: **IN PROGRESS — universe, calendar, execution-price, corporate-action, PIT CORE+VAL, PIT RSC and PIT M1 foundations are closed; historical M2/remaining modules and real cutoff policy remain open.**

Target window: **2021-08 .. 2026-07 (60 months)**

Goal: run the locked monthly Total Rasyo portfolio contract against an auditable point-in-time historical BIST100/XU100 input set without survivorship bias, restatement hindsight, price fallback, or current-state leakage. No return/performance result may be published until the full historical Total Rasyo authority and V24-G readiness gates are satisfied.

## Verified state

| Family | Status | Current evidence / contract |
|---|---|---|
| XU100 signal calendar | **CLOSED** | 60/60 real first XU100 trading dates, 2021-08-02 through 2026-07-01 |
| Historical BIST100 universe | **CLOSED** | Current 100-member anchor + 21 periodic groups / 180 replacement pairs + all 114 non-periodic announcements audited + ticker lineage |
| Non-periodic XU100 membership | **CLOSED** | Exactly one audited event in the catalog changes XU100: 2026-06-18 KONTR out / BERA in, KAP 1618229 |
| Ticker lineage | **CLOSED** | 38 official equity-code changes; historical identity rendered to the ticker valid on each target date |
| Monthly member execution prices | **CLOSED** | 60 × 100 = 6000/6000 exact signal-day prices |
| Yahoo price gaps | **CLOSED** | 5988 existing Yahoo/lineage rows remain immutable; exactly 12 audited gaps are filled from official Borsa THB |
| THB schema evolution | **CLOSED** | 2022/2023 use 56 fields, 2024 uses 57; parser requires aligned TR/EN headers and exact OPEN/CLOSE semantics instead of a fixed field count |
| Minimum wage source | **SOURCE LOCKED** | `WAGE_TR_NET_CSGB_2021_2026_V1`; contribution rule = 2 × effective official net minimum wage |
| Corporate actions | **ENGINE SEMANTICS CLOSED** | split/bonus share adjustment, dividend cash credit, ticker-change position migration and ordering are implemented/tested in the event-aware portfolio path |
| PIT CORE+VAL ratios | **CLOSED** | historical PIT ratio foundation; no cutoff-later restatement may enter |
| PIT RSC | **CLOSED** | DB-free replay consumes only `HistoricalPitRatioReplayResult.combined_ratios` |
| PIT M1 | **CLOSED** | DB-free 8-quarter replay consumes only PIT `rsc_summary` and reuses production trend math |
| PIT sector M2 | **OPEN** | BANK/NONFIN/HOLDING/GYO/INSURANCE/FINANCIAL historical point-in-time input/replay path still required |
| PIT M3/EK4/EK1/EK9 | **OPEN** | production source semantics must be replayed without current-state leakage |
| Signal cutoff/execution policy | **OPEN** | real 60-month policy is not authorized; test fixture times must not be promoted silently |
| Full historical Total Rasyo authority | **OPEN** | requires the remaining PIT modules and final historical scoring/ranking assembly |
| Final 5-year portfolio result | **BLOCKED BY ABOVE** | no performance claim until readiness is `READY` |

## Historical BIST100 universe

The backtest universe is **XU100/BIST100**, not all listed BIST shares and not today's constituents copied backward.

The reconstruction uses:

1. the official Borsa İstanbul BIST100 snapshot dated 2026-08-17 as the later anchor;
2. 21 official periodic constituent-change groups, representing 180 replacement pairs;
3. an audit of all 114 non-periodic Benchmark constituent-change announcements in the catalog;
4. the one non-periodic event that actually changes XU100 membership: **2026-06-18 KONTR → BERA**;
5. 38 official ticker-code changes, applied as identity lineage rather than membership changes.

Quarterly effective dates are the first actual XU100 trading day of the quarter, not a blindly assumed calendar day 1.

## Signal dates

The frozen real signal calendar contains exactly 60 monthly dates:

- first: **2021-08-02**;
- last: **2026-07-01**.

Each is the first observed XU100 trading day of its month.

## Minimum-wage source

`data/backtest_sources/minimum_wage_csgb_2021_2026.csv` contains eight contiguous official Ministry evidence intervals under schedule key `WAGE_TR_NET_CSGB_2021_2026_V1`:

- 2021: 2,825.90 TL net;
- 2022 H1: 4,253.40 TL net;
- 2022 H2: 5,500.35 TL net;
- 2023 H1: 8,506.80 TL net;
- 2023 H2: 11,402.32 TL net;
- 2024: 17,002.12 TL net;
- 2025: 22,104.67 TL net;
- 2026: 28,075.50 TL net.

The locked portfolio contribution rule is **2 × the net minimum wage effective on that month's execution date**.

## Exact execution-price closure

The frozen Yahoo/lineage coverage has **6000 monthly-member rows**. It originally contained **5988 exact execution prices and 12 holes**:

- INVES: 2022-07-01, 2022-08-01, 2022-09-01;
- KLRHO: 2023-01-02, 2023-02-01, 2023-03-01, 2023-04-03, 2023-05-02, 2023-06-01;
- ASGYO: 2024-01-02, 2024-02-01, 2024-03-01.

Borsa İstanbul's official DataFilePaths registry exposed the Pay Piyasası Günlük Bülteni archive contract under `/data/thb/YYYY/AA/`. The exact 12 target days were found in official THB archives and source/hash evidence was locked in `data/backtest_sources/borsa_exact_price_gap_discovery/thb_schema_proof.json`.

The parser uses the bilingual THB schema:

- `ACILIS FIYATI` / `OPENING PRICE` → OPEN;
- `KAPANIS FIYATI` / `CLOSING PRICE` → CLOSE;
- ordinary equity `.E` series only.

`src/analytics/historical_price_supplement.py` is deliberately **not** a generic fallback chain. Its supplement key set must equal the audited missing-key set exactly. An official Borsa row may fill one of the 12 known holes; it may not overwrite an existing Yahoo/lineage price or introduce a new date/ticker.

The official files also exposed a real schema evolution: 2022/2023 THB rows have 56 fields while 2024 rows have 57. The implementation therefore validates aligned Turkish header / English header / target-row lengths plus required bilingual semantics, instead of assuming a fixed field count.

Result: **6000/6000 exact monthly-member signal prices, 0 remaining gaps.**

## Corporate-action treatment

The historical portfolio path is event-aware. The locked semantics now include:

- split/bonus events adjust share count;
- cash dividends credit portfolio cash;
- ticker-code changes migrate the position identity without manufacturing a buy/sell;
- deterministic event ordering is tested.

This replaces the earlier invalid assumption that raw OPEN/CLOSE alone could be used across corporate-action discontinuities.

## PIT replay chain currently closed

### CORE + VAL

`src/analytics/historical_pit_ratio_replay.py` constructs the historical ratio foundation under the historical `analysis_at`/knowledge boundary. It currently provides PIT-safe CORE ratios and the six VAL ratios required by the existing scoring stack.

### RSC

`src/analytics/historical_pit_rsc_replay.py` has **no database connection argument**. It accepts only the PIT ratio foundation's `combined_ratios`, the historical routing map, and fixed scoring configuration. It rejects:

- routing-outside tickers;
- unsupported sector families;
- unknown ratio names;
- future `period_end` rows;
- malformed `is_na` values;
- duplicate ratio keys;
- scorer output that invents a period/version not present in the PIT input.

This prevents historical replay from accidentally reading today's full `analytics.ratios_quarterly` table.

### M1

`src/analytics/historical_pit_m1_replay.py` is also **database-free**. It consumes only `HistoricalPitRscReplayResult.rsc_summary` and reproduces the production 8-quarter trend mathematics using `period_trend._slope`, `_trend_score`, and `_trend_label`.

It locks:

- latest/previous score;
- 8Q average/min/max;
- 1Q and 4Q changes;
- 8Q slope;
- RSC and `good_count_ge8` changes;
- `quality_trend_score` / label;
- M1 = the clipped production quality-trend score.

It rejects multiple PIT versions for the same ticker+financial period so restatement versions cannot be mistaken for extra quarters.

## Cutoff policy boundary — still open

V24-F's production registry deliberately seeded **no authoritative historical cutoff values**. The hard contract requires:

- timezone-aware timestamps;
- `cutoff_at < execution_at`;
- execution local Europe/Istanbul date equals `signal_date`;
- append-only provenance.

`MONTHLY_FIRST_OPEN_V1`, previous-day 20:00 cutoff, and signal-day 10:00 execution occur in tests as **`POLICY_FIXTURE`**. They are not evidence for the real backtest. A real 60-month cutoff/execution profile must be explicitly decided or sourced before final readiness.

## Latest CI evidence

Canonical evidence file: `docs/V24_REAL_DATA_CI_EVIDENCE.json`.

Tested code SHA: **`ae3b37d4c06e5cbf8e111ad13206006ab57fd665`**.

Latest base-branch evidence:

- schema migration: **PASS**;
- targeted real-data contracts: **96 passed, 0 failed**;
- full repository regression: **1625 passed, 0 failed**;
- BANK v4.7 regression: **277 passed, 1 xfailed**;
- exact monthly-member execution prices: **6000/6000**;
- official Borsa THB supplements: **12**;
- remaining execution-price gaps: **0**;
- overwrite existing Yahoo prices: **false**;
- PIT CORE+VAL: **PASS**;
- DB-free PIT RSC: **PASS**;
- DB-free PIT M1: **PASS**.

An independent PR validation of the same base code also passed:

- validation run: `32171062890`;
- merge SHA tested by GitHub: `31072e590d7a3cb4716988cbd8adab91bb177fbd`;
- targeted: **96 passed**;
- full regression: **1625 passed**;
- BANK: **277 passed, 1 xfailed**.

## Next work order

1. Build PIT-safe **M2 sector replay** for BANK, NONFIN, HOLDING, GYO, INSURANCE and FINANCIAL without current-state DB leakage.
2. Reconstruct PIT sources for **M3, EK4, EK1 and EK9** under the same historical knowledge boundary.
3. Assemble full historical Total Rasyo results/rankings for all 60 monthly cutoffs.
4. Resolve and register the real cutoff/execution policy.
5. Run V24-G real readiness; require `READY`.
6. Only then run the locked monthly portfolio and publish holdings, trades, NAV, contributions, and XU100 comparison.

The production `main` branch remains separate and unchanged by this experimental historical-data branch.
