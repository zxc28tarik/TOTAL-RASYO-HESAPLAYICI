from __future__ import annotations

"""PIT-safe, database-free replay of production Ek4 sector momentum."""

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from src.analytics.ek4_momentum import compute_ek4_momentum_point


ISTANBUL = ZoneInfo("Europe/Istanbul")
EK4_COLUMNS = [
    "ticker",
    "ek4",
    "stock_return_20d",
    "sector_return_20d",
    "excess_return_20d",
    "sector_index_code",
    "start_date",
    "end_date",
]
REJECTION_COLUMNS = ["ticker", "reason", "start_date", "end_date"]


class HistoricalPitEk4ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitEk4ReplayResult:
    analysis_at: datetime
    asof_date: date
    market_asof_date: date
    start_date: date | None
    end_date: date | None
    lookback_days: int
    tickers: tuple[str, ...]
    ek4_scores: pd.DataFrame
    rejections: pd.DataFrame


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalPitEk4ReplayError("analysis_at timezone-aware datetime olmali")
    return value


def _date_value(value: object, field: str) -> date:
    try:
        parsed = pd.Timestamp(value)
    except Exception as exc:
        raise HistoricalPitEk4ReplayError(f"{field} gecersiz") from exc
    if pd.isna(parsed):
        raise HistoricalPitEk4ReplayError(f"{field} bos olamaz")
    return parsed.date()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitEk4ReplayError(f"{field} dolu metin olmali")
    return value.strip().upper()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HistoricalPitEk4ReplayError(f"{field} pozitif int olmali")
    return value


def _require(frame: pd.DataFrame, columns: Iterable[str], name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise HistoricalPitEk4ReplayError(f"{name} DataFrame olmali")
    missing = set(columns) - set(frame.columns)
    if missing:
        raise HistoricalPitEk4ReplayError(f"{name} missing columns: {sorted(missing)}")
    return frame.copy(deep=True)


def _prepare_universe(
    frame: pd.DataFrame,
    *,
    market_index: str,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    out = _require(frame, ("ticker", "sector_index_code"), "universe")
    if out.empty:
        raise HistoricalPitEk4ReplayError("historical universe bos olamaz")
    out["ticker"] = out["ticker"].map(lambda value: _text(value, "ticker"))
    out["sector_index_code"] = out["sector_index_code"].map(
        lambda value: _text(value, "sector_index_code")
    )
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitEk4ReplayError("universe duplicate ticker iceriyor")
    if (out["sector_index_code"] == market_index).any():
        raise HistoricalPitEk4ReplayError(
            "XU100 Ek4 sektor rotasi/fallback'i olarak kullanilamaz"
        )
    out = out.loc[:, ["ticker", "sector_index_code"]].sort_values("ticker").reset_index(
        drop=True
    )
    return out, tuple(out["ticker"])


def _prepare_calendar(frame: pd.DataFrame, *, market_asof: date) -> list[date]:
    out = _require(frame, ("trade_date",), "trading_calendar")
    if out.empty:
        raise HistoricalPitEk4ReplayError("trading_calendar bos olamaz")
    try:
        days = pd.to_datetime(out["trade_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitEk4ReplayError("trading_calendar.trade_date gecersiz") from exc
    if days.isna().any():
        raise HistoricalPitEk4ReplayError("trading_calendar.trade_date bos olamaz")
    if days.duplicated().any():
        raise HistoricalPitEk4ReplayError("trading_calendar duplicate trade_date iceriyor")
    if any(day > market_asof for day in days):
        raise HistoricalPitEk4ReplayError("market_asof_date sonrasi takvim gunu Ek4'e sizdi")
    return sorted(days.tolist())


def _finite_positive(series: pd.Series, field: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    invalid = numeric.isna() | ~numeric.map(
        lambda value: math.isfinite(float(value)) and float(value) > 0
    )
    if invalid.any():
        raise HistoricalPitEk4ReplayError(f"{field} pozitif sonlu olmali")
    return numeric.astype(float)


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
        raise HistoricalPitEk4ReplayError(
            f"stock_prices historical universe disi ticker iceriyor: {foreign}"
        )
    try:
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitEk4ReplayError("stock_prices.trade_date gecersiz") from exc
    if out["trade_date"].isna().any():
        raise HistoricalPitEk4ReplayError("stock_prices.trade_date bos olamaz")
    if any(day > market_asof for day in out["trade_date"]):
        raise HistoricalPitEk4ReplayError("market_asof_date sonrasi hisse fiyati Ek4'e sizdi")
    if out.duplicated(["ticker", "trade_date"]).any():
        raise HistoricalPitEk4ReplayError("stock_prices duplicate ticker.trade_date iceriyor")
    adjusted = pd.to_numeric(out["adj_close"], errors="coerce")
    close = pd.to_numeric(out["close"], errors="coerce")
    out["px"] = _finite_positive(
        adjusted.where(adjusted.notna(), close),
        "stock_prices COALESCE(adj_close,close)",
    )
    return out.loc[:, ["ticker", "trade_date", "px"]].sort_values(
        ["trade_date", "ticker"]
    ).reset_index(drop=True)


def _prepare_index_prices(frame: pd.DataFrame, *, market_asof: date) -> pd.DataFrame:
    out = _require(frame, ("index_code", "trade_date", "close"), "index_prices")
    if out.empty:
        return pd.DataFrame(columns=["index_code", "trade_date", "px"])
    out["index_code"] = out["index_code"].map(
        lambda value: _text(value, "index_prices.index_code")
    )
    try:
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitEk4ReplayError("index_prices.trade_date gecersiz") from exc
    if out["trade_date"].isna().any():
        raise HistoricalPitEk4ReplayError("index_prices.trade_date bos olamaz")
    if any(day > market_asof for day in out["trade_date"]):
        raise HistoricalPitEk4ReplayError("market_asof_date sonrasi endeks fiyati Ek4'e sizdi")
    if out.duplicated(["index_code", "trade_date"]).any():
        raise HistoricalPitEk4ReplayError("index_prices duplicate index_code.trade_date iceriyor")
    out["px"] = _finite_positive(out["close"], "index_prices.close")
    return out.loc[:, ["index_code", "trade_date", "px"]].sort_values(
        ["trade_date", "index_code"]
    ).reset_index(drop=True)


def _empty_result(
    *,
    analysis_at: datetime,
    asof: date,
    market_asof: date,
    lookback: int,
    tickers: tuple[str, ...],
    reason: str,
    start: date | None = None,
    end: date | None = None,
) -> HistoricalPitEk4ReplayResult:
    return HistoricalPitEk4ReplayResult(
        analysis_at=analysis_at,
        asof_date=asof,
        market_asof_date=market_asof,
        start_date=start,
        end_date=end,
        lookback_days=lookback,
        tickers=tickers,
        ek4_scores=pd.DataFrame(columns=EK4_COLUMNS),
        rejections=pd.DataFrame(
            [(ticker, reason, start, end) for ticker in tickers],
            columns=REJECTION_COLUMNS,
        ),
    )


def run_historical_pit_ek4_replay(
    *,
    analysis_at: datetime,
    asof_date: str | date | datetime,
    market_asof_date: str | date | datetime,
    universe: pd.DataFrame,
    trading_calendar: pd.DataFrame,
    stock_prices: pd.DataFrame,
    index_prices: pd.DataFrame,
    lookback_days: int = 20,
    market_index: str = "XU100",
) -> HistoricalPitEk4ReplayResult:
    """Reproduce Ek4 from two endpoints separated by 20 trading intervals.

    ``asof_date`` labels the signal. ``market_asof_date`` is the last market
    knowledge date allowed by the caller's cutoff policy.  The caller supplies
    the date-correct sector route resolved from the shared historical M3 package.
    """

    analysis = _aware(analysis_at)
    asof = _date_value(asof_date, "asof_date")
    market_asof = _date_value(market_asof_date, "market_asof_date")
    local_day = analysis.astimezone(ISTANBUL).date()
    if asof > local_day:
        raise HistoricalPitEk4ReplayError("analysis_at sonrasi asof_date Ek4'e sizdi")
    if market_asof > asof:
        raise HistoricalPitEk4ReplayError("market_asof_date asof_date'den sonra olamaz")
    if market_asof > local_day:
        raise HistoricalPitEk4ReplayError("analysis_at sonrasi market_asof_date Ek4'e sizdi")

    lookback = _positive_int(lookback_days, "lookback_days")
    market = _text(market_index, "market_index")
    hist_universe, tickers = _prepare_universe(universe, market_index=market)
    calendar = _prepare_calendar(trading_calendar, market_asof=market_asof)
    prices = _prepare_stock_prices(stock_prices, tickers=tickers, market_asof=market_asof)
    indices = _prepare_index_prices(index_prices, market_asof=market_asof)
    calendar_set = set(calendar)
    if not set(prices["trade_date"]).issubset(calendar_set):
        raise HistoricalPitEk4ReplayError("stock_prices trading_calendar disi tarih iceriyor")
    if not set(indices["trade_date"]).issubset(calendar_set):
        raise HistoricalPitEk4ReplayError("index_prices trading_calendar disi tarih iceriyor")

    eligible_days = [day for day in calendar if day <= market_asof]
    if not eligible_days:
        return _empty_result(
            analysis_at=analysis,
            asof=asof,
            market_asof=market_asof,
            lookback=lookback,
            tickers=tickers,
            reason="MARKET_END_DATE_UNAVAILABLE",
        )
    end = eligible_days[-1]
    end_pos = calendar.index(end)
    if end_pos < lookback:
        return _empty_result(
            analysis_at=analysis,
            asof=asof,
            market_asof=market_asof,
            lookback=lookback,
            tickers=tickers,
            reason="EK4_WINDOW_UNAVAILABLE",
            end=end,
        )
    start = calendar[end_pos - lookback]

    endpoint_prices = prices.loc[prices["trade_date"].isin([start, end])]
    endpoint_indices = indices.loc[indices["trade_date"].isin([start, end])]
    stock_values = {
        (str(row.ticker), row.trade_date): float(row.px)
        for row in endpoint_prices.itertuples(index=False)
    }
    index_values = {
        (str(row.index_code), row.trade_date): float(row.px)
        for row in endpoint_indices.itertuples(index=False)
    }
    sector_map = dict(zip(hist_universe["ticker"], hist_universe["sector_index_code"]))
    score_rows: list[dict[str, object]] = []
    rejection_rows: list[tuple[object, ...]] = []
    for ticker in tickers:
        sector = sector_map[ticker]
        if (ticker, start) not in stock_values or (ticker, end) not in stock_values:
            rejection_rows.append((ticker, "STOCK_WINDOW_PRICE_MISSING", start, end))
            continue
        if (sector, start) not in index_values or (sector, end) not in index_values:
            rejection_rows.append((ticker, "SECTOR_WINDOW_PRICE_MISSING", start, end))
            continue
        point = compute_ek4_momentum_point(
            stock_start=stock_values[(ticker, start)],
            stock_end=stock_values[(ticker, end)],
            sector_start=index_values[(sector, start)],
            sector_end=index_values[(sector, end)],
        )
        score_rows.append(
            {
                "ticker": ticker,
                "ek4": point.score,
                "stock_return_20d": point.stock_return,
                "sector_return_20d": point.sector_return,
                "excess_return_20d": point.excess_return,
                "sector_index_code": sector,
                "start_date": start,
                "end_date": end,
            }
        )

    scores = pd.DataFrame(score_rows, columns=EK4_COLUMNS).sort_values("ticker").reset_index(
        drop=True
    )
    rejections = pd.DataFrame(rejection_rows, columns=REJECTION_COLUMNS).sort_values(
        "ticker"
    ).reset_index(drop=True)
    if set(scores["ticker"]) & set(rejections["ticker"]):
        raise HistoricalPitEk4ReplayError("Ek4 ticker hem score hem rejection uretti")
    if set(scores["ticker"]) | set(rejections["ticker"]) != set(tickers):
        raise HistoricalPitEk4ReplayError("Ek4 score/rejection coverage invariant bozuldu")

    return HistoricalPitEk4ReplayResult(
        analysis_at=analysis,
        asof_date=asof,
        market_asof_date=market_asof,
        start_date=start,
        end_date=end,
        lookback_days=lookback,
        tickers=tickers,
        ek4_scores=scores,
        rejections=rejections,
    )
