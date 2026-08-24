from __future__ import annotations

"""PIT-safe, database-free replay of production Ek9 return volatility."""

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.analytics.ek9_volatility import (
    EK9_LOOKBACK_DAYS,
    compute_ek9_volatility_scores,
)

ISTANBUL = ZoneInfo("Europe/Istanbul")
EK9_COLUMNS = ["ticker", "ek9", "volatility", "observations", "start_date", "end_date"]
REJECTION_COLUMNS = ["ticker", "reason", "start_date", "end_date"]


class HistoricalPitEk9ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitEk9ReplayResult:
    analysis_at: datetime
    asof_date: date
    market_asof_date: date
    start_date: date | None
    end_date: date | None
    lookback_days: int
    market_index: str
    tickers: tuple[str, ...]
    ek9_scores: pd.DataFrame
    rejections: pd.DataFrame


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalPitEk9ReplayError("analysis_at timezone-aware datetime olmali")
    return value


def _date_value(value: object, field: str) -> date:
    try:
        parsed = pd.Timestamp(value)
    except Exception as exc:
        raise HistoricalPitEk9ReplayError(f"{field} gecersiz") from exc
    if pd.isna(parsed):
        raise HistoricalPitEk9ReplayError(f"{field} bos olamaz")
    return parsed.date()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitEk9ReplayError(f"{field} dolu metin olmali")
    return value.strip().upper()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HistoricalPitEk9ReplayError(f"{field} pozitif int olmali")
    return value


def _require(frame: pd.DataFrame, columns: Iterable[str], name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise HistoricalPitEk9ReplayError(f"{name} DataFrame olmali")
    missing = set(columns) - set(frame.columns)
    if missing:
        raise HistoricalPitEk9ReplayError(f"{name} missing columns: {sorted(missing)}")
    return frame.copy(deep=True)


def _prepare_universe(frame: pd.DataFrame) -> tuple[pd.DataFrame, tuple[str, ...]]:
    out = _require(frame, ("ticker",), "universe")
    if out.empty:
        raise HistoricalPitEk9ReplayError("historical universe bos olamaz")
    out["ticker"] = out["ticker"].map(lambda value: _text(value, "ticker"))
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitEk9ReplayError("universe duplicate ticker iceriyor")
    out = out.loc[:, ["ticker"]].sort_values("ticker").reset_index(drop=True)
    return out, tuple(out["ticker"])


def _prepare_calendar(frame: pd.DataFrame, *, market_asof: date) -> list[date]:
    out = _require(frame, ("trade_date",), "trading_calendar")
    if out.empty:
        raise HistoricalPitEk9ReplayError("trading_calendar bos olamaz")
    try:
        days = pd.to_datetime(out["trade_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitEk9ReplayError("trading_calendar.trade_date gecersiz") from exc
    if days.isna().any():
        raise HistoricalPitEk9ReplayError("trading_calendar.trade_date bos olamaz")
    if days.duplicated().any():
        raise HistoricalPitEk9ReplayError("trading_calendar duplicate trade_date iceriyor")
    if any(day > market_asof for day in days):
        raise HistoricalPitEk9ReplayError("market_asof_date sonrasi takvim gunu Ek9'a sizdi")
    return sorted(days.tolist())


def _numeric_or_error(series: pd.Series, field: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    raw_missing = series.isna()
    if ((~raw_missing) & numeric.isna()).any():
        raise HistoricalPitEk9ReplayError(f"{field} sayisal olmali")
    return numeric


def _finite_positive(series: pd.Series, field: str) -> pd.Series:
    invalid = series.isna() | ~series.map(
        lambda value: value is not None and math.isfinite(float(value)) and float(value) > 0
    )
    if invalid.any():
        raise HistoricalPitEk9ReplayError(f"{field} pozitif sonlu olmali")
    return series.astype(float)


def _prepare_stock_prices(
    frame: pd.DataFrame,
    *,
    tickers: tuple[str, ...],
    market_asof: date,
) -> pd.DataFrame:
    out = _require(frame, ("ticker", "trade_date", "adj_close", "close"), "stock_prices")
    if out.empty:
        return pd.DataFrame(columns=["ticker", "trade_date", "px"])
    out["ticker"] = out["ticker"].map(lambda value: _text(value, "stock_prices.ticker"))
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitEk9ReplayError(
            f"stock_prices historical universe disi ticker iceriyor: {foreign}"
        )
    try:
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitEk9ReplayError("stock_prices.trade_date gecersiz") from exc
    if out["trade_date"].isna().any():
        raise HistoricalPitEk9ReplayError("stock_prices.trade_date bos olamaz")
    if any(day > market_asof for day in out["trade_date"]):
        raise HistoricalPitEk9ReplayError("market_asof_date sonrasi hisse fiyati Ek9'a sizdi")
    if out.duplicated(["ticker", "trade_date"]).any():
        raise HistoricalPitEk9ReplayError("stock_prices duplicate ticker.trade_date iceriyor")
    adjusted = _numeric_or_error(out["adj_close"], "stock_prices.adj_close")
    close = _numeric_or_error(out["close"], "stock_prices.close")
    out["px"] = _finite_positive(
        adjusted.where(adjusted.notna(), close),
        "stock_prices COALESCE(adj_close,close)",
    )
    return out.loc[:, ["ticker", "trade_date", "px"]].sort_values(
        ["trade_date", "ticker"]
    ).reset_index(drop=True)


def _empty_result(
    *,
    analysis_at: datetime,
    asof: date,
    market_asof: date,
    lookback: int,
    market_index: str,
    tickers: tuple[str, ...],
    reason: str,
    start: date | None = None,
    end: date | None = None,
) -> HistoricalPitEk9ReplayResult:
    return HistoricalPitEk9ReplayResult(
        analysis_at=analysis_at,
        asof_date=asof,
        market_asof_date=market_asof,
        start_date=start,
        end_date=end,
        lookback_days=lookback,
        market_index=market_index,
        tickers=tickers,
        ek9_scores=pd.DataFrame(columns=EK9_COLUMNS),
        rejections=pd.DataFrame(
            [(ticker, reason, start, end) for ticker in tickers],
            columns=REJECTION_COLUMNS,
        ),
    )


def run_historical_pit_ek9_replay(
    *,
    analysis_at: datetime,
    asof_date: str | date | datetime,
    market_asof_date: str | date | datetime,
    universe: pd.DataFrame,
    trading_calendar: pd.DataFrame,
    stock_prices: pd.DataFrame,
    lookback_days: int = EK9_LOOKBACK_DAYS,
    market_index: str = "XU100",
) -> HistoricalPitEk9ReplayResult:
    """Reproduce production Ek9 from 63 daily returns without DB access.

    The caller supplies the date-correct historical universe, an XU100 trading
    calendar already bounded by the cutoff policy, and historical stock prices.
    XU100 is a calendar authority only; an index price is never accepted as a
    substitute for a missing stock observation.
    """
    analysis = _aware(analysis_at)
    asof = _date_value(asof_date, "asof_date")
    market_asof = _date_value(market_asof_date, "market_asof_date")
    local_day = analysis.astimezone(ISTANBUL).date()
    if asof > local_day:
        raise HistoricalPitEk9ReplayError("analysis_at sonrasi asof_date Ek9'a sizdi")
    if market_asof > asof:
        raise HistoricalPitEk9ReplayError("market_asof_date asof_date'den sonra olamaz")
    if market_asof > local_day:
        raise HistoricalPitEk9ReplayError("analysis_at sonrasi market_asof_date Ek9'a sizdi")

    lookback = _positive_int(lookback_days, "lookback_days")
    if lookback != EK9_LOOKBACK_DAYS:
        raise HistoricalPitEk9ReplayError("production Ek9 lookback_days 63 olmali")
    market = _text(market_index, "market_index")
    if market != "XU100":
        raise HistoricalPitEk9ReplayError("V24 Ek9 trading calendar authority XU100 olmali")

    _, tickers = _prepare_universe(universe)
    calendar = _prepare_calendar(trading_calendar, market_asof=market_asof)
    prices = _prepare_stock_prices(stock_prices, tickers=tickers, market_asof=market_asof)
    calendar_set = set(calendar)
    if not set(prices["trade_date"]).issubset(calendar_set):
        raise HistoricalPitEk9ReplayError("stock_prices trading_calendar disi tarih iceriyor")

    eligible_days = [day for day in calendar if day <= market_asof]
    if not eligible_days:
        return _empty_result(
            analysis_at=analysis,
            asof=asof,
            market_asof=market_asof,
            lookback=lookback,
            market_index=market,
            tickers=tickers,
            reason="MARKET_END_DATE_UNAVAILABLE",
        )
    end = eligible_days[-1]
    if len(eligible_days) < lookback + 2:
        return _empty_result(
            analysis_at=analysis,
            asof=asof,
            market_asof=market_asof,
            lookback=lookback,
            market_index=market,
            tickers=tickers,
            reason="EK9_WINDOW_UNAVAILABLE",
            end=end,
        )

    window_dates = eligible_days[-(lookback + 1):]
    start = window_dates[0]
    wanted = set(window_dates)
    return_series: dict[str, pd.Series] = {}
    rejection_rows: list[tuple[object, ...]] = []
    for ticker in tickers:
        sub = prices.loc[
            (prices["ticker"] == ticker) & prices["trade_date"].isin(wanted),
            ["trade_date", "px"],
        ].set_index("trade_date")["px"]
        if set(sub.index) != wanted:
            rejection_rows.append((ticker, "STOCK_WINDOW_PRICE_MISSING", start, end))
            continue
        ordered = sub.reindex(window_dates)
        ret = ordered.pct_change(fill_method=None).iloc[1:]
        if len(ret) != lookback or ret.isna().any() or not np.isfinite(ret.to_numpy(dtype=float)).all():
            rejection_rows.append((ticker, "STOCK_RETURN_WINDOW_INVALID", start, end))
            continue
        return_series[ticker] = pd.Series(
            ret.to_numpy(dtype=float), index=window_dates[1:], dtype=float
        )

    score_rows: list[dict[str, object]] = []
    if return_series:
        returns = pd.DataFrame(return_series, index=window_dates[1:])
        scored = compute_ek9_volatility_scores(returns)
        for ticker in sorted(return_series):
            point = scored.loc[ticker]
            score_rows.append(
                {
                    "ticker": ticker,
                    "ek9": float(point["ek9"]),
                    "volatility": float(point["volatility"]),
                    "observations": lookback,
                    "start_date": start,
                    "end_date": end,
                }
            )

    scores = pd.DataFrame(score_rows, columns=EK9_COLUMNS).sort_values("ticker").reset_index(drop=True)
    rejections = pd.DataFrame(rejection_rows, columns=REJECTION_COLUMNS).sort_values("ticker").reset_index(drop=True)
    if set(scores["ticker"]) & set(rejections["ticker"]):
        raise HistoricalPitEk9ReplayError("Ek9 ticker hem score hem rejection uretti")
    if set(scores["ticker"]) | set(rejections["ticker"]) != set(tickers):
        raise HistoricalPitEk9ReplayError("Ek9 score/rejection coverage invariant bozuldu")

    return HistoricalPitEk9ReplayResult(
        analysis_at=analysis,
        asof_date=asof,
        market_asof_date=market_asof,
        start_date=start,
        end_date=end,
        lookback_days=lookback,
        market_index=market,
        tickers=tickers,
        ek9_scores=scores,
        rejections=rejections,
    )
