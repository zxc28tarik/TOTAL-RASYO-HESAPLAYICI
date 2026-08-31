# Public KAP Source Inventory Contract

Status: **OFFLINE ASSEMBLER CONTRACT — DOES NOT AUTHORIZE REAL SCORING**

This layer sits between the independently closed Public KAP PIT snapshot/parser
contract (PR #25) and the still-missing authoritative historical financial
source package required by Issue #24.

It performs **no live KAP scraping**.

## Closed universe basis

The inventory consumes the already-closed historical monthly BIST100 matrix
represented by:

`data/backtest_sources/yahoo_resolved/monthly_member_signal_price_coverage.csv`

For source-coverage purposes only the closed identity columns are used:

- `month`
- `signal_date`
- `index_code`
- `ticker`

The assembler requires exactly:

- 60 ordered months: `2021-08 .. 2026-07`
- 100 historical members per month
- 6000 unique `month+ticker` cells
- XU100 only
- one signal date per month

Deleting, duplicating or reordering month blocks fails closed.

## Timing authority

The assembler does not invent its own cutoff logic.  The supplied schedule must
pass the already-closed
`validate_authorized_cutoff_execution_schedule(...)` contract for
`TOTAL_RASYO_MONTHLY_OPEN_V1`.

A drifted cutoff is rejected before any source coverage is classified.

## Exactly four source-coverage states

Every one of the 6000 cells must end in exactly one of these states:

### `AWAITING_AUTHORIZED_SOURCE`

No authoritative enumeration receipt exists for the historical ticker through
the cell's cutoff, or the receipt is explicitly partial/incomplete.

**Missing local data is never translated to `NOT_FOUND`.**

### `NOT_FOUND`

Allowed only when an authorized enumeration receipt proves that source
enumeration is complete through the cutoff and no visible FR notification was
found by that cutoff.

### `PARSING_REJECTED`

Authorized enumeration is complete, but at least one source notification known
to be visible by that cutoff could not be safely parsed.  This state dominates
`FOUND`, because the rejected notification might be the PIT-valid latest
version.

A rejection published after a cutoff does not leak backward into an earlier
cell.

### `FOUND`

Authorized enumeration is complete through the cutoff, no visible parse
rejection exists, and at least one financial-report version is visible.

Visible versions are selected by the exact PR #25 function:

`select_visible_financial_report_versions(...)`

The inventory layer must not reimplement or bypass that selector.  Future
restatements therefore cannot become visible at earlier cutoffs.

## Authorized enumeration receipt kinds

The assembler accepts only evidence labelled as one of:

- `KAP_DATA_DISTRIBUTION_REST`
- `IMMUTABLE_KAP_EXPORT`

This is a structural allow-list, not proof that credentials/licensing were
valid.  The actual source package still requires provenance and independent
source audit.

High-volume direct scraping of the public KAP website is not an accepted source
route.  PR #25 remains useful for low-volume verification and deterministic
offline validation of already captured snapshots.

## Identity and hash rules

For every accepted parsed notification, PR #25 already requires stable KAP
notification identity and a 64-character lower-case SHA256.

For enumeration evidence this assembler additionally requires:

- canonical historical ticker
- timezone-aware coverage boundary
- source kind
- non-empty source identity
- SHA256 of the enumeration/export evidence
- explicit `enumeration_complete` boolean

A notification ID cannot simultaneously be a parsed report and a parsing
rejection.

## Important semantic boundary: `FOUND` is not scoring-ready

`FOUND` means only:

> an authorized source enumeration was complete through this cutoff and one or
> more PIT-visible FR versions exist without a visible parsing ambiguity.

It does **not** prove:

- required 8-period depth for M2 or other modules
- taxonomy/semantic field completeness
- bank/non-bank/holding/GYO/insurance/financial schema-family coverage
- required ratio derivations
- downstream Total Rasyo input completeness

The summary therefore always records:

- `source_presence_only = true`
- `downstream_period_depth_verified = false`
- `schema_family_coverage_verified = false`
- `real_total_rasyo_scoring_authorized = false`

A structural inventory `PASS` can never authorize the real 60-cutoff Total
Rasyo run.

## Machine-readable output

The inventory output contains one row per closed `month+ticker` cell with at
least:

- month / signal date / historical ticker
- authorized cutoff timestamp
- one of the four statuses above
- selected visible notification IDs
- visible parse rejection IDs
- enumeration completeness/boundary
- source kind / identity / SHA256
- explicit reason

The summary requires status counts to sum to exactly 6000.

## Required mutation guards

The test suite must catch at minimum:

1. deleting one of the 6000 cells;
2. reordering the 60 month blocks;
3. changing a month from 100 members;
4. treating missing/partial source evidence as `NOT_FOUND`;
5. accepting a non-authorized source kind such as public high-volume scrape;
6. allowing a future correction to replace an earlier visible version;
7. allowing a future parse rejection to poison an earlier cutoff;
8. failing to let a visible parse rejection dominate `FOUND`;
9. allowing one notification ID to be both parsed and rejected;
10. cutoff drift away from the closed timing policy;
11. allowing the structural inventory summary to authorize real scoring.

## Current project state after this contract

Even if this assembler is accepted:

- Issue #24 remains OPEN;
- the real authoritative 2021-2026 financial source package is still missing;
- real 60-cutoff Total Rasyo scores are not produced;
- V24-G is not READY;
- no portfolio-performance claim is authorized.
