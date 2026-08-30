# Historical Financial PIT Source Plan

Status: **OPEN / NOT_READY**

This document records the missing real-data layer between the already-closed PIT replay algorithms and the next project objective: producing concrete Total Rasyo outputs for all 60 real cutoffs (2021-08 through 2026-07), followed by V24-G readiness.

## What is already closed

The replay/scoring contracts for PIT CORE+VAL, RSC, M1, all six M2 families, M3, Ek4, Ek1/good_count_ge8, Ek9, the combined 60-cutoff Total Rasyo scorer/ranker, and `TOTAL_RASYO_MONTHLY_OPEN_V1` timing policy are already closed by independent audit and persisted CI evidence.

That proves the calculation/replay code can reject future information and reuse production math from explicit PIT inputs. It does **not** prove that the repository already contains the real 2021-2026 financial input corpus needed to instantiate those inputs for every historical BIST100 member and cutoff.

## Repository inventory finding

The committed `data/backtest_sources` package currently contains real historical universe, market-price, XU100 calendar/signal-date, minimum-wage and M3 source material. It does not contain a closed historical financial PIT source package covering the required company financial statements, sector-specific valuation inputs and dated auxiliary contexts.

Existing replay tests for ratios/RSC/M1/M2 intentionally use explicit fixtures. Those tests remain valid algorithm/contract proofs, but fixture values must never be promoted as historical performance inputs.

## Public KAP acquisition route

A credential-free public KAP route has now been implemented in `src/ingest/api/kap_public_financials.py`:

- list endpoint: `POST https://www.kap.org.tr/tr/api/disclosure/members/byCriteria`
- detail endpoint: `GET https://www.kap.org.tr/tr/api/notification/attachment-detail/{disclosureIndex}`
- only disclosures with subject exactly `Finansal Rapor` and class/type `FR` are accepted;
- publication timestamp is preserved in `Europe/Istanbul`;
- correction/restatement disclosures remain separate source versions;
- list/detail identity is locked by disclosure index, publication time and ticker compatibility;
- queries are bounded to seven-day windows and fail closed at the 2000-result boundary;
- source payloads receive canonical SHA-256 identities;
- taxonomy-bearing KAP HTML rows are extracted without guessing production semantic metrics.

The endpoint mechanics are the public KAP web application's data path. They are not represented here as a credentialed or formally versioned MKK API Portal product contract.

### Live CI proof

`V24 KAP Public Financial PIT CI` run #2 (`33331792555`) on commit `67b830a42a601e72fd6ce5f48384bbf1058eb314` proved the route against a real KAP financial report:

- ticker: `GARFA`
- disclosure index: `1598274`
- publication time: `2026-04-28T18:17:10+03:00`
- summary SHA-256: `12579e9d1510c887d584a8e7421a279867dbe548ec47aab9e521f981ecd7259e`
- detail SHA-256: `968d79f55d9d55e2357121e3708d990d55a59a30cf1dfde1794d4177991da3f1`
- extracted taxonomy-tag count: `276`
- required balance-sheet taxonomy tag observed: `kap-fr_StatementOfFinancialPositionBalanceSheetLineItems`

The same run passed 14 focused fail-closed unit tests.

This is **acquisition-path proof only**. It is not evidence that the full 2021-2026 corpus has been collected or mapped.

## Machine-readable source-class gate

`src/analytics/historical_financial_pit_source_readiness.py` defines the exact source classes that must all be `CLOSED` before the financial PIT source package can be considered ready:

| Source class | Current status | Why it is required |
| --- | --- | --- |
| `KAP_FINANCIAL_REPORTS` | `ACQUISITION_PROVEN` | Full historical KAP financial-report/version corpus is not yet collected and closed. |
| `CORE_SEMANTIC_METRICS` | `OPEN` | CORE/VAL/RSC/M1 need audited semantic facts with `published_at` lineage. |
| `NONFIN_FINANCIALS` | `OPEN` | NONFIN M2 needs real periodic financial inputs, not test frames. |
| `HOLDING_NAV` | `OPEN` | HOLDING M2 requires dated/published NAV, shares and lineage. |
| `GYO_NAV` | `OPEN` | GYO M2 additionally requires property-portfolio value and NAV-source method. |
| `INSURANCE_METRICS` | `OPEN` | Insurance M2 requires sector metrics including premiums, claims, technical result and solvency. |
| `FINANCIAL_METRICS` | `OPEN` | Financial-institution M2 requires receivables, NPL/provisions, capital adequacy and related metrics. |
| `BANK_QUARTER_SLOTS` | `OPEN` | BANK v4.7 needs exact PIT eight-quarter canonical input slots. |
| `BANK_ASSUMPTIONS` | `OPEN` | BANK replay requires dated resolved assumptions valid at the cutoff. |
| `M2_FOLLOW_CONTEXTS` | `OPEN` | Real run must not silently use neutral/default follow context. |

The gate requires the **exact** class set. Missing classes and foreign/fallback classes are errors. `OPEN` and `ACQUISITION_PROVEN` both prevent `READY`; only every requirement being exactly `CLOSED` can produce `READY`.

## Non-negotiable PIT rules for the real package

1. Every source record must carry a source identity and a publication/availability time where the upstream source exposes one.
2. For each authorized cutoff, only versions public at or before that cutoff may be selected.
3. Later corrections/restatements may not rewrite an earlier cutoff's source view.
4. Current-state company facts, current NAV, current assumptions, current peer/follow context and current universe are forbidden fallbacks.
5. Missing required inputs remain explicit missing/rejection states; no neutral fill or weight redistribution may make them appear complete.
6. Exact ordered 60-month coverage remains mandatory.
7. A company must appear in the final real output as either a valid Total Rasyo result or an explicit rejection with machine-readable reason and lineage; silent ticker loss is forbidden.

## Next implementation sequence

1. Build a deterministic collector for the union of historical BIST100 members that discovers all relevant KAP `Finansal Rapor` disclosures over the required historical horizon in bounded date windows, preserving every version and source hash.
2. Persist an auditable compact source package/manifest and define the raw-payload retention/refetch contract before marking `KAP_FINANCIAL_REPORTS=CLOSED`.
3. Map KAP taxonomy data into the existing project semantic contracts without label guessing; verify taxonomy/version changes and mutation-protect the mapping.
4. Close each sector-specific source class above with real dated evidence. HOLDING/GYO NAV and sector-specific solvency/capital metrics may require additional official disclosure classes beyond generic financial statements and must be handled explicitly.
5. Resolve and audit the real `M2_FOLLOW_CONTEXTS` policy/source; optional production defaults are not acceptable for the real 60-cutoff evidence set.
6. Only after all source classes are `CLOSED`, generate the concrete 60-cutoff Total Rasyo company result/rejection set.
7. Run V24-G against exactly that generated set and require `READY` before any portfolio or five-year performance claim.

## Claims explicitly forbidden at this stage

- historical Total Rasyo return/performance;
- V24-G `READY`;
- full 60-cutoff real score production complete;
- historical financial PIT package `CLOSED`;
- public KAP acquisition proof being equivalent to semantic/sector-source closure.
