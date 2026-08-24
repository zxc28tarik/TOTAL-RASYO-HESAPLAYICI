# Historical M3 Source Package Contract V1

## Status

**CONTRACT IMPLEMENTED; REAL SOURCE PACKAGE CLOSED AND MERGED.**

The committed package now passes the gates for real historical sector routing and
daily XU100/sector-index closes. This closes the M3 source component only; it does
not make the full V24 backtest `READY` while the other documented open modules and
cutoff policy remain unresolved.

The executable validator is
`src/analytics/historical_m3_source_package.py`; the machine-readable shape is
`config/historical_m3_source_package_v1.schema.json`.

## Required committed package

A real package lives under `data/backtest_sources/m3_source_package/` and contains:

- `manifest.json` using contract `HISTORICAL_M3_SOURCE_PACKAGE_V1`;
- canonical `sector_routes.csv.gz`;
- canonical `index_closes.csv.gz`;
- every raw source file used by either canonical file;
- deterministic raw-to-canonical transformation code;
- a determinism/reproduction test;
- `SHA256SUMS` covering all and only those evidence files.

An Actions artifact is not durable source evidence. A URL and a hash without a
committed raw payload are also insufficient for this two-reviewer workflow. An
`OPEN` package may record such a gap, but a `CLOSED` package may not.

## Canonical schemas

`sector_routes.csv.gz` has exactly these columns, in this order:

```text
ticker,valid_from,valid_to,sector_index_code,source_id
```

Intervals are half-open: `[valid_from, valid_to)`. A blank `valid_to` is open
ended. Intervals for the same ticker cannot overlap, and an open-ended interval
cannot be followed by another interval. For each one of the 6,000 historical
monthly membership rows there must be exactly one applicable route. The route
ticker union must equal the historical membership ticker union. `XU100` is the
market factor and is forbidden as a missing-sector fallback.

`index_closes.csv.gz` has exactly these columns, in this order:

```text
index_code,trade_date,close,source_id
```

`(index_code, trade_date)` is unique. Closes are positive and finite. Dates must
belong to the locked trading calendar and stay inside the declared coverage
window. The code set must equal XU100 plus the sector indices actually selected
by the historical routes; unrelated current-state indices are rejected.

## Hard production coverage

The validator's production defaults are code-level constraints, not values that a
manifest can weaken:

- signal months: 60 consecutive months, `2021-08` through `2026-07`;
- members per signal: exactly 100;
- membership-route rows checked: exactly 6,000;
- market index: `XU100`;
- beta lookback: 252 trading days;
- trailing-alpha window: 63 trading days.

For every signal month and every index required by that month's routes, every
daily close in the 252-trading-day lookback plus the signal endpoint must exist.
This forces the first real price coverage to begin roughly in July 2020. Missing
closes are errors; they are not forward-filled, fetched from current state, or
replaced with XU100.

## Provenance and reproducibility

Every raw source record contains:

- a unique `source_id` used by canonical rows;
- publisher;
- public HTTPS source URL;
- stable artifact/document identity;
- timezone-aware retrieval timestamp no later than package assembly;
- committed raw path;
- lowercase SHA256 of the raw bytes.

Every canonical `source_id` must map to the raw-source registry, and every
registered raw source must actually contribute canonical rows. Canonical files,
raw files, transformation entrypoint and determinism test are independently hash
locked. Repository-relative paths cannot escape the repository and symlinks are
not accepted as evidence files.

The committed reproduction command must run the determinism test with `pytest`.
The real-data PR must demonstrate that rebuilding from committed raw inputs
produces byte-identical canonical outputs. The validator does not execute network
downloads and therefore does not pretend to prove URL origin by itself.

## Fail-closed status rule

`package_status: CLOSED` is accepted only when every structural, hash, lineage,
coverage, calendar and reproducibility gate passes. `package_status: OPEN` remains
effectively open even if all currently present files validate. If a raw source is
not committed, the package reports `RAW_SOURCE_NOT_COMMITTED:<source_id>` and
cannot become CLOSED.

Merged PR #15 supplies and independently reproduces the package, so the
M3 source component is now **CLOSED**. The full backtest remains **NOT READY**
because separate PIT modules and the real cutoff policy are still open.

## Committed real package

Merged PR #15 adds a `CLOSED` package with:

- 210 half-open route rows covering the exact 209-ticker historical membership
  union;
- 7,415 official daily closes: 1,483 common trading days for each of `XU100`,
  `XUSIN`, `XUHIZ`, `XUMAL`, and `XUTEK` from 2020-07-27 through 2026-07-01;
- seven committed direct raw sources (KAP sector classification, KAP notification
  1331451, and five Borsa Istanbul index-graphic payloads);
- a committed Borsa Istanbul announcement archive audit showing that `GRTHO` is
  the only historical-member ticker affected by a sector-change announcement in
  the archive;
- a committed KAP broad-index snapshot audit proving that 207/209 current ticker
  descendants agree with the top-sector mapping; the two non-index snapshot gaps
  (`KONTR`, `TRILC`) are still resolved by their committed KAP sector classes;
- deterministic gzip output (`mtime=0`) and a byte-identical reproduction test.

The two large KAP page snapshots are stored in deterministic gzip containers;
decompression restores the exact fetched HTML bytes. Direct-source and canonical
container hashes are locked in the manifest and `SHA256SUMS`; audit-container
hashes are locked by the hash-pinned transformation code.

KAP top-level sectors are mapped to the four long-running broad BIST sector
indices. The mapping is deterministic and agrees with the current broad-index
membership snapshot. The only in-window exception is GRAINTURK HOLDING: KAP
notification 1331451 moves it from `XUHIZ` to `XUMAL` effective 2024-09-09.
The notification names the then-current ticker `GRTRK`; the canonical `GRTHO`
identity is explicitly tied to Borsa Istanbul's official 2024-10-01 ticker-code
change through hash-locked CSV and provenance files.

The actual manifest and executable test are authoritative: the M3 source
component is `CLOSED`; the full V24 backtest remains **NOT READY** for unrelated
open gates.
