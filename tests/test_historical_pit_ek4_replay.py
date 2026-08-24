from __future__ import annotations

from datetime import datetime, timezone
import inspect

import numpy as np
import pandas as pd
import pytest

from src.analytics.historical_pit_ek4_replay import (
    HistoricalPitEk4ReplayError,
    run_historical_pit_ek4_replay,
)


ANALYSIS = datetime(2025, 1, 3, 7, 0, tzinfo=timezone.utc)
ASOF = "2025-01-03"
MARKET_ASOF = "2025-01-02"


def _frames():
    days = pd.bdate_range(end=MARKET_ASOF, periods=40)
    step = np.arange(len(days), dtype=float)
    universe = pd.DataFrame(
        [
            {"ticker": "AAA", "sector_index_code": "XTEST"},
            {"ticker": "BBB", "sector_index_code": "XTEST"},
        ]
    )
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
                ("AAA", 100.0 * np.power(1.01, step)),
                ("BBB", 100.0 * np.power(1.002, step)),
            )
        ],
        ignore_index=True,
    )
    index_prices = pd.concat(
        [
            pd.DataFrame(
                {"index_code": code, "trade_date": days, "close": values}
            )
            for code, values in (
                ("XU100", 100.0 * np.power(1.03, step)),
                ("XTEST", 100.0 * np.power(1.005, step)),
            )
        ],
        ignore_index=True,
    )
    return universe, calendar, stock_prices, index_prices


def _run(**overrides):
    universe, calendar, stock_prices, index_prices = _frames()
    kwargs = {
        "analysis_at": ANALYSIS,
        "asof_date": ASOF,
        "market_asof_date": MARKET_ASOF,
        "universe": universe,
        "trading_calendar": calendar,
        "stock_prices": stock_prices,
        "index_prices": index_prices,
    }
    kwargs.update(overrides)
    return run_historical_pit_ek4_replay(**kwargs)


def test_historical_ek4_has_no_database_connection_parameter():
    assert "conn" not in inspect.signature(run_historical_pit_ek4_replay).parameters


def test_historical_ek4_uses_exact_20_trading_intervals_and_production_score():
    universe, calendar, stock_prices, index_prices = _frames()
    result = _run(
        universe=universe,
        trading_calendar=calendar,
        stock_prices=stock_prices,
        index_prices=index_prices,
    )

    days = list(pd.to_datetime(calendar["trade_date"]).dt.date)
    assert result.end_date == pd.Timestamp(MARKET_ASOF).date()
    assert result.start_date == days[-21]
    assert days.index(result.end_date) - days.index(result.start_date) == 20
    aaa = result.ek4_scores.set_index("ticker").loc["AAA"]
    stock_return = float(np.power(1.01, 20) - 1.0)
    sector_return = float(np.power(1.005, 20) - 1.0)
    excess = stock_return - sector_return
    assert float(aaa["stock_return_20d"]) == pytest.approx(stock_return)
    assert float(aaa["sector_return_20d"]) == pytest.approx(sector_return)
    assert float(aaa["excess_return_20d"]) == pytest.approx(excess)
    assert float(aaa["ek4"]) == pytest.approx(np.clip((excess + 0.2) / 0.4, 0, 1))
    assert result.rejections.empty


def test_historical_ek4_subtracts_raw_sector_return_not_market_adjusted_return():
    result = _run()
    aaa = result.ek4_scores.set_index("ticker").loc["AAA"]
    # XU100 grows much faster than both AAA and XTEST in the fixture.  The exact
    # result still depends only on AAA minus XTEST; no M3-style market correction.
    expected = (np.power(1.01, 20) - 1.0) - (np.power(1.005, 20) - 1.0)
    assert float(aaa["excess_return_20d"]) == pytest.approx(expected)


def test_historical_ek4_keeps_signal_and_market_cutoff_dates_separate():
    result = _run()
    assert result.asof_date == pd.Timestamp(ASOF).date()
    assert result.market_asof_date == pd.Timestamp(MARKET_ASOF).date()
    assert result.end_date == result.market_asof_date


def test_historical_ek4_rejects_naive_analysis_at():
    with pytest.raises(HistoricalPitEk4ReplayError, match="timezone-aware"):
        _run(analysis_at=datetime(2025, 1, 3, 7, 0))


def test_historical_ek4_rejects_market_cutoff_after_signal_date():
    with pytest.raises(HistoricalPitEk4ReplayError, match="market_asof_date"):
        _run(market_asof_date="2025-01-04")


def test_historical_ek4_rejects_future_stock_price_instead_of_filtering_it():
    universe, calendar, stock_prices, index_prices = _frames()
    future = stock_prices.iloc[[0]].copy()
    future["trade_date"] = ASOF
    with pytest.raises(HistoricalPitEk4ReplayError, match="market_asof_date sonrasi hisse"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=pd.concat([stock_prices, future], ignore_index=True),
            index_prices=index_prices,
        )


def test_historical_ek4_rejects_future_index_price_instead_of_filtering_it():
    universe, calendar, stock_prices, index_prices = _frames()
    future = index_prices.iloc[[0]].copy()
    future["trade_date"] = ASOF
    with pytest.raises(HistoricalPitEk4ReplayError, match="market_asof_date sonrasi endeks"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
            index_prices=pd.concat([index_prices, future], ignore_index=True),
        )


def test_historical_ek4_rejects_current_universe_contamination():
    universe, calendar, stock_prices, index_prices = _frames()
    foreign = stock_prices.iloc[[0]].copy()
    foreign["ticker"] = "TODAYONLY"
    with pytest.raises(HistoricalPitEk4ReplayError, match="historical universe disi"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=pd.concat([stock_prices, foreign], ignore_index=True),
            index_prices=index_prices,
        )


@pytest.mark.parametrize("route", ["", "XU100"])
def test_historical_ek4_rejects_missing_or_market_sector_route(route):
    universe, calendar, stock_prices, index_prices = _frames()
    universe.loc[0, "sector_index_code"] = route
    with pytest.raises(HistoricalPitEk4ReplayError, match="sector|XU100"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
            index_prices=index_prices,
        )


def test_historical_ek4_rejects_duplicate_stock_price_key():
    universe, calendar, stock_prices, index_prices = _frames()
    with pytest.raises(HistoricalPitEk4ReplayError, match="duplicate ticker.trade_date"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=pd.concat([stock_prices, stock_prices.iloc[[0]]], ignore_index=True),
            index_prices=index_prices,
        )


def test_historical_ek4_rejects_off_calendar_observation():
    universe, calendar, stock_prices, index_prices = _frames()
    stock_prices = stock_prices.copy()
    stock_prices.loc[0, "trade_date"] = "2024-11-09"
    with pytest.raises(HistoricalPitEk4ReplayError, match="trading_calendar disi"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
            index_prices=index_prices,
        )


def test_historical_ek4_rejects_missing_stock_endpoint_explicitly():
    universe, calendar, stock_prices, index_prices = _frames()
    end = pd.Timestamp(MARKET_ASOF)
    stock_prices = stock_prices.loc[
        ~((stock_prices["ticker"] == "AAA") & (stock_prices["trade_date"] == end))
    ]
    result = _run(
        universe=universe,
        trading_calendar=calendar,
        stock_prices=stock_prices,
        index_prices=index_prices,
    )
    assert set(result.ek4_scores["ticker"]) == {"BBB"}
    assert result.rejections.to_dict("records") == [
        {
            "ticker": "AAA",
            "reason": "STOCK_WINDOW_PRICE_MISSING",
            "start_date": result.start_date,
            "end_date": result.end_date,
        }
    ]


def test_historical_ek4_rejects_missing_sector_endpoint_without_xu100_fallback():
    universe, calendar, stock_prices, index_prices = _frames()
    end = pd.Timestamp(MARKET_ASOF)
    index_prices = index_prices.loc[
        ~((index_prices["index_code"] == "XTEST") & (index_prices["trade_date"] == end))
    ]
    result = _run(
        universe=universe,
        trading_calendar=calendar,
        stock_prices=stock_prices,
        index_prices=index_prices,
    )
    assert result.ek4_scores.empty
    assert set(result.rejections["reason"]) == {"SECTOR_WINDOW_PRICE_MISSING"}
    assert "XU100" in set(index_prices["index_code"])


def test_historical_ek4_insufficient_window_rejects_every_ticker():
    universe, calendar, stock_prices, index_prices = _frames()
    calendar = calendar.tail(20).reset_index(drop=True)
    allowed = set(pd.to_datetime(calendar["trade_date"]))
    stock_prices = stock_prices.loc[stock_prices["trade_date"].isin(allowed)]
    index_prices = index_prices.loc[index_prices["trade_date"].isin(allowed)]
    result = _run(
        universe=universe,
        trading_calendar=calendar,
        stock_prices=stock_prices,
        index_prices=index_prices,
    )
    assert result.ek4_scores.empty
    assert set(result.rejections["ticker"]) == {"AAA", "BBB"}
    assert set(result.rejections["reason"]) == {"EK4_WINDOW_UNAVAILABLE"}
