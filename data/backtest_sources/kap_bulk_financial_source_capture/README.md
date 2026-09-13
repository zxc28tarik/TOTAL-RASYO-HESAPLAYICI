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

## 2026-09-02 public re-acquisition evidence

The official KAP bulk-download route was re-acquired by workflow run `33568804543` using acquisition code head `d890a3a996a7da9080f86853df32b276549a5d27`.

The preserved 2026-08-31 archive manifest is intentionally unchanged. Re-acquisition found:

- 28/28 current raw KAP ZIPs downloaded successfully and preserved;
- 28/28 current raw ZIP identities independently re-hashed against the acquisition receipt by the public-evidence workflow;
- 26/28 current ZIPs still match the preserved 2026-08-31 manifest in SHA256, compressed byte size, XLS member count and total uncompressed bytes;
- 2/28 official KAP archives have byte-level drift relative to the preserved snapshot:
  - `KAP_2025_Y.zip`: 762 -> 765 XLS members;
  - `KAP_2026_6A.zip`: 753 -> 757 XLS members.

The drift is recorded rather than normalized away. The exact current raw bytes, observed SHA256 list and acquisition receipt are publicly downloadable from GitHub prerelease tag `kap-bulk-byte-evidence-run-33568804543`, targeted at evidence commit `2a624a3019599c3f16f7f7351e608dfdc5b048d8`.

Machine-readable committed evidence is under `public_byte_evidence/`:

- `run_33568804543.json`
- `SHA256SUMS.run_33568804543.observed`
- `acquisition_receipt.run_33568804543.json`

This evidence closes the raw-byte accessibility gap for independent reviewers: they can now download the exact 28 current ZIP assets and hash them without GitHub Actions authentication. It does **not** make the newer two drifted ZIPs identical to the 2026-08-31 snapshot.

The missing historical gzip report catalogs referenced by `SHA256SUMS` have not been recovered here, so the exact workbook-id membership delta inside the two drifted archives is not asserted.

## PIT status

This capture is **not** authoritative PIT materialization yet.

Blocker: `SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED`

The capture-date bulk exports expose one latest workbook per issuer/report-period/statement-scope. They do not prove that every superseded pre-correction notification version has been enumerated. Therefore:

- `historical_version_enumeration_complete=false`
- `semantic_mapping_authorized=false` for the new public-byte evidence by itself
- `pit_materialization_authorized=false`
- `real_60_cutoff_scoring_authorized=false`

An explicitly risk-accepted experimental inventory found source presence for 5,997 of the 6,000 month+ticker cells and 3 not-found cells, but that does not authorize a real PIT performance claim.

## Integrity

The three recovered text records were reconstructed from the preserved source files and verified against the original package checksums before this Git commit:

- `archive_manifest.json` — `066efe98ffa6ebd67a2f529dcaceceadeedbf99df39c94b2d16dd1211907cd26`
- `summary.json` — `a779182ed39c7e4256836bca44fbb9c344e6a4858db04b643673fbd618845197`
- `real_parser_probes.json` — `929438b46d312da895b28a025027622fcdef439323fdbfac2a1d40b487fb3e73`

Do not silently replace these records with a newer capture. A newer capture must use a new identity/hash and document the relationship to this package.
