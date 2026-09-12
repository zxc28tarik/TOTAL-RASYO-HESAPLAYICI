# W2 — Current source and W1 impact audit

2026-09-13. Start: `f3731ceb47e666f483474f97fe47be1d69ec1cc1`.
Scope: audit only. Production code, config, data/live and historical scores
were not changed. This is not a fresh market capture or deployment.

## Decision

**The W1 fixes did not change any of the 61 target results. Nevertheless,
the two stored Total results do not match today's CORE derivation code.**

48 M2 and 11 Ek9 rows: `UNAFFECTED`, source-reproduced.
Two Total rows: `UNRESOLVED` for *current-engine consistency*, with their
original provenance now fully reproduced and the discrepancy explained.
They are not untraceable or secretly contaminated by W1; their M1/Ek1 inputs
are stale after an earlier derivation fix. No clean-current-score approval.

| Ticker | Stored Total / 100 | Same frozen inputs, current CORE code | W1-only change | Decision |
|---|---:|---:|---:|---|
| RGYAS | 46.2949460644 | 46.6021011290 | 0 | UZAK → UZAK |
| TABGD | 40.7156025426 | 43.9061751217 | 0 | UZAK → UZAK |

Candidate scores are audit-only, not promoted to data/live. Ordering remains
RGYAS then TABGD. Coverage is not expanded or reduced; the 807-row universe,
48 M2, 11 Ek9, 2 stored totals and 805 stored rejections remain untouched.

## Established cause, not a hypothesis

1. CORE was generated at `2026-09-08T20:50:35.449423+00:00`, with generator
   commit `e93b7c53e5b85f3555a652982657e67fa54ecc85`.
2. `fad20cccc88f230628999c1a06770c0f7329a12c`, dated September 10, corrected
   flow selection: explicit quarter contexts are preferred; a quarter value
   is not reduced by a cumulative prior; YTD subtraction requires matching
   period starts. That commit regenerated NONFIN valuation/FOLLOW/M2 and
   Total but **did not regenerate CORE**.
3. Current code, the original CORE clock, and the same verified primary ZIP
   bytes produce different M1/Ek1/good_count inputs in **130/131 CORE rows**.
4. A separate replay restores only the original derivation functions in an
   isolated process, checks that the dataclass contracts are unchanged, and
   uses the same primary reports, route evidence and clock. **131/131 rows
   match the stored CORE artifact**, with no remaining difference.
5. M2/FOLLOW generation at `2026-09-10T20:07:33.673312+00:00` and Total at
   `2026-09-10T20:29:06.346647+00:00` were assembled with that older CORE.
   W1 occurred later, so it cannot be the origin of this discrepancy.

Detailed changed CORE fields are in `phases.json`; each Total row includes
its old/new M1, Ek1 and good_count, original generator commit, market inputs,
W1 phase results and diagnostic corrected Total.

## Evidence and reproduction

- [baseline.json](../data/audit/w2_current_provenance_v1/baseline.json):
  immutable W2 input inventory linked to the W1 pre-change baseline. Every
  tracked live input is pinned. Only recorded CRLF/LF checkout differences
  are tolerated; content changes fail verification.
- [primary_replay.json.gz](../data/audit/w2_current_provenance_v1/primary_replay.json.gz):
  849 primary reports from six SHA256-verified local KAP ZIPs; 20,580 consumed
  facts, exact report/member hashes, disclosure IDs, publication dates,
  contexts, quarter lineage, TTM snapshots, peer sets and CORE diagnostics.
  All 77 current valuations, 77 previous valuations, 48 FOLLOW and 48 M2
  match stored outputs within the declared finite floating-point tolerance.
- [original_core_replay.json.gz](../data/audit/w2_current_provenance_v1/original_core_replay.json.gz):
  original derivation source/commit/hash, 14,715 original consumed facts,
  original CORE lineage and the matching 131-row regeneration.
- [rows.jsonl](../data/audit/w2_current_provenance_v1/rows.jsonl): every one
  of 48 M2 + 11 Ek9 + 2 Total rows has status, before/after, reasons and source
  references. M2 includes current/prior bands and peers, exact FOLLOW price
  dates/values, certified nominal-unit denominator and action-bundle hashes.
  Ek9 includes its selected stock observations, window and source capture.
- [phases.json](../data/audit/w2_current_provenance_v1/phases.json): separate
  original/W1-A/W1-B/W1-C checks and transitive local import dependencies.
- [receipt.json](../data/audit/w2_current_provenance_v1/receipt.json): all
  output hashes, source Git blobs, counts, cause and explicit non-acceptance
  of the stale Total artifacts as current-engine output.

```text
python scripts/audit_w2_current_provenance.py --primary-replay
python scripts/audit_w2_original_core.py
python scripts/audit_w2_current_provenance.py --assemble
python scripts/audit_w2_current_provenance.py --check
python -m pytest -q tests/test_w2_current_provenance.py tests/test_current_nonfin_follow.py tests/test_current_live_artifacts.py
```

No network source acquisition is needed. Primary ZIP remapping was performed
locally because those six original ZIPs are deliberately not in Git. CI must
not claim to repeat primary-byte remapping: it verifies the committed evidence
hashes and identities, rebuilds valuation/FOLLOW/M2 from quarter/peer evidence,
and reruns original/current Ek9 and M3/Ek4 from repository price observations.
The separate local verification pass uses a temporary output directory and
compares all 61 reconstructed rows. This is a same-agent second pass with
isolated processes and adversarial tests, not an independent agent or Claude
approval. Finite float comparisons use relative/absolute tolerance 1e-12;
identities, key sets, missingness, classifications and source hashes are exact.

## W1 phase interpretation

W1 A/B/C shipped together in `7132392`. No separate contemporaneous phase
artifacts existed; these are explicitly **retrospective isolated replays**,
not invented historical checkpoints. The current M2/M3 producers do not
import the DB daily pipeline. The only changed files reachable by current
materializers are the Ek9 arithmetic helper and historical replay wrapper.
Original helper+wrapper are loaded from the pinned pre-W1 Git commit for
baseline/A/B; current helper+wrapper are used for C. Every phase retains
11 Ek9 scores and 140 explicit Ek9 rejections. Every target Total is unchanged
by W1 when its actually-stored other module inputs are held fixed.

## Receipt chronology and remaining limits

`current_total_rasyo_run_v1/receipt.json` still describes the September 8 run
with M2=0/Total=0, while the September 10 component receipts describe 48/2.
It is preserved as an old-run receipt, never relabeled as a successful new
capture. There is no single fresh run covering all current artifacts.

Raw KAP publication/member binding, dated economic routes and certified current
quote units are retained. These results do not establish historical NAV,
superseded-version completeness or historical M2. Fixed semantic `mapped_at`
metadata in the experimental mapper is not treated as a real ingestion clock.
The unused issued-capital/1 share field remains diagnostic: current valuation
uses independently certified quoted nominal units, not assumed legal shares.

## Required correction before clean W2 closure

Reason: `CORE_ARTIFACT_NOT_REGENERATED_AFTER_PRE_W1_FLOW_DERIVATION_FIX`.
Evidence paths have been exhausted for this discrepancy: same primary archives,
publication cutoffs, source/peer lineages, original generation commit and
current generation have all been replayed. This is **not a paid-data blocker**.

The audit request does not authorize silently replacing published current
results. W2 therefore remains BLOCKED for clean-current acceptance until the
following narrowly scoped correction is authorized and completed:

1. Preserve current CORE/Total/receipt artifacts as the pre-correction snapshot.
2. Regenerate CORE with the corrected flow derivation, the same frozen inputs
   and recorded clock. Label it a correction, not a new live capture.
3. Regenerate Total/ranking from that CORE and the unchanged verified remaining
   modules; preserve all universe/rejection rows and all model rules.
4. Bind the corrected component receipts in a current-assembly receipt without
   overwriting or mislabeling the September 8 run evidence.
5. Verify rows, source/score hashes, full regression, final-head CI and update
   Issue #37/this ledger. Only then grant clean W2 acceptance and start W5.
