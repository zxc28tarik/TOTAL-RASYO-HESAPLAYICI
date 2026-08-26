from __future__ import annotations

"""PIT-safe, database-free replay of the production M1 8-quarter trend logic."""

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable

import numpy as np
import pandas as pd

from src.analytics.historical_pit_rsc_replay import HistoricalPitRscReplayResult
from src.analytics.period_trend import _slope, _trend_label, _trend_score


PERIOD_COLUMNS = (
    "ticker", "asof_date", "latest_period_end", "period_count", "score_latest", "score_prev",
    "score_avg_8q", "score_min_8q", "score_max_8q", "score_change_1q", "score_change_4q",
    "score_slope_8q", "rsc_latest", "rsc_prev", "rsc_change_1q", "good_count_latest",
    "good_count_prev", "good_count_change_1q", "quality_trend_score", "quality_trend_label",
)


class HistoricalPitM1ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitM1ReplayResult:
    analysis_at: datetime
    asof_date: date
    tickers: tuple[str, ...]
    period_comparison: pd.DataFrame
    m1_scores: pd.DataFrame


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitM1ReplayError(f"{field} dolu metin olmali")
    return value.strip().upper()


def _normalize_asof(value: str | date | datetime) -> date:
    try:
        ts = pd.Timestamp(value)
    except Exception as exc:
        raise HistoricalPitM1ReplayError("asof_date gecersiz") from exc
    if pd.isna(ts):
        raise HistoricalPitM1ReplayError("asof_date bos olamaz")
    return ts.date()


def _required(frame: pd.DataFrame, cols: Iterable[str]) -> None:
    missing = set(cols) - set(frame.columns)
    if missing:
        raise HistoricalPitM1ReplayError(f"rsc_summary missing columns: {sorted(missing)}")


def _validate_summary(replay: HistoricalPitRscReplayResult) -> pd.DataFrame:
    frame = replay.rsc_summary
    _required(
        frame,
        ("ticker", "period_end", "version_tag", "rsc_core_norm", "good_count_ge8", "score_mean"),
    )
    out = frame.copy(deep=True)
    if out.empty:
        return out

    out["ticker"] = out["ticker"].map(lambda v: _text(v, "ticker"))
    foreign = sorted(set(out["ticker"]) - set(replay.tickers))
    if foreign:
        raise HistoricalPitM1ReplayError(f"PIT RSC routing disi ticker iceriyor: {foreign}")

    try:
        out["period_end"] = pd.to_datetime(out["period_end"], errors="raise").dt.normalize()
    except Exception as exc:
        raise HistoricalPitM1ReplayError("period_end gecersiz") from exc
    if out["period_end"].isna().any():
        raise HistoricalPitM1ReplayError("period_end bos olamaz")

    analysis_day = pd.Timestamp(replay.analysis_at).tz_convert("UTC").tz_localize(None).normalize()
    if (out["period_end"] > analysis_day).any():
        raise HistoricalPitM1ReplayError("analysis_at sonrasi period_end M1'e sizdi")

    out["version_tag"] = out["version_tag"].map(lambda v: _text(v, "version_tag"))
    # M1 means eight financial periods, not eight restatement versions.  The PIT
    # foundation must have selected exactly one eligible version per ticker-period.
    if out.duplicated(["ticker", "period_end"]).any():
        raise HistoricalPitM1ReplayError("ticker+period_end icin birden fazla PIT version var")

    for field in ("score_mean", "rsc_core_norm"):
        out[field] = pd.to_numeric(out[field], errors="coerce")
        nonfinite = out[field].notna() & ~out[field].map(lambda x: math.isfinite(float(x)) if pd.notna(x) else True)
        if nonfinite.any():
            raise HistoricalPitM1ReplayError(f"{field} sonlu olmali")

    goods = pd.to_numeric(out["good_count_ge8"], errors="coerce").fillna(0)
    if (~goods.map(lambda x: math.isfinite(float(x)))).any():
        raise HistoricalPitM1ReplayError("good_count_ge8 sonlu olmali")
    if (goods < 0).any():
        raise HistoricalPitM1ReplayError("good_count_ge8 negatif olamaz")
    out["good_count_ge8"] = goods.astype(int)
    return out.sort_values(["ticker", "period_end"]).reset_index(drop=True)


def run_historical_pit_m1_replay(
    rsc_replay: HistoricalPitRscReplayResult,
    *,
    asof_date: str | date | datetime,
) -> HistoricalPitM1ReplayResult:
    """Reproduce production M1 from the PIT-safe RSC summary only."""

    if not isinstance(rsc_replay, HistoricalPitRscReplayResult):
        raise HistoricalPitM1ReplayError("rsc_replay HistoricalPitRscReplayResult olmali")
    if rsc_replay.analysis_at.tzinfo is None or rsc_replay.analysis_at.utcoffset() is None:
        raise HistoricalPitM1ReplayError("analysis_at timezone-aware olmali")

    asof = _normalize_asof(asof_date)
    df = _validate_summary(rsc_replay)
    rows: list[tuple[object, ...]] = []

    for ticker, group in df.groupby("ticker", sort=False):
        g = group.sort_values("period_end").tail(8).reset_index(drop=True)
        count = int(len(g))
        if count == 0:
            continue
        scores = g["score_mean"].tolist()
        rscs = g["rsc_core_norm"].tolist()
        goods = g["good_count_ge8"].tolist()

        latest = scores[-1] if pd.notna(scores[-1]) else None
        prev = scores[-2] if count >= 2 and pd.notna(scores[-2]) else None
        clean = [float(v) for v in scores if v is not None and pd.notna(v) and np.isfinite(float(v))]
        avg8 = float(np.mean(clean)) if clean else None
        min8 = float(np.min(clean)) if clean else None
        max8 = float(np.max(clean)) if clean else None
        change1 = float(latest) - float(prev) if latest is not None and prev is not None else None
        base4 = scores[-5] if count >= 5 and pd.notna(scores[-5]) else None
        change4 = float(latest) - float(base4) if latest is not None and base4 is not None else None
        slope8 = _slope([v for v in scores if pd.notna(v)])

        rsc_latest = rscs[-1] if pd.notna(rscs[-1]) else None
        rsc_prev = rscs[-2] if count >= 2 and pd.notna(rscs[-2]) else None
        rsc_change1 = (
            float(rsc_latest) - float(rsc_prev)
            if rsc_latest is not None and rsc_prev is not None else None
        )
        good_latest = int(goods[-1])
        good_prev = int(goods[-2]) if count >= 2 else None
        good_change1 = good_latest - good_prev if good_prev is not None else None
        qscore = _trend_score(latest, avg8, change1, change4, slope8)
        qlabel = _trend_label(qscore, slope8, change1)

        rows.append((
            ticker, asof, g["period_end"].iloc[-1].date(), count,
            latest, prev, avg8, min8, max8, change1, change4, slope8,
            rsc_latest, rsc_prev, rsc_change1, good_latest, good_prev, good_change1,
            qscore, qlabel,
        ))

    period = pd.DataFrame(rows, columns=PERIOD_COLUMNS)
    if period.empty:
        m1 = pd.DataFrame(columns=["ticker", "m1", "period_end", "good_count_ge8"])
    else:
        m1 = period[["ticker", "quality_trend_score", "latest_period_end", "good_count_latest"]].copy()
        m1.columns = ["ticker", "m1", "period_end", "good_count_ge8"]
        m1["m1"] = pd.to_numeric(m1["m1"], errors="raise").clip(0.0, 1.0)
        m1["good_count_ge8"] = pd.to_numeric(m1["good_count_ge8"], errors="raise").astype(int)

    return HistoricalPitM1ReplayResult(
        analysis_at=rsc_replay.analysis_at,
        asof_date=asof,
        tickers=rsc_replay.tickers,
        period_comparison=period,
        m1_scores=m1,
    )
