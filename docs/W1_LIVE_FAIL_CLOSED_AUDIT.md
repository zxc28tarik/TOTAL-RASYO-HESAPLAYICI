# W1 — Live M2/M3/Ek9 fail-closed audit

Date: 2026-09-12. Scope: W1 only; W2 financial provenance and W5 historical
SMRTG are not started. Final acceptance is gated by exact-head CI in Issue #37.

## Immutable starting point

Production baseline: `0db6cc19a357892afdd43b2fa103968d30ce1825`.
Baseline-only commit: `8d38aa5ca48799b0bd164559319ab78fc6de16c1` (pushed before
production edits). [baseline.json](../data/audit/w1_live_fail_closed_v1/baseline.json)
SHA256: `de89e23b117f785918fb4ca5912d96c51ff64995e6fb4b0f9523026f206289a6`.
The capture refuses overwrite. Its nullability inventory is versioned DDL,
not a claim that a production database was inspected.

## Reachable defects and repairs

| Path | Reproduced defect | W1 behavior |
|---|---|---|
| `m2_period_comparison.m2_final` → M2 reader | Nullable score became `0.5` | Missing/invalid stays missing; source identity retained |
| `alpha_trailing.alpha_score` → M3 reader | Nullable score became `0.5` | Missing/invalid stays missing |
| Stock prices → live Ek9 → shared volatility scorer | Partial/undefined returns could become zero volatility and score 1 | Complete finite window required; explicit per-ticker rejection |
| Daily finalizer → `module_scores` | No persistent module-specific rejection diagnostics | NULL Total + `YETERSIZ_VERI` + nullable JSONB `module_rejections` |

Callers include `src/app/cli.py` run-daily and
`src/analytics/backtest_runner.py`. Tests run the real daily orchestration,
M2/M3 readers, finalizer and persistence adapter, stubbing unrelated producers.
This establishes reachability; it does not establish contamination of current
file-based artifacts, whose call path is separate.

M2 sources inventoried: period comparison, NONFIN, HOLDING, GYO, INSURANCE,
FINANCIAL_INSTITUTION and BANK score tables. The period field is nullable;
specialized fields are NOT NULL in versioned DDL. Their priority, analysis
cutoffs, source labels and score-input JSON are unchanged. An invalid overriding
row cannot revive a lower-priority period score.

Actual numeric scores including 0, 0.5 and 1 are preserved. Missing, nonfinite,
boolean and out-of-domain values are not clipped or fabricated into scores.
The finalizer retains all universe rows. SQL migration
`043_module_score_rejections.sql` is additive/idempotent and included in
`make core migrate`; existing rows keep NULL diagnostics, without invented
provenance. Deployments must apply migration 043 before using the updated
daily pipeline. No production database deployment is claimed here.

## Unchanged math and explicit boundaries

- M1 definition, Total weights, veto, peer/coverage thresholds and universe are
  unchanged. W1 is not an audit of every other module's upstream defaults.
- Ek9 preserves the global 65-session gate, selected 64 stock prices / 63
  finite returns, `std(ddof=1)`, cap 0.06 and genuine zero-volatility score 1.
- XU100 provides the observed session dates, not replacement stock prices.
  No interpolation or forward fill occurs. Duplicate/invalid/missing stock
  observations reject the affected stock without removing its universe row.
- Stock/index calendar disagreement rejects the shared window conservatively.
  A corrupt isolated stock date can over-reject otherwise complete series;
  masking it without evidence could instead bridge a real missing session.
  Sessions absent from both sources cannot be discovered from observed data
  alone. No arbitrary freshness rule is introduced.
- The historical replay already enforced complete windows. Its only added
  behavior is explicit rejection if finite returns still overflow the shared
  variance calculation. This is not historical M2/Total coverage gain.

## Verification and artifacts

- Root pre-fix safety tests: 24 failed, 2 passed, 1 skipped. This includes
  missing new diagnostics/finalizer interfaces, not 24 independent defects.
- Ek9 pre-fix tests: 29 failed, 5 passed, observed by the delegated audit.
- Combined W1/Ek9/history/migration targets: 83 passed, 1 PostgreSQL-dependent
  test skipped locally. Actual DB NULL/read/finalize/upsert round-trip is in CI.
- BANK v4.7 locally: 277 passed, 1 expected failure.
- First full local regression: 2062 passed, 234 skipped, 1 failure: the
  migration-inventory test still expected 37 rather than 38 migrations. Its
  count and explicit 043 entry were updated; final full rerun/CI pending.
- [mutations.json](../data/audit/w1_live_fail_closed_v1/mutations.json): 6/6
  production mutations killed, clean test baseline. Mutants run only in
  temporary checkouts with database access disabled.
- [frozen_outputs.json](../data/audit/w1_live_fail_closed_v1/frozen_outputs.json):
  19 tracked frozen live artifacts remain byte-identical locally. Current Ek9
  rerun: 11 valid/140 rejected, same tickers and rejection reasons, maximum
  score difference `4.17e-16` (CSV floating-point round-trip). Current Total
  rerun: identical JSON records for 2 totals/ranks and 805 rejections/807 rows.
  Linux checks also accept **only** CRLF/LF differences against the baseline's
  exact Git blobs, recording them separately rather than claiming byte identity.
- 48 M2/48 FOLLOW are immutable snapshot checks, **not financial re-derivation**.
  No current output file was overwritten; no network data was fetched.

Reproduction:

```text
python scripts/audit_w1_baseline.py --check
python -m pytest -q --junitxml=w1-full.xml
make core migrate  # disposable CI PostgreSQL, including migration 043
make test-bank-v47
python scripts/audit_w1_mutations.py --output w1-mutations.json
python scripts/audit_w1_frozen_outputs.py --output w1-frozen-outputs.json
```

Separate Codex second pass found no material defect in root M2/M3, finalizer,
SQL persistence or migration changes; 63 tests passed/1 DB test skipped in that
bounded review. Root separately reviewed delegated Ek9 changes. This is not a
Claude or cross-model approval. Integration workflows run Linux PostgreSQL,
pandas 2.2.3, Windows, BANK and mutation checks; final-head results are required.

## Handoff to W2, not W2 completion

`current_total_rasyo_run_v1/receipt.json` describes the older September 8
M2=0 run, while newer component/Total artifacts contain 48 M2/2 Total. The
old receipt is preserved and identified, not relabeled as current. W2 must
trace all 48 M2/11 Ek9/2 Total to actual sources and classify per-row impact,
including the chronology of W1 subchanges. A basic frozen-output smoke check
does not close that provenance audit. Historical M2 remains 0.
