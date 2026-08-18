from __future__ import annotations

"""Database-free point-in-time replay of the production GYO M2 engine."""

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from src.analytics.gyo_batch_pipeline import build_gyo_snapshots_from_frames
from src.analytics.gyo_valuation import (
    GyoValuationConfig,
    GyoValuationError,
    evaluate_gyo_batch,
    validate_gyo_config,
)


ISTANBUL = ZoneInfo("Europe/Istanbul")


class HistoricalPitGyoM2ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitGyoM2ReplayResult:
    analysis_at: datetime
    tickers: tuple[str, ...]
    valuation_profile: str
    valuation_version: int
    report: Mapping[str, object]
    m2_scores: pd.DataFrame
    rejections: pd.DataFrame


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalPitGyoM2ReplayError("analysis_at timezone-aware datetime olmali")
    return value


def _ticker(value: object, field: str = "ticker") -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitGyoM2ReplayError(f"{field} dolu metin olmali")
    return value.strip().upper()


def _strict_bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if type(value).__name__ == "bool_" and str(value) in {"True", "False"}:
        return str(value) == "True"
    raise HistoricalPitGyoM2ReplayError(f"{field} Python/numpy bool olmali")


def _finite_01(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise HistoricalPitGyoM2ReplayError(f"{field} [0,1] sonlu sayi olmali")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HistoricalPitGyoM2ReplayError(f"{field} [0,1] sonlu sayi olmali") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise HistoricalPitGyoM2ReplayError(f"{field} [0,1] sonlu sayi olmali")
    return number


def _require(frame: pd.DataFrame, required: set[str], name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise HistoricalPitGyoM2ReplayError(f"{name} DataFrame olmali")
    missing = required - set(frame.columns)
    if missing:
        raise HistoricalPitGyoM2ReplayError(f"{name} missing columns: {sorted(missing)}")
    return frame.copy(deep=True)


def _prepare_universe(frame: pd.DataFrame) -> tuple[pd.DataFrame, tuple[str, ...]]:
    out = _require(frame, {"ticker", "peer_group", "sector_family"}, "universe")
    if out.empty:
        raise HistoricalPitGyoM2ReplayError("GYO historical universe bos olamaz")
    out["ticker"] = out["ticker"].map(_ticker)
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitGyoM2ReplayError("universe duplicate ticker iceriyor")
    out["sector_family"] = out["sector_family"].map(lambda v: _ticker(v, "sector_family"))
    if not out["sector_family"].eq("GYO").all():
        raise HistoricalPitGyoM2ReplayError("GYO replay baska sektor ailesi iceremez")
    out["peer_group"] = out["peer_group"].map(lambda v: _ticker(v, "peer_group"))
    tickers = tuple(sorted(out["ticker"].tolist()))
    return out.sort_values("ticker").reset_index(drop=True), tickers


def _prepare_navs(frame: pd.DataFrame, *, analysis_at: datetime, tickers: tuple[str, ...], config: GyoValuationConfig) -> pd.DataFrame:
    required = {
        "ticker", "nav_asof_date", "nav_published_at", "nav_total", "shares_out", "share_basis", "currency",
        "property_portfolio_value", "nav_source_method", "source_confidence", "source_document_id",
        "source_sha256", "nav_profile", "nav_version",
    }
    out = _require(frame, required, "navs")
    if out.empty:
        return out
    out["ticker"] = out["ticker"].map(_ticker)
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitGyoM2ReplayError(f"navs historical universe disi ticker iceriyor: {foreign}")
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitGyoM2ReplayError("navs ticker basina tam bir PIT satir olmali")
    try:
        nav_dates = pd.to_datetime(out["nav_asof_date"], errors="raise").dt.date
        published = pd.to_datetime(out["nav_published_at"], errors="raise", utc=True)
    except Exception as exc:
        raise HistoricalPitGyoM2ReplayError("nav tarih alanlari gecersiz") from exc
    analysis_utc = pd.Timestamp(analysis_at).tz_convert("UTC")
    if published.isna().any() or (published > analysis_utc).any():
        raise HistoricalPitGyoM2ReplayError("analysis_at sonrasi nav_published_at M2'ye sizdi")
    if any(d > p.tz_convert(ISTANBUL).date() for d, p in zip(nav_dates, published)):
        raise HistoricalPitGyoM2ReplayError("nav_asof_date nav_published_at sonrasinda olamaz")
    if not out["nav_profile"].astype(str).eq(config.source_nav_profile).all():
        raise HistoricalPitGyoM2ReplayError("nav_profile config source_nav_profile ile ayni olmali")
    versions = pd.to_numeric(out["nav_version"], errors="coerce")
    if not versions.eq(config.source_nav_version).all():
        raise HistoricalPitGyoM2ReplayError("nav_version config source_nav_version ile ayni olmali")
    if not out["share_basis"].astype(str).str.upper().eq(config.share_basis).all():
        raise HistoricalPitGyoM2ReplayError("share_basis config ile ayni olmali")
    if not out["currency"].astype(str).str.upper().eq(config.currency).all():
        raise HistoricalPitGyoM2ReplayError("currency config ile ayni olmali")
    methods = out["nav_source_method"].astype(str).str.upper()
    if not methods.isin({"DIRECT", "DERIVED"}).all():
        raise HistoricalPitGyoM2ReplayError("nav_source_method DIRECT/DERIVED olmali")
    out["nav_asof_date"] = nav_dates
    out["nav_published_at"] = published.map(lambda x: x.to_pydatetime())
    out["nav_source_method"] = methods
    return out.sort_values("ticker").reset_index(drop=True)


def _prepare_prices(frame: pd.DataFrame, *, analysis_at: datetime, tickers: tuple[str, ...]) -> pd.DataFrame:
    out = _require(frame, {"ticker", "price_trade_date", "current_price"}, "prices")
    if out.empty:
        return out
    out["ticker"] = out["ticker"].map(_ticker)
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitGyoM2ReplayError(f"prices historical universe disi ticker iceriyor: {foreign}")
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitGyoM2ReplayError("prices ticker basina tam bir satir olmali")
    try:
        dates = pd.to_datetime(out["price_trade_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitGyoM2ReplayError("price_trade_date gecersiz") from exc
    if any(day > analysis_at.astimezone(ISTANBUL).date() for day in dates):
        raise HistoricalPitGyoM2ReplayError("analysis_at sonrasi fiyat M2'ye sizdi")
    prices = pd.to_numeric(out["current_price"], errors="coerce")
    if (prices.isna() | ~prices.map(lambda x: math.isfinite(float(x)) and float(x) > 0)).any():
        raise HistoricalPitGyoM2ReplayError("current_price pozitif sonlu olmali")
    out["price_trade_date"] = dates
    out["current_price"] = prices.astype(float)
    return out.sort_values("ticker").reset_index(drop=True)


def _prepare_follow_contexts(frame: pd.DataFrame | None, *, analysis_at: datetime, tickers: tuple[str, ...]) -> dict[str, dict[str, object]]:
    if frame is None:
        return {}
    out = _require(frame, {"ticker", "follow_score", "follow_active", "asof_date"}, "follow_contexts")
    if out.empty:
        return {}
    out["ticker"] = out["ticker"].map(_ticker)
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitGyoM2ReplayError(f"follow_contexts historical universe disi ticker iceriyor: {foreign}")
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitGyoM2ReplayError("follow_contexts duplicate ticker iceriyor")
    try:
        dates = pd.to_datetime(out["asof_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitGyoM2ReplayError("follow_contexts.asof_date gecersiz") from exc
    if any(day > analysis_at.astimezone(ISTANBUL).date() for day in dates):
        raise HistoricalPitGyoM2ReplayError("analysis_at sonrasi follow context M2'ye sizdi")
    result: dict[str, dict[str, object]] = {}
    for row in out.itertuples(index=False):
        ticker = _ticker(row.ticker)
        result[ticker] = {
            "follow_score": _finite_01(row.follow_score, f"{ticker}.follow_score"),
            "follow_active": _strict_bool(row.follow_active, f"{ticker}.follow_active"),
        }
    return result


def run_historical_pit_gyo_m2_replay(*, analysis_at: datetime, universe: pd.DataFrame, navs: pd.DataFrame, prices: pd.DataFrame, config: GyoValuationConfig, follow_contexts: pd.DataFrame | None = None) -> HistoricalPitGyoM2ReplayResult:
    analysis = _aware(analysis_at)
    if not isinstance(config, GyoValuationConfig):
        raise HistoricalPitGyoM2ReplayError("config GyoValuationConfig olmali")
    try:
        config = validate_gyo_config(config)
    except GyoValuationError as exc:
        raise HistoricalPitGyoM2ReplayError("GYO config gecersiz") from exc
    hist_universe, tickers = _prepare_universe(universe)
    hist_navs = _prepare_navs(navs, analysis_at=analysis, tickers=tickers, config=config)
    hist_prices = _prepare_prices(prices, analysis_at=analysis, tickers=tickers)
    contexts = _prepare_follow_contexts(follow_contexts, analysis_at=analysis, tickers=tickers)
    try:
        snapshots, rejected = build_gyo_snapshots_from_frames(universe=hist_universe, navs=hist_navs, prices=hist_prices, analysis_at=analysis)
        report = evaluate_gyo_batch(snapshots, config=config, follow_contexts=contexts)
    except (GyoValuationError, ValueError, TypeError, OverflowError) as exc:
        raise HistoricalPitGyoM2ReplayError("GYO production math replay basarisiz") from exc
    result_rows: list[dict[str, object]] = []
    result_tickers: set[str] = set()
    for item in report.get("results", []):
        if not isinstance(item, Mapping):
            raise HistoricalPitGyoM2ReplayError("GYO report result mapping olmali")
        ticker = _ticker(item.get("ticker"))
        if ticker not in tickers or ticker in result_tickers:
            raise HistoricalPitGyoM2ReplayError("GYO report ticker contract ihlali")
        m2, valuation = item.get("m2"), item.get("valuation")
        if not isinstance(m2, Mapping) or not isinstance(valuation, Mapping):
            raise HistoricalPitGyoM2ReplayError("GYO report valuation/m2 eksik")
        score = _finite_01(m2.get("m2"), f"{ticker}.m2")
        if m2.get("m2_source") != "GYO_PD_NAV_TWO_AXIS_V1":
            raise HistoricalPitGyoM2ReplayError("GYO m2_source beklenmeyen deger")
        if m2.get("analysis_at") != analysis or valuation.get("analysis_at") != analysis:
            raise HistoricalPitGyoM2ReplayError("GYO report analysis_at degistirdi")
        result_tickers.add(ticker)
        result_rows.append({
            "ticker": ticker, "m2": score, "m2_source": str(m2.get("m2_source")),
            "nav_asof_date": m2.get("nav_asof_date"), "valuation_usable": bool(m2.get("valuation_usable")),
            "valuation_status": valuation.get("status"), "valuation_score": valuation.get("valuation_score"),
            "valuation_confidence": valuation.get("v_conf"), "score_inputs": m2.get("score_inputs"),
        })
    m2_scores = pd.DataFrame(result_rows, columns=["ticker", "m2", "m2_source", "nav_asof_date", "valuation_usable", "valuation_status", "valuation_score", "valuation_confidence", "score_inputs"])
    if not m2_scores.empty:
        m2_scores = m2_scores.sort_values(["m2", "ticker"], ascending=[False, True]).reset_index(drop=True)
    rejection_frame = pd.DataFrame(rejected, columns=["ticker", "reason"])
    if not rejection_frame.empty:
        rejection_frame["ticker"] = rejection_frame["ticker"].map(_ticker)
        if not set(rejection_frame["ticker"]).issubset(set(tickers)):
            raise HistoricalPitGyoM2ReplayError("GYO rejection historical universe disi ticker")
    covered = result_tickers | set(rejection_frame["ticker"].tolist())
    if covered != set(tickers):
        raise HistoricalPitGyoM2ReplayError(f"GYO replay sessiz ticker kaybetti: {sorted(set(tickers) - covered)}")
    return HistoricalPitGyoM2ReplayResult(analysis_at=analysis, tickers=tickers, valuation_profile=config.valuation_profile, valuation_version=config.valuation_version, report=report, m2_scores=m2_scores, rejections=rejection_frame)
