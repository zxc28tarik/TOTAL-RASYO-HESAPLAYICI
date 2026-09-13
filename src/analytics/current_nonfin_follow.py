from __future__ import annotations

"""Fail-closed current NONFIN FOLLOW axis from two real valuation periods."""

from datetime import date
import math
from typing import Any, Iterable, Mapping

import pandas as pd

from src.analytics.m2_period import compute_m2_follow_score


CONTRACT = "CURRENT_NONFIN_FOLLOW_AXIS_V1"


def _positive(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} pozitif sonlu sayi olmali")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} pozitif sonlu sayi olmali")
    return number


def _quarter_before(value: date) -> date:
    if value.month == 3 and value.day == 31:
        return date(value.year - 1, 12, 31)
    if value.month == 6 and value.day == 30:
        return date(value.year, 3, 31)
    if value.month == 9 and value.day == 30:
        return date(value.year, 6, 30)
    if value.month == 12 and value.day == 31:
        return date(value.year, 9, 30)
    raise ValueError("anchor_period_end takvim ceyrek sonu olmali")


def materialize_current_nonfin_follow(
    *,
    current_valuations: Iterable[Mapping[str, Any]],
    previous_valuations: Iterable[Mapping[str, Any]],
    adjusted_prices: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    required = {"ticker", "trade_date", "adj_close"}
    if not isinstance(adjusted_prices, pd.DataFrame) or not required.issubset(adjusted_prices.columns):
        raise ValueError("adjusted_prices gerekli kolonlari icermeli")
    prices = adjusted_prices.copy()
    prices["ticker"] = prices["ticker"].astype(str).str.strip().str.upper()
    prices["trade_date"] = pd.to_datetime(prices["trade_date"], errors="raise").dt.date
    prices["adj_close"] = pd.to_numeric(prices["adj_close"], errors="coerce")
    previous = {str(row.get("ticker", "")).strip().upper(): row for row in previous_valuations}
    rows: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    for current in sorted(current_valuations, key=lambda row: str(row.get("ticker", ""))):
        ticker = str(current.get("ticker", "")).strip().upper()
        try:
            if current.get("status") != "OK":
                raise ValueError("CURRENT_VALUATION_NOT_OK")
            prior = previous.get(ticker)
            if prior is None or prior.get("status") != "OK":
                raise ValueError("PREVIOUS_VALUATION_NOT_OK")
            current_anchor = pd.Timestamp(current["anchor_period_end"]).date()
            prior_anchor = pd.Timestamp(prior["anchor_period_end"]).date()
            if prior_anchor != _quarter_before(current_anchor):
                raise ValueError("VALUATION_PERIODS_NOT_CONSECUTIVE")
            current_mid = _positive(current.get("V_mid"), "current.V_mid")
            prior_mid = _positive(prior.get("V_mid"), "previous.V_mid")
            ticker_prices = prices.loc[
                (prices["ticker"] == ticker) & prices["adj_close"].notna()
            ].sort_values("trade_date")
            prior_px_rows = ticker_prices.loc[ticker_prices["trade_date"] <= prior_anchor]
            if prior_px_rows.empty or ticker_prices.empty:
                raise ValueError("ADJUSTED_PRICE_AXIS_MISSING")
            prior_px_row = prior_px_rows.iloc[-1]
            current_px_row = ticker_prices.iloc[-1]
            prior_px = _positive(prior_px_row["adj_close"], "previous.adjusted_close")
            current_px = _positive(current_px_row["adj_close"], "current.adjusted_close")
            band_change = current_mid / prior_mid - 1.0
            price_change = current_px / prior_px - 1.0
            gap = band_change - price_change
            rows.append({
                "ticker": ticker,
                "follow_score": compute_m2_follow_score(gap),
                "follow_active": True,
                "current_period_end": current_anchor.isoformat(),
                "previous_period_end": prior_anchor.isoformat(),
                "current_band_mid": current_mid,
                "previous_band_mid": prior_mid,
                "band_mid_change_1q": band_change,
                "current_price_trade_date": current_px_row["trade_date"].isoformat(),
                "previous_price_trade_date": prior_px_row["trade_date"].isoformat(),
                "current_adjusted_close": current_px,
                "previous_adjusted_close": prior_px,
                "price_change_since_previous_period": price_change,
                "follow_gap_1q": gap,
                "price_return_basis": "YAHOO_ADJUSTED_CLOSE_RETURN_CONTINUITY_ONLY",
                "valuation_band_basis": "COMMON_CURRENT_QUOTED_NOMINAL_UNITS",
            })
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            rejections.append({"ticker": ticker, "reason": str(exc)})
    return rows, rejections
