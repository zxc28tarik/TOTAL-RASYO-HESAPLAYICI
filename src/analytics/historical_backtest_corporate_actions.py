from __future__ import annotations

"""Fail-closed diagnostic for raw-price corporate-action discontinuities.

V24-B executes and marks positions with raw OPEN/CLOSE.  ``prices_daily`` also
stores ``adj_close`` when the yfinance loader is used.  A change in
``adj_close / close`` between monthly execution dates is evidence that raw
prices are not return-continuous across that holding interval (for example a
split/bonus issue or a cash-distribution adjustment).

This module does not guess the corporate action and does not repair prices or
shares.  It only blocks a real-performance claim until the event semantics are
handled explicitly.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Any, Optional

import pandas as pd

from src.analytics.historical_backtest_db import HistoricalBacktestDatabaseError
from src.analytics.historical_backtest_inventory import (
    _active_tickers,
    _read_sql,
    _signal_dates,
    _window,
)


@dataclass(frozen=True)
class CorporateActionPriceAudit:
    start_month: str
    end_month: str
    index_code: str
    expected_execution_ticker_pairs: int
    verified_adjustment_pairs: int
    unverifiable_pairs: int
    adjustment_change_tickers: tuple[str, ...]
    adjustment_change_count: int

    @property
    def status(self) -> str:
        if self.expected_execution_ticker_pairs == 0:
            return "NO_EXECUTION_UNIVERSE"
        if self.unverifiable_pairs or self.adjustment_change_tickers:
            return "BLOCKED"
        return "CLEAR_NO_ADJUSTMENT_CHANGE"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "start_month": self.start_month,
            "end_month": self.end_month,
            "index_code": self.index_code,
            "expected_execution_ticker_pairs": self.expected_execution_ticker_pairs,
            "verified_adjustment_pairs": self.verified_adjustment_pairs,
            "unverifiable_pairs": self.unverifiable_pairs,
            "adjustment_change_tickers": list(self.adjustment_change_tickers),
            "adjustment_change_count": self.adjustment_change_count,
            "interpretation": (
                "BLOCKED means raw OPEN/CLOSE cannot yet support a defensible real-performance claim; "
                "this audit never repairs prices, share counts, or dividends."
            ),
        }


def audit_corporate_action_price_continuity(
    conn: Any,
    *,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
    factor_tolerance: float = 1e-8,
) -> CorporateActionPriceAudit:
    start_date, end_exclusive, _ = _window(start_month, end_month, expected_months)
    code = str(index_code).strip().upper()
    if not code:
        raise HistoricalBacktestDatabaseError("index_code dolu metin olmali")
    if not isfinite(float(factor_tolerance)) or float(factor_tolerance) < 0:
        raise HistoricalBacktestDatabaseError("factor_tolerance negatif olmayan sonlu sayi olmali")

    index_prices = _read_sql(
        conn,
        """
        SELECT index_code, trade_date, open, close
          FROM core.index_prices_daily
         WHERE index_code = %s
           AND trade_date >= %s
           AND trade_date < %s
         ORDER BY trade_date
        """,
        (code, start_date.date(), end_exclusive.date()),
    )
    signals = _signal_dates(index_prices, code)
    membership = _read_sql(
        conn,
        """
        SELECT ticker, valid_from, valid_to, is_tradable
          FROM core.universe_membership_history
         WHERE valid_from < %s
           AND (valid_to IS NULL OR valid_to > %s)
         ORDER BY ticker, valid_from
        """,
        (end_exclusive.date(), start_date.date()),
    )
    expected = {
        (signal, ticker)
        for signal in signals
        for ticker in _active_tickers(membership, signal)
    }
    tickers = sorted({ticker for _, ticker in expected})
    if signals and tickers:
        prices = _read_sql(
            conn,
            """
            SELECT ticker, trade_date, close, adj_close
              FROM core.prices_daily
             WHERE trade_date = ANY(%s)
               AND ticker = ANY(%s)
             ORDER BY ticker, trade_date
            """,
            ([d.date() for d in signals], tickers),
        )
    else:
        prices = pd.DataFrame(columns=["ticker", "trade_date", "close", "adj_close"])

    frame = prices.copy()
    if not frame.empty:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
        frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame["adj_close"] = pd.to_numeric(frame["adj_close"], errors="coerce")
        if frame.duplicated(["trade_date", "ticker"]).any():
            return CorporateActionPriceAudit(
                start_month, end_month, code, len(expected), 0, len(expected), tuple(), 0
            )

    factor_by_pair: dict[tuple[pd.Timestamp, str], float] = {}
    for row in frame.itertuples(index=False):
        key = (pd.Timestamp(row.trade_date).normalize(), str(row.ticker))
        try:
            close = float(row.close)
            adj = float(row.adj_close)
        except (TypeError, ValueError):
            continue
        if not (isfinite(close) and isfinite(adj) and close > 0 and adj > 0):
            continue
        factor_by_pair[key] = adj / close

    verified = expected & set(factor_by_pair)
    unverifiable = len(expected - set(factor_by_pair))
    changed: list[str] = []
    change_count = 0
    for ticker in tickers:
        series = sorted(
            (day, factor_by_pair[(day, ticker)])
            for day, t in expected
            if t == ticker and (day, ticker) in factor_by_pair
        )
        ticker_changed = False
        for (_, left), (_, right) in zip(series, series[1:]):
            scale = max(1.0, abs(left), abs(right))
            if abs(right - left) > float(factor_tolerance) * scale:
                change_count += 1
                ticker_changed = True
        if ticker_changed:
            changed.append(ticker)

    return CorporateActionPriceAudit(
        start_month=start_month,
        end_month=end_month,
        index_code=code,
        expected_execution_ticker_pairs=len(expected),
        verified_adjustment_pairs=len(verified),
        unverifiable_pairs=unverifiable,
        adjustment_change_tickers=tuple(sorted(changed)),
        adjustment_change_count=change_count,
    )
