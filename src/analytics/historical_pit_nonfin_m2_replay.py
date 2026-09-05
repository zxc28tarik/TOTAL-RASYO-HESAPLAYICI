from __future__ import annotations

"""Database-free point-in-time replay of the production NONFIN M2 engine.

The production batch runner discovers today's universe and reads current
materializations. Historical replay must not. This module accepts only explicit
historical frames that have already been selected for the target knowledge
boundary, re-validates that boundary, and then reuses the production pure
snapshot/value/combine functions unchanged.
"""

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from src.analytics.price_level_adapter import attach_basis_receipts
from src.analytics.nonfin_batch_pipeline import build_nonfin_snapshots_from_frames
from src.analytics.nonfin_valuation import (
    NonfinValuationConfig,
    NonfinValuationError,
    evaluate_nonfin_batch,
)


ISTANBUL = ZoneInfo("Europe/Istanbul")


class HistoricalPitNonfinM2ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitNonfinM2ReplayResult:
    analysis_at: datetime
    tickers: tuple[str, ...]
    valuation_profile: str
    valuation_version: int
    report: Mapping[str, object]
    m2_scores: pd.DataFrame
    rejections: pd.DataFrame


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalPitNonfinM2ReplayError("analysis_at timezone-aware datetime olmali")
    return value


def _ticker(value: object, field: str = "ticker") -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitNonfinM2ReplayError(f"{field} dolu metin olmali")
    return value.strip().upper()


def _strict_bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if type(value).__name__ == "bool_" and str(value) in {"True", "False"}:
        return str(value) == "True"
    raise HistoricalPitNonfinM2ReplayError(f"{field} Python/numpy bool olmali")


def _finite_01(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise HistoricalPitNonfinM2ReplayError(f"{field} [0,1] sonlu sayi olmali")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HistoricalPitNonfinM2ReplayError(f"{field} [0,1] sonlu sayi olmali") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise HistoricalPitNonfinM2ReplayError(f"{field} [0,1] sonlu sayi olmali")
    return number


def _require(frame: pd.DataFrame, required: set[str], name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise HistoricalPitNonfinM2ReplayError(f"{name} DataFrame olmali")
    missing = required - set(frame.columns)
    if missing:
        raise HistoricalPitNonfinM2ReplayError(f"{name} missing columns: {sorted(missing)}")
    return frame.copy(deep=True)


def _prepare_universe(frame: pd.DataFrame) -> tuple[pd.DataFrame, tuple[str, ...]]:
    out = _require(frame, {"ticker", "peer_group", "sector_family"}, "universe")
    if out.empty:
        raise HistoricalPitNonfinM2ReplayError("NONFIN historical universe bos olamaz")
    out["ticker"] = out["ticker"].map(_ticker)
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitNonfinM2ReplayError("universe duplicate ticker iceriyor")
    out["sector_family"] = out["sector_family"].map(lambda x: _ticker(x, "sector_family"))
    if not out["sector_family"].eq("NONFIN").all():
        raise HistoricalPitNonfinM2ReplayError("NONFIN replay baska sektor ailesi iceremez")
    peer = out["peer_group"].map(lambda x: _ticker(x, "peer_group"))
    out["peer_group"] = peer
    tickers = tuple(sorted(out["ticker"].tolist()))
    return out.sort_values("ticker").reset_index(drop=True), tickers


def _prepare_financials(
    frame: pd.DataFrame,
    *,
    analysis_at: datetime,
    tickers: tuple[str, ...],
    config: NonfinValuationConfig,
) -> pd.DataFrame:
    required = {
        "ticker", "period_end", "published_at", "derivation_profile", "derivation_version",
        "revenue", "ebit", "net_income", "total_equity", "debt_st", "debt_lt",
        "cash_and_eq", "st_investments", "shares_out",
    }
    out = _require(frame, required, "financials")
    if out.empty:
        return out
    out["ticker"] = out["ticker"].map(_ticker)
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitNonfinM2ReplayError(
            f"financials historical universe disi ticker iceriyor: {foreign}"
        )
    try:
        out["period_end"] = pd.to_datetime(out["period_end"], errors="raise").dt.normalize()
        published = pd.to_datetime(out["published_at"], errors="raise", utc=True)
    except Exception as exc:
        raise HistoricalPitNonfinM2ReplayError("financials tarih alanlari gecersiz") from exc
    if out["period_end"].isna().any() or published.isna().any():
        raise HistoricalPitNonfinM2ReplayError("financials tarih alanlari bos olamaz")
    analysis_utc = pd.Timestamp(analysis_at).tz_convert("UTC")
    if (published > analysis_utc).any():
        raise HistoricalPitNonfinM2ReplayError("analysis_at sonrasi published_at M2'ye sizdi")
    analysis_day = analysis_utc.tz_localize(None).normalize()
    if (out["period_end"] > analysis_day).any():
        raise HistoricalPitNonfinM2ReplayError("analysis_at sonrasi period_end M2'ye sizdi")

    profiles = out["derivation_profile"].astype(str)
    versions = pd.to_numeric(out["derivation_version"], errors="coerce")
    mismatch = (profiles != config.source_derivation_profile) | (versions != config.source_derivation_version)
    if mismatch.any():
        raise HistoricalPitNonfinM2ReplayError(
            "financials source derivation profile/version config ile birebir ayni olmali"
        )

    # The upstream PIT selector must have collapsed restatements to one eligible
    # version for each ticker/financial period.  Refuse ambiguous input instead
    # of silently sorting by a version tag that may encode future knowledge.
    if out.duplicated(["ticker", "period_end"]).any():
        raise HistoricalPitNonfinM2ReplayError(
            "financials ticker+period_end icin birden fazla PIT version iceriyor"
        )
    out["period_end"] = out["period_end"].dt.date
    return out.sort_values(["ticker", "period_end"]).reset_index(drop=True)


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
        raise HistoricalPitNonfinM2ReplayError(f"prices historical universe disi ticker iceriyor: {foreign}")
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitNonfinM2ReplayError("prices ticker basina tam bir satir olmali")
    try:
        dates = pd.to_datetime(out["price_trade_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitNonfinM2ReplayError("price_trade_date gecersiz") from exc
    local_day = analysis_at.astimezone(ISTANBUL).date()
    if any(day > local_day for day in dates):
        raise HistoricalPitNonfinM2ReplayError("analysis_at sonrasi fiyat M2'ye sizdi")
    prices = pd.to_numeric(out["current_price"], errors="coerce")
    invalid = prices.isna() | ~prices.map(lambda x: math.isfinite(float(x)) and float(x) > 0)
    if invalid.any():
        raise HistoricalPitNonfinM2ReplayError("current_price pozitif sonlu olmali")
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
        raise HistoricalPitNonfinM2ReplayError(
            f"follow_contexts historical universe disi ticker iceriyor: {foreign}"
        )
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitNonfinM2ReplayError("follow_contexts duplicate ticker iceriyor")
    try:
        dates = pd.to_datetime(out["asof_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitNonfinM2ReplayError("follow_contexts.asof_date gecersiz") from exc
    local_day = analysis_at.astimezone(ISTANBUL).date()
    if any(day > local_day for day in dates):
        raise HistoricalPitNonfinM2ReplayError("analysis_at sonrasi follow context M2'ye sizdi")
    result: dict[str, dict[str, object]] = {}
    for row in out.itertuples(index=False):
        ticker = _ticker(row.ticker)
        active = _strict_bool(row.follow_active, f"follow_contexts.{ticker}.follow_active")
        score = _finite_01(row.follow_score, f"follow_contexts.{ticker}.follow_score")
        result[ticker] = {"follow_score": score, "follow_active": active}
    return result


def run_historical_pit_nonfin_m2_replay(
    *,
    analysis_at: datetime,
    universe: pd.DataFrame,
    financials: pd.DataFrame,
    prices: pd.DataFrame,
    config: NonfinValuationConfig,
    follow_contexts: pd.DataFrame | None = None,
) -> HistoricalPitNonfinM2ReplayResult:
    """Run production NONFIN valuation+M2 math from explicit PIT frames only."""

    analysis = _aware(analysis_at)
    if not isinstance(config, NonfinValuationConfig):
        raise HistoricalPitNonfinM2ReplayError("config NonfinValuationConfig olmali")

    hist_universe, tickers = _prepare_universe(universe)
    hist_financials = _prepare_financials(
        financials,
        analysis_at=analysis,
        tickers=tickers,
        config=config,
    )
    hist_prices = _prepare_prices(prices, analysis_at=analysis, tickers=tickers)
    contexts = _prepare_follow_contexts(
        follow_contexts,
        analysis_at=analysis,
        tickers=tickers,
    )

    basis_receipts = {}
    try:
        snapshots, rejected = build_nonfin_snapshots_from_frames(
            universe=hist_universe,
            financials=hist_financials,
            prices=hist_prices,
            analysis_at=analysis,
            anchor_period_end=None,
            basis_receipts=basis_receipts,
        )
        accepted = {snapshot.ticker for snapshot in snapshots}
        report = evaluate_nonfin_batch(snapshots, config=config,
            follow_contexts={ticker: value for ticker, value in contexts.items() if ticker in accepted})
        attach_basis_receipts(report, basis_receipts)
    except (NonfinValuationError, ValueError, TypeError, OverflowError) as exc:
        raise HistoricalPitNonfinM2ReplayError("NONFIN production math replay basarisiz") from exc

    result_rows: list[dict[str, object]] = []
    result_tickers: set[str] = set()
    for item in report.get("results", []):
        if not isinstance(item, Mapping):
            raise HistoricalPitNonfinM2ReplayError("NONFIN report result mapping olmali")
        ticker = _ticker(item.get("ticker"))
        if ticker not in tickers or ticker in result_tickers:
            raise HistoricalPitNonfinM2ReplayError("NONFIN report ticker contract ihlali")
        m2 = item.get("m2")
        valuation = item.get("valuation")
        if not isinstance(m2, Mapping) or not isinstance(valuation, Mapping):
            raise HistoricalPitNonfinM2ReplayError("NONFIN report valuation/m2 eksik")
        score = _finite_01(m2.get("m2"), f"{ticker}.m2")
        if m2.get("m2_source") != "NONFIN_RELATIVE_TWO_AXIS_V1":
            raise HistoricalPitNonfinM2ReplayError("NONFIN m2_source beklenmeyen deger")
        if m2.get("analysis_at") != analysis or valuation.get("analysis_at") != analysis:
            raise HistoricalPitNonfinM2ReplayError("NONFIN report analysis_at degistirdi")
        result_tickers.add(ticker)
        result_rows.append(
            {
                "ticker": ticker,
                "m2": score,
                "m2_source": str(m2.get("m2_source")),
                "anchor_period_end": m2.get("anchor_period_end"),
                "valuation_usable": bool(m2.get("valuation_usable")),
                "valuation_status": valuation.get("status"),
                "valuation_score": valuation.get("valuation_score"),
                "valuation_confidence": valuation.get("v_conf"),
                "score_inputs": m2.get("score_inputs"),
            }
        )

    m2_scores = pd.DataFrame(
        result_rows,
        columns=[
            "ticker", "m2", "m2_source", "anchor_period_end", "valuation_usable",
            "valuation_status", "valuation_score", "valuation_confidence", "score_inputs",
        ],
    ).sort_values(["m2", "ticker"], ascending=[False, True]).reset_index(drop=True)

    rejection_frame = pd.DataFrame(rejected, columns=["ticker", "reason"])
    if not rejection_frame.empty:
        rejection_frame["ticker"] = rejection_frame["ticker"].map(_ticker)
        if not set(rejection_frame["ticker"]).issubset(set(tickers)):
            raise HistoricalPitNonfinM2ReplayError("NONFIN rejection historical universe disi ticker")

    covered = result_tickers | set(rejection_frame["ticker"].tolist())
    if covered != set(tickers):
        missing = sorted(set(tickers) - covered)
        raise HistoricalPitNonfinM2ReplayError(f"NONFIN replay sessiz ticker kaybetti: {missing}")

    return HistoricalPitNonfinM2ReplayResult(
        analysis_at=analysis,
        tickers=tickers,
        valuation_profile=config.valuation_profile,
        valuation_version=config.valuation_version,
        report=report,
        m2_scores=m2_scores,
        rejections=rejection_frame,
    )
