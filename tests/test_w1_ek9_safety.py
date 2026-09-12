from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.analytics import run_daily_pipeline
from src.analytics.ek9_volatility import compute_ek9_volatility_scores
from src.analytics.historical_pit_ek9_replay import run_historical_pit_ek9_replay


def _frames():
    days = pd.bdate_range("2024-01-01", periods=70)
    prices = pd.concat([
        pd.DataFrame({"ticker": ticker, "trade_date": days, "px": values})
        for ticker, values in (
            ("VALID", np.linspace(100, 140, len(days))),
            ("TEST", np.full(len(days), 100.0)),
        )
    ], ignore_index=True)
    return days, prices


def _run(monkeypatch, prices, days, *, calendar=None):
    if calendar is None:
        calendar = pd.DataFrame({"trade_date": days})
    def read_sql(sql, conn, params):
        assert params["asof"] == days[-1].date()
        if "core.index_prices_daily" in sql:
            assert "XU100" in sql or "XU100" in params.values()
            assert "<= %(asof)s" in sql
            return calendar.copy()
        return prices.copy()
    monkeypatch.setattr(run_daily_pipeline.pd, "read_sql", read_sql)
    return run_daily_pipeline._compute_ek9_vol(object(), days[-1].date()).set_index("ticker")


@pytest.mark.parametrize("values", [
    [], [0.0], [np.nan, np.nan], [0.0, np.nan], [np.inf, 0.0],
    [-np.inf, 0.0], ["bad", 0.0], [1e200, 0.0],
])
def test_shared_arithmetic_never_turns_invalid_window_into_score(values):
    result = compute_ek9_volatility_scores(pd.DataFrame({"TEST": values}))
    assert result.loc["TEST"].isna().all()


@pytest.mark.parametrize("position", [6, 30, 69])
def test_missing_window_price_is_not_filled(monkeypatch, position):
    days, prices = _frames()
    prices = prices.loc[~((prices.ticker == "TEST") & (prices.trade_date == days[position]))]
    result = _run(monkeypatch, prices, days)
    assert np.isfinite(result.loc["VALID", "ek9"])
    assert pd.isna(result.loc["TEST", "ek9"])
    assert result.loc["TEST", "ek9_rejection_reason"] == "STOCK_WINDOW_PRICE_MISSING"


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf, 0.0, -1.0, "bad"])
def test_invalid_price_rejects_only_affected_stock(monkeypatch, invalid):
    days, prices = _frames()
    prices["px"] = prices["px"].astype(object)
    prices.loc[(prices.ticker == "TEST") & (prices.trade_date == days[30]), "px"] = invalid
    result = _run(monkeypatch, prices, days)
    assert np.isfinite(result.loc["VALID", "ek9"])
    assert pd.isna(result.loc["TEST", "ek9"])
    assert result.loc["TEST", "ek9_rejection_reason"] == "STOCK_WINDOW_PRICE_INVALID"


def test_whole_missing_stock_session_is_found_using_index_axis(monkeypatch):
    days, prices = _frames()
    prices = prices.loc[prices.trade_date != days[30]]
    result = _run(monkeypatch, prices, days)
    assert result.ek9.isna().all()
    assert set(result.ek9_rejection_reason) == {"STOCK_WINDOW_PRICE_MISSING"}


def test_all_missing_stock_is_retained_and_constant_valid_prices_score_one(monkeypatch):
    days, prices = _frames()
    prices.loc[prices.ticker == "VALID", "px"] = np.nan
    result = _run(monkeypatch, prices, days)
    assert result.loc["TEST", "ek9"] == 1.0
    assert pd.isna(result.loc["VALID", "ek9"])
    assert result.loc["VALID", "ek9_rejection_reason"] == "STOCK_WINDOW_PRICE_INVALID"


def test_duplicate_stock_date_rejects_only_affected_stock(monkeypatch):
    days, prices = _frames()
    prices = pd.concat([prices, prices.loc[(prices.ticker == "TEST") & (prices.trade_date == days[30])]])
    result = _run(monkeypatch, prices, days)
    assert np.isfinite(result.loc["VALID", "ek9"])
    assert pd.isna(result.loc["TEST", "ek9"])
    assert result.loc["TEST", "ek9_rejection_reason"] == "STOCK_WINDOW_PRICE_DUPLICATE"


@pytest.mark.parametrize("mode", ["empty", "duplicate", "interior_missing", "end_missing"])
def test_bad_calendar_never_bridges_observed_stock_sessions(monkeypatch, mode):
    days, prices = _frames()
    calendar = pd.DataFrame({"trade_date": days})
    reason = "EK9_CALENDAR_INVALID"
    if mode == "empty":
        calendar = calendar.iloc[:0]
        reason = "EK9_CALENDAR_UNAVAILABLE"
    elif mode == "duplicate":
        calendar = pd.concat([calendar, calendar.iloc[[30]]])
    else:
        calendar = calendar.drop(index=30 if mode == "interior_missing" else 69)
        reason = "EK9_CALENDAR_STOCK_MISMATCH"
    result = _run(monkeypatch, prices, days, calendar=calendar)
    assert result.ek9.isna().all()
    assert set(result.ek9_rejection_reason) == {reason}


def test_exact_65_sessions_keep_original_gate_and_64_price_math(monkeypatch):
    days, prices = _frames()
    days = days[-65:]
    prices = prices.loc[prices.trade_date.isin(days)]
    result = _run(monkeypatch, prices, days)
    selected = prices.loc[prices.ticker == "VALID"].set_index("trade_date").px.tail(64)
    expected = 1 - np.clip(selected.pct_change(fill_method=None).iloc[1:].std(ddof=1) / .06, 0, 1)
    assert result.loc["VALID", "ek9"] == expected
    assert result.loc["TEST", "ek9"] == 1.0


def test_missing_price_outside_selected_window_does_not_reject(monkeypatch):
    days, prices = _frames()
    prices = prices.loc[~((prices.ticker == "TEST") & (prices.trade_date == days[0]))]
    assert _run(monkeypatch, prices, days).loc["TEST", "ek9"] == 1.0


def test_live_finite_returns_with_overflowed_variance_remain_unscored(monkeypatch):
    days, prices = _frames()
    prices.loc[(prices.ticker == "TEST") & (prices.trade_date == days[30]), "px"] = 1e202
    result = _run(monkeypatch, prices, days)
    assert pd.isna(result.loc["TEST", "ek9"])
    assert result.loc["TEST", "ek9_rejection_reason"] == "STOCK_RETURN_WINDOW_INVALID"
    assert np.isfinite(result.loc["VALID", "ek9"])


def test_historical_overflowed_variance_is_explicit_rejection():
    days, prices = _frames()
    prices.loc[(prices.ticker == "TEST") & (prices.trade_date == days[30]), "px"] = 1e202
    prices = prices.rename(columns={"px": "adj_close"}).assign(close=100.0)
    result = run_historical_pit_ek9_replay(
        analysis_at=datetime.combine(days[-1].date(), datetime.min.time(), ZoneInfo("Europe/Istanbul")),
        asof_date=days[-1].date(), market_asof_date=days[-1].date(),
        universe=pd.DataFrame({"ticker": ["VALID", "TEST"]}),
        trading_calendar=pd.DataFrame({"trade_date": days}), stock_prices=prices,
    )
    assert set(result.ek9_scores.ticker) == {"VALID"}
    assert result.rejections[["ticker", "reason"]].to_dict("records") == [
        {"ticker": "TEST", "reason": "STOCK_RETURN_WINDOW_INVALID"}
    ]
