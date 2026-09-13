# Data recovery second pass

Starting point: `50c3b40691ef9dbabbd588fd95255f1f5207ee52`, PR #40, branch `codex/astra-v24-finalize`. Existing valuation engines, weights, return paths and original manifests were not changed.

## Original catalog recovery

No original catalog was recovered. The new bounded search inspected all 3094 local Git objects and hashed all 1078 blobs, including both unreachable blobs and reflog history. It inspected all 71 currently available Actions artifacts: 6164 member entries, 6134 bounded member hashes, and 30 explicitly listed oversized members left unhashed. It also scanned 31 workflow log archives from the original capture window, the central directories of all 28 release ZIPs, relevant local project copies and 8 Downloads packages (1303 member hashes).

The catalog producer was not found in historical Python blobs. The capture metadata preservation commit already described the catalogs as unavailable in its file context. Only 26/28 reacquired raw archives match the original snapshot, and original producer/version, ordering, serialization and gzip settings are unknown. A deterministic original reconstruction cannot be claimed. No current snapshot was substituted and no historical checksum was rewritten.

Exact five paths/hashes, inspected scopes, skipped members, acquisition lineage and reproduction constraints: `data/audit/catalog_recovery_v3/recovery_decision.json` and adjacent receipts. The reusable artifact scanner restores a file only when its bytes match a recorded original SHA256; tests cover nested recovery and wrong-hash rejection.

## Real dated-share sources

Six real KAP reports now establish candidate dated shares for the 12 P2 cells:

| Ticker | Dated shares | Source disclosure IDs |
|---|---:|---|
| INVES | 187500000 | 1027019, 1052635 |
| KLRHO | 650000000 | 1079063, 1124175, 1150263 |
| ASGYO | 659000000 | 1210998 |

The audit checks original XLS bytes, report period, publication timestamp and capital presentation units. KLRHO's raw 650000 is presented in thousands of TRY, yielding 650 million TRY; PDF note page references document the 1 TRY nominal share definition. The reviewed PDF extraction is bound to the original PDF source hash. The received attachments use a Java serialized byte-array envelope; both envelope and decoded PDF hashes are retained.

The 41 bulky raw captures are preserved in a deterministic 11.2 MB ZIP, with original member names/hashes and original KAP archive/member lineage. Compact query requests/responses remain readable. The offline audit validates original source bytes and all query request/response hashes, then independently selects candidate source periods published before each cutoff. This is an experimental visible-version reconstruction, not proof that these were the original 993-package selections or that superseded reports were exhaustively enumerated.

## Completeness remains unproven

The official monthly corporate-action calendar returns an empty array for June 2023, although real KLRHO notification 1158799 records a June capital action. Empty calendar results therefore cannot certify absence. The detailed official byCriteria endpoint returned INVES 28, KLRHO 112 and ASGYO 94 notifications. Repeated full queries were byte-identical and monthly ID unions matched, establishing observed query consistency. This does not prove historical deletion/version retention or historical as-of completeness. Acquisition timestamps in 2026 were not backdated as publication evidence. Earlier announcements that become effective after the source share date remain another coverage gap.

Two independent calls of the existing v2 materializer now use the real dated-share candidates for all 12 cells. They still produce 12 explicit `ACTION_COMPLETENESS_EVIDENCE_MISSING` rejections. No runtime completeness bundle was manufactured. Receipt: `data/backtest_sources/p2_action_research_v1/dated_share_replay_receipt.json`.

The original 993 candidate/source package and its producer remain unavailable; the prior 981 usable summary is not a fresh replay. P3's global source blocker is still present, so P4/P5/P6 and P7 cannot be marked completed. #39 remains open. This research narrows the missing evidence to action enumeration for the 12 reconstructed candidates; it does not certify the full historical pipeline.

## Reproduction and review

```text
python -m scripts.audit_p2_action_research_v1
python -m pytest -q tests/test_p2_action_research.py tests/test_catalog_artifact_search.py
python scripts/search_original_catalog_artifacts.py
python -m scripts.research_p2_action_sources_v1 --output-dir private/new-action-capture
```

Network research results are time-dependent; reruns use a new directory instead of overwriting preserved captures. Local full-suite and targeted test receipts plus current-head GitHub CI are recorded separately. Source gathering was delegated in parallel at the user's request; the parent checked the package, removed the undeclared lxml dependency, bound the PDF extraction to source hashes and independently reran the offline audit. This is not an authoritative historical-data PASS or a completed P6 audit.
