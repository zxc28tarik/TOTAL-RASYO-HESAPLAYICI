from __future__ import annotations

"""Shared production arithmetic for the Ek9 daily-return volatility score."""

import numpy as np
import pandas as pd

EK9_LOOKBACK_DAYS = 63
EK9_VOLATILITY_CAP = 0.06


def compute_ek9_volatility_scores(returns: pd.DataFrame) -> pd.DataFrame:
    """Apply the live Ek9 volatility/score arithmetic column-wise.

    Data selection, pct_change semantics and PIT validation belong to the caller.
    Keeping this function arithmetic-only lets the live DB path preserve its
    existing preprocessing while the historical replay shares the exact
    ``std(ddof=1)`` and score mapping.
    """
    if not isinstance(returns, pd.DataFrame):
        raise TypeError("returns DataFrame olmali")
    vol = returns.std(ddof=1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    ek9 = 1.0 - (vol / EK9_VOLATILITY_CAP).clip(0.0, 1.0)
    return pd.DataFrame({"volatility": vol.astype(float), "ek9": ek9.astype(float)})
