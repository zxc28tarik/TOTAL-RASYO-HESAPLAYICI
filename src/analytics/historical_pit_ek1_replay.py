from __future__ import annotations

"""PIT-safe, database-free replay of production Ek1 and its veto input."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.analytics.ek1_quality import compute_ek1_score_from_good_count
from src.analytics.historical_pit_m1_replay import HistoricalPitM1ReplayResult


ISTANBUL = ZoneInfo("Europe/Istanbul")
EK1_COLUMNS = ["ticker", "ek1", "good_count_ge8", "period_end"]
REJECTION_COLUMNS = ["ticker", "reason", "period_end"]


class HistoricalPitEk1ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitEk1ReplayResult:
    analysis_at: datetime
    asof_date: date
    tickers: tuple[str, ...]
    ek1_scores: pd.DataFrame
    rejections: pd.DataFrame


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitEk1ReplayError(f"{field} dolu metin olmali")
    return value.strip().upper()


def _date_value(value: object, field: str) -> date:
    try:
        parsed = pd.Timestamp(value)
    except Exception as exc:
        raise HistoricalPitEk1ReplayError(f"{field} gecersiz") from exc
    if pd.isna(parsed):
        raise HistoricalPitEk1ReplayError(f"{field} bos olamaz")
    return parsed.date()


def _date_series(series: pd.Series, field: str) -> pd.Series:
    try:
        out = pd.to_datetime(series, errors="raise").dt.date
    except Exception as exc:
        raise HistoricalPitEk1ReplayError(f"{field} gecersiz") from exc
    if out.isna().any():
        raise HistoricalPitEk1ReplayError(f"{field} bos olamaz")
    return out


def _good_counts(series: pd.Series, field: str) -> pd.Series:
    if series.map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise HistoricalPitEk1ReplayError(f"{field} bool olamaz")
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.isna().any() or (~numeric.map(lambda value: math.isfinite(float(value)))).any():
        raise HistoricalPitEk1ReplayError(f"{field} sonlu tam sayi olmali")
    integers = numeric.astype(int)
    if (numeric.astype(float) != integers.astype(float)).any():
        raise HistoricalPitEk1ReplayError(f"{field} tam sayi olmali")
    if (integers < 0).any():
        raise HistoricalPitEk1ReplayError(f"{field} negatif olamaz")
    return integers


def _prepare_m1_scores(
    replay: HistoricalPitM1ReplayResult,
    *,
    ticker_set: set[str],
    analysis_day: date,
) -> pd.DataFrame:
    frame = replay.m1_scores
    if not isinstance(frame, pd.DataFrame):
        raise HistoricalPitEk1ReplayError("m1_scores DataFrame olmali")
    required = {"ticker", "m1", "period_end", "good_count_ge8"}
    missing = required - set(frame.columns)
    if missing:
        raise HistoricalPitEk1ReplayError(f"m1_scores missing columns: {sorted(missing)}")
    out = frame.copy(deep=True)
    if out.empty:
        return pd.DataFrame(columns=["ticker", "period_end", "good_count_ge8"])
    out["ticker"] = out["ticker"].map(lambda value: _text(value, "m1_scores.ticker"))
    foreign = sorted(set(out["ticker"]) - ticker_set)
    if foreign:
        raise HistoricalPitEk1ReplayError(
            f"m1_scores historical universe disi ticker iceriyor: {foreign}"
        )
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitEk1ReplayError("m1_scores duplicate ticker iceriyor")
    out["period_end"] = _date_series(out["period_end"], "m1_scores.period_end")
    if any(day > analysis_day for day in out["period_end"]):
        raise HistoricalPitEk1ReplayError("analysis_at sonrasi period_end Ek1'e sizdi")
    out["good_count_ge8"] = _good_counts(
        out["good_count_ge8"], "m1_scores.good_count_ge8"
    )
    return out.loc[:, ["ticker", "period_end", "good_count_ge8"]].sort_values(
        "ticker"
    ).reset_index(drop=True)


def _prepare_period_comparison(
    replay: HistoricalPitM1ReplayResult,
    *,
    ticker_set: set[str],
    analysis_day: date,
) -> pd.DataFrame:
    frame = replay.period_comparison
    if not isinstance(frame, pd.DataFrame):
        raise HistoricalPitEk1ReplayError("period_comparison DataFrame olmali")
    required = {"ticker", "asof_date", "latest_period_end", "good_count_latest"}
    missing = required - set(frame.columns)
    if missing:
        raise HistoricalPitEk1ReplayError(
            f"period_comparison missing columns: {sorted(missing)}"
        )
    out = frame.copy(deep=True)
    if out.empty:
        return pd.DataFrame(columns=["ticker", "period_end", "good_count_ge8"])
    out["ticker"] = out["ticker"].map(
        lambda value: _text(value, "period_comparison.ticker")
    )
    foreign = sorted(set(out["ticker"]) - ticker_set)
    if foreign:
        raise HistoricalPitEk1ReplayError(
            f"period_comparison historical universe disi ticker iceriyor: {foreign}"
        )
    if out.duplicated(["ticker"]).any():
        raise HistoricalPitEk1ReplayError("period_comparison duplicate ticker iceriyor")
    out["asof_date"] = _date_series(out["asof_date"], "period_comparison.asof_date")
    if set(out["asof_date"]) != {replay.asof_date}:
        raise HistoricalPitEk1ReplayError("period_comparison asof_date M1 replay ile eslesmiyor")
    out["period_end"] = _date_series(
        out["latest_period_end"], "period_comparison.latest_period_end"
    )
    if any(day > analysis_day for day in out["period_end"]):
        raise HistoricalPitEk1ReplayError("analysis_at sonrasi latest_period_end Ek1'e sizdi")
    out["good_count_ge8"] = _good_counts(
        out["good_count_latest"], "period_comparison.good_count_latest"
    )
    return out.loc[:, ["ticker", "period_end", "good_count_ge8"]].sort_values(
        "ticker"
    ).reset_index(drop=True)


def run_historical_pit_ek1_replay(
    m1_replay: HistoricalPitM1ReplayResult,
) -> HistoricalPitEk1ReplayResult:
    """Reproduce Ek1 from the same PIT period row used by historical M1.

    This adapter deliberately consumes ``HistoricalPitM1ReplayResult`` instead
    of rebuilding the latest RSC period.  Production M1 and Ek1 both read
    ``period_8q_comparison``; preserving that single source prevents the Ek1
    score and Total Rasyo veto input from drifting to different periods.
    """

    if not isinstance(m1_replay, HistoricalPitM1ReplayResult):
        raise HistoricalPitEk1ReplayError("m1_replay HistoricalPitM1ReplayResult olmali")
    analysis = m1_replay.analysis_at
    if not isinstance(analysis, datetime) or analysis.tzinfo is None or analysis.utcoffset() is None:
        raise HistoricalPitEk1ReplayError("analysis_at timezone-aware olmali")
    local_day = analysis.astimezone(ISTANBUL).date()
    asof = _date_value(m1_replay.asof_date, "asof_date")
    if asof > local_day:
        raise HistoricalPitEk1ReplayError("analysis_at sonrasi asof_date Ek1'e sizdi")
    # Historical M1 validates financial period_end against the UTC analysis
    # day.  Reuse that exact boundary when checking a possibly mutated result.
    period_analysis_day = analysis.astimezone(timezone.utc).date()

    tickers = tuple(_text(value, "tickers") for value in m1_replay.tickers)
    if not tickers:
        raise HistoricalPitEk1ReplayError("historical ticker listesi bos olamaz")
    if len(tickers) != len(set(tickers)):
        raise HistoricalPitEk1ReplayError("historical ticker listesi duplicate iceriyor")
    ticker_set = set(tickers)
    scores_source = _prepare_m1_scores(
        m1_replay, ticker_set=ticker_set, analysis_day=period_analysis_day
    )
    period_source = _prepare_period_comparison(
        m1_replay, ticker_set=ticker_set, analysis_day=period_analysis_day
    )

    if set(scores_source["ticker"]) != set(period_source["ticker"]):
        raise HistoricalPitEk1ReplayError(
            "m1_scores ve period_comparison ticker kapsami eslesmiyor"
        )
    if not scores_source.equals(period_source):
        raise HistoricalPitEk1ReplayError(
            "m1_scores ve period_comparison period/good_count lineage eslesmiyor"
        )

    output_rows = []
    for row in scores_source.itertuples(index=False):
        output_rows.append(
            {
                "ticker": row.ticker,
                "ek1": compute_ek1_score_from_good_count(row.good_count_ge8),
                "good_count_ge8": int(row.good_count_ge8),
                "period_end": row.period_end,
            }
        )
    outputs = pd.DataFrame(output_rows, columns=EK1_COLUMNS).sort_values("ticker").reset_index(
        drop=True
    )
    missing_tickers = sorted(ticker_set - set(outputs["ticker"]))
    rejections = pd.DataFrame(
        [(ticker, "PIT_RSC_PERIOD_UNAVAILABLE", None) for ticker in missing_tickers],
        columns=REJECTION_COLUMNS,
    )
    if set(outputs["ticker"]) & set(rejections["ticker"]):
        raise HistoricalPitEk1ReplayError("Ek1 ticker hem score hem rejection uretti")
    if set(outputs["ticker"]) | set(rejections["ticker"]) != ticker_set:
        raise HistoricalPitEk1ReplayError("Ek1 score/rejection coverage invariant bozuldu")

    return HistoricalPitEk1ReplayResult(
        analysis_at=analysis,
        asof_date=asof,
        tickers=tickers,
        ek1_scores=outputs,
        rejections=rejections,
    )
