from __future__ import annotations

import pandas as pd

import src.analytics.historical_backtest_signal_dates as signal_dates


def _frame():
    return pd.DataFrame([
        {"index_code": "XU100", "trade_date": "2022-01-04", "open": 100.0, "close": 101.0},
        {"index_code": "XU100", "trade_date": "2022-01-05", "open": 102.0, "close": 103.0},
        {"index_code": "XU100", "trade_date": "2022-02-01", "open": 110.0, "close": 111.0},
    ])


def test_signal_date_manifest_uses_first_observed_index_day_and_leaves_policy_empty(monkeypatch):
    monkeypatch.setattr(signal_dates, "_read_sql", lambda conn, query, params: _frame().copy())

    result = signal_dates.build_historical_signal_date_manifest(
        object(), start_month="2022-01", end_month="2022-02", expected_months=2,
    )

    assert result.status == "COMPLETE_SIGNAL_DATES_POLICY_UNRESOLVED"
    assert result.missing_months == ()
    assert [row["signal_date"] for row in result.rows] == ["2022-01-04", "2022-02-01"]
    assert all(row["cutoff_at"] is None for row in result.rows)
    assert all(row["execution_at"] is None for row in result.rows)
    assert all(row["cutoff_policy_status"] == "UNRESOLVED" for row in result.rows)


def test_signal_date_manifest_reports_missing_month_without_inventing_date(monkeypatch):
    frame = _frame()
    frame = frame[pd.to_datetime(frame["trade_date"]).dt.month == 1].copy()
    monkeypatch.setattr(signal_dates, "_read_sql", lambda conn, query, params: frame)

    result = signal_dates.build_historical_signal_date_manifest(
        object(), start_month="2022-01", end_month="2022-02", expected_months=2,
    )

    assert result.status == "BLOCKED_MISSING_SIGNAL_DATES"
    assert result.missing_months == ("2022-02",)
    assert len(result.rows) == 1
    assert result.rows[0]["month"] == "2022-01"


def test_signal_date_manifest_filters_wrong_index_code(monkeypatch):
    frame = pd.concat([
        _frame(),
        pd.DataFrame([{"index_code": "XU030", "trade_date": "2022-01-03", "open": 1.0, "close": 1.0}]),
    ], ignore_index=True)
    monkeypatch.setattr(signal_dates, "_read_sql", lambda conn, query, params: frame)

    result = signal_dates.build_historical_signal_date_manifest(
        object(), start_month="2022-01", end_month="2022-02", expected_months=2,
        index_code="XU100",
    )

    assert result.rows[0]["signal_date"] == "2022-01-04"
