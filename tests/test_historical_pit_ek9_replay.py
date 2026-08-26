from __future__ import annotations

from datetime import datetime, timezone
import inspect

import numpy as np
import pandas as pd
import pytest

from src.analytics.historical_pit_ek9_replay import (
    HistoricalPitEk9ReplayError,
    run_historical_pit_ek9_replay,
)

ANALYSIS = datetime(2025, 1, 3, 7, 0, tzinfo=timezone.utc)
ASOF = "2025-01-03"
MARKET_ASOF = "2025-01-02"


def _prices_from_returns(returns: np.ndarray) -> np.ndarray:
    return 100.0 * np.cumprod(np.r_[1.0, 1.0 + returns])


def _frames():
    days = pd.bdate_range(end=MARKET_ASOF, periods=70)
    returns_a = np.array(([0.01, -0.01, 0.02, -0.02, 0.0] * 20)[:69], dtype=float)
    returns_b = np.array(([0.002, -0.002, 0.001, -0.001, 0.0] * 20)[:69], dtype=float)
    universe = pd.DataFrame({"ticker": ["BBB", "AAA"]})
    calendar = pd.DataFrame({"trade_date": days})
    stock_prices = pd.concat(
        [
            pd.DataFrame(
                {
                    "ticker": ticker,
                    "trade_date": days,
                    "adj_close": values,
                    "close": values,
                }
            )
            for ticker, values in (
                ("AAA", _prices_from_returns(returns_a)),
                ("BBB", _prices_from_returns(returns_b)),
            )
        ],
        ignore_index=True,
    )
    return universe, calendar, stock_prices


def _run(**overrides):
    universe, calendar, stock_prices = _frames()
    kwargs = {
        "analysis_at": ANALYSIS,
        "asof_date": ASOF,
        "market_asof_date": MARKET_ASOF,
        "universe": universe,
        "trading_calendar": calendar,
        "stock_prices": stock_prices,
    }
    kwargs.update(overrides)
    return run_historical_pit_ek9_replay(**kwargs)


def test_historical_ek9_has_no_database_or_index_price_parameter():
    params = inspect.signature(run_historical_pit_ek9_replay).parameters
    assert "conn" not in params
    assert "index_prices" not in params


def test_historical_ek9_uses_exact_63_returns_and_production_math():
    result = _run()
    assert result.rejections.empty
    assert result.lookback_days == 63
    assert result.end_date == pd.Timestamp(MARKET_ASOF).date()

    aaa = result.ek9_scores.set_index("ticker").loc["AAA"]
    assert int(aaa["observations"]) == 63

    _, calendar, prices = _frames()
    dates = list(pd.to_datetime(calendar["trade_date"]).dt.date)[-64:]
    aaa_prices = prices.loc[prices["ticker"] == "AAA"].copy()
    aaa_prices["trade_date"] = pd.to_datetime(aaa_prices["trade_date"]).dt.date
    px = aaa_prices.set_index("trade_date")["adj_close"].reindex(dates)
    returns = px.pct_change(fill_method=None).iloc[1:]
    volatility = float(returns.std(ddof=1))

    assert len(returns) == 63
    assert float(aaa["volatility"]) == pytest.approx(volatility)
    assert float(aaa["ek9"]) == pytest.approx(
        1.0 - np.clip(volatility / 0.06, 0.0, 1.0)
    )


def test_historical_ek9_pct_change_explicitly_disables_fill(monkeypatch):
    original = pd.Series.pct_change
    seen: list[dict] = []

    def spy(self, *args, **kwargs):
        seen.append(dict(kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pd.Series, "pct_change", spy)
    result = _run()
    assert result.rejections.empty
    assert seen
    assert all("fill_method" in call and call["fill_method"] is None for call in seen)


def test_historical_ek9_keeps_signal_and_market_cutoff_dates_separate():
    result = _run()
    assert result.asof_date == pd.Timestamp(ASOF).date()
    assert result.market_asof_date == pd.Timestamp(MARKET_ASOF).date()
    assert result.end_date == result.market_asof_date


def test_historical_ek9_rejects_naive_analysis_at():
    with pytest.raises(HistoricalPitEk9ReplayError, match="timezone-aware"):
        _run(analysis_at=datetime(2025, 1, 3, 7, 0))


def test_historical_ek9_rejects_market_cutoff_after_signal_date():
    with pytest.raises(HistoricalPitEk9ReplayError, match="market_asof_date"):
        _run(market_asof_date="2025-01-04")


def test_historical_ek9_rejects_nonproduction_lookback():
    with pytest.raises(HistoricalPitEk9ReplayError, match="63"):
        _run(lookback_days=62)


def test_historical_ek9_rejects_non_xu100_calendar_authority():
    with pytest.raises(HistoricalPitEk9ReplayError, match="XU100"):
        _run(market_index="XTEST")


def test_historical_ek9_rejects_future_calendar_row_instead_of_filtering_it():
    universe, calendar, stock_prices = _frames()
    calendar = pd.concat(
        [calendar, pd.DataFrame({"trade_date": [ASOF]})], ignore_index=True
    )
    with pytest.raises(HistoricalPitEk9ReplayError, match="takvim"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
        )


def test_historical_ek9_rejects_future_stock_price_instead_of_filtering_it():
    universe, calendar, stock_prices = _frames()
    future = stock_prices.iloc[[0]].copy()
    future["trade_date"] = ASOF
    with pytest.raises(HistoricalPitEk9ReplayError, match="hisse"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=pd.concat([stock_prices, future], ignore_index=True),
        )


def test_historical_ek9_rejects_current_universe_contamination():
    universe, calendar, stock_prices = _frames()
    foreign = stock_prices.iloc[[0]].copy()
    foreign["ticker"] = "TODAYONLY"
    with pytest.raises(HistoricalPitEk9ReplayError, match="historical universe disi"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=pd.concat([stock_prices, foreign], ignore_index=True),
        )


def test_historical_ek9_rejects_duplicate_stock_price_key():
    universe, calendar, stock_prices = _frames()
    with pytest.raises(HistoricalPitEk9ReplayError, match="duplicate ticker.trade_date"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=pd.concat([stock_prices, stock_prices.iloc[[0]]], ignore_index=True),
        )


def test_historical_ek9_rejects_off_calendar_observation():
    universe, calendar, stock_prices = _frames()
    stock_prices = stock_prices.copy()
    stock_prices.loc[0, "trade_date"] = "2024-01-06"
    with pytest.raises(HistoricalPitEk9ReplayError, match="trading_calendar disi"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
        )


def test_historical_ek9_missing_window_price_is_rejection_without_xu100_fallback():
    universe, calendar, stock_prices = _frames()
    end = pd.Timestamp(MARKET_ASOF)
    stock_prices = stock_prices.loc[
        ~((stock_prices["ticker"] == "AAA") & (stock_prices["trade_date"] == end))
    ]
    result = _run(
        universe=universe,
        trading_calendar=calendar,
        stock_prices=stock_prices,
    )
    assert set(result.ek9_scores["ticker"]) == {"BBB"}
    assert result.rejections.to_dict("records") == [
        {
            "ticker": "AAA",
            "reason": "STOCK_WINDOW_PRICE_MISSING",
            "start_date": result.start_date,
            "end_date": result.end_date,
        }
    ]


def test_historical_ek9_insufficient_global_window_rejects_every_ticker():
    universe, calendar, stock_prices = _frames()
    calendar = calendar.tail(64).reset_index(drop=True)
    allowed = set(pd.to_datetime(calendar["trade_date"]))
    stock_prices = stock_prices.loc[stock_prices["trade_date"].isin(allowed)]
    result = _run(
        universe=universe,
        trading_calendar=calendar,
        stock_prices=stock_prices,
    )
    assert result.ek9_scores.empty
    assert set(result.rejections["ticker"]) == {"AAA", "BBB"}
    assert set(result.rejections["reason"]) == {"EK9_WINDOW_UNAVAILABLE"}


def test_historical_ek9_rejects_nonpositive_selected_price():
    universe, calendar, stock_prices = _frames()
    stock_prices = stock_prices.copy()
    stock_prices.loc[0, "adj_close"] = 0.0
    with pytest.raises(HistoricalPitEk9ReplayError, match="pozitif sonlu"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
        )


def test_historical_ek9_rejects_nonnumeric_selected_price_instead_of_fallback():
    universe, calendar, stock_prices = _frames()
    stock_prices = stock_prices.copy()
    stock_prices["adj_close"] = stock_prices["adj_close"].astype(object)
    stock_prices.loc[0, "adj_close"] = "bad"
    with pytest.raises(HistoricalPitEk9ReplayError, match="sayisal"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
        )


def test_historical_ek9_uses_close_only_when_adjusted_close_is_missing():
    universe, calendar, stock_prices = _frames()
    stock_prices = stock_prices.copy()
    target = stock_prices.index[0]
    stock_prices.loc[target, "adj_close"] = np.nan
    result = _run(
        universe=universe,
        trading_calendar=calendar,
        stock_prices=stock_prices,
    )
    assert result.rejections.empty


def test_historical_ek9_outputs_are_deterministic_and_exhaustive():
    result = _run()
    assert list(result.ek9_scores["ticker"]) == ["AAA", "BBB"]
    assert set(result.ek9_scores["ticker"]) | set(result.rejections["ticker"]) == {
        "AAA",
        "BBB",
    }
