# Independent review of the experimental P3/P4 materialization

The actual source-to-rejection audit passed. The P3 and P4 **exhaustive artifact contracts** are met: every historical membership cell has an explicit result. This is not a successful ranking-driven investment backtest: there are zero score-input-ready cells and zero valid Total Rasyo scores.

## Verified artifacts

- Profile: `EXPERIMENTAL_RISK_ACCEPTED_5Y`; authoritative claims remain disabled.
- P3: **6,000 unique, ordered historical membership cells = 0 ready + 6,000 explicit rejections** across 60 months.
- P4: **60 monthly artifacts, each with 100 explicit rejections**. Every row is hash-bound to its P3 cell; no rejected row has a score, rank or buy decision.
- Independently reopened and hashed **2,115 selected KAP members in 22 original immutable archives**; original parser revalidated report headers, publication and notification identities.
- Verified **118,434 selected own-period fact occurrences** against versioned semantic artifacts, cutoff, disclosure ID and archive/member dimensions.
- Independently verified **12 canonical THB closes from the actual ZIP/CSV bytes**. Their price proof does not establish corporate-action completeness.
- Verified exact archived entity-token bindings and official forward predecessor lineage without treating them as price or share-class evidence.
- Rechecked the latest eligible generic stock-price source rows and the authorized cutoff/execution schedule.
- Independently compared all **64 gzip artifacts** in the committed output directory with `private/experimental_materialization_second`: every byte matches the declared rebuild hashes. The primary semantic source rebuild is separately bound by `rebuild_audit.json`.

## Materialization outcome and remaining dependencies

Financial source materialization is substantive: **5,633 cells contain own-period semantic facts**. The rebuilt HOLDING/GYO candidate set contains **1,113 candidates, zero usable full M2 results**. The historical 993/981 counts were not reproduced or assumed.

The all-rejection result is not the former blanket missing-catalog preflight. The actual overlapping rejection inventory includes:

| Dependency | Cell occurrences |
| --- | ---: |
| Historical sector-index evidence missing | 5,984 |
| Historical economic-family evidence missing | 4,241 |
| Dated nominal-share evidence missing | 1,231 |
| Raw-close basis evidence missing | 1,238 |
| BANK PIT assumptions evidence missing | 509 |
| Stock-window prices missing | 402 |
| Primary financial source bytes missing | 227 |
| Semantic mapping unresolved | 137 |
| Corporate-action completeness evidence missing | 12 |

These counts overlap and must not be added into a cell total. M3 and Ek4 each have **16** non-null module values; Ek9 has **5,598**. M2, M1 and Ek1 each have **zero** complete module values. There are **4,798 CORE-only diagnostic cells**, which are not interchangeable with complete CORE+VAL module inputs.

## Acceptance boundary

- **P3:** source materialization and ready-or-explicit-rejection exhaustiveness pass. Score-input availability remains zero.
- **P4:** the 60-month score-or-explicit-rejection artifact contract passes. The valid ranking sets are empty; no economic score or ranking success is claimed.
- **P5:** these artifacts cannot support a ranking-driven strategy performance claim. A separately labelled zero-trade/all-cash replay can test contribution and ledger mechanics, but cannot replace the requested strategy evaluation.
- **P6:** `P6_PARTIAL_SOURCE_TO_REJECTION_AUDIT_V1` passes. This pass did not independently remap every financial number, recalculate every sector engine or audit portfolio NAV/trade conservation. Full P6 remains unproven by this receipt.
- **P7:** neither reconstructed catalog determinism nor this audit establishes enumeration of superseded historical KAP versions.

No issue closure or merge is implied by this review.

## Hashes

- P3: `0da3b3242dee3112a4a6bfb42d0df6a618d713dc23afdd46e20e2e8ad5b9894f`
- P4 aggregate: `72f18890435e0a2f22e30dafc17ee7e6b55d9b63b3ea1acdeee3e094d3e99531`
- Independent audit: `ff43efc0c2acafa376b9f42deedac287901f6fecb98089bb9ef023077894851d`
