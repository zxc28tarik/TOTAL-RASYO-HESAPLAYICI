# V24 Real Historical Data Bootstrap

Status: **IN PROGRESS — universe, calendar, execution-price, corporate-action, PIT CORE+VAL, PIT RSC, PIT M1, all six PIT sector M2 families and the DB-free M3 replay engine are closed in code; real M3 source coverage, remaining historical modules and the real cutoff policy remain open.**

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
| PIT sector M2 | **CLOSED** | DB-free replay is closed for NONFIN, HOLDING, GYO, INSURANCE, FINANCIAL and BANK; latest sector CI is green |
| PIT M3 replay engine | **CLOSED** | PR #13 passed two independent audits; DB-free history stays isolated from live beta behavior and pandas 2.2.3 compatibility is a permanent CI gate |
| Real 60-month M3 source coverage | **OPEN — CONTRACT IMPLEMENTED** | fail-closed package/hash/lineage/6000-route/daily-close gates exist; real committed raw and canonical sources are still missing |
| PIT EK4/EK1/EK9 | **OPEN** | production source semantics must be replayed without current-state leakage |
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

### M2

The DB-free historical sector replay path is closed for all six production families:

- NONFIN;
- HOLDING;
- GYO;
- INSURANCE;
- FINANCIAL;
- BANK v4.7.

Each replay consumes explicit point-in-time frames, rejects future financial/NAV/price-follow observations, and does not fall back to the current universe. The implementations reuse the corresponding production sector valuation and two-axis M2 semantics instead of defining a second historical scoring model.

### M3

`src/analytics/historical_pit_m3_replay.py` has no database connection argument. It consumes an explicit historical universe with sector-index routing, a pre-cut trading calendar, adjusted stock prices and index closes. `asof_date` labels the signal while `market_asof_date` records the last market day allowed by the caller's cutoff policy; the replay rejects any later calendar or price row instead of filtering it silently.

The live database path and the historical adapter share:

- `betas._ols_2f` for the production two-factor OLS and shrinkage model;
- `trailing_alpha.compute_trailing_alpha_from_frames` for 63-trading-day stock/market/sector alpha, score and label;
- the sector-excess factor `sector_return - market_return`, not raw sector return;
- production beta priors `beta_mkt=1`, `beta_sec=0` when fewer than 60 finite observations are available, with an explicit `beta_source` diagnostic.

Historical beta inputs are normalized to an explicit union date axis and use
`pct_change(fill_method=None)`, so a missing price remains missing instead of
becoming a synthetic zero return. This preprocessing is intentionally isolated
from `estimate_betas_for_date`: the live database path retains its pre-M3 date-axis
and missing-market behavior unchanged.

Every historical ticker is either scored or emitted in `rejections`; current-universe contamination, missing sector routing, duplicate keys, off-calendar observations and post-cutoff prices are hard errors. Unlike the live compatibility path, historical replay never substitutes XU100 for a missing sector index. The real 60-month run is still blocked because the repository's frozen source package does not yet contain provenance/hash-locked daily XU100 and sector-index closes plus date-correct sector routing for every monthly member.

`src/analytics/historical_m3_source_package.py` now defines the separate
`HISTORICAL_M3_SOURCE_PACKAGE_V1` gate. It requires committed raw payloads,
canonical routes/closes, exact SHA256 coverage, deterministic transformation code,
date-ranged non-overlapping routes, all 6,000 membership-route resolutions and a
complete 252-trading-day index-close window for every signal. The contract and its
mutation tests are implemented; this does **not** close the real-data task. See
`docs/HISTORICAL_M3_SOURCE_PACKAGE_CONTRACT.md`.

## Cutoff policy boundary — still open

V24-F's production registry deliberately seeded **no authoritative historical cutoff values**. The hard contract requires:

- timezone-aware timestamps;
- `cutoff_at < execution_at`;
- execution local Europe/Istanbul date equals `signal_date`;
- append-only provenance.

`MONTHLY_FIRST_OPEN_V1`, previous-day 20:00 cutoff, and signal-day 10:00 execution occur in tests as **`POLICY_FIXTURE`**. They are not evidence for the real backtest. A real 60-month cutoff/execution profile must be explicitly decided or sourced before final readiness.

## Latest CI evidence

Latest verified active-branch evidence commit: [`4fce014`](https://github.com/zxc28tarik/TOTAL-RASYO-HESAPLAYICI/commit/4fce01482b7bae9574f055c2382a8f43ea86f3f3).

Tested code SHA: **`533aaaf7b9ef7f3050d63d3b80e7376bb4ae59ef`**.

Latest base-branch results:

- schema migration: **PASS**;
- pandas 2.2.3 / numpy 1.26.4 compatibility gate: **17 passed, 0 failed**;
- targeted real-data contracts: **155 passed, 0 failed**;
- full repository regression: **1684 passed, 0 failed**;
- BANK v4.7 regression: **277 passed, 1 xfailed**;
- exact monthly-member execution prices: **6000/6000**;
- official Borsa THB supplements: **12**;
- remaining execution-price gaps: **0**;
- overwrite existing Yahoo prices: **false**;
- PIT CORE+VAL: **PASS**;
- DB-free PIT RSC: **PASS**;
- DB-free PIT M1: **PASS**;
- DB-free PIT M3: **PASS**;
- DB-free PIT M2 for all six sector families: **PASS**.

The M3 replay and live-beta compatibility fix passed two independent reviews,
including mutation tests under pandas 2.2.3, before merge. The source-package
contract in the current PR is a new gate and does not alter that live path.

`docs/V24_REAL_DATA_CI_EVIDENCE.json` is the machine-readable consolidated evidence file. It will record the source-package contract as PASS but the real source package as OPEN until committed real inputs pass every gate.

## Next work order

1. Independently audit the M3 source-package contract, then freeze the real 60-month M3 sector-routing/index-price sources through it.
2. Reconstruct PIT **EK4, EK1, `good_count_ge8` and EK9** under the same historical knowledge boundary.
3. Assemble full historical Total Rasyo results/rankings for all 60 monthly cutoffs.
4. Resolve and register the real cutoff/execution policy.
5. Run V24-G real readiness; require `READY`.
6. Only then run the locked monthly portfolio and publish holdings, trades, NAV, contributions, and XU100 comparison.

The production `main` branch remains separate and unchanged by this experimental historical-data branch.
