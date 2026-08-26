from __future__ import annotations

"""PIT-safe, database-free replay of production M3 trailing alpha."""

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.analytics.betas import estimate_betas_from_frames
from src.analytics.trailing_alpha import ALPHA_COLUMNS, compute_trailing_alpha_from_frames


ISTANBUL = ZoneInfo("Europe/Istanbul")


class HistoricalPitM3ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitM3ReplayResult:
    analysis_at: datetime
    asof_date: date
    market_asof_date: date
    start_date: date | None
    end_date: date | None
    tickers: tuple[str, ...]
    beta_estimates: pd.DataFrame
    alpha_trailing: pd.DataFrame
    m3_scores: pd.DataFrame
    rejections: pd.DataFrame


M3_COLUMNS = [
    "ticker", "m3", "alpha_trailing", "alpha_label", "beta_mkt", "beta_sec",
    "beta_n_obs", "beta_source", "start_date", "end_date",
]
REJECTION_COLUMNS = ["ticker", "reason", "start_date", "end_date"]
BETA_COLUMNS = ["ticker", "t0_date", "beta_mkt", "beta_sec", "r2", "n_obs"]


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalPitM3ReplayError("analysis_at timezone-aware datetime olmali")
    return value


def _date_value(value: object, field: str) -> date:
    try:
        parsed = pd.Timestamp(value)
    except Exception as exc:
        raise HistoricalPitM3ReplayError(f"{field} gecersiz") from exc
    if pd.isna(parsed):
        raise HistoricalPitM3ReplayError(f"{field} bos olamaz")
    return parsed.date()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitM3ReplayError(f"{field} dolu metin olmali")
    return value.strip().upper()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HistoricalPitM3ReplayError(f"{field} pozitif int olmali")
    return value


def _require(frame: pd.DataFrame, columns: Iterable[str], name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise HistoricalPitM3ReplayError(f"{name} DataFrame olmali")
    missing = set(columns) - set(frame.columns)
    if missing:
        raise HistoricalPitM3ReplayError(f"{name} missing columns: {sorted(missing)}")
    return frame.copy(deep=True)


def _prepare_universe(frame: pd.DataFrame) -> tuple[pd.DataFrame, tuple[str, ...]]:
    out = _require(frame, ("ticker", "sector_index_code"), "universe")
    if out.empty:
        raise HistoricalPitM3ReplayError("historical universe bos olamaz")
    out["ticker"] = out["ticker"].map(lambda value: _text(value, "ticker"))
    out["sector_index_code"] = out["sector_index_code"].map(
        lambda value: _text(value, "sector_index_code")
    )
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitM3ReplayError("universe duplicate ticker iceriyor")
    out = out.loc[:, ["ticker", "sector_index_code"]].sort_values("ticker").reset_index(drop=True)
    return out, tuple(out["ticker"])


def _prepare_calendar(frame: pd.DataFrame, *, market_asof: date) -> list[date]:
    out = _require(frame, ("trade_date",), "trading_calendar")
    if out.empty:
        raise HistoricalPitM3ReplayError("trading_calendar bos olamaz")
    try:
        days = pd.to_datetime(out["trade_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitM3ReplayError("trading_calendar.trade_date gecersiz") from exc
    if days.isna().any():
        raise HistoricalPitM3ReplayError("trading_calendar.trade_date bos olamaz")
    if days.duplicated().any():
        raise HistoricalPitM3ReplayError("trading_calendar duplicate trade_date iceriyor")
    if any(day > market_asof for day in days):
        raise HistoricalPitM3ReplayError("market_asof_date sonrasi takvim gunu M3'e sizdi")
    return sorted(days.tolist())


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
        raise HistoricalPitM3ReplayError(
            f"stock_prices historical universe disi ticker iceriyor: {foreign}"
        )
    try:
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitM3ReplayError("stock_prices.trade_date gecersiz") from exc
    if out["trade_date"].isna().any():
        raise HistoricalPitM3ReplayError("stock_prices.trade_date bos olamaz")
    if any(day > market_asof for day in out["trade_date"]):
        raise HistoricalPitM3ReplayError("market_asof_date sonrasi hisse fiyati M3'e sizdi")
    if out.duplicated(["ticker", "trade_date"]).any():
        raise HistoricalPitM3ReplayError("stock_prices duplicate ticker.trade_date iceriyor")

    adjusted = pd.to_numeric(out["adj_close"], errors="coerce")
    close = pd.to_numeric(out["close"], errors="coerce")
    px = adjusted.where(adjusted.notna(), close)
    invalid = px.isna() | ~px.map(lambda value: math.isfinite(float(value)) and float(value) > 0)
    if invalid.any():
        raise HistoricalPitM3ReplayError("stock_prices COALESCE(adj_close,close) pozitif sonlu olmali")
    out["px"] = px.astype(float)
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
        raise HistoricalPitM3ReplayError("index_prices.trade_date gecersiz") from exc
    if out["trade_date"].isna().any():
        raise HistoricalPitM3ReplayError("index_prices.trade_date bos olamaz")
    if any(day > market_asof for day in out["trade_date"]):
        raise HistoricalPitM3ReplayError("market_asof_date sonrasi endeks fiyati M3'e sizdi")
    if out.duplicated(["index_code", "trade_date"]).any():
        raise HistoricalPitM3ReplayError("index_prices duplicate index_code.trade_date iceriyor")
    close = pd.to_numeric(out["close"], errors="coerce")
    invalid = close.isna() | ~close.map(
        lambda value: math.isfinite(float(value)) and float(value) > 0
    )
    if invalid.any():
        raise HistoricalPitM3ReplayError("index_prices.close pozitif sonlu olmali")
    out["px"] = close.astype(float)
    return out.loc[:, ["index_code", "trade_date", "px"]].sort_values(
        ["trade_date", "index_code"]
    ).reset_index(drop=True)


def _empty_result(
    *,
    analysis_at: datetime,
    asof: date,
    market_asof: date,
    tickers: tuple[str, ...],
    reason: str,
    start: date | None = None,
    end: date | None = None,
) -> HistoricalPitM3ReplayResult:
    rejections = pd.DataFrame(
        [(ticker, reason, start, end) for ticker in tickers],
        columns=REJECTION_COLUMNS,
    )
    return HistoricalPitM3ReplayResult(
        analysis_at=analysis_at,
        asof_date=asof,
        market_asof_date=market_asof,
        start_date=start,
        end_date=end,
        tickers=tickers,
        beta_estimates=pd.DataFrame(columns=BETA_COLUMNS),
        alpha_trailing=pd.DataFrame(columns=ALPHA_COLUMNS),
        m3_scores=pd.DataFrame(columns=M3_COLUMNS),
        rejections=rejections,
    )


def run_historical_pit_m3_replay(
    *,
    analysis_at: datetime,
    asof_date: str | date | datetime,
    market_asof_date: str | date | datetime,
    universe: pd.DataFrame,
    trading_calendar: pd.DataFrame,
    stock_prices: pd.DataFrame,
    index_prices: pd.DataFrame,
    window_days: int = 63,
    beta_lookback_days: int = 252,
    market_index: str = "XU100",
) -> HistoricalPitM3ReplayResult:
    """Reproduce M3 without current-state reads or post-cutoff market data.

    ``asof_date`` labels the signal. ``market_asof_date`` is the last market
    knowledge date allowed by the caller's cutoff policy.  Keeping them separate
    prevents an opening-time signal from accidentally consuming that day's close.
    """

    analysis = _aware(analysis_at)
    asof = _date_value(asof_date, "asof_date")
    market_asof = _date_value(market_asof_date, "market_asof_date")
    local_day = analysis.astimezone(ISTANBUL).date()
    if asof > local_day:
        raise HistoricalPitM3ReplayError("analysis_at sonrasi asof_date M3'e sizdi")
    if market_asof > asof:
        raise HistoricalPitM3ReplayError("market_asof_date asof_date'den sonra olamaz")
    if market_asof > local_day:
        raise HistoricalPitM3ReplayError("analysis_at sonrasi market_asof_date M3'e sizdi")

    window = _positive_int(window_days, "window_days")
    beta_window = _positive_int(beta_lookback_days, "beta_lookback_days")
    market = _text(market_index, "market_index")
    hist_universe, tickers = _prepare_universe(universe)
    calendar = _prepare_calendar(trading_calendar, market_asof=market_asof)
    prices = _prepare_stock_prices(stock_prices, tickers=tickers, market_asof=market_asof)
    indices = _prepare_index_prices(index_prices, market_asof=market_asof)
    calendar_set = set(calendar)
    if not set(prices["trade_date"]).issubset(calendar_set):
        raise HistoricalPitM3ReplayError("stock_prices trading_calendar disi tarih iceriyor")
    if not set(indices["trade_date"]).issubset(calendar_set):
        raise HistoricalPitM3ReplayError("index_prices trading_calendar disi tarih iceriyor")

    eligible_days = [day for day in calendar if day <= market_asof]
    if not eligible_days:
        return _empty_result(
            analysis_at=analysis,
            asof=asof,
            market_asof=market_asof,
            tickers=tickers,
            reason="MARKET_END_DATE_UNAVAILABLE",
        )
    end = eligible_days[-1]
    end_pos = calendar.index(end)
    if end_pos < window:
        return _empty_result(
            analysis_at=analysis,
            asof=asof,
            market_asof=market_asof,
            tickers=tickers,
            reason="ALPHA_WINDOW_UNAVAILABLE",
            end=end,
        )
    start = calendar[end_pos - window]
    beta_start = calendar[max(0, end_pos - beta_window)]

    beta_prices = prices.loc[prices["trade_date"].between(beta_start, end)].copy()
    beta_indices = indices.loc[indices["trade_date"].between(beta_start, end)].copy()
    betas = estimate_betas_from_frames(
        universe=hist_universe,
        prices=beta_prices,
        index_prices=beta_indices,
        t0_date=end,
        market_index=market,
    )
    alpha_prices = prices.loc[prices["trade_date"].isin([start, end])].copy()
    alpha_indices = indices.loc[indices["trade_date"].isin([start, end])].copy()
    stock_pairs = set(zip(alpha_prices["ticker"], alpha_prices["trade_date"]))
    index_pairs = set(zip(alpha_indices["index_code"], alpha_indices["trade_date"]))
    sector_map = dict(zip(hist_universe["ticker"], hist_universe["sector_index_code"]))
    market_window_available = (market, start) in index_pairs and (market, end) in index_pairs
    eligible_tickers = [
        ticker
        for ticker in tickers
        if market_window_available
        and (ticker, start) in stock_pairs
        and (ticker, end) in stock_pairs
        and (sector_map[ticker], start) in index_pairs
        and (sector_map[ticker], end) in index_pairs
    ]
    alpha_universe = hist_universe.loc[hist_universe["ticker"].isin(eligible_tickers)].copy()
    alpha = compute_trailing_alpha_from_frames(
        universe=alpha_universe,
        prices=alpha_prices,
        index_prices=alpha_indices,
        betas=betas,
        asof_date=asof,
        start_date=start,
        end_date=end,
        window_days=window,
        market_index=market,
    ).sort_values("ticker").reset_index(drop=True)

    scored = set(alpha["ticker"]) if not alpha.empty else set()
    rejection_rows: list[tuple[object, ...]] = []
    for ticker in tickers:
        if ticker in scored:
            continue
        sector = sector_map[ticker]
        if (market, start) not in index_pairs or (market, end) not in index_pairs:
            reason = "MARKET_WINDOW_PRICE_MISSING"
        elif (ticker, start) not in stock_pairs or (ticker, end) not in stock_pairs:
            reason = "STOCK_WINDOW_PRICE_MISSING"
        elif (sector, start) not in index_pairs or (sector, end) not in index_pairs:
            reason = "SECTOR_WINDOW_PRICE_MISSING"
        else:
            reason = "PRODUCTION_ALPHA_REJECTED"
        rejection_rows.append((ticker, reason, start, end))

    beta_by_ticker = betas.set_index("ticker") if not betas.empty else pd.DataFrame()
    score_rows: list[dict[str, object]] = []
    for row in alpha.itertuples(index=False):
        raw_beta = beta_by_ticker.loc[row.ticker] if row.ticker in beta_by_ticker.index else None
        n_obs = int(raw_beta["n_obs"]) if raw_beta is not None else 0
        raw_mkt = float(raw_beta["beta_mkt"]) if raw_beta is not None else np.nan
        raw_sec = float(raw_beta["beta_sec"]) if raw_beta is not None else np.nan
        if math.isfinite(raw_mkt) and math.isfinite(raw_sec):
            beta_source = "ESTIMATED_TWO_FACTOR_SHRUNK"
        elif n_obs < 60:
            beta_source = "PRODUCTION_PRIOR_INSUFFICIENT_OBS"
        else:
            beta_source = "PRODUCTION_PRIOR_NONFINITE_ESTIMATE"
        score_rows.append(
            {
                "ticker": row.ticker,
                "m3": float(row.alpha_score),
                "alpha_trailing": float(row.alpha_trailing),
                "alpha_label": row.alpha_label,
                "beta_mkt": float(row.beta_mkt),
                "beta_sec": float(row.beta_sec),
                "beta_n_obs": n_obs,
                "beta_source": beta_source,
                "start_date": start,
                "end_date": end,
            }
        )

    m3 = pd.DataFrame(score_rows, columns=M3_COLUMNS).sort_values("ticker").reset_index(drop=True)
    rejections = pd.DataFrame(rejection_rows, columns=REJECTION_COLUMNS).sort_values(
        "ticker"
    ).reset_index(drop=True)
    if set(m3["ticker"]) & set(rejections["ticker"]):
        raise HistoricalPitM3ReplayError("M3 ticker hem score hem rejection uretti")
    if set(m3["ticker"]) | set(rejections["ticker"]) != set(tickers):
        raise HistoricalPitM3ReplayError("M3 score/rejection coverage invariant bozuldu")

    return HistoricalPitM3ReplayResult(
        analysis_at=analysis,
        asof_date=asof,
        market_asof_date=market_asof,
        start_date=start,
        end_date=end,
        tickers=tickers,
        beta_estimates=betas.sort_values("ticker").reset_index(drop=True),
        alpha_trailing=alpha,
        m3_scores=m3,
        rejections=rejections,
    )
