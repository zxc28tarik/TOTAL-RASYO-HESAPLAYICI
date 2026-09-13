from __future__ import annotations

"""Shared production arithmetic for the Ek9 daily-return volatility score."""

import numpy as np
import pandas as pd

EK9_LOOKBACK_DAYS = 63
EK9_VOLATILITY_CAP = 0.06


def compute_ek9_volatility_scores(returns: pd.DataFrame) -> pd.DataFrame:
    """Apply the live Ek9 volatility/score arithmetic column-wise.

    Date selection, the 63-return window and PIT validation belong to the caller.
    Undefined or incomplete arithmetic stays missing. Valid columns retain the
    exact ``std(ddof=1)`` and score mapping, including genuine zero volatility.
    """
    if not isinstance(returns, pd.DataFrame):
        raise TypeError("returns DataFrame olmali")
    numeric = returns.apply(pd.to_numeric, errors="coerce").astype(float)
    valid = np.isfinite(numeric).all(axis=0) & (len(numeric) >= 2)
    vol = pd.Series(np.nan, index=numeric.columns, dtype=float)
    with np.errstate(over="ignore", invalid="ignore"):
        vol.loc[valid] = numeric.loc[:, valid].std(ddof=1)
    vol = vol.where(np.isfinite(vol))
    ek9 = 1.0 - (vol / EK9_VOLATILITY_CAP).clip(0.0, 1.0)
    return pd.DataFrame({"volatility": vol.astype(float), "ek9": ek9.astype(float)})
