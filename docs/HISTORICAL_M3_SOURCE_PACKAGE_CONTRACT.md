# Historical M3 Source Package Contract V1

## Status

**CONTRACT IMPLEMENTED; REAL SOURCE PACKAGE OPEN.**

This contract does not claim that the 60-month M3 source data exists. It defines
the gates that real historical sector routing and daily XU100/sector-index closes
must pass before the package can be labelled `CLOSED` or consumed by a READY
backtest.

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

Until a separate real-data PR supplies and independently reproduces the full
package, V24 real M3 source coverage remains **OPEN** and the backtest remains
**NOT READY**.
