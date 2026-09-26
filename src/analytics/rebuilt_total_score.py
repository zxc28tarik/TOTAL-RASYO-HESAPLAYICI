from __future__ import annotations

"""The rebuilt scoring layer: four measured defects removed, nothing fitted.

Every parameter the original design chose by intuition is either removed or
replaced by a choice with no freedom in it. Nothing here is tuned to a sample,
because R2 established that this sample carries no defensible forward signal --
fitting to it would repeat the mistake the rebuild exists to correct.

What changed and why each change needs no fitting
-------------------------------------------------
1. **Mappings become cross-sectional ranks.** R1 measured the old bands: M3 and
   Ek4 shared clip((excess + 0.20) / 0.40) against an input with a 38.7% standard
   deviation, clipping 42.3% of it; Ek1 divided good_count by 18 when 1 company
   in 372 reaches 18; Ek9 needed volatility of exactly zero for a perfect score,
   leaving the top 22% of its range unreachable. Ranking a cohort removes every
   one of those constants at once. R3 measured the effect on M3: the floor share
   went 19.0% -> 0.0% and the forward rank correlation roughly doubled in the
   same direction at all three horizons tested.

2. **Weights become equal.** This is the no-knowledge default, not a fit. R2
   found no module clears significance at any horizon, so there is nothing to
   justify 0.40 on M2 or 0.18 on M1 -- and equally nothing to justify a vector of
   my own choosing. Equal weight is the only assignment that adds no claim.

3. **The veto is gone.** good_count fed Ek1 additively at 0.08 AND the veto
   multiplicatively at 0.60 past a threshold of 5, so one variable was scored
   twice with opposing signs. R0 measured the veto firing on 186 of 372 companies
   while separating them by 0.046 of base score; R2 found Ek1 negative at every
   horizon, so the penalty fell on the companies that went on to do better.
   Removing the second channel is a coherence fix, not a calibration.

4. **Bands are defined by quantile, not by a level.** The old 0.70 / 0.55 cut a
   score that inhabited 10.4-71.1 of 0-100, so AL was reached by 2 companies of
   372. Ranks are near-uniform by construction, which makes a level meaningless
   and a quantile exact: the caller says what share of the cohort it wants to
   call AL, and gets that share.

What this does NOT claim
------------------------
It does not claim to predict returns. The five modules it can score showed no
forward skill in R2, and removing four defects does not manufacture a signal. It
claims only that the score is now coherent: it uses its range, counts each input
once, and carries no constant that nobody can justify.
"""

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import pandas as pd

from src.analytics.cross_sectional_score import percentile_score_by_cohort

CONTRACT = "REBUILT_TOTAL_SCORE_V1"

# Modules whose own mapping already clipped are ranked on raw inputs, because the
# order inside a clipped group is gone and ranking the score cannot recover it.
CLIPPED_BY_OLD_MAPPING = frozenset({"M3", "Ek4"})

# A rank over a handful of names is not a ranking. Below this the cohort's scores
# come back missing rather than spread over three companies.
MINIMUM_COHORT = 5


@dataclass(frozen=True)
class BandPolicy:
    """What share of each cohort is called AL and IZLE, as a definition."""

    al_share: float = 0.10
    izle_share: float = 0.25

    def __post_init__(self) -> None:
        if not 0.0 < self.al_share < self.izle_share < 1.0:
            raise ValueError("0 < al_share < izle_share < 1 olmali")

    def decide(self, percentile: float | None) -> str | None:
        """Cut strictly above the boundary, so a share means that share.

        ``percentile_score`` puts the worst of n names at 1/n and the best at 1.0,
        so a cohort of 100 has ranks 0.01 .. 1.00. Admitting the boundary itself
        would let 0.90 through and return 11 names for a tenth, which a test
        caught. Strict comparison returns exactly the share asked for whenever the
        inputs are distinct; genuine ties can still straddle the line, and they
        should, because breaking them would be inventing an order.
        """
        if percentile is None or pd.isna(percentile):
            return None
        if percentile > 1.0 - self.al_share:
            return "AL"
        if percentile > 1.0 - self.izle_share:
            return "IZLE"
        return "UZAK"


@dataclass(frozen=True)
class RebuiltScoreConfig:
    """Modules in play, and the two things a caller may legitimately decide."""

    modules: Sequence[str]
    bands: BandPolicy = field(default_factory=BandPolicy)
    minimum_cohort: int = MINIMUM_COHORT

    def __post_init__(self) -> None:
        if not self.modules:
            raise ValueError("modules bos olamaz")
        if len(set(self.modules)) != len(self.modules):
            raise ValueError("modules yinelenen isim icermemeli")

    @property
    def weights(self) -> Mapping[str, float]:
        """Equal by construction. There is no setter, so no vector can be slipped in."""
        share = 1.0 / len(self.modules)
        return {module: share for module in self.modules}


def rebuilt_scores(frame: pd.DataFrame, *, config: RebuiltScoreConfig,
                   cohort_columns: Sequence[str] = ("month",)) -> pd.DataFrame:
    """Rank each module within its cohort, average the ranks, rank the average.

    A module column absent from `frame` is an error rather than a silent zero: a
    missing module must be visible, because averaging over whichever modules
    happen to be present would quietly reweight the survivors.

    A row missing any module's value yields no score. That is the original
    design's one sound instinct -- an incomplete row is refused, never filled.
    """
    missing = [module for module in config.modules if module not in frame.columns]
    if missing:
        raise ValueError(f"REBUILT_SCORE_MODULE_COLUMN_ABSENT:{missing}")
    cohorts = list(cohort_columns)
    if not cohorts:
        raise ValueError("cohort_columns bos olamaz")

    output = frame.copy()
    ranked_columns = []
    for module in config.modules:
        column = f"{module}__rank"
        output[column] = percentile_score_by_cohort(
            output, value_column=module, cohort_columns=cohorts,
            minimum_cohort=config.minimum_cohort)
        ranked_columns.append(column)

    weights = config.weights
    blended = sum(output[f"{module}__rank"].astype("Float64") * weights[module]
                  for module in config.modules)
    # Any missing module leaves the blend missing, which is the fail-closed rule.
    output["blended"] = blended
    output["score_percentile"] = percentile_score_by_cohort(
        output, value_column="blended", cohort_columns=cohorts,
        minimum_cohort=config.minimum_cohort)
    output["total_rasyo_100"] = (output["score_percentile"].astype("Float64") * 100.0)
    output["decision"] = [config.bands.decide(value) for value in output["score_percentile"]]
    output["ranked_columns"] = [ranked_columns] * len(output)
    return output
