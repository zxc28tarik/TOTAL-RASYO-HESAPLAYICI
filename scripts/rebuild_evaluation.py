from __future__ import annotations

"""Shared evaluation substrate for the scoring-layer rebuild.

Why this module exists
----------------------
The scoring layer (weights, veto, decision bands, per-module 0-1 mappings) is
being rebuilt because it was measured to be inverted: the top-20 twelve-month
gainers score 3.2 points BELOW the bottom-20 losers, because M2 at weight 0.40
points against realised returns and cancels M3+Ek4 at 0.28 by a factor of 1.43.

The defect was not a coding bug. It was a parameter set chosen by intuition and
never evaluated. Replacing it with a different set chosen by a different
intuition would reproduce the failure, so this module exists to make every
proposed parameter set answerable to 60 months of data instead.

Two rules it enforces
---------------------
1. **Holdout lock.** The last 24 months are unreadable unless a caller passes
   the explicit unlock token. Parameter selection may only see the first 36.
   Every receipt records whether the holdout was touched, so an accidental peek
   is visible afterwards rather than silent.

2. **Forward-looking scoring metrics.** A score's job is to say something about
   what happens next. Correlating a score with the return that already happened
   measures internal coherence, not skill, so the two are computed and reported
   separately and never mixed: `trailing_*` diagnoses the model against the past,
   `forward_*` evaluates it against the future it did not see.

Sample-size honesty: 36 in-sample months over roughly 100 names per month is a
small sample. Free parameters must stay few; fitting many knobs against it would
produce a confident number and no knowledge.
"""

from dataclasses import dataclass
from datetime import date
import math
from typing import Iterable, Mapping, Sequence

import pandas as pd

# 2021-08 .. 2026-07 is the declared backtest span. The split is fixed here once
# and never tuned; moving it to improve a result would defeat its purpose.
IN_SAMPLE_FIRST = "2021-08"
IN_SAMPLE_LAST = "2024-07"
HOLDOUT_FIRST = "2024-08"
HOLDOUT_LAST = "2026-07"

# Passing this token is a deliberate, auditable act, not a default.
HOLDOUT_UNLOCK_TOKEN = "I_AM_RUNNING_THE_ONE_SHOT_HOLDOUT_TEST"


class EvaluationSplitError(RuntimeError):
    """Raised when the holdout is reached for without the explicit unlock."""


@dataclass(frozen=True)
class Split:
    name: str
    first_month: str
    last_month: str

    def contains(self, month: str) -> bool:
        return self.first_month <= month <= self.last_month


IN_SAMPLE = Split("IN_SAMPLE", IN_SAMPLE_FIRST, IN_SAMPLE_LAST)
HOLDOUT = Split("HOLDOUT", HOLDOUT_FIRST, HOLDOUT_LAST)


def months_of(split: Split, available: Iterable[str]) -> list[str]:
    return sorted(month for month in set(available) if split.contains(month))


def select_split(rows: Sequence[Mapping], *, split: Split,
                 unlock: str | None = None) -> list[Mapping]:
    """Return only the rows inside `split`, refusing the holdout by default."""
    if split is HOLDOUT and unlock != HOLDOUT_UNLOCK_TOKEN:
        raise EvaluationSplitError(
            "HOLDOUT_LOCKED: the final 24 months may only be read by the one-shot "
            "holdout test, which must pass HOLDOUT_UNLOCK_TOKEN explicitly. "
            "Parameter selection must use IN_SAMPLE."
        )
    return [row for row in rows if split.contains(str(row["month"]))]


def forward_returns(prices: pd.DataFrame, signal_days: Sequence[str]) -> pd.DataFrame:
    """Return, per (month, ticker), the OPEN-to-OPEN return to the next signal day.

    OPEN to OPEN is used because the portfolio engine executes at the signal-day
    OPEN, so this is the return a position actually earns between two rebalances.
    The final month has no successor and is therefore absent rather than zero.
    """
    required = {"trade_date", "ticker", "open"}
    if missing := required - set(prices.columns):
        raise ValueError(f"forward_returns missing columns: {sorted(missing)}")
    days = sorted(set(signal_days))
    frame = prices.loc[prices.trade_date.isin(days), ["trade_date", "ticker", "open"]].copy()
    frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
    frame = frame.loc[frame.open > 0]
    wide = frame.pivot_table(index="trade_date", columns="ticker", values="open", aggfunc="last")
    wide = wide.reindex(days)
    forward = wide.shift(-1) / wide - 1.0
    rows = []
    for day in days[:-1]:
        month = day[:7]
        series = forward.loc[day].dropna()
        rows.extend({"month": month, "signal_date": day, "ticker": ticker,
                     "forward_return": float(value)}
                    for ticker, value in series.items())
    return pd.DataFrame(rows, columns=("month", "signal_date", "ticker", "forward_return"))


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    frame = pd.DataFrame({"a": left, "b": right}).dropna()
    if len(frame) < 3 or frame.a.nunique() < 2 or frame.b.nunique() < 2:
        return None
    value = frame.corr(method="spearman").iloc[0, 1]
    return None if pd.isna(value) else float(value)


def discrimination(scored: pd.DataFrame, *, score_column: str,
                   return_column: str, extreme_n: int = 20) -> dict:
    """How well a score separates the best from the worst outcomes.

    `top_minus_bottom_score_gap` is the headline: take the `extreme_n` names with
    the highest realised return and the `extreme_n` with the lowest, and report
    the difference in their mean scores. A sound score makes this strongly
    positive; the pre-rebuild model makes it negative.
    """
    frame = scored[[score_column, return_column]].dropna()
    if len(frame) < 2 * extreme_n:
        return {"usable": False, "row_count": int(len(frame))}
    best = frame.nlargest(extreme_n, return_column)
    worst = frame.nsmallest(extreme_n, return_column)
    per_month = []
    if "month" in scored.columns:
        for month, group in scored.groupby("month"):
            value = _spearman(group[score_column].tolist(), group[return_column].tolist())
            if value is not None:
                per_month.append(value)
    return {
        "usable": True,
        "row_count": int(len(frame)),
        "spearman_pooled": _spearman(frame[score_column].tolist(), frame[return_column].tolist()),
        "spearman_monthly_mean": (
            float(sum(per_month) / len(per_month)) if per_month else None),
        "spearman_monthly_positive_share": (
            float(sum(1 for v in per_month if v > 0) / len(per_month)) if per_month else None),
        "month_count": len(per_month),
        "extreme_n": extreme_n,
        "top_return_mean": float(best[return_column].mean()),
        "bottom_return_mean": float(worst[return_column].mean()),
        "top_score_mean": float(best[score_column].mean()),
        "bottom_score_mean": float(worst[score_column].mean()),
        "top_minus_bottom_score_gap": float(best[score_column].mean() - worst[score_column].mean()),
    }


def scale_usage(scores: Sequence[float], *, low: float = 0.0, high: float = 100.0) -> dict:
    """Whether the 0-100 scale is actually inhabited, or crammed into a band."""
    series = pd.Series([value for value in scores if value is not None and math.isfinite(value)])
    if series.empty:
        return {"usable": False}
    span = float(high - low)
    return {
        "usable": True,
        "count": int(len(series)),
        "min": float(series.min()), "max": float(series.max()),
        "p10": float(series.quantile(0.10)), "median": float(series.median()),
        "p90": float(series.quantile(0.90)),
        "stdev": float(series.std()),
        "observed_span": float(series.max() - series.min()),
        "observed_span_share_of_scale": float((series.max() - series.min()) / span),
        "middle_80pct_band": float(series.quantile(0.90) - series.quantile(0.10)),
    }
