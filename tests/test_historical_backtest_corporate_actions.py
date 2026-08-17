from __future__ import annotations

import pandas as pd

import src.analytics.historical_backtest_corporate_actions as guard


def _frames():
    dates = [pd.Timestamp("2022-01-03"), pd.Timestamp("2022-02-01"), pd.Timestamp("2022-03-01")]
    return {
        "index": pd.DataFrame([
            {"index_code": "XU100", "trade_date": d, "open": 100 + i, "close": 101 + i}
            for i, d in enumerate(dates)
        ]),
        "membership": pd.DataFrame([
            {"ticker": "AAA", "valid_from": "2020-01-01", "valid_to": None, "is_tradable": True},
        ]),
        "prices": pd.DataFrame([
            {"ticker": "AAA", "trade_date": dates[0], "close": 10.0, "adj_close": 10.0},
            {"ticker": "AAA", "trade_date": dates[1], "close": 11.0, "adj_close": 11.0},
            {"ticker": "AAA", "trade_date": dates[2], "close": 12.0, "adj_close": 12.0},
        ]),
    }


def _install(monkeypatch, frames):
    def fake_read_sql(conn, query, params):
        if "FROM core.index_prices_daily" in query:
            return frames["index"].copy()
        if "FROM core.universe_membership_history" in query:
            return frames["membership"].copy()
        if "FROM core.prices_daily" in query:
            return frames["prices"].copy()
        raise AssertionError(query)

    monkeypatch.setattr(guard, "_read_sql", fake_read_sql)


def test_stable_adjustment_factor_is_clear(monkeypatch):
    frames = _frames()
    _install(monkeypatch, frames)

    result = guard.audit_corporate_action_price_continuity(
        object(), start_month="2022-01", end_month="2022-03", expected_months=3,
    )

    assert result.status == "CLEAR_NO_ADJUSTMENT_CHANGE"
    assert result.expected_execution_ticker_pairs == 3
    assert result.verified_adjustment_pairs == 3
    assert result.unverifiable_pairs == 0
    assert result.adjustment_change_tickers == ()


def test_adjustment_factor_change_blocks_raw_price_performance_claim(monkeypatch):
    frames = _frames()
    frames["prices"] = frames["prices"].copy()
    # Simulate an adjustment boundary between January and February.
    frames["prices"].loc[0, "adj_close"] = 5.0
    _install(monkeypatch, frames)

    result = guard.audit_corporate_action_price_continuity(
        object(), start_month="2022-01", end_month="2022-03", expected_months=3,
    )

    assert result.status == "BLOCKED"
    assert result.adjustment_change_tickers == ("AAA",)
    assert result.adjustment_change_count == 1


def test_missing_adj_close_is_unverifiable_and_blocks(monkeypatch):
    frames = _frames()
    frames["prices"] = frames["prices"].copy()
    frames["prices"].loc[1, "adj_close"] = None
    _install(monkeypatch, frames)

    result = guard.audit_corporate_action_price_continuity(
        object(), start_month="2022-01", end_month="2022-03", expected_months=3,
    )

    assert result.status == "BLOCKED"
    assert result.verified_adjustment_pairs == 2
    assert result.unverifiable_pairs == 1


def test_empty_execution_universe_is_not_reported_clear(monkeypatch):
    frames = _frames()
    frames["membership"] = frames["membership"].iloc[0:0].copy()
    frames["prices"] = frames["prices"].iloc[0:0].copy()
    _install(monkeypatch, frames)

    result = guard.audit_corporate_action_price_continuity(
        object(), start_month="2022-01", end_month="2022-03", expected_months=3,
    )

    assert result.status == "NO_EXECUTION_UNIVERSE"
    assert result.expected_execution_ticker_pairs == 0
