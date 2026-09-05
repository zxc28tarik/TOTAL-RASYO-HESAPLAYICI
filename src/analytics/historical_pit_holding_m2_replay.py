from __future__ import annotations

"""Database-free point-in-time replay of the production HOLDING M2 engine.

Historical replay accepts only explicit frames already selected at the target
knowledge boundary.  It refuses future NAV publications/prices/follow context
and then reuses the production pure snapshot + valuation + M2 math unchanged.
"""

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Mapping
from zoneinfo import ZoneInfo

from src.analytics.price_level_adapter import SOURCE_SHARE_BASIS, attach_basis_receipts

import pandas as pd

from src.analytics.holding_batch_pipeline import build_holding_snapshots_from_frames
from src.analytics.holding_valuation import (
    HoldingValuationConfig,
    HoldingValuationError,
    evaluate_holding_batch,
    validate_holding_config,
)


ISTANBUL = ZoneInfo("Europe/Istanbul")


class HistoricalPitHoldingM2ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitHoldingM2ReplayResult:
    analysis_at: datetime
    tickers: tuple[str, ...]
    valuation_profile: str
    valuation_version: int
    report: Mapping[str, object]
    m2_scores: pd.DataFrame
    rejections: pd.DataFrame


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalPitHoldingM2ReplayError("analysis_at timezone-aware datetime olmali")
    return value


def _ticker(value: object, field: str = "ticker") -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitHoldingM2ReplayError(f"{field} dolu metin olmali")
    return value.strip().upper()


def _strict_bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if type(value).__name__ == "bool_" and str(value) in {"True", "False"}:
        return str(value) == "True"
    raise HistoricalPitHoldingM2ReplayError(f"{field} Python/numpy bool olmali")


def _finite_01(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise HistoricalPitHoldingM2ReplayError(f"{field} [0,1] sonlu sayi olmali")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HistoricalPitHoldingM2ReplayError(f"{field} [0,1] sonlu sayi olmali") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise HistoricalPitHoldingM2ReplayError(f"{field} [0,1] sonlu sayi olmali")
    return number


def _require(frame: pd.DataFrame, required: set[str], name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise HistoricalPitHoldingM2ReplayError(f"{name} DataFrame olmali")
    missing = required - set(frame.columns)
    if missing:
        raise HistoricalPitHoldingM2ReplayError(f"{name} missing columns: {sorted(missing)}")
    return frame.copy(deep=True)


def _prepare_universe(frame: pd.DataFrame) -> tuple[pd.DataFrame, tuple[str, ...]]:
    out = _require(frame, {"ticker", "peer_group", "sector_family"}, "universe")
    if out.empty:
        raise HistoricalPitHoldingM2ReplayError("HOLDING historical universe bos olamaz")
    out["ticker"] = out["ticker"].map(_ticker)
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitHoldingM2ReplayError("universe duplicate ticker iceriyor")
    out["sector_family"] = out["sector_family"].map(lambda v: _ticker(v, "sector_family"))
    if not out["sector_family"].eq("HOLDING").all():
        raise HistoricalPitHoldingM2ReplayError("HOLDING replay baska sektor ailesi iceremez")
    out["peer_group"] = out["peer_group"].map(lambda v: _ticker(v, "peer_group"))
    tickers = tuple(sorted(out["ticker"].tolist()))
    return out.sort_values("ticker").reset_index(drop=True), tickers


def _prepare_navs(
    frame: pd.DataFrame,
    *,
    analysis_at: datetime,
    tickers: tuple[str, ...],
    config: HoldingValuationConfig,
) -> pd.DataFrame:
    required = {
        "ticker", "nav_asof_date", "nav_published_at", "nav_total", "shares_out",
        "share_basis", "currency", "source_confidence", "source_document_id",
        "source_sha256", "nav_profile", "nav_version",
    }
    out = _require(frame, required, "navs")
    if out.empty:
        return out
    out["ticker"] = out["ticker"].map(_ticker)
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitHoldingM2ReplayError(f"navs historical universe disi ticker iceriyor: {foreign}")
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitHoldingM2ReplayError("navs ticker basina tam bir PIT satir olmali")

    try:
        nav_dates = pd.to_datetime(out["nav_asof_date"], errors="raise").dt.date
        published = pd.to_datetime(out["nav_published_at"], errors="raise", utc=True)
    except Exception as exc:
        raise HistoricalPitHoldingM2ReplayError("nav tarih alanlari gecersiz") from exc
    if published.isna().any():
        raise HistoricalPitHoldingM2ReplayError("nav_published_at bos olamaz")
    analysis_utc = pd.Timestamp(analysis_at).tz_convert("UTC")
    if (published > analysis_utc).any():
        raise HistoricalPitHoldingM2ReplayError("analysis_at sonrasi nav_published_at M2'ye sizdi")
    if any(d > p.tz_convert(ISTANBUL).date() for d, p in zip(nav_dates, published)):
        raise HistoricalPitHoldingM2ReplayError("nav_asof_date nav_published_at sonrasinda olamaz")

    if not out["nav_profile"].astype(str).eq(config.source_nav_profile).all():
        raise HistoricalPitHoldingM2ReplayError("nav_profile config source_nav_profile ile ayni olmali")
    versions = pd.to_numeric(out["nav_version"], errors="coerce")
    if not versions.eq(config.source_nav_version).all():
        raise HistoricalPitHoldingM2ReplayError("nav_version config source_nav_version ile ayni olmali")
    if not out["share_basis"].astype(str).str.upper().eq(SOURCE_SHARE_BASIS).all():
        raise HistoricalPitHoldingM2ReplayError("share_basis dated unadjusted source olmali")
    if not out["currency"].astype(str).str.upper().eq(config.currency).all():
        raise HistoricalPitHoldingM2ReplayError("currency config ile ayni olmali")

    out["nav_asof_date"] = nav_dates
    out["nav_published_at"] = published.map(lambda x: x.to_pydatetime())
    return out.sort_values("ticker").reset_index(drop=True)


def _prepare_prices(
    frame: pd.DataFrame,
    *,
    analysis_at: datetime,
    tickers: tuple[str, ...],
) -> pd.DataFrame:
    out = _require(frame, {"ticker", "price_trade_date", "current_price"}, "prices")
    if out.empty:
        return out
    out["ticker"] = out["ticker"].map(_ticker)
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitHoldingM2ReplayError(f"prices historical universe disi ticker iceriyor: {foreign}")
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitHoldingM2ReplayError("prices ticker basina tam bir satir olmali")
    try:
        dates = pd.to_datetime(out["price_trade_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitHoldingM2ReplayError("price_trade_date gecersiz") from exc
    local_day = analysis_at.astimezone(ISTANBUL).date()
    if any(day > local_day for day in dates):
        raise HistoricalPitHoldingM2ReplayError("analysis_at sonrasi fiyat M2'ye sizdi")
    prices = pd.to_numeric(out["current_price"], errors="coerce")
    invalid = prices.isna() | ~prices.map(lambda x: math.isfinite(float(x)) and float(x) > 0)
    if invalid.any():
        raise HistoricalPitHoldingM2ReplayError("current_price pozitif sonlu olmali")
    out["price_trade_date"] = dates
    out["current_price"] = prices.astype(float)
    return out.sort_values("ticker").reset_index(drop=True)


def _prepare_follow_contexts(
    frame: pd.DataFrame | None,
    *,
    analysis_at: datetime,
    tickers: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    if frame is None:
        return {}
    out = _require(frame, {"ticker", "follow_score", "follow_active", "asof_date"}, "follow_contexts")
    if out.empty:
        return {}
    out["ticker"] = out["ticker"].map(_ticker)
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitHoldingM2ReplayError(f"follow_contexts historical universe disi ticker iceriyor: {foreign}")
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitHoldingM2ReplayError("follow_contexts duplicate ticker iceriyor")
    try:
        dates = pd.to_datetime(out["asof_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitHoldingM2ReplayError("follow_contexts.asof_date gecersiz") from exc
    local_day = analysis_at.astimezone(ISTANBUL).date()
    if any(day > local_day for day in dates):
        raise HistoricalPitHoldingM2ReplayError("analysis_at sonrasi follow context M2'ye sizdi")
    result: dict[str, dict[str, object]] = {}
    for row in out.itertuples(index=False):
        ticker = _ticker(row.ticker)
        result[ticker] = {
            "follow_score": _finite_01(row.follow_score, f"{ticker}.follow_score"),
            "follow_active": _strict_bool(row.follow_active, f"{ticker}.follow_active"),
        }
    return result


def run_historical_pit_holding_m2_replay(
    *,
    analysis_at: datetime,
    universe: pd.DataFrame,
    navs: pd.DataFrame,
    prices: pd.DataFrame,
    config: HoldingValuationConfig,
    follow_contexts: pd.DataFrame | None = None,
) -> HistoricalPitHoldingM2ReplayResult:
    """Run production HOLDING valuation+M2 math from explicit PIT frames only."""

    analysis = _aware(analysis_at)
    if not isinstance(config, HoldingValuationConfig):
        raise HistoricalPitHoldingM2ReplayError("config HoldingValuationConfig olmali")
    try:
        config = validate_holding_config(config)
    except HoldingValuationError as exc:
        raise HistoricalPitHoldingM2ReplayError("HOLDING config gecersiz") from exc

    hist_universe, tickers = _prepare_universe(universe)
    hist_navs = _prepare_navs(navs, analysis_at=analysis, tickers=tickers, config=config)
    hist_prices = _prepare_prices(prices, analysis_at=analysis, tickers=tickers)
    contexts = _prepare_follow_contexts(follow_contexts, analysis_at=analysis, tickers=tickers)

    basis_receipts = {}
    try:
        snapshots, rejected = build_holding_snapshots_from_frames(
            basis_receipts=basis_receipts,
            universe=hist_universe,
            navs=hist_navs,
            prices=hist_prices,
            analysis_at=analysis,
        )
        report = evaluate_holding_batch(snapshots, config=config, follow_contexts={k: v for k, v in contexts.items() if k in {s.ticker for s in snapshots}})
        attach_basis_receipts(report, basis_receipts)
    except (HoldingValuationError, ValueError, TypeError, OverflowError) as exc:
        raise HistoricalPitHoldingM2ReplayError("HOLDING production math replay basarisiz") from exc

    result_rows: list[dict[str, object]] = []
    result_tickers: set[str] = set()
    for item in report.get("results", []):
        if not isinstance(item, Mapping):
            raise HistoricalPitHoldingM2ReplayError("HOLDING report result mapping olmali")
        ticker = _ticker(item.get("ticker"))
        if ticker not in tickers or ticker in result_tickers:
            raise HistoricalPitHoldingM2ReplayError("HOLDING report ticker contract ihlali")
        m2 = item.get("m2")
        valuation = item.get("valuation")
        if not isinstance(m2, Mapping) or not isinstance(valuation, Mapping):
            raise HistoricalPitHoldingM2ReplayError("HOLDING report valuation/m2 eksik")
        score = _finite_01(m2.get("m2"), f"{ticker}.m2")
        if m2.get("m2_source") != "HOLDING_NAV_DISCOUNT_TWO_AXIS_V1":
            raise HistoricalPitHoldingM2ReplayError("HOLDING m2_source beklenmeyen deger")
        if m2.get("analysis_at") != analysis or valuation.get("analysis_at") != analysis:
            raise HistoricalPitHoldingM2ReplayError("HOLDING report analysis_at degistirdi")
        result_tickers.add(ticker)
        result_rows.append({
            "ticker": ticker,
            "m2": score,
            "m2_source": str(m2.get("m2_source")),
            "nav_asof_date": m2.get("nav_asof_date"),
            "valuation_usable": bool(m2.get("valuation_usable")),
            "valuation_status": valuation.get("status"),
            "valuation_score": valuation.get("valuation_score"),
            "valuation_confidence": valuation.get("v_conf"),
            "score_inputs": m2.get("score_inputs"),
        })

    m2_scores = pd.DataFrame(result_rows, columns=[
        "ticker", "m2", "m2_source", "nav_asof_date", "valuation_usable",
        "valuation_status", "valuation_score", "valuation_confidence", "score_inputs",
    ])
    if not m2_scores.empty:
        m2_scores = m2_scores.sort_values(["m2", "ticker"], ascending=[False, True]).reset_index(drop=True)

    rejection_frame = pd.DataFrame(rejected, columns=["ticker", "reason"])
    if not rejection_frame.empty:
        rejection_frame["ticker"] = rejection_frame["ticker"].map(_ticker)
        if not set(rejection_frame["ticker"]).issubset(set(tickers)):
            raise HistoricalPitHoldingM2ReplayError("HOLDING rejection historical universe disi ticker")

    covered = result_tickers | set(rejection_frame["ticker"].tolist())
    if covered != set(tickers):
        missing = sorted(set(tickers) - covered)
        raise HistoricalPitHoldingM2ReplayError(f"HOLDING replay sessiz ticker kaybetti: {missing}")

    return HistoricalPitHoldingM2ReplayResult(
        analysis_at=analysis,
        tickers=tickers,
        valuation_profile=config.valuation_profile,
        valuation_version=config.valuation_version,
        report=report,
        m2_scores=m2_scores,
        rejections=rejection_frame,
    )
