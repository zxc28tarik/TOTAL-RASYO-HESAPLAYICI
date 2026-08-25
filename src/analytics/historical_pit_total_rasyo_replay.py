from __future__ import annotations

"""PIT-safe historical Total Rasyo replay and 60-cutoff ranking orchestration.

This layer does not implement module math. It consumes authoritative M2 family
replay results, calls the existing M1/M3/Ek4/Ek1/Ek9 replay functions, converts
those outputs into the production combiner contracts, and delegates Total Rasyo
weights/veto/decision math to ``total_rasyo_combine.combine_company_result``.

Any missing/rejected module remains missing. There is no neutral fill, weight
redistribution, current-state fallback, or cross-cutoff borrowing.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from src.analytics.historical_pit_bank_m2_replay import HistoricalPitBankM2ReplayResult
from src.analytics.historical_pit_ek1_replay import (
    HistoricalPitEk1ReplayResult,
    run_historical_pit_ek1_replay,
)
from src.analytics.historical_pit_ek4_replay import (
    HistoricalPitEk4ReplayResult,
    run_historical_pit_ek4_replay,
)
from src.analytics.historical_pit_ek9_replay import (
    HistoricalPitEk9ReplayResult,
    run_historical_pit_ek9_replay,
)
from src.analytics.historical_pit_financial_m2_replay import HistoricalPitFinancialM2ReplayResult
from src.analytics.historical_pit_gyo_m2_replay import HistoricalPitGyoM2ReplayResult
from src.analytics.historical_pit_holding_m2_replay import HistoricalPitHoldingM2ReplayResult
from src.analytics.historical_pit_insurance_m2_replay import HistoricalPitInsuranceM2ReplayResult
from src.analytics.historical_pit_m1_replay import (
    HistoricalPitM1ReplayResult,
    run_historical_pit_m1_replay,
)
from src.analytics.historical_pit_m3_replay import (
    HistoricalPitM3ReplayResult,
    run_historical_pit_m3_replay,
)
from src.analytics.historical_pit_nonfin_m2_replay import HistoricalPitNonfinM2ReplayResult
from src.analytics.historical_pit_rsc_replay import HistoricalPitRscReplayResult
from src.analytics.total_rasyo_combine import STATUS_OK, CompanyResult, combine_company_result
from src.analytics.total_rasyo_engine_isolation import EngineRun, RUN_OK
from src.analytics.total_rasyo_module_reader import (
    CompanyModuleContext,
    ModuleComponent,
    READ_MODULE_KEYS,
)

ISTANBUL = ZoneInfo("Europe/Istanbul")
M2_FAMILY_TYPES = {
    HistoricalPitBankM2ReplayResult: "BANK",
    HistoricalPitNonfinM2ReplayResult: "NONFIN",
    HistoricalPitHoldingM2ReplayResult: "HOLDING",
    HistoricalPitGyoM2ReplayResult: "GYO",
    HistoricalPitInsuranceM2ReplayResult: "INSURANCE",
    HistoricalPitFinancialM2ReplayResult: "FINANCIAL",
}
MODULE_SOURCE_TYPES = {
    "M1": "HISTORICAL_PIT_M1_REPLAY",
    "M3": "HISTORICAL_PIT_M3_REPLAY",
    "Ek4": "HISTORICAL_PIT_EK4_REPLAY",
    "Ek1": "HISTORICAL_PIT_EK1_REPLAY",
    "Ek9": "HISTORICAL_PIT_EK9_REPLAY",
}
EXPECTED_CUTOFF_MONTHS = tuple(
    str(period) for period in pd.period_range("2021-08", "2026-07", freq="M")
)
SCORE_COLUMNS = [
    "analysis_at", "asof_date", "market_asof_date", "ticker", "rank", "routed_engine",
    "m2", "m1", "m3", "ek4", "ek1", "ek9", "good_count_ge8", "base_score",
    "final_score", "total_rasyo_100", "veto_flag", "decision",
]
REJECTION_COLUMNS = [
    "analysis_at", "asof_date", "market_asof_date", "ticker", "routed_engine",
    "total_rasyo_status", "rejection_reason", "insufficiency_reason", "missing_modules",
    "module_reasons", "engine_status", "engine_reason",
]


class HistoricalPitTotalRasyoReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitTotalRasyoCutoffInput:
    analysis_at: datetime
    asof_date: date
    market_asof_date: date
    universe: pd.DataFrame
    trading_calendar: pd.DataFrame
    stock_prices: pd.DataFrame
    index_prices: pd.DataFrame
    rsc_replay: HistoricalPitRscReplayResult
    m2_replays: tuple[object, ...]


@dataclass(frozen=True)
class HistoricalPitTotalRasyoCutoffResult:
    analysis_at: datetime
    asof_date: date
    market_asof_date: date
    tickers: tuple[str, ...]
    company_results: tuple[CompanyResult, ...]
    scores: pd.DataFrame
    rejections: pd.DataFrame
    m1_replay: HistoricalPitM1ReplayResult
    m3_replay: HistoricalPitM3ReplayResult
    ek4_replay: HistoricalPitEk4ReplayResult
    ek1_replay: HistoricalPitEk1ReplayResult
    ek9_replay: HistoricalPitEk9ReplayResult


@dataclass(frozen=True)
class HistoricalPitTotalRasyoReplayResult:
    cutoff_months: tuple[str, ...]
    cutoff_results: tuple[HistoricalPitTotalRasyoCutoffResult, ...]
    scores: pd.DataFrame
    rejections: pd.DataFrame


def _aware(value: object, field: str = "analysis_at") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalPitTotalRasyoReplayError(f"{field} timezone-aware datetime olmali")
    return value


def _date_value(value: object, field: str) -> date:
    try:
        parsed = pd.Timestamp(value)
    except Exception as exc:
        raise HistoricalPitTotalRasyoReplayError(f"{field} gecersiz") from exc
    if pd.isna(parsed):
        raise HistoricalPitTotalRasyoReplayError(f"{field} bos olamaz")
    return parsed.date()


def _ticker(value: object, field: str = "ticker") -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitTotalRasyoReplayError(f"{field} dolu metin olmali")
    return value.strip().upper()


def _strict_bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if type(value).__name__ == "bool_" and str(value) in {"True", "False"}:
        return str(value) == "True"
    raise HistoricalPitTotalRasyoReplayError(f"{field} Python/numpy bool olmali")


def _normalize_tickers(values: Sequence[object], field: str) -> tuple[str, ...]:
    tickers = tuple(_ticker(value, field) for value in values)
    if not tickers:
        raise HistoricalPitTotalRasyoReplayError(f"{field} bos olamaz")
    if len(tickers) != len(set(tickers)):
        raise HistoricalPitTotalRasyoReplayError(f"{field} duplicate ticker iceriyor")
    return tuple(sorted(tickers))


def _universe_tickers(universe: pd.DataFrame) -> tuple[str, ...]:
    if not isinstance(universe, pd.DataFrame):
        raise HistoricalPitTotalRasyoReplayError("universe DataFrame olmali")
    if "ticker" not in universe.columns:
        raise HistoricalPitTotalRasyoReplayError("universe ticker sutunu zorunlu")
    if universe.empty:
        raise HistoricalPitTotalRasyoReplayError("historical universe bos olamaz")
    return _normalize_tickers(universe["ticker"].tolist(), "universe.ticker")


def _family_for_replay(replay: object) -> str:
    for replay_type, family in M2_FAMILY_TYPES.items():
        if isinstance(replay, replay_type):
            return family
    raise HistoricalPitTotalRasyoReplayError(
        f"desteklenmeyen M2 replay tipi: {type(replay).__name__}"
    )


def _frame(value: object, name: str, required: set[str]) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame):
        raise HistoricalPitTotalRasyoReplayError(f"{name} DataFrame olmali")
    missing = required - set(value.columns)
    if missing:
        raise HistoricalPitTotalRasyoReplayError(f"{name} missing columns: {sorted(missing)}")
    return value.copy(deep=True)


def _normalize_m2_replays(
    replays: Sequence[object],
    *,
    analysis_at: datetime,
    tickers: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, EngineRun]]:
    if isinstance(replays, (str, bytes)) or not isinstance(replays, Sequence) or not replays:
        raise HistoricalPitTotalRasyoReplayError("m2_replays dolu sequence olmali")

    expected = set(tickers)
    owners: dict[str, str] = {}
    engine_runs: dict[str, EngineRun] = {}
    seen_families: set[str] = set()

    for replay in replays:
        family = _family_for_replay(replay)
        if family in seen_families:
            raise HistoricalPitTotalRasyoReplayError(f"M2 family replay birden fazla verildi: {family}")
        seen_families.add(family)
        replay_analysis = _aware(getattr(replay, "analysis_at", None), f"{family}.analysis_at")
        if replay_analysis != analysis_at:
            raise HistoricalPitTotalRasyoReplayError(
                f"{family} M2 analysis_at Total Rasyo cutoff ile eslesmiyor"
            )
        replay_tickers = _normalize_tickers(tuple(getattr(replay, "tickers", ())), f"{family}.tickers")
        scores = _frame(
            getattr(replay, "m2_scores", None),
            f"{family}.m2_scores",
            {"ticker", "m2", "m2_source", "valuation_usable"},
        )
        rejects = _frame(
            getattr(replay, "rejections", None), f"{family}.rejections", {"ticker", "reason"}
        )
        if not scores.empty:
            scores["ticker"] = scores["ticker"].map(
                lambda value: _ticker(value, f"{family}.m2_scores.ticker")
            )
            if scores.duplicated("ticker").any():
                raise HistoricalPitTotalRasyoReplayError(f"{family}.m2_scores duplicate ticker iceriyor")
        if not rejects.empty:
            rejects["ticker"] = rejects["ticker"].map(
                lambda value: _ticker(value, f"{family}.rejections.ticker")
            )
            if rejects.duplicated("ticker").any():
                raise HistoricalPitTotalRasyoReplayError(f"{family}.rejections duplicate ticker iceriyor")

        scored = set(scores["ticker"].tolist())
        rejected = set(rejects["ticker"].tolist())
        replay_set = set(replay_tickers)
        if scored & rejected:
            raise HistoricalPitTotalRasyoReplayError(f"{family} ticker hem M2 score hem rejection iceriyor")
        if scored | rejected != replay_set:
            raise HistoricalPitTotalRasyoReplayError(f"{family} M2 score/rejection coverage invariant bozuldu")
        if not replay_set.issubset(expected):
            raise HistoricalPitTotalRasyoReplayError(
                f"{family} historical universe disi ticker iceriyor: {sorted(replay_set - expected)}"
            )
        overlap = set(owners) & replay_set
        if overlap:
            raise HistoricalPitTotalRasyoReplayError(f"M2 tek motor sahipligi ihlali: {sorted(overlap)}")

        m2_by_ticker: dict[str, dict[str, object]] = {}
        for row in scores.to_dict(orient="records"):
            ticker = _ticker(row["ticker"], f"{family}.m2_scores.ticker")
            source = row.get("m2_source")
            if not isinstance(source, str) or not source.strip():
                raise HistoricalPitTotalRasyoReplayError(f"{family}.{ticker}.m2_source dolu metin olmali")
            usable = _strict_bool(row.get("valuation_usable"), f"{family}.{ticker}.valuation_usable")
            if "analysis_at" in row and pd.notna(row.get("analysis_at")):
                row_analysis = _aware(row.get("analysis_at"), f"{family}.{ticker}.analysis_at")
                if row_analysis != analysis_at:
                    raise HistoricalPitTotalRasyoReplayError(
                        f"{family}.{ticker} M2 satir analysis_at cutoff ile eslesmiyor"
                    )
            confidence = row.get("valuation_confidence")
            if confidence is None or pd.isna(confidence):
                confidence = row.get("v_conf")
            m2_by_ticker[ticker] = {
                "m2": row.get("m2"),
                "m2_source": source.strip(),
                "m2_source_at": analysis_at,
                "valuation_usable": usable,
                "valuation_status": row.get("valuation_status"),
                "valuation_reason": row.get("valuation_reason") or row.get("valuation_status"),
                "valuation_confidence": confidence,
            }

        rejection_map = {
            _ticker(row["ticker"], f"{family}.rejections.ticker"): str(row["reason"])
            for row in rejects.to_dict(orient="records")
        }
        for ticker in replay_tickers:
            owners[ticker] = family
        engine_runs[family] = EngineRun(
            engine=family,
            status=RUN_OK,
            m2_by_ticker=m2_by_ticker,
            rejections=rejection_map,
        )

    if set(owners) != expected:
        raise HistoricalPitTotalRasyoReplayError(
            "M2 family replay union historical universe ile tam eslesmeli; "
            f"missing={sorted(expected - set(owners))}"
        )
    return owners, engine_runs


def _score_and_rejection_maps(
    *,
    result_name: str,
    tickers: tuple[str, ...],
    score_frame: pd.DataFrame,
    score_column: str,
    rejection_frame: pd.DataFrame | None,
    require_exhaustive: bool,
) -> tuple[dict[str, object], dict[str, str], pd.DataFrame]:
    scores = _frame(score_frame, f"{result_name}.scores", {"ticker", score_column})
    if not scores.empty:
        scores["ticker"] = scores["ticker"].map(
            lambda value: _ticker(value, f"{result_name}.scores.ticker")
        )
        if scores.duplicated("ticker").any():
            raise HistoricalPitTotalRasyoReplayError(f"{result_name}.scores duplicate ticker iceriyor")
    score_map = {row["ticker"]: row[score_column] for row in scores.to_dict(orient="records")}

    rejection_map: dict[str, str] = {}
    if rejection_frame is not None:
        rejects = _frame(rejection_frame, f"{result_name}.rejections", {"ticker", "reason"})
        if not rejects.empty:
            rejects["ticker"] = rejects["ticker"].map(
                lambda value: _ticker(value, f"{result_name}.rejections.ticker")
            )
            if rejects.duplicated("ticker").any():
                raise HistoricalPitTotalRasyoReplayError(
                    f"{result_name}.rejections duplicate ticker iceriyor"
                )
            rejection_map = {
                row["ticker"]: str(row["reason"]) for row in rejects.to_dict(orient="records")
            }

    expected = set(tickers)
    scored = set(score_map)
    rejected = set(rejection_map)
    if not (scored | rejected).issubset(expected):
        raise HistoricalPitTotalRasyoReplayError(f"{result_name} historical universe disi ticker iceriyor")
    if scored & rejected:
        raise HistoricalPitTotalRasyoReplayError(f"{result_name} ticker hem score hem rejection iceriyor")
    if require_exhaustive and scored | rejected != expected:
        raise HistoricalPitTotalRasyoReplayError(
            f"{result_name} score/rejection coverage invariant bozuldu"
        )
    return score_map, rejection_map, scores


def _same_replay_boundary(
    result: object,
    *,
    name: str,
    analysis_at: datetime,
    asof_date: date,
    tickers: tuple[str, ...],
    market_asof_date: date | None = None,
) -> None:
    replay_analysis = _aware(getattr(result, "analysis_at", None), f"{name}.analysis_at")
    if replay_analysis != analysis_at:
        raise HistoricalPitTotalRasyoReplayError(
            f"{name} analysis_at Total Rasyo cutoff ile eslesmiyor"
        )
    replay_asof = _date_value(getattr(result, "asof_date", None), f"{name}.asof_date")
    if replay_asof != asof_date:
        raise HistoricalPitTotalRasyoReplayError(
            f"{name} asof_date Total Rasyo cutoff ile eslesmiyor"
        )
    replay_tickers = _normalize_tickers(tuple(getattr(result, "tickers", ())), f"{name}.tickers")
    if replay_tickers != tickers:
        raise HistoricalPitTotalRasyoReplayError(
            f"{name} historical ticker kapsami Total Rasyo evreni ile eslesmiyor"
        )
    if market_asof_date is not None:
        replay_market_asof = _date_value(
            getattr(result, "market_asof_date", None), f"{name}.market_asof_date"
        )
        if replay_market_asof != market_asof_date:
            raise HistoricalPitTotalRasyoReplayError(
                f"{name} market_asof_date Total Rasyo cutoff ile eslesmiyor"
            )


def _m1_ek1_lineage(m1_scores: pd.DataFrame, ek1_scores: pd.DataFrame) -> None:
    required = {"ticker", "period_end", "good_count_ge8"}
    if required - set(m1_scores.columns):
        raise HistoricalPitTotalRasyoReplayError("M1 score lineage sutunlari eksik")
    if required - set(ek1_scores.columns):
        raise HistoricalPitTotalRasyoReplayError("Ek1 score lineage sutunlari eksik")
    left = m1_scores.loc[:, ["ticker", "period_end", "good_count_ge8"]].copy()
    right = ek1_scores.loc[:, ["ticker", "period_end", "good_count_ge8"]].copy()
    left["ticker"] = left["ticker"].map(_ticker)
    right["ticker"] = right["ticker"].map(_ticker)
    left = left.sort_values("ticker").reset_index(drop=True)
    right = right.sort_values("ticker").reset_index(drop=True)
    if set(left["ticker"]) != set(right["ticker"]):
        raise HistoricalPitTotalRasyoReplayError("M1/Ek1 scored ticker kapsami ayni olmali")
    if left.empty:
        return
    try:
        left["period_end"] = pd.to_datetime(left["period_end"], errors="raise").dt.date
        right["period_end"] = pd.to_datetime(right["period_end"], errors="raise").dt.date
        left_num = pd.to_numeric(left["good_count_ge8"], errors="raise")
        right_num = pd.to_numeric(right["good_count_ge8"], errors="raise")
    except Exception as exc:
        raise HistoricalPitTotalRasyoReplayError("M1/Ek1 lineage alanlari gecersiz") from exc
    left_int = left_num.astype(int)
    right_int = right_num.astype(int)
    if (left_num.astype(float) != left_int.astype(float)).any() or (
        right_num.astype(float) != right_int.astype(float)
    ).any():
        raise HistoricalPitTotalRasyoReplayError("M1/Ek1 good_count_ge8 tam sayi olmali")
    left["good_count_ge8"] = left_int
    right["good_count_ge8"] = right_int
    if not left.equals(right):
        raise HistoricalPitTotalRasyoReplayError(
            "M1/Ek1 period_end ve good_count_ge8 lineage eslesmiyor"
        )


def _module_component(
    key: str,
    ticker: str,
    *,
    score_map: Mapping[str, object],
    rejection_map: Mapping[str, str],
    analysis_at: datetime,
) -> ModuleComponent:
    if ticker in score_map:
        return ModuleComponent(
            key=key,
            score=score_map[ticker],
            source_at=analysis_at,
            source_type=MODULE_SOURCE_TYPES[key],
            missing=False,
            reason=None,
        )
    return ModuleComponent(
        key=key,
        score=None,
        source_at=None,
        source_type=None,
        missing=True,
        reason=rejection_map.get(ticker) or f"{key}_KAYNAGI_YOK",
    )


def combine_historical_pit_total_rasyo_results(
    *,
    analysis_at: datetime,
    asof_date: date,
    market_asof_date: date,
    tickers: tuple[str, ...],
    m2_replays: Sequence[object],
    m1_replay: HistoricalPitM1ReplayResult,
    m3_replay: HistoricalPitM3ReplayResult,
    ek4_replay: HistoricalPitEk4ReplayResult,
    ek1_replay: HistoricalPitEk1ReplayResult,
    ek9_replay: HistoricalPitEk9ReplayResult,
) -> HistoricalPitTotalRasyoCutoffResult:
    """Fail-closed combination of already-produced PIT replay outputs."""

    analysis = _aware(analysis_at)
    asof = _date_value(asof_date, "asof_date")
    market_asof = _date_value(market_asof_date, "market_asof_date")
    if market_asof > asof:
        raise HistoricalPitTotalRasyoReplayError("market_asof_date asof_date'den sonra olamaz")
    if asof > analysis.astimezone(ISTANBUL).date():
        raise HistoricalPitTotalRasyoReplayError("analysis_at sonrasi asof_date Total Rasyo'ya sizdi")
    canonical_tickers = _normalize_tickers(tickers, "tickers")

    _same_replay_boundary(
        m1_replay, name="M1", analysis_at=analysis, asof_date=asof, tickers=canonical_tickers
    )
    _same_replay_boundary(
        m3_replay,
        name="M3",
        analysis_at=analysis,
        asof_date=asof,
        market_asof_date=market_asof,
        tickers=canonical_tickers,
    )
    _same_replay_boundary(
        ek4_replay,
        name="Ek4",
        analysis_at=analysis,
        asof_date=asof,
        market_asof_date=market_asof,
        tickers=canonical_tickers,
    )
    _same_replay_boundary(
        ek1_replay, name="Ek1", analysis_at=analysis, asof_date=asof, tickers=canonical_tickers
    )
    _same_replay_boundary(
        ek9_replay,
        name="Ek9",
        analysis_at=analysis,
        asof_date=asof,
        market_asof_date=market_asof,
        tickers=canonical_tickers,
    )

    owners, engine_runs = _normalize_m2_replays(
        m2_replays, analysis_at=analysis, tickers=canonical_tickers
    )
    m1_scores, _, m1_frame = _score_and_rejection_maps(
        result_name="M1",
        tickers=canonical_tickers,
        score_frame=m1_replay.m1_scores,
        score_column="m1",
        rejection_frame=None,
        require_exhaustive=False,
    )
    m1_rejections = {
        ticker: "PIT_RSC_PERIOD_UNAVAILABLE" for ticker in canonical_tickers if ticker not in m1_scores
    }
    m3_scores, m3_rejections, _ = _score_and_rejection_maps(
        result_name="M3",
        tickers=canonical_tickers,
        score_frame=m3_replay.m3_scores,
        score_column="m3",
        rejection_frame=m3_replay.rejections,
        require_exhaustive=True,
    )
    ek4_scores, ek4_rejections, _ = _score_and_rejection_maps(
        result_name="Ek4",
        tickers=canonical_tickers,
        score_frame=ek4_replay.ek4_scores,
        score_column="ek4",
        rejection_frame=ek4_replay.rejections,
        require_exhaustive=True,
    )
    ek1_scores, ek1_rejections, ek1_frame = _score_and_rejection_maps(
        result_name="Ek1",
        tickers=canonical_tickers,
        score_frame=ek1_replay.ek1_scores,
        score_column="ek1",
        rejection_frame=ek1_replay.rejections,
        require_exhaustive=True,
    )
    ek9_scores, ek9_rejections, _ = _score_and_rejection_maps(
        result_name="Ek9",
        tickers=canonical_tickers,
        score_frame=ek9_replay.ek9_scores,
        score_column="ek9",
        rejection_frame=ek9_replay.rejections,
        require_exhaustive=True,
    )
    _m1_ek1_lineage(m1_frame, ek1_frame)
    ek1_rows = ek1_frame.set_index("ticker") if not ek1_frame.empty else None

    company_results: list[CompanyResult] = []
    for ticker in canonical_tickers:
        components = {
            "M1": _module_component(
                "M1", ticker, score_map=m1_scores, rejection_map=m1_rejections, analysis_at=analysis
            ),
            "M3": _module_component(
                "M3", ticker, score_map=m3_scores, rejection_map=m3_rejections, analysis_at=analysis
            ),
            "Ek4": _module_component(
                "Ek4", ticker, score_map=ek4_scores, rejection_map=ek4_rejections, analysis_at=analysis
            ),
            "Ek1": _module_component(
                "Ek1", ticker, score_map=ek1_scores, rejection_map=ek1_rejections, analysis_at=analysis
            ),
            "Ek9": _module_component(
                "Ek9", ticker, score_map=ek9_scores, rejection_map=ek9_rejections, analysis_at=analysis
            ),
        }
        if tuple(components) != READ_MODULE_KEYS:
            raise HistoricalPitTotalRasyoReplayError(
                "historical module context anahtar sirasi production reader ile eslesmiyor"
            )
        if ticker in ek1_scores:
            if ek1_rows is None:
                raise HistoricalPitTotalRasyoReplayError("Ek1 score map/frame tutarsiz")
            row = ek1_rows.loc[ticker]
            good_count = int(row["good_count_ge8"])
            good_missing = False
            good_reason = None
        else:
            good_count = None
            good_missing = True
            good_reason = ek1_rejections.get(ticker) or "good_count_ge8_KAYNAGI_YOK"

        module_context = CompanyModuleContext(
            ticker=ticker,
            components=components,
            good_count_ge8=good_count,
            good_count_missing=good_missing,
            good_count_reason=good_reason,
            asof_date=asof,
            analysis_at=analysis,
        )
        family = owners[ticker]
        company_results.append(
            combine_company_result(
                ticker=ticker,
                routed_engine=family,
                engine_run=engine_runs[family],
                module_context=module_context,
            )
        )

    score_rows: list[dict[str, object]] = []
    rejection_rows: list[dict[str, object]] = []
    for result in company_results:
        if result.total_rasyo_status == STATUS_OK:
            score_rows.append(
                {
                    "analysis_at": analysis,
                    "asof_date": asof,
                    "market_asof_date": market_asof,
                    "ticker": result.ticker,
                    "rank": None,
                    "routed_engine": result.routed_engine,
                    "m2": result.modules["M2"]["score"],
                    "m1": result.modules["M1"]["score"],
                    "m3": result.modules["M3"]["score"],
                    "ek4": result.modules["Ek4"]["score"],
                    "ek1": result.modules["Ek1"]["score"],
                    "ek9": result.modules["Ek9"]["score"],
                    "good_count_ge8": result.good_count_ge8,
                    "base_score": result.base_score,
                    "final_score": result.final_score,
                    "total_rasyo_100": result.total_rasyo_100,
                    "veto_flag": result.veto_flag,
                    "decision": result.decision,
                }
            )
        else:
            reasons = {
                key: value.get("reason")
                for key, value in result.modules.items()
                if value.get("missing")
            }
            rejection_rows.append(
                {
                    "analysis_at": analysis,
                    "asof_date": asof,
                    "market_asof_date": market_asof,
                    "ticker": result.ticker,
                    "routed_engine": result.routed_engine,
                    "total_rasyo_status": result.total_rasyo_status,
                    "rejection_reason": result.rejection_reason,
                    "insufficiency_reason": result.insufficiency_reason,
                    "missing_modules": result.missing_modules,
                    "module_reasons": reasons,
                    "engine_status": result.engine_status,
                    "engine_reason": result.engine_reason,
                }
            )

    scores = pd.DataFrame(score_rows, columns=SCORE_COLUMNS)
    if not scores.empty:
        scores = scores.sort_values(["final_score", "ticker"], ascending=[False, True]).reset_index(drop=True)
        scores["rank"] = range(1, len(scores) + 1)
    rejections = pd.DataFrame(rejection_rows, columns=REJECTION_COLUMNS)
    if not rejections.empty:
        rejections = rejections.sort_values("ticker").reset_index(drop=True)

    scored = set(scores["ticker"].tolist())
    rejected = set(rejections["ticker"].tolist())
    if scored & rejected:
        raise HistoricalPitTotalRasyoReplayError("Total Rasyo ticker hem score hem rejection uretti")
    if scored | rejected != set(canonical_tickers):
        raise HistoricalPitTotalRasyoReplayError("Total Rasyo score/rejection coverage invariant bozuldu")

    return HistoricalPitTotalRasyoCutoffResult(
        analysis_at=analysis,
        asof_date=asof,
        market_asof_date=market_asof,
        tickers=canonical_tickers,
        company_results=tuple(company_results),
        scores=scores,
        rejections=rejections,
        m1_replay=m1_replay,
        m3_replay=m3_replay,
        ek4_replay=ek4_replay,
        ek1_replay=ek1_replay,
        ek9_replay=ek9_replay,
    )


def run_historical_pit_total_rasyo_cutoff(
    inputs: HistoricalPitTotalRasyoCutoffInput,
) -> HistoricalPitTotalRasyoCutoffResult:
    """Run the existing replay adapters and combine one historical cutoff."""

    if not isinstance(inputs, HistoricalPitTotalRasyoCutoffInput):
        raise HistoricalPitTotalRasyoReplayError("inputs HistoricalPitTotalRasyoCutoffInput olmali")
    analysis = _aware(inputs.analysis_at)
    asof = _date_value(inputs.asof_date, "asof_date")
    market_asof = _date_value(inputs.market_asof_date, "market_asof_date")
    if market_asof > asof:
        raise HistoricalPitTotalRasyoReplayError("market_asof_date asof_date'den sonra olamaz")
    if asof > analysis.astimezone(ISTANBUL).date():
        raise HistoricalPitTotalRasyoReplayError("analysis_at sonrasi asof_date Total Rasyo'ya sizdi")

    tickers = _universe_tickers(inputs.universe)
    if not isinstance(inputs.rsc_replay, HistoricalPitRscReplayResult):
        raise HistoricalPitTotalRasyoReplayError("rsc_replay HistoricalPitRscReplayResult olmali")
    if _aware(inputs.rsc_replay.analysis_at, "rsc_replay.analysis_at") != analysis:
        raise HistoricalPitTotalRasyoReplayError("RSC analysis_at Total Rasyo cutoff ile eslesmiyor")
    if _normalize_tickers(inputs.rsc_replay.tickers, "rsc_replay.tickers") != tickers:
        raise HistoricalPitTotalRasyoReplayError("RSC ticker kapsami historical universe ile eslesmiyor")

    m1_replay = run_historical_pit_m1_replay(inputs.rsc_replay, asof_date=asof)
    m3_replay = run_historical_pit_m3_replay(
        analysis_at=analysis,
        asof_date=asof,
        market_asof_date=market_asof,
        universe=inputs.universe,
        trading_calendar=inputs.trading_calendar,
        stock_prices=inputs.stock_prices,
        index_prices=inputs.index_prices,
    )
    ek4_replay = run_historical_pit_ek4_replay(
        analysis_at=analysis,
        asof_date=asof,
        market_asof_date=market_asof,
        universe=inputs.universe,
        trading_calendar=inputs.trading_calendar,
        stock_prices=inputs.stock_prices,
        index_prices=inputs.index_prices,
    )
    ek1_replay = run_historical_pit_ek1_replay(m1_replay)
    ek9_replay = run_historical_pit_ek9_replay(
        analysis_at=analysis,
        asof_date=asof,
        market_asof_date=market_asof,
        universe=inputs.universe,
        trading_calendar=inputs.trading_calendar,
        stock_prices=inputs.stock_prices,
    )

    return combine_historical_pit_total_rasyo_results(
        analysis_at=analysis,
        asof_date=asof,
        market_asof_date=market_asof,
        tickers=tickers,
        m2_replays=inputs.m2_replays,
        m1_replay=m1_replay,
        m3_replay=m3_replay,
        ek4_replay=ek4_replay,
        ek1_replay=ek1_replay,
        ek9_replay=ek9_replay,
    )


def run_historical_pit_total_rasyo_60_cutoffs(
    cutoff_inputs: Sequence[HistoricalPitTotalRasyoCutoffInput],
) -> HistoricalPitTotalRasyoReplayResult:
    """Run the locked 2021-08..2026-07 monthly Total Rasyo replay."""

    if isinstance(cutoff_inputs, (str, bytes)) or not isinstance(cutoff_inputs, Sequence):
        raise HistoricalPitTotalRasyoReplayError("cutoff_inputs sequence olmali")
    if len(cutoff_inputs) != 60:
        raise HistoricalPitTotalRasyoReplayError(f"tam 60 cutoff zorunlu; gelen={len(cutoff_inputs)}")
    months = tuple(
        f"{_date_value(item.asof_date, 'cutoff.asof_date'):%Y-%m}" for item in cutoff_inputs
    )
    if months != EXPECTED_CUTOFF_MONTHS:
        raise HistoricalPitTotalRasyoReplayError("cutoff ay sirasi tam 2021-08..2026-07 olmali")

    results = tuple(run_historical_pit_total_rasyo_cutoff(item) for item in cutoff_inputs)
    score_frames = [result.scores for result in results if not result.scores.empty]
    rejection_frames = [result.rejections for result in results if not result.rejections.empty]
    scores = (
        pd.concat(score_frames, ignore_index=True)
        if score_frames
        else pd.DataFrame(columns=SCORE_COLUMNS)
    )
    rejections = (
        pd.concat(rejection_frames, ignore_index=True)
        if rejection_frames
        else pd.DataFrame(columns=REJECTION_COLUMNS)
    )
    if not scores.empty:
        scores = scores.sort_values(["asof_date", "rank", "ticker"]).reset_index(drop=True)
    if not rejections.empty:
        rejections = rejections.sort_values(["asof_date", "ticker"]).reset_index(drop=True)

    return HistoricalPitTotalRasyoReplayResult(
        cutoff_months=months,
        cutoff_results=results,
        scores=scores,
        rejections=rejections,
    )
