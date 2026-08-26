from __future__ import annotations

"""Shared production math for the Ek4 20-trading-day sector momentum score."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Ek4MomentumPoint:
    stock_return: float
    sector_return: float
    excess_return: float
    score: float


def compute_ek4_momentum_point(
    *,
    stock_start: float,
    stock_end: float,
    sector_start: float,
    sector_end: float,
) -> Ek4MomentumPoint:
    """Apply the live Ek4 formula to one stock/sector endpoint pair.

    Input validation and point-in-time coverage belong to the caller.  Keeping
    this function arithmetic-only lets the live DB path and historical replay
    share one exact formula without changing the live path's data contract.
    """

    stock_return = float(stock_end) / float(stock_start) - 1.0
    sector_return = float(sector_end) / float(sector_start) - 1.0
    excess_return = stock_return - sector_return
    score = float(np.clip((excess_return + 0.20) / 0.40, 0.0, 1.0))
    return Ek4MomentumPoint(
        stock_return=float(stock_return),
        sector_return=float(sector_return),
        excess_return=float(excess_return),
        score=score,
    )
