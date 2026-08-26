from __future__ import annotations

"""Database-free PIT replay adapter for the production FINANCIAL M2 engine."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from src.analytics.financial_institution_batch_pipeline import build_financial_institution_snapshots_from_frames
from src.analytics.financial_institution_valuation import (
    FinancialInstitutionValuationConfig,
    FinancialInstitutionValuationError,
    evaluate_financial_institution_batch,
    validate_financial_institution_config,
)

ISTANBUL = ZoneInfo("Europe/Istanbul")


class HistoricalPitFinancialM2ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitFinancialM2ReplayResult:
    analysis_at: datetime
    tickers: tuple[str, ...]
    valuation_profile: str
    valuation_version: int
    report: Mapping[str, object]
    m2_scores: pd.DataFrame
    rejections: pd.DataFrame


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalPitFinancialM2ReplayError("analysis_at timezone-aware datetime olmali")
    return value


def _output_datetime(value: object, field: str) -> datetime:
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    elif isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise HistoricalPitFinancialM2ReplayError(f"{field} ISO datetime olmali") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalPitFinancialM2ReplayError(f"{field} timezone-aware datetime olmali")
    return value


def _same_instant(value: object, expected: datetime, field: str) -> bool:
    return _output_datetime(value, field).astimezone(timezone.utc) == expected.astimezone(timezone.utc)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitFinancialM2ReplayError(f"{field} dolu metin olmali")
    return value.strip().upper()


def _score(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise HistoricalPitFinancialM2ReplayError(f"{field} [0,1] sonlu sayi olmali")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HistoricalPitFinancialM2ReplayError(f"{field} [0,1] sonlu sayi olmali") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise HistoricalPitFinancialM2ReplayError(f"{field} [0,1] sonlu sayi olmali")
    return number


def _frame(frame: pd.DataFrame, required: set[str], name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise HistoricalPitFinancialM2ReplayError(f"{name} DataFrame olmali")
    missing = required - set(frame.columns)
    if missing:
        raise HistoricalPitFinancialM2ReplayError(f"{name} missing columns: {sorted(missing)}")
    return frame.copy(deep=True)


def _prepare_universe(frame: pd.DataFrame) -> tuple[pd.DataFrame, tuple[str, ...]]:
    out = _frame(frame, {"ticker", "sector_family"}, "universe")
    if out.empty:
        raise HistoricalPitFinancialM2ReplayError("FINANCIAL historical universe bos olamaz")
    out["ticker"] = out["ticker"].map(lambda x: _text(x, "ticker"))
    out["sector_family"] = out["sector_family"].map(lambda x: _text(x, "sector_family"))
    if out.duplicated("ticker").any():
        raise HistoricalPitFinancialM2ReplayError("universe duplicate ticker iceriyor")
    if not out["sector_family"].eq("FINANCIAL").all():
        raise HistoricalPitFinancialM2ReplayError("FINANCIAL replay baska sektor ailesi iceremez")
    tickers = tuple(sorted(out["ticker"].tolist()))
    return out.sort_values("ticker").reset_index(drop=True), tickers


def _prepare_metrics(frame: pd.DataFrame, *, analysis_at: datetime, tickers: tuple[str, ...], config: FinancialInstitutionValuationConfig) -> pd.DataFrame:
    required = {
        "ticker", "period_end", "published_at", "business_type", "accounting_profile", "accounting_version",
        "currency", "shares_out", "share_basis", "total_equity", "net_income_ttm", "average_equity",
        "total_assets", "finance_receivables", "npl_gross", "provisions", "net_finance_income_ttm",
        "funding_cost_ttm", "operating_expenses_ttm", "capital_adequacy_ratio", "source_confidence",
        "source_document_id", "source_sha256", "metrics_profile", "metrics_version",
    }
    out = _frame(frame, required, "metrics")
    if out.empty:
        return out
    out["ticker"] = out["ticker"].map(lambda x: _text(x, "ticker"))
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitFinancialM2ReplayError(f"metrics historical universe disi ticker iceriyor: {foreign}")
    if out.duplicated("ticker").any():
        raise HistoricalPitFinancialM2ReplayError("metrics ticker basina tam bir PIT satir olmali")
    try:
        period_ends = pd.to_datetime(out["period_end"], errors="raise").dt.date
        published = pd.to_datetime(out["published_at"], errors="raise", utc=True)
    except Exception as exc:
        raise HistoricalPitFinancialM2ReplayError("metrics tarih alanlari gecersiz") from exc
    analysis_utc = pd.Timestamp(analysis_at).tz_convert("UTC")
    if published.isna().any() or (published > analysis_utc).any():
        raise HistoricalPitFinancialM2ReplayError("analysis_at sonrasi published_at M2'ye sizdi")
    if any(d > p.tz_convert(ISTANBUL).date() for d, p in zip(period_ends, published)):
        raise HistoricalPitFinancialM2ReplayError("period_end published_at sonrasinda olamaz")
    if not out["metrics_profile"].astype(str).eq(config.source_metrics_profile).all():
        raise HistoricalPitFinancialM2ReplayError("metrics_profile config ile ayni olmali")
    if not pd.to_numeric(out["metrics_version"], errors="coerce").eq(config.source_metrics_version).all():
        raise HistoricalPitFinancialM2ReplayError("metrics_version config ile ayni olmali")
    if not out["accounting_profile"].astype(str).str.upper().eq(config.accounting_profile).all():
        raise HistoricalPitFinancialM2ReplayError("accounting_profile config ile ayni olmali")
    if not pd.to_numeric(out["accounting_version"], errors="coerce").eq(config.accounting_version).all():
        raise HistoricalPitFinancialM2ReplayError("accounting_version config ile ayni olmali")
    if not out["share_basis"].astype(str).str.upper().eq(config.share_basis).all():
        raise HistoricalPitFinancialM2ReplayError("share_basis config ile ayni olmali")
    if not out["currency"].astype(str).str.upper().eq(config.currency).all():
        raise HistoricalPitFinancialM2ReplayError("currency config ile ayni olmali")
    out["period_end"] = period_ends
    out["published_at"] = published.map(lambda x: x.to_pydatetime())
    return out.sort_values("ticker").reset_index(drop=True)


def _prepare_prices(frame: pd.DataFrame, *, analysis_at: datetime, tickers: tuple[str, ...]) -> pd.DataFrame:
    out = _frame(frame, {"ticker", "price_trade_date", "current_price"}, "prices")
    if out.empty:
        return out
    out["ticker"] = out["ticker"].map(lambda x: _text(x, "ticker"))
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitFinancialM2ReplayError(f"prices historical universe disi ticker iceriyor: {foreign}")
    if out.duplicated("ticker").any():
        raise HistoricalPitFinancialM2ReplayError("prices duplicate ticker iceriyor")
    try:
        dates = pd.to_datetime(out["price_trade_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitFinancialM2ReplayError("price_trade_date gecersiz") from exc
    if any(d > analysis_at.astimezone(ISTANBUL).date() for d in dates):
        raise HistoricalPitFinancialM2ReplayError("analysis_at sonrasi fiyat M2'ye sizdi")
    prices = pd.to_numeric(out["current_price"], errors="coerce")
    if (prices.isna() | ~prices.map(lambda x: math.isfinite(float(x)) and float(x) > 0)).any():
        raise HistoricalPitFinancialM2ReplayError("current_price pozitif sonlu olmali")
    out["price_trade_date"] = dates
    out["current_price"] = prices.astype(float)
    return out.sort_values("ticker").reset_index(drop=True)


def _follow(frame: pd.DataFrame | None, *, analysis_at: datetime, tickers: tuple[str, ...]) -> dict[str, dict[str, object]]:
    if frame is None:
        return {}
    out = _frame(frame, {"ticker", "follow_score", "follow_active", "asof_date"}, "follow_contexts")
    if out.empty:
        return {}
    out["ticker"] = out["ticker"].map(lambda x: _text(x, "ticker"))
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitFinancialM2ReplayError(f"follow_contexts historical universe disi ticker iceriyor: {foreign}")
    if out.duplicated("ticker").any():
        raise HistoricalPitFinancialM2ReplayError("follow_contexts duplicate ticker iceriyor")
    try:
        dates = pd.to_datetime(out["asof_date"], errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitFinancialM2ReplayError("follow_contexts.asof_date gecersiz") from exc
    if any(d > analysis_at.astimezone(ISTANBUL).date() for d in dates):
        raise HistoricalPitFinancialM2ReplayError("analysis_at sonrasi follow context M2'ye sizdi")
    result: dict[str, dict[str, object]] = {}
    for row in out.itertuples(index=False):
        active = row.follow_active
        if type(active) is not bool:
            raise HistoricalPitFinancialM2ReplayError("follow_active Python bool olmali")
        result[_text(row.ticker, "ticker")] = {"follow_score": _score(row.follow_score, "follow_score"), "follow_active": active}
    return result


def run_historical_pit_financial_m2_replay(*, analysis_at: datetime, universe: pd.DataFrame, metrics: pd.DataFrame, prices: pd.DataFrame, config: FinancialInstitutionValuationConfig, follow_contexts: pd.DataFrame | None = None) -> HistoricalPitFinancialM2ReplayResult:
    analysis = _aware(analysis_at)
    if not isinstance(config, FinancialInstitutionValuationConfig):
        raise HistoricalPitFinancialM2ReplayError("config FinancialInstitutionValuationConfig olmali")
    try:
        config = validate_financial_institution_config(config)
    except FinancialInstitutionValuationError as exc:
        raise HistoricalPitFinancialM2ReplayError("FINANCIAL config gecersiz") from exc
    hist_universe, tickers = _prepare_universe(universe)
    hist_metrics = _prepare_metrics(metrics, analysis_at=analysis, tickers=tickers, config=config)
    hist_prices = _prepare_prices(prices, analysis_at=analysis, tickers=tickers)
    contexts = _follow(follow_contexts, analysis_at=analysis, tickers=tickers)
    try:
        snapshots, rejected = build_financial_institution_snapshots_from_frames(universe=hist_universe, metrics=hist_metrics, prices=hist_prices, analysis_at=analysis)
        report = evaluate_financial_institution_batch(snapshots, config=config, follow_contexts=contexts)
    except (FinancialInstitutionValuationError, ValueError, TypeError, OverflowError) as exc:
        raise HistoricalPitFinancialM2ReplayError("FINANCIAL production math replay basarisiz") from exc
    rows: list[dict[str, object]] = []
    result_tickers: set[str] = set()
    for item in report.get("results", []):
        if not isinstance(item, Mapping):
            raise HistoricalPitFinancialM2ReplayError("FINANCIAL report result mapping olmali")
        ticker = _text(item.get("ticker"), "ticker")
        m2, valuation = item.get("m2"), item.get("valuation")
        if ticker not in tickers or ticker in result_tickers or not isinstance(m2, Mapping) or not isinstance(valuation, Mapping):
            raise HistoricalPitFinancialM2ReplayError("FINANCIAL report contract ihlali")
        value = _score(m2.get("m2"), f"{ticker}.m2")
        if m2.get("m2_source") != "FINANCIAL_INSTITUTION_PB_PE_TWO_AXIS_V1":
            raise HistoricalPitFinancialM2ReplayError("FINANCIAL m2_source beklenmeyen deger")
        if not _same_instant(m2.get("analysis_at"), analysis, f"{ticker}.m2.analysis_at"):
            raise HistoricalPitFinancialM2ReplayError("FINANCIAL m2 analysis_at degistirdi")
        if not _same_instant(valuation.get("analysis_at"), analysis, f"{ticker}.valuation.analysis_at"):
            raise HistoricalPitFinancialM2ReplayError("FINANCIAL valuation analysis_at degistirdi")
        result_tickers.add(ticker)
        rows.append({"ticker": ticker, "m2": value, "m2_source": m2.get("m2_source"), "period_end": m2.get("period_end"), "valuation_usable": bool(m2.get("valuation_usable")), "valuation_status": valuation.get("status"), "valuation_score": valuation.get("valuation_score"), "valuation_confidence": valuation.get("v_conf"), "score_inputs": m2.get("score_inputs")})
    scores = pd.DataFrame(rows, columns=["ticker", "m2", "m2_source", "period_end", "valuation_usable", "valuation_status", "valuation_score", "valuation_confidence", "score_inputs"])
    rejected_frame = pd.DataFrame(rejected, columns=["ticker", "reason"])
    if not rejected_frame.empty:
        rejected_frame["ticker"] = rejected_frame["ticker"].map(lambda x: _text(x, "ticker"))
        if not set(rejected_frame["ticker"]).issubset(set(tickers)):
            raise HistoricalPitFinancialM2ReplayError("FINANCIAL rejection historical universe disi ticker")
    covered = result_tickers | set(rejected_frame.get("ticker", pd.Series(dtype=str)).tolist())
    if covered != set(tickers):
        raise HistoricalPitFinancialM2ReplayError(f"FINANCIAL replay sessiz ticker kaybetti: {sorted(set(tickers) - covered)}")
    return HistoricalPitFinancialM2ReplayResult(analysis_at=analysis, tickers=tickers, valuation_profile=config.valuation_profile, valuation_version=config.valuation_version, report=report, m2_scores=scores.sort_values(["m2", "ticker"], ascending=[False, True]).reset_index(drop=True) if not scores.empty else scores, rejections=rejected_frame)
