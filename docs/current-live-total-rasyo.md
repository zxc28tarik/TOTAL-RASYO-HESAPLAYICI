# Current Total Rasyo pipeline

The current/live path is deliberately separate from the historical PIT backtest.
It never fills a missing module with a neutral score and never turns issued capital
into a share count by assuming `1 TRY = 1 share`.

Run a live refresh:

```powershell
python scripts/run_current_total_rasyo.py --date 2026-09-08
```

Reproduce the same-day run without network access:

```powershell
python scripts/run_current_total_rasyo.py --date 2026-09-08 --offline
```

Offline mode requires a same-date raw-close receipt and verifies the cached KAP
universe hash and row count. The pipeline emits explicit rejection/readiness ledgers
under `data/live/`; a zero M2 or Total count remains zero and is never synthesized.

Market capitalisation is emitted only when KAP supplies explicit per-class nominal
values, the legal share count reconciles, the issuer maps to a single ticker, and the
observed nominal unit is explicitly `1 TRY`. Other quote-unit relationships are
rejected rather than inferred.
