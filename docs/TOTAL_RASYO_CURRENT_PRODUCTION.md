# Total Rasyo — Current Production Snapshot

Status: PRODUCTION SNAPSHOT CANDIDATE

This document identifies the current working Total Rasyo product code separately from historical 5-year backtest experiments.

## Production baseline

- Repository: `zxc28tarik/letsmakemoney`
- Baseline branch: `main`
- Baseline code commit before this snapshot wrapper: `51d1acc95b2f03caec52c486c4fbe31d0f3a8074`
- Main production orchestrator: `src/analytics/total_rasyo_orchestrator.py`
- Daily/module production pipeline: `src/analytics/run_daily_pipeline.py`
- Sector-specific production engines and persistence remain part of the normal repository tree.
- PostgreSQL schema/migrations through V24-F are part of the baseline.

## What is production code

The current product is the live/current Total Rasyo system: official-data ingestion, PIT-aware financial/market processing, sector routing and M2 engines, M1/M3/Ek modules, Total Rasyo combination/orchestration, persistence, run registry, status taxonomy, lineage and reconciliation safeguards.

## What is NOT promoted as production in this snapshot

The `v24-real-data-work` branch is an experimental validation/backtest branch. Its 5-year BIST100 reconstruction, historical price discovery, Yahoo/Mynet/Investing/MarketScreener/Ekofin audits, monthly portfolio event experiments, and related real-data workflows are intentionally NOT merged into this production snapshot.

The V24-G readiness layer is also a backtest-readiness extension. It may remain isolated from the production snapshot unless explicitly promoted later.

## Validation rule

The production snapshot is accepted only after GitHub Actions runs the full repository regression and the dedicated BANK v4.7 regression on the exact staging commit. The production snapshot branch must be fast-forwarded only after that run succeeds.

## Branch policy

- `main`: canonical current product line.
- `total-rasyo-production-current`: immutable-style named pointer to the last accepted production snapshot.
- `total-rasyo-production-staging`: temporary validation branch for the next production snapshot.
- `v24-real-data-work`: separate backtest/research work; not production.
