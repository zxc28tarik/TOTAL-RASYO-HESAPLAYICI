# W2 — Frozen CORE / Total correction

Date: 2026-09-13. Source audit head:
`9b4d8fe64bb05e32786c9f469696eb1d6cf5fd59`.

## Result

The stale CORE defect established by the W2 provenance audit is corrected.
The correction used the six previously verified local KAP archives, the original
CORE clock `2026-09-08T20:50:35.449423+00:00`, the unchanged economic-route
evidence, and the current flow derivation. It did not fetch market data and is
not represented as a new live capture.

| Ticker | Before Total / 100 | Corrected Total / 100 | Decision | Rank |
|---|---:|---:|---|---:|
| RGYAS | 46.2949460644 | 46.6021011290 | UZAK | 1 |
| TABGD | 40.7156025426 | 43.9061751217 | UZAK | 2 |

Coverage remains 131 CORE, 48 M2, 11 Ek9, 2 Total and 805 explicit
rejections across the 807-row universe. Ranking order and both decisions are
unchanged. No weights, veto behavior, peer/coverage threshold, universe rule,
neutral-fill behavior, or production formula changed.

## Preserved evidence

`data/audit/w2_current_correction_v1/pre_correction/` preserves all eight
pre-correction CORE, Total and old run-receipt files. Its manifest binds their
LF-canonical content hashes to the W2 audited baseline. In particular, the
September 8 `CURRENT_TOTAL_RASYO_RUN_V1` receipt remains intact there with
M2=0 and Total=0; it was not relabeled.

The active `data/live/current_total_rasyo_run_v1/receipt.json` is now a
`CURRENT_TOTAL_RASYO_CORRECTION_ASSEMBLY_V1` receipt. It binds the unchanged
input component receipts, corrected CORE and Total receipts, preserved
snapshot, exact counts, root cause, correction clock, and the explicit
`new_market_capture=false` declaration.

The correction receipt is
`data/audit/w2_current_correction_v1/receipt.json`. Text artifact hashes use
`LF_CANONICAL_SHA256_V1` so Windows and Linux checkouts verify the same
content.

## Reproduction and gates

```text
python scripts/materialize_w2_current_correction.py --check
python scripts/audit_w1_frozen_outputs.py --output w1-after-w2-check.json
python -m pytest -q tests/test_w2_current_correction.py tests/test_w2_current_provenance.py tests/test_current_live_artifacts.py tests/test_current_nonfin_follow.py tests/test_total_rasyo_score.py
```

The correction script is idempotent after application: another `--apply`
performs verification instead of silently rematerializing or changing the
clock. Its check enforces:

- every pre-correction snapshot hash;
- every corrected live artifact hash;
- unchanged non-target live inputs from the W2 baseline;
- old CORE reproduction with the original generator;
- corrected CORE equality to the audited 131-row current-code replay;
- an independent Total/ranking/rejection rematerialization;
- every component binding in the new assembly receipt;
- zero model, threshold, veto, universe, or neutral-fill changes.

The W1 frozen-output control now reads the intentionally changed files from the
preserved pre-correction snapshot. This retains the original W1 claim without
pretending W2's authorized correction never happened. W5 and all historical
coverage work remain unstarted.

Implementation head `6a73096` passed all six required GitHub CI workflows:
PIT Total, KAP Semantic, Real Sector M2, Real Data, and both push/PR integration
audits. W2 is accepted as `DONE`; this does not start or claim W5.
