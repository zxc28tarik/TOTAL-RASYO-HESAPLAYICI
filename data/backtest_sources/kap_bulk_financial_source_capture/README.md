# KAP bulk financial source capture — preservation record

This directory preserves the identity and audit metadata of the real KAP bulk financial source capture acquired on 2026-08-31 for the V24 60-cutoff historical work.

## Captured source

- Contract: `KAP_BULK_FINANCIAL_SOURCE_PACKAGE_V1`
- Archive contract: `KAP_BULK_FINANCIAL_EXPORT_ARCHIVES_V1`
- Period window: 2019Q3 through 2026Q2
- Archive count: 28
- Total archive members / unique notification ids: 16,624
- Target historical reports: 5,489
- Target historical tickers: 209
- Total compressed bytes represented by the archive manifest: 605,848,102
- Total uncompressed bytes represented by the archive manifest: 20,911,951,404
- Acquisition method: `PUBLIC_KAP_FINANSAL_TABLOLAR_UI_COMPANY_BLANK_YEAR_PERIOD_DOWNLOAD`
- Source page: https://kap.org.tr/tr
- Source identity SHA256: `4c1c564b77a451e493cb6b2257dd00b280f8d80f60495636353e297b8910102f`

## Why the raw ZIPs are not committed here

The project rulebook forbids placing large reproducible raw downloads directly in normal Git history. The original capture also recorded `raw_archives_committed_to_git=false`. The exact filename, byte size, member count and SHA256 for every one of the 28 raw KAP ZIPs is therefore preserved in `archive_manifest.json`. A re-acquired archive must match that hash before it can be treated as the same source.

The source-package component hashes known at capture time are preserved in `SHA256SUMS`. Some gzip catalog/inventory components are not present in the current ChatGPT file context; their hashes are preserved so a recovered copy can be verified bit-for-bit.

## PIT status

This capture is **not** authoritative PIT materialization yet.

Blocker: `SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED`

The capture-date bulk exports expose one latest workbook per issuer/report-period/statement-scope. They do not prove that every superseded pre-correction notification version has been enumerated. Therefore:

- `historical_version_enumeration_complete=false`
- `pit_materialization_authorized=false`
- `real_60_cutoff_scoring_authorized=false`

An explicitly risk-accepted experimental inventory found source presence for 5,997 of the 6,000 month+ticker cells and 3 not-found cells, but that does not authorize a real PIT performance claim.

## Integrity

The three recovered text records were reconstructed from the preserved source files and verified against the original package checksums before this Git commit:

- `archive_manifest.json` — `066efe98ffa6ebd67a2f529dcaceceadeedbf99df39c94b2d16dd1211907cd26`
- `summary.json` — `a779182ed39c7e4256836bca44fbb9c344e6a4858db04b643673fbd618845197`
- `real_parser_probes.json` — `929438b46d312da895b28a025027622fcdef439323fdbfac2a1d40b487fb3e73`

Do not silently replace these records with a newer capture. A newer capture must use a new identity/hash and document the relationship to this package.
