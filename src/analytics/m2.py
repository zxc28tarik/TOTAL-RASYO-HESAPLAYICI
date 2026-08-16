from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd


def _close(conn, ticker: str, d: date) -> Optional[float]:
    df = pd.read_sql(
        "SELECT COALESCE(adj_close, close) AS px FROM core.prices_daily WHERE ticker=%(t)s AND trade_date=%(d)s",
        conn, params={"t": ticker, "d": d}
    )
    if df.empty:
        return None
    v = df.iloc[0, 0]
    return None if v is None else float(v)


def score_m2_from_band(current_px: float, low: float, high: float) -> float:
    """
    Idea:
      - If price is below LOW band => strong undervaluation => score ~1
      - If within [low, high] => neutral => score ~0.5
      - If above HIGH => overvaluation => score ~0
    Smooth with distance.
    """
    if not np.isfinite(current_px) or not np.isfinite(low) or not np.isfinite(high):
        return 0.5
    if high <= low:
        return 0.5

    if current_px < low:
        # deeper below => higher
        z = (low - current_px) / max(low, 1e-9)
        return float(min(1.0, 0.6 + 0.8 * z))
    if current_px > high:
        z = (current_px - high) / max(high, 1e-9)
        return float(max(0.0, 0.4 - 0.8 * z))
    # inside band
    mid = (low + high) / 2.0
    dist = abs(current_px - mid) / max(mid, 1e-9)
    return float(max(0.2, 0.5 - 0.6 * dist))


def compute_m2_scores(conn, asof_date: str, horizon_days: int = 63) -> pd.DataFrame:
    asof = pd.to_datetime(asof_date).date()

    # latest band per ticker
    band = pd.read_sql(
        """
        SELECT DISTINCT ON (ticker)
          ticker, t0_date, horizon_days, p_exp_low, p_exp_high
        FROM analytics.expected_price_band
        WHERE horizon_days=%(h)s AND t0_date <= %(asof)s
        ORDER BY ticker, t0_date DESC
        """,
        conn, params={"h": int(horizon_days), "asof": asof}
    )
    if band.empty:
        return pd.DataFrame(columns=["ticker","m2"])

    rows = []
    for r in band.itertuples(index=False):
        px = _close(conn, str(r.ticker), asof)
        if px is None:
            continue
        m2 = score_m2_from_band(float(px), float(r.p_exp_low), float(r.p_exp_high))
        rows.append((str(r.ticker), float(m2)))

    return pd.DataFrame(rows, columns=["ticker","m2"])