from __future__ import annotations

"""Database-free PIT replay adapter for the production BANK v4.7 M2 engine.

The adapter deliberately accepts already-selected eight-quarter slot rows,
point-in-time assumptions, and a dated market/follow context frame.  It never
queries current universe, current assumptions, prices, or period-comparison
state.  Production canonicalization, valuation and two-axis M2 math are reused.
"""

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Mapping, Sequence

import pandas as pd

from src.analytics.bank_batch_pipeline import (
    BankM2Context,
    ResolvedBankAssumption,
    daily_price_cutoff_date,
    evaluate_bank_batch,
)
from src.analytics.bank_valuation_pipeline import (
    CanonicalBankRow,
    CanonicalizationError,
    _as_aware_datetime,
    _as_date,
    to_canonical_row,
)


class HistoricalPitBankM2ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitBankM2ReplayResult:
    analysis_at: datetime
    anchor_period_end: date
    tickers: tuple[str, ...]
    valuation_results: tuple[Mapping[str, object], ...]
    m2_scores: pd.DataFrame
    rejections: pd.DataFrame


def _ticker(value: object, field: str = "ticker") -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitBankM2ReplayError(f"{field} dolu metin olmali")
    return value.strip().upper()


def _finite(value: object, field: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise HistoricalPitBankM2ReplayError(f"{field} sonlu sayi olmali")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HistoricalPitBankM2ReplayError(f"{field} sonlu sayi olmali") from exc
    if not math.isfinite(number):
        raise HistoricalPitBankM2ReplayError(f"{field} sonlu sayi olmali")
    if minimum is not None and number < minimum:
        raise HistoricalPitBankM2ReplayError(f"{field} {minimum} altinda olamaz")
    if maximum is not None and number > maximum:
        raise HistoricalPitBankM2ReplayError(f"{field} {maximum} ustunde olamaz")
    return number


def _normalize_slot_mapping(value: Mapping[str, Sequence[Mapping[str, object]]]) -> tuple[dict[str, Sequence[Mapping[str, object]]], tuple[str, ...]]:
    if not isinstance(value, Mapping) or not value:
        raise HistoricalPitBankM2ReplayError("quarter_slots dolu mapping olmali")
    result: dict[str, Sequence[Mapping[str, object]]] = {}
    for raw_ticker, rows in value.items():
        ticker = _ticker(raw_ticker)
        if ticker in result:
            raise HistoricalPitBankM2ReplayError(f"quarter_slots yinelenen ticker: {ticker}")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise HistoricalPitBankM2ReplayError(f"{ticker} quarter_slots sequence olmali")
        result[ticker] = rows
    tickers = tuple(sorted(result))
    return result, tickers


def _prepare_assumptions(
    assumptions: Mapping[str, ResolvedBankAssumption],
    *,
    tickers: tuple[str, ...],
    analysis_at: datetime,
) -> dict[str, ResolvedBankAssumption]:
    if not isinstance(assumptions, Mapping):
        raise HistoricalPitBankM2ReplayError("assumptions mapping olmali")
    result: dict[str, ResolvedBankAssumption] = {}
    for raw_ticker, assumption in assumptions.items():
        ticker = _ticker(raw_ticker)
        if ticker not in tickers:
            raise HistoricalPitBankM2ReplayError(f"assumptions historical universe disi ticker iceriyor: {ticker}")
        if ticker in result:
            raise HistoricalPitBankM2ReplayError(f"assumptions yinelenen ticker: {ticker}")
        if not isinstance(assumption, ResolvedBankAssumption):
            raise HistoricalPitBankM2ReplayError(f"{ticker} assumption ResolvedBankAssumption olmali")
        try:
            effective = _as_aware_datetime("effective_at", assumption.effective_at)
        except CanonicalizationError as exc:
            raise HistoricalPitBankM2ReplayError(f"{ticker} assumption effective_at gecersiz") from exc
        if effective > analysis_at:
            raise HistoricalPitBankM2ReplayError(f"{ticker} analysis_at sonrasi assumption M2'ye sizdi")
        scope_type = str(assumption.scope_type).strip().upper()
        scope_code = str(assumption.scope_code).strip().upper()
        if scope_type == "BANK":
            if scope_code != "BANK":
                raise HistoricalPitBankM2ReplayError(f"{ticker} BANK assumption scope_code BANK olmali")
        elif scope_type == "TICKER":
            if scope_code != ticker:
                raise HistoricalPitBankM2ReplayError(f"{ticker} TICKER assumption scope_code ticker ile ayni olmali")
        else:
            raise HistoricalPitBankM2ReplayError(f"{ticker} assumption scope_type gecersiz")
        result[ticker] = assumption
    return result


def _prepare_contexts(
    frame: pd.DataFrame,
    *,
    tickers: tuple[str, ...],
    analysis_at: datetime,
) -> dict[str, BankM2Context]:
    required = {
        "ticker", "price_trade_date", "current_price", "price_source",
        "lag_asof_date", "s_lag_effective", "lag_active", "lag_source",
    }
    if not isinstance(frame, pd.DataFrame):
        raise HistoricalPitBankM2ReplayError("contexts DataFrame olmali")
    missing = required - set(frame.columns)
    if missing:
        raise HistoricalPitBankM2ReplayError(f"contexts missing columns: {sorted(missing)}")
    if frame.empty:
        raise HistoricalPitBankM2ReplayError("contexts bos olamaz")
    out = frame.copy(deep=True)
    out["ticker"] = out["ticker"].map(_ticker)
    if out.duplicated("ticker").any():
        raise HistoricalPitBankM2ReplayError("contexts duplicate ticker iceriyor")
    if set(out["ticker"]) != set(tickers):
        missing_tickers = sorted(set(tickers) - set(out["ticker"]))
        foreign = sorted(set(out["ticker"]) - set(tickers))
        raise HistoricalPitBankM2ReplayError(
            f"contexts historical universe ile tam eslesmeli; missing={missing_tickers}, foreign={foreign}"
        )

    cutoff = daily_price_cutoff_date(analysis_at)
    result: dict[str, BankM2Context] = {}
    for row in out.itertuples(index=False):
        ticker = _ticker(row.ticker)
        price_date: date | None
        if pd.isna(row.price_trade_date):
            price_date = None
        else:
            try:
                price_date = _as_date("price_trade_date", row.price_trade_date)
            except CanonicalizationError as exc:
                raise HistoricalPitBankM2ReplayError(f"{ticker} price_trade_date gecersiz") from exc
            if price_date > cutoff:
                raise HistoricalPitBankM2ReplayError(f"{ticker} analysis_at sonrasi/kullanilamaz fiyat M2'ye sizdi")

        if pd.isna(row.current_price):
            current_price = None
        else:
            current_price = _finite(row.current_price, f"{ticker}.current_price", minimum=0.0)
            if current_price <= 0:
                raise HistoricalPitBankM2ReplayError(f"{ticker}.current_price pozitif olmali")
            if price_date is None:
                raise HistoricalPitBankM2ReplayError(f"{ticker} current_price varsa price_trade_date zorunlu")

        if type(row.lag_active) is not bool:
            raise HistoricalPitBankM2ReplayError(f"{ticker}.lag_active Python bool olmali")
        lag_score = _finite(row.s_lag_effective, f"{ticker}.s_lag_effective", minimum=0.0, maximum=1.0)
        if pd.isna(row.lag_asof_date):
            lag_asof = None
        else:
            try:
                lag_asof = _as_date("lag_asof_date", row.lag_asof_date)
            except CanonicalizationError as exc:
                raise HistoricalPitBankM2ReplayError(f"{ticker}.lag_asof_date gecersiz") from exc
            if lag_asof > cutoff:
                raise HistoricalPitBankM2ReplayError(f"{ticker} gelecekteki follow context M2'ye sizdi")
        if row.lag_active:
            if lag_asof != cutoff:
                raise HistoricalPitBankM2ReplayError(
                    f"{ticker} aktif lag_asof_date production cutoff ile tam eslesmeli"
                )
        elif lag_asof is not None and lag_asof > cutoff:
            raise HistoricalPitBankM2ReplayError(f"{ticker} lag_asof_date cutoff sonrasi olamaz")

        if not isinstance(row.price_source, str) or not row.price_source.strip():
            raise HistoricalPitBankM2ReplayError(f"{ticker}.price_source dolu metin olmali")
        if not isinstance(row.lag_source, str) or not row.lag_source.strip():
            raise HistoricalPitBankM2ReplayError(f"{ticker}.lag_source dolu metin olmali")
        result[ticker] = BankM2Context(
            current_price=current_price,
            price_trade_date=price_date,
            price_source=row.price_source.strip(),
            s_lag_effective=lag_score,
            lag_active=row.lag_active,
            lag_source=row.lag_source.strip(),
        )
    return result


def run_historical_pit_bank_m2_replay(
    *,
    analysis_at: datetime,
    anchor_period_end: date,
    quarter_slots: Mapping[str, Sequence[Mapping[str, object]]],
    assumptions: Mapping[str, ResolvedBankAssumption],
    contexts: pd.DataFrame,
) -> HistoricalPitBankM2ReplayResult:
    """Replay BANK v4.7 valuation + M2 from explicit PIT inputs only."""
    try:
        analysis = _as_aware_datetime("analysis_at", analysis_at)
        anchor = _as_date("anchor_period_end", anchor_period_end)
    except CanonicalizationError as exc:
        raise HistoricalPitBankM2ReplayError("analysis_at/anchor_period_end gecersiz") from exc

    slot_map, tickers = _normalize_slot_mapping(quarter_slots)
    prepared_assumptions = _prepare_assumptions(assumptions, tickers=tickers, analysis_at=analysis)
    prepared_contexts = _prepare_contexts(contexts, tickers=tickers, analysis_at=analysis)

    canonicals: dict[str, CanonicalBankRow] = {}
    rejection_rows: list[dict[str, str]] = []
    for ticker in tickers:
        try:
            canonicals[ticker] = to_canonical_row(
                slot_map[ticker], ticker=ticker, analysis_at=analysis, anchor_period_end=anchor
            )
        except CanonicalizationError as exc:
            rejection_rows.append({"ticker": ticker, "reason": f"CANONICAL_REJECT:{exc}"})

    results = evaluate_bank_batch(canonicals, prepared_assumptions, prepared_contexts)
    score_rows: list[dict[str, object]] = []
    result_tickers: set[str] = set()
    for result in results:
        ticker = _ticker(result.get("ticker"))
        if ticker in result_tickers:
            raise HistoricalPitBankM2ReplayError(f"BANK result duplicate ticker: {ticker}")
        result_tickers.add(ticker)
        if result.get("analysis_at") != analysis or result.get("anchor_period_end") != anchor:
            raise HistoricalPitBankM2ReplayError(f"{ticker} BANK result time boundary degistirdi")
        m2_result = result.get("m2")
        if not isinstance(m2_result, Mapping):
            reason = str(result.get("reason") or result.get("m2_error") or "M2_RESULT_MISSING")
            rejection_rows.append({"ticker": ticker, "reason": reason})
            continue
        if m2_result.get("analysis_at") != analysis or m2_result.get("anchor_period_end") != anchor:
            raise HistoricalPitBankM2ReplayError(f"{ticker} BANK M2 time boundary degistirdi")
        score = _finite(m2_result.get("m2_score"), f"{ticker}.m2_score", minimum=0.0, maximum=1.0)
        score_rows.append({
            "ticker": ticker,
            "m2": score,
            "m2_source": "BANK_TWO_AXIS_V47",
            "analysis_at": analysis,
            "anchor_period_end": anchor,
            "valuation_status": m2_result.get("valuation_status"),
            "valuation_reason": m2_result.get("valuation_reason"),
            "valuation_usable": bool(m2_result.get("valuation_usable")),
            "v_conf": m2_result.get("v_conf"),
            "score_inputs": m2_result.get("score_inputs"),
            "diagnostics": m2_result.get("diagnostics"),
        })

    rejection_frame = pd.DataFrame(rejection_rows, columns=["ticker", "reason"])
    if not rejection_frame.empty:
        if rejection_frame.duplicated("ticker").any():
            duplicates = sorted(rejection_frame.loc[rejection_frame.duplicated("ticker", keep=False), "ticker"].unique())
            raise HistoricalPitBankM2ReplayError(f"BANK ticker hem/coklu reject oldu: {duplicates}")
    scored = {row["ticker"] for row in score_rows}
    rejected = set(rejection_frame["ticker"].tolist()) if not rejection_frame.empty else set()
    if scored & rejected:
        raise HistoricalPitBankM2ReplayError(f"BANK ticker hem score hem reject oldu: {sorted(scored & rejected)}")
    if scored | rejected != set(tickers):
        raise HistoricalPitBankM2ReplayError(f"BANK replay sessiz ticker kaybetti: {sorted(set(tickers) - scored - rejected)}")

    scores = pd.DataFrame(score_rows, columns=[
        "ticker", "m2", "m2_source", "analysis_at", "anchor_period_end",
        "valuation_status", "valuation_reason", "valuation_usable", "v_conf",
        "score_inputs", "diagnostics",
    ])
    if not scores.empty:
        scores = scores.sort_values(["m2", "ticker"], ascending=[False, True]).reset_index(drop=True)

    return HistoricalPitBankM2ReplayResult(
        analysis_at=analysis,
        anchor_period_end=anchor,
        tickers=tickers,
        valuation_results=tuple(results),
        m2_scores=scores,
        rejections=rejection_frame,
    )
