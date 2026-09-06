# Reconstructed experimental KAP report index

Contract: `RECONSTRUCTED_SOURCE_CATALOG_V1`; profile: `EXPERIMENTAL_RISK_ACCEPTED_5Y`.

This is newly generated metadata, not recovered original catalog bytes. The five missing historical gzip checksums and immutable archive manifest remain unchanged. Their absence remains the explicit `ORIGINAL_CATALOG_BYTES_UNAVAILABLE` risk; superseded historical versions remain unenumerated. The classification receipt separates derived report/source-presence indexes from enumeration evidence. It documents that the missing files themselves cannot be directly inspected.

The generator downloads/reuses all 28 preserved public release ZIPs, hashes each one, and parses only 26 matching original immutable hashes. It reads every accepted member, hashes its complete bytes, and uses the existing versioned KAP report parser for source entity, report period, scope, publication, currency and scale. Technical roles are diagnostic taxonomy identities, not a current sector inference. No financial values or current aliases are filled by this index.

`reports.jsonl.gz` contains accepted report metadata. `parse_errors.jsonl.gz` preserves rejected member lineage. `archive_inventory.json` explicitly quarantines the two drifted archives. `drift_dependency_metadata.jsonl.gz` separately parses observed replacement headers for possible dependency diagnosis: those rows are not accepted sources and do not assert original publication/member identity. Observed replacement publication cannot prove the original publication; downstream use must preserve that limitation.

Run from repository root:

```text
python scripts/reconstruct_experimental_kap_catalog.py --acquire --output-dir data/backtest_sources/reconstructed_experimental_kap_v1
python scripts/reconstruct_experimental_kap_catalog.py --output-dir private/reconstructed_kap_independent_build
```

Large raw ZIPs remain under `private/reconstructed_kap_archives`; acquisition URLs are preserved in `data/audit/catalog_recovery_v3/release_archive_contents.json`. Their original hashes remain in `data/backtest_sources/kap_bulk_financial_source_capture/archive_manifest.json`. Downstream financial parsing must reopen exact archive/member bytes and verify these identities. The compressed index is canonical sorted-key JSONL and deterministic gzip under the same runtime; original capture serializer identity is not claimed.
