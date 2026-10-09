from __future__ import annotations

from datetime import date

import pytest

from scripts.materialize_current_market_modules import stock_bulk_frontier_date


def test_frontier_is_mode_of_each_tickers_own_latest_date_not_the_single_latest():
    # THYAO alone reaches 2026-09-15 (Yahoo happened to update it early);
    # every other ticker's own bulk series only reaches 2026-09-14. Using the
    # single latest date across all rows would pick 2026-09-15 and reject the
    # rest of the universe with STOCK_WINDOW_PRICE_MISSING; the mode of each
    # ticker's own latest date correctly picks 2026-09-14.
    rows = [
        {"ticker": "THYAO", "trade_date": date(2026, 9, 14)},
        {"ticker": "THYAO", "trade_date": date(2026, 9, 15)},
    ] + [
        {"ticker": ticker, "trade_date": date(2026, 9, 14)}
        for ticker in ("AKBNK", "GARAN", "SISE", "EREGL")
    ]
    assert stock_bulk_frontier_date(rows) == date(2026, 9, 14)


def test_frontier_raises_on_empty_stock_rows():
    with pytest.raises(ValueError, match="current Yahoo bulk stock series missing"):
        stock_bulk_frontier_date([])


def test_frontier_picks_each_tickers_own_max_before_taking_mode():
    # A ticker with two rows must contribute only its own latest date, not both.
    rows = [
        {"ticker": "AKBNK", "trade_date": date(2026, 9, 10)},
        {"ticker": "AKBNK", "trade_date": date(2026, 9, 14)},
        {"ticker": "GARAN", "trade_date": date(2026, 9, 14)},
    ]
    assert stock_bulk_frontier_date(rows) == date(2026, 9, 14)
