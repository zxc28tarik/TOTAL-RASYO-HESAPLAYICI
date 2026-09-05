# Astra price-level v2 second pass — 2026-09-05

Baseline: `91430ffcd4718fbbbcb853592a54d129329c1add`, branch `codex/astra-v24-finalize`, draft PR #40.
User authorization in `ASTRA_EXECUTION_AUTHORIZATION.md` applies. This is a second pass by the same model, not an independent Claude review.

## Code boundary

All five production family price queries select raw `close`. Their snapshot builders require a hash-verified action bundle and normalize source-date shares to the price date before calling the unchanged valuation math. Five historical replay wrappers use the same boundary and retain evidence receipts in score inputs. Rejected tickers' follow contexts are excluded from evaluation, so missing proof creates explicit rejections instead of crashing the replay.

Use `config/<family>_valuation.raw_close_pit_v2.json` with the corresponding batch command and `--action-bundle-index <index.json>`. Existing v1 config files remain historical artifacts. The four NAV/metrics source adapters require `DATED_UNADJUSTED_SHARES_V1`; normalized snapshots use `POINT_IN_TIME_MARKET_CLOSE_SHARES_V1`. NONFIN derives the dated share count from the anchor balance-sheet period; the proof independently binds the exact count, date and unadjusted basis. No arbitrary conversion factor is accepted.

The bundle index contract is `PRICE_LEVEL_ACTION_BUNDLE_INDEX_V1`, with one entry per ticker for one batch/cutoff. Each entry declares `ticker`, `manifest_path`, `manifest_sha256`, `events_path`, `events_sha256`, and `sources` (`source_ref`, `path`, `sha256`). Paths must stay within the bundle directory. Each manifest uses `PRICE_LEVEL_ACTION_COVERAGE_V1`; publication, effective dates, event inventory and original bytes are validated at consumption. See `price_level_action_evidence.py` for the schema. A matching hash proves identity, not the factual completeness of a source. The collector/reviewer must establish exhaustive coverage; no real completeness manifest was manufactured during this work. Rights/paid-capital events remain fail-closed without an adequate economic contract.

Returns, alpha/beta, BANK valuation, ranking weights and veto arithmetic were not changed. Tests' synthetic proof helper is confined to `tests/`; no production audit imports it.

## Real evidence

All 12 official Borsa Istanbul THB ZIPs were reacquired and match their original catalog hashes. `data/backtest_sources/p2_raw_close_pit_v2/` retains the original bytes and acquisition inventory. The v2 materializer validates both ZIP and CSV-member SHA256, canonical key and historical cutoff, then uses raw close plus normalized shares. The legacy adjustment-factor materializer can no longer succeed. Historical receipt labels remain readable.

Two independent real P2 runs agree byte-for-byte: all 12 are explicitly rejected with `ACTION_COMPLETENESS_EVIDENCE_MISSING`. Their raw prices are proven; dated share/action completeness is not. The former 993/981 result was not recalculated and is not promoted to 993/993. Synthetic positive unit tests are not real-data materialization evidence.

Five original gzip catalogs remain absent. `original_catalog_search.json` records exact paths/hashes, reachable history searches, metadata for 67 Actions artifacts, public release asset names, and the two inspected bulk receipt ZIPs. It does not claim every artifact payload was inspected. The acquisition receipts contain no missing catalogs and confirm historical raw-archive mismatches; current snapshots were not relabeled. Original manifests, checksums, raw captures and the hash-locked M3 determinism test remain byte-identical.

P3 source preflight independently reconstructs the 60 x 100 historical membership keys twice, verifies exact uniqueness and accounts for all 6000 in `p3_source_preflight_cells.csv`. Each is explicitly blocked at the missing original financial catalog boundary. This is not completed P3 materialization. There are no ready cells, P4 rankings, P5 portfolio returns or P6 PASS. P7 historical superseded-version authority remains unproven.

## Verification and platform handling

Production mutation audit: 26/26 killed, clean baseline. The ten additional mutants put adjusted close back into each family query or bypass each family's share transform. Targeted P2 tests use the actual 12 ZIPs and clearly synthetic share evidence to exercise positive normalization, missing proof, bad ZIP bytes and deterministic output.

The original M3 test is hash-locked by its manifest. Full discovery uses its portable successor containing all original assertions plus same-runtime compressed determinism and canonical decompressed equality. The original file remains executable directly and unchanged; only its duplicate collection is excluded. Linux still checks exact original compressed bytes. Windows explicitly skips that Linux compressor contract and POSIX 0600 mode checks; content, secret handling, source identity and portable path checks run. The integration workflow now includes a Windows Python 3.14 job as well as Linux Python 3.11 / pandas 2.2.3 / PostgreSQL.

Local full regression and exact-head GitHub runs are recorded separately in the validation receipt after completion. #39, #31 and P3–P7 remain open until their data and final validation gates pass. PR #40 must not be merged on the basis of unit tests alone.

Reproduce from the repository root:

```text
python -m scripts.audit_p2_price_level_v2 --output p2-materialization.json
python -m scripts.audit_p3_source_preflight --output-dir preflight
python scripts/audit_price_level_mutations.py --output mutation-results.json
python -X utf8 -m pytest -q
```
