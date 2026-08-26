from __future__ import annotations

import pandas as pd
import pytest

from src.analytics.historical_backtest_db import HistoricalBacktestDatabaseError
import src.analytics.historical_backtest_inventory as inventory_db


def _frames():
    dates = [pd.Timestamp("2022-01-03"), pd.Timestamp("2022-02-01")]
    return {
        "index": pd.DataFrame([
            {"index_code": "XU100", "trade_date": dates[0], "open": 100.0, "close": 101.0},
            {"index_code": "XU100", "trade_date": dates[1], "open": 110.0, "close": 111.0},
        ]),
        "membership": pd.DataFrame([
            {"ticker": "AAA", "valid_from": "2020-01-01", "valid_to": None, "is_tradable": True},
            {"ticker": "BBB", "valid_from": "2022-02-01", "valid_to": None, "is_tradable": True},
        ]),
        "prices": pd.DataFrame([
            {"ticker": "AAA", "trade_date": dates[0], "open": 10.0, "close": 10.5},
            {"ticker": "AAA", "trade_date": dates[1], "open": 11.0, "close": 11.5},
            {"ticker": "BBB", "trade_date": dates[1], "open": 20.0, "close": 20.5},
        ]),
        "wages": pd.DataFrame([
            {
                "schedule_key": "WAGE_AUDITED", "valid_from": "2021-01-01", "valid_to": None,
                "net_min_wage": 5000.0, "source": "TEST", "source_ref": "w",
                "source_sha256": "a" * 64, "row_sha256": "b" * 64,
            },
        ]),
        "cutoffs": pd.DataFrame([
            {
                "profile_key": "CUT_AUDITED", "signal_date": dates[0],
                "cutoff_at": "2022-01-02T19:00:00Z", "execution_at": "2022-01-03T07:00:00Z",
                "source": "TEST", "source_ref": "c1", "source_sha256": "c" * 64, "row_sha256": "d" * 64,
            },
            {
                "profile_key": "CUT_AUDITED", "signal_date": dates[1],
                "cutoff_at": "2022-01-31T19:00:00Z", "execution_at": "2022-02-01T07:00:00Z",
                "source": "TEST", "source_ref": "c2", "source_sha256": "e" * 64, "row_sha256": "f" * 64,
            },
        ]),
        "total": pd.DataFrame([{
            "authority_run_count": 2,
            "first_analysis_at": pd.Timestamp("2022-01-02T18:00:00Z"),
            "last_analysis_at": pd.Timestamp("2022-01-31T18:00:00Z"),
        }]),
    }


def _install(monkeypatch, frames):
    calls = []

    def fake_read_sql(conn, query, params):
        calls.append((query, params))
        if "FROM core.index_prices_daily" in query:
            return frames["index"].copy()
        if "FROM core.universe_membership_history" in query:
            return frames["membership"].copy()
        if "FROM core.prices_daily" in query:
            return frames["prices"].copy()
        if "FROM core.backtest_minimum_wage_schedule" in query:
            return frames["wages"].copy()
        if "FROM analytics.backtest_signal_cutoff_schedule" in query:
            return frames["cutoffs"].copy()
        if "FROM analytics.total_rasyo_run" in query:
            return frames["total"].copy()
        raise AssertionError(query)

    monkeypatch.setattr(inventory_db, "_read_sql", fake_read_sql)
    return calls


def test_inventory_discovers_unique_keys_and_exact_execution_price_coverage(monkeypatch):
    frames = _frames()
    calls = _install(monkeypatch, frames)

    result = inventory_db.inventory_backtest_database(
        object(), start_month="2022-01", end_month="2022-02", expected_months=2,
    )

    assert result.status == "CANDIDATE_READY_FOR_V24G"
    assert result.hard_blockers == ()
    assert result.candidate_wage_schedule_keys == ("WAGE_AUDITED",)
    assert result.candidate_cutoff_profile_keys == ("CUT_AUDITED",)
    assert result.benchmark["covered_months"] == 2
    assert result.universe["months_with_nonempty_universe"] == 2
    assert result.universe["execution_ticker_pairs"] == 3
    assert result.prices["valid_execution_ticker_pairs"] == 3
    assert result.total_rasyo["authority_run_count_before_window_end"] == 2
    assert len(calls) == 6


def test_inventory_reports_each_empty_input_family_without_throwing(monkeypatch):
    frames = _frames()
    frames["index"] = frames["index"].iloc[0:0].copy()
    frames["membership"] = frames["membership"].iloc[0:0].copy()
    frames["prices"] = frames["prices"].iloc[0:0].copy()
    frames["wages"] = frames["wages"].iloc[0:0].copy()
    frames["cutoffs"] = frames["cutoffs"].iloc[0:0].copy()
    frames["total"] = pd.DataFrame([{
        "authority_run_count": 0, "first_analysis_at": pd.NaT, "last_analysis_at": pd.NaT,
    }])
    calls = _install(monkeypatch, frames)

    result = inventory_db.inventory_backtest_database(
        object(), start_month="2022-01", end_month="2022-02", expected_months=2,
    )

    assert result.status == "BLOCKED"
    assert set(result.hard_blockers) == {
        "BENCHMARK_MONTH_COVERAGE",
        "UNIVERSE_MONTH_COVERAGE",
        "WAGE_SCHEDULE_COVERAGE",
        "CUTOFF_PROFILE_COVERAGE",
        "TOTAL_RASYO_AUTHORITY_ABSENT",
    }
    # No execution universe means there is no price pair to declare missing yet.
    assert "PRICE_EXECUTION_COVERAGE" not in result.hard_blockers
    assert result.prices["expected_execution_ticker_pairs"] == 0
    assert len(calls) == 5  # price query is intentionally skipped


def test_inventory_marks_missing_or_invalid_exact_execution_price(monkeypatch):
    frames = _frames()
    frames["prices"] = frames["prices"].copy()
    frames["prices"].loc[
        (frames["prices"]["ticker"] == "BBB"), "open"
    ] = float("nan")
    _install(monkeypatch, frames)

    result = inventory_db.inventory_backtest_database(
        object(), start_month="2022-01", end_month="2022-02", expected_months=2,
    )

    assert result.status == "BLOCKED"
    assert "PRICE_EXECUTION_COVERAGE" in result.hard_blockers
    assert result.prices["expected_execution_ticker_pairs"] == 3
    assert result.prices["valid_execution_ticker_pairs"] == 2
    assert result.prices["missing_or_invalid_execution_ticker_pairs"] == 1


def test_cutoff_candidate_must_match_all_first_index_trading_days(monkeypatch):
    frames = _frames()
    frames["cutoffs"] = frames["cutoffs"].iloc[:1].copy()
    _install(monkeypatch, frames)

    result = inventory_db.inventory_backtest_database(
        object(), start_month="2022-01", end_month="2022-02", expected_months=2,
    )

    assert result.candidate_cutoff_profile_keys == ()
    assert "CUTOFF_PROFILE_COVERAGE" in result.hard_blockers
    assert result.cutoff_profiles[0]["missing_signal_date_count"] == 1


def test_wage_candidate_must_cover_window_without_gap(monkeypatch):
    frames = _frames()
    frames["wages"] = pd.DataFrame([
        {
            "schedule_key": "WAGE_GAP", "valid_from": "2021-01-01", "valid_to": "2022-01-15",
            "net_min_wage": 5000.0, "source": "TEST", "source_ref": "w1",
            "source_sha256": "a" * 64, "row_sha256": "b" * 64,
        },
        {
            "schedule_key": "WAGE_GAP", "valid_from": "2022-02-01", "valid_to": None,
            "net_min_wage": 6000.0, "source": "TEST", "source_ref": "w2",
            "source_sha256": "c" * 64, "row_sha256": "d" * 64,
        },
    ])
    _install(monkeypatch, frames)

    result = inventory_db.inventory_backtest_database(
        object(), start_month="2022-01", end_month="2022-02", expected_months=2,
    )

    assert result.candidate_wage_schedule_keys == ()
    assert "WAGE_SCHEDULE_COVERAGE" in result.hard_blockers
    assert result.wage_schedules[0]["covers_full_window"] is False


def test_inventory_database_failure_remains_technical_error(monkeypatch):
    def boom(conn, query, params):
        raise RuntimeError("db down")

    monkeypatch.setattr(pd, "read_sql_query", boom)
    with pytest.raises(HistoricalBacktestDatabaseError, match="inventory database read failed"):
        inventory_db.inventory_backtest_database(
            object(), start_month="2022-01", end_month="2022-01", expected_months=1,
        )
