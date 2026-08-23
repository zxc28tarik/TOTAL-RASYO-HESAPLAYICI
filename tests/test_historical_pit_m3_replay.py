from __future__ import annotations

from datetime import datetime, timezone
import inspect

import numpy as np
import pandas as pd
import pytest

from src.analytics.betas import estimate_betas_from_frames
from src.analytics.historical_pit_m3_replay import (
    HistoricalPitM3ReplayError,
    run_historical_pit_m3_replay,
)
from src.analytics.trailing_alpha import compute_trailing_alpha_from_frames


ANALYSIS = datetime(2025, 1, 3, 7, 0, tzinfo=timezone.utc)
ASOF = "2025-01-03"
MARKET_ASOF = "2025-01-02"


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    days = pd.bdate_range(end=MARKET_ASOF, periods=320)
    step = np.arange(len(days), dtype=float)
    market = 100.0 * np.power(1.0010, step)
    sector = 100.0 * np.power(1.0013, step)
    aaa = 50.0 * np.power(1.0016, step)
    bbb = 70.0 * np.power(1.0007, step)

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
            for ticker, values in (("AAA", aaa), ("BBB", bbb))
        ],
        ignore_index=True,
    )
    index_prices = pd.concat(
        [
            pd.DataFrame(
                {"index_code": code, "trade_date": days, "close": values}
            )
            for code, values in (("XU100", market), ("XTEST", sector))
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
    return run_historical_pit_m3_replay(**kwargs)


def test_historical_m3_replay_has_no_database_connection_parameter():
    assert "conn" not in inspect.signature(run_historical_pit_m3_replay).parameters


def test_historical_m3_reuses_beta_and_trailing_alpha_frame_math():
    universe, calendar, stock_prices, index_prices = _frames()
    replay = _run()

    days = list(pd.to_datetime(calendar["trade_date"]).dt.date)
    end = pd.Timestamp(MARKET_ASOF).date()
    beta_start = days[days.index(end) - 252]
    alpha_start = days[days.index(end) - 63]
    stock = stock_prices.assign(
        trade_date=pd.to_datetime(stock_prices["trade_date"]).dt.date,
        px=stock_prices["adj_close"],
    )
    indices = index_prices.assign(
        trade_date=pd.to_datetime(index_prices["trade_date"]).dt.date,
        px=index_prices["close"],
    )
    direct_betas = estimate_betas_from_frames(
        universe=universe,
        prices=stock.loc[stock["trade_date"].between(beta_start, end), ["ticker", "trade_date", "px"]],
        index_prices=indices.loc[
            indices["trade_date"].between(beta_start, end),
            ["index_code", "trade_date", "px"],
        ],
        t0_date=end,
    )
    direct_alpha = compute_trailing_alpha_from_frames(
        universe=universe,
        prices=stock.loc[stock["trade_date"].isin([alpha_start, end]), ["ticker", "trade_date", "px"]],
        index_prices=indices.loc[
            indices["trade_date"].isin([alpha_start, end]),
            ["index_code", "trade_date", "px"],
        ],
        betas=direct_betas,
        asof_date=ASOF,
        start_date=alpha_start,
        end_date=end,
        window_days=63,
    )

    pd.testing.assert_frame_equal(
        replay.beta_estimates.reset_index(drop=True),
        direct_betas.reset_index(drop=True),
    )
    pd.testing.assert_frame_equal(
        replay.alpha_trailing.reset_index(drop=True),
        direct_alpha.reset_index(drop=True),
    )
    assert replay.rejections.empty
    assert set(replay.m3_scores["ticker"]) == {"AAA", "BBB"}
    assert replay.m3_scores["m3"].between(0.0, 1.0).all()


def test_historical_beta_gap_is_missing_not_a_synthetic_zero_return():
    days = pd.bdate_range("2024-01-01", periods=100)
    stock_days = days.delete(20)
    universe = pd.DataFrame(
        [{"ticker": "AAA", "sector_index_code": "XTEST"}]
    )
    prices = pd.DataFrame(
        {
            "ticker": "AAA",
            "trade_date": stock_days,
            "px": 100.0
            * np.cumprod(
                1.001 + 0.0002 * np.sin(np.arange(len(stock_days), dtype=float))
            ),
        }
    )
    index_prices = pd.concat(
        [
            pd.DataFrame(
                {
                    "index_code": code,
                    "trade_date": days,
                    "px": values,
                }
            )
            for code, values in (
                (
                    "XU100",
                    100.0
                    * np.cumprod(
                        1.0008 + 0.0001 * np.cos(np.arange(len(days), dtype=float))
                    ),
                ),
                (
                    "XTEST",
                    100.0
                    * np.cumprod(
                        1.001 + 0.0001 * np.sin(np.arange(len(days), dtype=float))
                    ),
                ),
            )
        ],
        ignore_index=True,
    )

    result = estimate_betas_from_frames(
        universe=universe,
        prices=prices,
        index_prices=index_prices,
        t0_date=days[-1],
    )

    # First return, the absent stock day, and the following return are unknown.
    # Explicit no-fill semantics keep this identical on every pandas >=2.2.
    assert result.iloc[0]["n_obs"] == 97


def test_trailing_alpha_uses_sector_excess_factor_not_raw_sector_return():
    universe = pd.DataFrame([{"ticker": "AAA", "sector_index_code": "XTEST"}])
    prices = pd.DataFrame(
        [
            {"ticker": "AAA", "trade_date": "2024-01-01", "px": 100.0},
            {"ticker": "AAA", "trade_date": "2024-04-01", "px": 120.0},
        ]
    )
    indices = pd.DataFrame(
        [
            {"index_code": "XU100", "trade_date": "2024-01-01", "px": 100.0},
            {"index_code": "XU100", "trade_date": "2024-04-01", "px": 110.0},
            {"index_code": "XTEST", "trade_date": "2024-01-01", "px": 100.0},
            {"index_code": "XTEST", "trade_date": "2024-04-01", "px": 115.0},
        ]
    )
    betas = pd.DataFrame([{"ticker": "AAA", "beta_mkt": 1.2, "beta_sec": 0.4}])

    result = compute_trailing_alpha_from_frames(
        universe=universe,
        prices=prices,
        index_prices=indices,
        betas=betas,
        asof_date="2024-04-02",
        start_date="2024-01-01",
        end_date="2024-04-01",
        window_days=63,
    )

    expected = 0.20 - 1.2 * 0.10 - 0.4 * (0.15 - 0.10)
    assert float(result.iloc[0]["alpha_trailing"]) == pytest.approx(expected)


def test_historical_m3_rejects_naive_analysis_at():
    with pytest.raises(HistoricalPitM3ReplayError, match="timezone-aware"):
        _run(analysis_at=datetime(2025, 1, 3, 7, 0))


def test_historical_m3_rejects_market_cutoff_after_signal_date():
    with pytest.raises(HistoricalPitM3ReplayError, match="market_asof_date"):
        _run(market_asof_date="2025-01-04")


def test_historical_m3_rejects_future_stock_price_instead_of_filtering_it():
    universe, calendar, stock_prices, index_prices = _frames()
    future = stock_prices.iloc[[0]].copy()
    future["trade_date"] = "2025-01-03"
    stock_prices = pd.concat([stock_prices, future], ignore_index=True)
    with pytest.raises(HistoricalPitM3ReplayError, match="market_asof_date sonrasi hisse fiyati"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
            index_prices=index_prices,
        )


def test_historical_m3_rejects_future_index_price_instead_of_filtering_it():
    universe, calendar, stock_prices, index_prices = _frames()
    future = index_prices.iloc[[0]].copy()
    future["trade_date"] = "2025-01-03"
    index_prices = pd.concat([index_prices, future], ignore_index=True)
    with pytest.raises(HistoricalPitM3ReplayError, match="market_asof_date sonrasi endeks fiyati"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
            index_prices=index_prices,
        )


def test_historical_m3_rejects_current_universe_contamination():
    universe, calendar, stock_prices, index_prices = _frames()
    foreign = stock_prices.iloc[[0]].copy()
    foreign["ticker"] = "TODAYONLY"
    stock_prices = pd.concat([stock_prices, foreign], ignore_index=True)
    with pytest.raises(HistoricalPitM3ReplayError, match="historical universe disi"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
            index_prices=index_prices,
        )


def test_historical_m3_rejects_missing_sector_route_instead_of_defaulting_to_xu100():
    universe, calendar, stock_prices, index_prices = _frames()
    universe.loc[0, "sector_index_code"] = ""
    with pytest.raises(HistoricalPitM3ReplayError, match="sector_index_code"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
            index_prices=index_prices,
        )


def test_historical_m3_rejects_duplicate_stock_price_key():
    universe, calendar, stock_prices, index_prices = _frames()
    stock_prices = pd.concat([stock_prices, stock_prices.iloc[[0]]], ignore_index=True)
    with pytest.raises(HistoricalPitM3ReplayError, match="duplicate ticker.trade_date"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
            index_prices=index_prices,
        )


def test_historical_m3_rejects_off_calendar_price_date():
    universe, calendar, stock_prices, index_prices = _frames()
    bad = stock_prices.iloc[[0]].copy()
    bad["trade_date"] = pd.Timestamp("2024-12-28")
    stock_prices = pd.concat([stock_prices, bad], ignore_index=True)
    with pytest.raises(HistoricalPitM3ReplayError, match="trading_calendar disi"):
        _run(
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
            index_prices=index_prices,
        )


def test_historical_m3_rejects_missing_sector_prices_without_live_xu100_fallback():
    universe, calendar, stock_prices, index_prices = _frames()
    index_prices = index_prices.loc[index_prices["index_code"] != "XTEST"].copy()

    replay = _run(
        universe=universe,
        trading_calendar=calendar,
        stock_prices=stock_prices,
        index_prices=index_prices,
    )

    assert replay.m3_scores.empty
    assert set(replay.rejections["ticker"]) == {"AAA", "BBB"}
    assert replay.rejections["reason"].eq("SECTOR_WINDOW_PRICE_MISSING").all()


def test_historical_m3_reports_missing_window_price_and_preserves_coverage_invariant():
    universe, calendar, stock_prices, index_prices = _frames()
    start = pd.Timestamp(calendar.iloc[-64]["trade_date"])
    stock_prices = stock_prices.loc[
        ~((stock_prices["ticker"] == "BBB") & (stock_prices["trade_date"] == start))
    ].copy()

    replay = _run(
        universe=universe,
        trading_calendar=calendar,
        stock_prices=stock_prices,
        index_prices=index_prices,
    )

    assert set(replay.m3_scores["ticker"]) == {"AAA"}
    assert replay.rejections.to_dict("records") == [
        {
            "ticker": "BBB",
            "reason": "STOCK_WINDOW_PRICE_MISSING",
            "start_date": pd.Timestamp(start).date(),
            "end_date": pd.Timestamp(MARKET_ASOF).date(),
        }
    ]
    assert set(replay.m3_scores["ticker"]) | set(replay.rejections["ticker"]) == set(replay.tickers)


def test_historical_m3_uses_documented_beta_priors_when_observations_are_insufficient():
    universe, calendar, stock_prices, index_prices = _frames()
    start = pd.Timestamp(calendar.iloc[-64]["trade_date"])
    end = pd.Timestamp(calendar.iloc[-1]["trade_date"])
    sparse_days = set(pd.to_datetime(calendar.tail(64)["trade_date"]).iloc[::4]) | {start, end}
    stock_prices = stock_prices.loc[stock_prices["trade_date"].isin(sparse_days)].reset_index(drop=True)

    replay = _run(
        universe=universe,
        trading_calendar=calendar,
        stock_prices=stock_prices,
        index_prices=index_prices,
    )

    assert replay.rejections.empty
    assert replay.beta_estimates["n_obs"].lt(60).all()
    assert replay.m3_scores["beta_source"].eq("PRODUCTION_PRIOR_INSUFFICIENT_OBS").all()
    assert replay.m3_scores["beta_mkt"].eq(1.0).all()
    assert replay.m3_scores["beta_sec"].eq(0.0).all()
