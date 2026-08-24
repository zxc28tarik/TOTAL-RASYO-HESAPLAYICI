from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analytics import betas


def _live_frames(*, omit_stock_day: bool, include_market_index: bool):
    days = pd.bdate_range("2024-01-01", periods=100)
    stock_days = days.delete(20) if omit_stock_day else days

    universe = pd.DataFrame(
        [{"ticker": "AAA", "sector_index_code": "XTEST"}]
    )
    trading_days = pd.DataFrame({"trade_date": days})
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
    index_parts = [
        pd.DataFrame(
            {
                "index_code": "XTEST",
                "trade_date": days,
                "px": 100.0
                * np.cumprod(
                    1.001 + 0.0001 * np.sin(np.arange(len(days), dtype=float))
                ),
            }
        )
    ]
    if include_market_index:
        index_parts.append(
            pd.DataFrame(
                {
                    "index_code": "XU100",
                    "trade_date": days,
                    "px": 100.0
                    * np.cumprod(
                        1.0008 + 0.0001 * np.cos(np.arange(len(days), dtype=float))
                    ),
                }
            )
        )
    return universe, trading_days, prices, pd.concat(index_parts, ignore_index=True)


def _patch_live_reads(
    monkeypatch: pytest.MonkeyPatch,
    *,
    omit_stock_day: bool,
    include_market_index: bool,
) -> None:
    universe, trading_days, prices, index_prices = _live_frames(
        omit_stock_day=omit_stock_day,
        include_market_index=include_market_index,
    )

    def fake_read_sql(query, _conn, params=None):
        del params
        normalized = " ".join(str(query).split())
        if "FROM core.universe_stocks" in normalized:
            return universe.copy()
        if normalized.startswith("SELECT trade_date FROM core.index_prices_daily"):
            return trading_days.copy()
        if "FROM core.prices_daily" in normalized:
            return prices.copy()
        if "SELECT index_code, trade_date, close AS px" in normalized:
            return index_prices.copy()
        raise AssertionError(f"Beklenmeyen SQL: {normalized}")

    monkeypatch.setattr(betas.pd, "read_sql", fake_read_sql)


def test_live_beta_keeps_pre_m3_failure_for_misaligned_global_date_axes(monkeypatch):
    """PR #12 historical alignment must not change the live compatibility path."""

    _patch_live_reads(
        monkeypatch,
        omit_stock_day=True,
        include_market_index=True,
    )

    with pytest.raises(ValueError, match="broadcast together"):
        betas.estimate_betas_for_date(object(), "2024-05-17")


def test_live_beta_keeps_pre_m3_empty_result_when_market_series_is_missing(monkeypatch):
    """The legacy live path returned no rows when XU100 was absent."""

    _patch_live_reads(
        monkeypatch,
        omit_stock_day=False,
        include_market_index=False,
    )

    result = betas.estimate_betas_for_date(object(), "2024-05-17")

    assert result.empty
    assert list(result.columns) == [
        "ticker",
        "t0_date",
        "beta_mkt",
        "beta_sec",
        "r2",
        "n_obs",
    ]
