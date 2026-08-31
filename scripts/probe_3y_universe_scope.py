#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "data/backtest_sources/yahoo_resolved/monthly_member_signal_price_coverage.csv"
START_MONTH = "2023-08"
END_MONTH = "2026-07"
EXPECTED_MONTHS = 36
EXPECTED_MEMBERS = 100


def main() -> None:
    frame = pd.read_csv(MATRIX, dtype=str, keep_default_na=False)
    scope = frame.loc[(frame["month"] >= START_MONTH) & (frame["month"] <= END_MONTH)].copy()
    months = scope["month"].drop_duplicates().tolist()
    if len(months) != EXPECTED_MONTHS:
        raise RuntimeError(f"expected 36 months, got {len(months)}")
    counts = scope.groupby("month")["ticker"].nunique()
    if not counts.eq(EXPECTED_MEMBERS).all():
        raise RuntimeError(f"month member-count drift: {counts[counts != EXPECTED_MEMBERS].to_dict()}")
    if len(scope) != EXPECTED_MONTHS * EXPECTED_MEMBERS:
        raise RuntimeError(f"expected 3600 rows, got {len(scope)}")
    tickers = sorted(scope["ticker"].unique())
    result = {
        "contract": "V24_EXPERIMENTAL_3Y_SCOPE_PROBE_V1",
        "performance_window": {"start_month": START_MONTH, "end_month": END_MONTH, "months": len(months)},
        "month_ticker_cells": len(scope),
        "unique_historical_tickers": len(tickers),
        "tickers": tickers,
        "note": "Performance window only. Financial source lookback may extend earlier for module period-depth requirements.",
    }
    out = ROOT / "artifacts" / "three_year_scope.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"THREE_YEAR_SCOPE_OK months={len(months)} cells={len(scope)} "
        f"unique_tickers={len(tickers)} start={months[0]} end={months[-1]}"
    )
    print("TICKERS=" + ",".join(tickers))


if __name__ == "__main__":
    main()
