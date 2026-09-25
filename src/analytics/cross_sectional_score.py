from __future__ import annotations

"""Cross-sectional percentile scoring: a 0-1 mapping with no constants to choose.

Why this exists
---------------
Every module's 0-1 mapping in the original design is a hardcoded linear band --
M3 and Ek4 share clip((excess + 0.20) / 0.40), Ek1 divides good_count by 18, Ek9
subtracts volatility from a 0.06 cap. R1 measured what those constants do to
BIST: the shared +/-20% band clips 42.3% of its own input, whose standard
deviation is 38.7%; Ek1's divisor is reached by 1 company in 372; Ek9's perfect
score needs volatility of exactly zero, so the top of its range is unreachable.

Ranking a month's cohort fixes all of that at once and, crucially, introduces no
parameter that could be tuned to the sample:

* nothing clips, so the ties a band creates at its floor disappear -- R3 measured
  M3's floor share going from 19.0% of in-sample cells to 0.0%,
* the full 0-1 range is inhabited by construction,
* the mapping is invariant to any monotone change in the input's scale, so
  inflation, a currency regime or a market-wide drawdown cannot silently move
  what a given score means.

Ranking is monotone, so applying it to an already-computed unclipped score gives
the same result as applying it to that score's raw input. Where a band has
clipped, it does not: the ordering inside the clipped group is already gone, so
those modules must be ranked on their raw inputs to recover it.

What it costs
-------------
The score becomes relative. In a month where every company is deteriorating,
someone still scores 1.0. That is correct for a ranking, and wrong for a decision
about whether to hold anything at all, so a caller that needs an absolute verdict
must get it from somewhere other than the rank.
"""

from typing import Hashable, Iterable, Mapping

import pandas as pd

CONTRACT = "CROSS_SECTIONAL_PERCENTILE_SCORE_V1"
TIE_METHOD = "average"


class CrossSectionalScoreError(ValueError):
    """Raised when a cohort cannot be ranked as asked."""


def percentile_score(values: pd.Series, *, minimum_cohort: int = 5) -> pd.Series:
    """Rank one cohort's values into (0, 1], leaving missing values missing.

    ``minimum_cohort`` guards the degenerate end: a rank over two names says
    almost nothing but would still emit a confident 0.5 and 1.0, so a cohort
    below the floor returns all-missing rather than a fabricated spread.
    """
    numeric = pd.to_numeric(values, errors="coerce")
    present = numeric.dropna()
    if len(present) < minimum_cohort or present.nunique() < 2:
        return pd.Series(pd.NA, index=values.index, dtype="Float64")
    ranked = present.rank(method=TIE_METHOD, pct=True)
    return ranked.reindex(values.index).astype("Float64")


def percentile_score_by_cohort(frame: pd.DataFrame, *, value_column: str,
                               cohort_columns: Iterable[str],
                               minimum_cohort: int = 5) -> pd.Series:
    """Rank ``value_column`` within each cohort defined by ``cohort_columns``.

    The cohort is normally the month, and may be narrowed further -- by sector,
    say -- when a score is only comparable inside a peer group.
    """
    cohorts = list(cohort_columns)
    if not cohorts:
        raise CrossSectionalScoreError("cohort_columns bos olamaz")
    missing = [column for column in (*cohorts, value_column) if column not in frame.columns]
    if missing:
        raise CrossSectionalScoreError(f"eksik kolonlar: {missing}")
    if frame.empty:
        return pd.Series(dtype="Float64", index=frame.index)
    return (frame.groupby(cohorts, dropna=False, sort=False)[value_column]
            .transform(lambda group: percentile_score(group, minimum_cohort=minimum_cohort)))


def clipping_share(values: pd.Series, *, low: float = 0.0, high: float = 1.0,
                   tolerance: float = 1e-9) -> Mapping[Hashable, float]:
    """How much of a banded score is pinned to either end -- the loss ranking undoes."""
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return {"at_low": 0.0, "at_high": 0.0, "total": 0.0}
    at_low = float((numeric <= low + tolerance).mean())
    at_high = float((numeric >= high - tolerance).mean())
    return {"at_low": at_low, "at_high": at_high, "total": at_low + at_high}
