from __future__ import annotations

from datetime import date
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values


def _bucket_from_breakpoints(v: float, breakpoints: list[float]) -> int:
    k = 0
    for bp in breakpoints:
        if v > bp:
            k += 1
    return k + 1


def build_expected_price_band(
    conn,
    asof_date: str,
    horizon_days: int = 63,
    bucket_count: int = 10,
    market_index: str = "XU100",
) -> pd.DataFrame:
    """
    For each ticker, use latest financial report (max period_end) and its t0_date.
    Then:
      p0 = price at t0
      rsc = rsc_core_norm for that period
      bucket = decile bucket for its sector_code using decile_thresholds(window_end=asof)
      expected alpha distribution from decile_map
      expected price band = p0*(1+alpha_mid/low/high)
    """
    asof = pd.to_datetime(asof_date).date()

    # latest report per ticker
    df = pd.read_sql(
        """
        SELECT f.ticker, f.period_end, f.version_tag, f.t0_date,
               COALESCE(u.sector_code,'NONFIN') AS sector_code
        FROM core.financials_quarterly f
        JOIN (
          SELECT ticker, MAX(period_end) AS max_pe
          FROM core.financials_quarterly
          WHERE period_end <= %(asof)s
          GROUP BY ticker
        ) m ON m.ticker=f.ticker AND m.max_pe=f.period_end
        LEFT JOIN core.universe_stocks u ON u.ticker=f.ticker
        WHERE f.t0_date IS NOT NULL
        """,
        conn, params={"asof": asof}
    )
    if df.empty:
        return pd.DataFrame(columns=[
            "ticker","t0_date","horizon_days","p0",
            "exp_alpha_mid","exp_alpha_low","exp_alpha_high",
            "p_exp_mid","p_exp_low","p_exp_high","bucket_count","bucket_id"
        ])

    # rsc values
    rsc = pd.read_sql(
        """
        SELECT ticker, period_end, version_tag, rsc_core_norm
        FROM analytics.rsc_summary_quarterly
        """,
        conn
    )
    df = df.merge(rsc, on=["ticker","period_end","version_tag"], how="left")
    df["rsc_core_norm"] = pd.to_numeric(df["rsc_core_norm"], errors="coerce")

    # thresholds for asof (take latest <= asof)
    thr = pd.read_sql(
        """
        SELECT DISTINCT ON (sector_code) sector_code, breakpoints
        FROM analytics.decile_thresholds
        WHERE horizon_days=%(h)s AND bucket_count=%(b)s AND window_end <= %(asof)s
        ORDER BY sector_code, window_end DESC
        """,
        conn, params={"h": int(horizon_days), "b": int(bucket_count), "asof": asof}
    )
    thr_map = {str(r.sector_code): list(r.breakpoints) for r in thr.itertuples(index=False)} if not thr.empty else {}

    # decile map (latest <= asof)
    dm = pd.read_sql(
        """
        SELECT DISTINCT ON (sector_code, bucket_id)
          sector_code, bucket_id, mu_alpha, sigma_alpha
        FROM analytics.decile_map
        WHERE horizon_days=%(h)s AND bucket_count=%(b)s AND window_end <= %(asof)s
        ORDER BY sector_code, bucket_id, window_end DESC
        """,
        conn, params={"h": int(horizon_days), "b": int(bucket_count), "asof": asof}
    )
    if dm.empty:
        return pd.DataFrame()

    dm_key = {(str(r.sector_code), int(r.bucket_id)): (r.mu_alpha, r.sigma_alpha) for r in dm.itertuples(index=False)}

    # price@t0
    p0s = pd.read_sql(
        """
        SELECT p.ticker, p.trade_date AS t0_date, COALESCE(p.adj_close, p.close) AS p0
        FROM core.prices_daily p
        """,
        conn
    )
    p0s["t0_date"] = pd.to_datetime(p0s["t0_date"]).dt.date
    df["t0_date"] = pd.to_datetime(df["t0_date"]).dt.date
    df = df.merge(p0s, on=["ticker","t0_date"], how="left")

    out = []
    for r in df.itertuples(index=False):
        if r.rsc_core_norm is None or not np.isfinite(float(r.rsc_core_norm)):
            continue
        if r.p0 is None or float(r.p0) <= 0:
            continue

        sec = str(r.sector_code)
        bps = thr_map.get(sec)
        if not bps:
            continue
        bid = _bucket_from_breakpoints(float(r.rsc_core_norm), bps)

        mu, sig = dm_key.get((sec, bid), (None, None))
        if mu is None:
            continue
        mu = float(mu)
        sig = float(sig) if sig is not None else 0.0

        exp_mid = mu
        exp_low = mu - sig
        exp_high = mu + sig

        p0 = float(r.p0)
        p_mid = p0 * (1.0 + exp_mid)
        p_low = p0 * (1.0 + exp_low)
        p_high = p0 * (1.0 + exp_high)

        out.append((
            str(r.ticker),
            r.t0_date,
            int(horizon_days),
            p0,
            exp_mid, exp_low, exp_high,
            p_mid, p_low, p_high,
            int(bucket_count),
            int(bid)
        ))

    return pd.DataFrame(out, columns=[
        "ticker","t0_date","horizon_days","p0",
        "exp_alpha_mid","exp_alpha_low","exp_alpha_high",
        "p_exp_mid","p_exp_low","p_exp_high","bucket_count","bucket_id"
    ])


def upsert_expected_band(conn, df: pd.DataFrame) -> None:
    if df.empty:
        return
    rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO analytics.expected_price_band
                  (ticker,t0_date,horizon_days,p0,exp_alpha_mid,exp_alpha_low,exp_alpha_high,
                   p_exp_mid,p_exp_low,p_exp_high,bucket_count,bucket_id)
                VALUES %s
                ON CONFLICT (ticker,t0_date,horizon_days)
                DO UPDATE SET
                  p0=EXCLUDED.p0,
                  exp_alpha_mid=EXCLUDED.exp_alpha_mid,
                  exp_alpha_low=EXCLUDED.exp_alpha_low,
                  exp_alpha_high=EXCLUDED.exp_alpha_high,
                  p_exp_mid=EXCLUDED.p_exp_mid,
                  p_exp_low=EXCLUDED.p_exp_low,
                  p_exp_high=EXCLUDED.p_exp_high,
                  bucket_count=EXCLUDED.bucket_count,
                  bucket_id=EXCLUDED.bucket_id
                """,
                rows, page_size=2000
            )