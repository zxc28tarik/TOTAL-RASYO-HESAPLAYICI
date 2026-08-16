from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values


def build_decile_map(
    conn,
    window_end: str,
    horizon_days: int = 63,
    bucket_count: int = 10
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Join:
      financials_quarterly(ticker,period_end,version_tag,t0_date)
      rsc_summary_quarterly(ticker,period_end,version_tag,rsc_core_norm)
      alpha_realized(ticker,t0_date,horizon_days,alpha_real)
      universe_stocks(ticker,sector_code)
    Build bucket breakpoints on rsc_core_norm within each sector_code.
    Return:
      decile_map_df (to analytics.decile_map)
      thresholds_df (to analytics.decile_thresholds)
    """
    w_end = pd.to_datetime(window_end).date()

    df = pd.read_sql(
        """
        SELECT
          f.ticker,
          f.period_end,
          f.version_tag,
          f.t0_date,
          COALESCE(u.sector_code,'NONFIN') AS sector_code,
          s.rsc_core_norm,
          a.alpha_real
        FROM core.financials_quarterly f
        JOIN analytics.rsc_summary_quarterly s
          ON s.ticker=f.ticker AND s.period_end=f.period_end AND s.version_tag=f.version_tag
        JOIN analytics.alpha_realized a
          ON a.ticker=f.ticker AND a.t0_date=f.t0_date AND a.horizon_days=%(h)s
        LEFT JOIN core.universe_stocks u ON u.ticker=f.ticker
        WHERE f.t0_date IS NOT NULL
          AND f.t0_date <= %(we)s
        """,
        conn, params={"h": int(horizon_days), "we": w_end}
    )
    if df.empty:
        empty_map = pd.DataFrame(columns=["sector_code","horizon_days","bucket_count","bucket_id","mu_alpha","sigma_alpha","n_obs","window_end"])
        empty_thr = pd.DataFrame(columns=["sector_code","horizon_days","bucket_count","window_end","breakpoints"])
        return empty_map, empty_thr

    df["rsc_core_norm"] = pd.to_numeric(df["rsc_core_norm"], errors="coerce")
    df["alpha_real"] = pd.to_numeric(df["alpha_real"], errors="coerce")
    df = df.dropna(subset=["rsc_core_norm","alpha_real"])

    maps = []
    thrs = []

    for sec, g in df.groupby("sector_code", sort=False):
        x = g["rsc_core_norm"].to_numpy(dtype=float)
        if len(x) < bucket_count * 10:
            # too few observations; still create buckets by quantile but may collapse
            pass

        # breakpoints for bucket_count: need bucket_count-1 cut points
        qs = [i / bucket_count for i in range(1, bucket_count)]
        bps = np.quantile(x, qs).tolist()

        # assign bucket id: 1..bucket_count
        def bucket(v: float) -> int:
            # count how many breakpoints v exceeds
            k = 0
            for bp in bps:
                if v > bp:
                    k += 1
            return k + 1

        g = g.copy()
        g["bucket_id"] = g["rsc_core_norm"].apply(bucket).astype(int)

        agg = g.groupby("bucket_id")["alpha_real"].agg(["mean","std","count"]).reset_index()
        for r in agg.itertuples(index=False):
            maps.append((
                str(sec),
                int(horizon_days),
                int(bucket_count),
                int(r.bucket_id),
                float(r.mean) if r.mean == r.mean else None,
                float(r.std) if r.std == r.std else None,
                int(r.count),
                w_end
            ))

        thrs.append((str(sec), int(horizon_days), int(bucket_count), w_end, bps))

    map_df = pd.DataFrame(
        maps,
        columns=["sector_code","horizon_days","bucket_count","bucket_id","mu_alpha","sigma_alpha","n_obs","window_end"]
    )
    thr_df = pd.DataFrame(
        thrs,
        columns=["sector_code","horizon_days","bucket_count","window_end","breakpoints"]
    )
    return map_df, thr_df


def upsert_decile_map(conn, map_df: pd.DataFrame, thr_df: pd.DataFrame) -> None:
    with conn:
        with conn.cursor() as cur:
            if not map_df.empty:
                rows = [tuple(r) for r in map_df.itertuples(index=False, name=None)]
                execute_values(
                    cur,
                    """
                    INSERT INTO analytics.decile_map
                      (sector_code,horizon_days,bucket_count,bucket_id,mu_alpha,sigma_alpha,n_obs,window_end)
                    VALUES %s
                    ON CONFLICT (sector_code,horizon_days,bucket_count,bucket_id,window_end)
                    DO UPDATE SET
                      mu_alpha=EXCLUDED.mu_alpha,
                      sigma_alpha=EXCLUDED.sigma_alpha,
                      n_obs=EXCLUDED.n_obs
                    """,
                    rows, page_size=2000
                )

            if not thr_df.empty:
                rows2 = []
                for r in thr_df.itertuples(index=False):
                    rows2.append((r.sector_code, int(r.horizon_days), int(r.bucket_count), r.window_end, list(r.breakpoints)))

                execute_values(
                    cur,
                    """
                    INSERT INTO analytics.decile_thresholds
                      (sector_code,horizon_days,bucket_count,window_end,breakpoints)
                    VALUES %s
                    ON CONFLICT (sector_code,horizon_days,bucket_count,window_end)
                    DO UPDATE SET
                      breakpoints=EXCLUDED.breakpoints
                    """,
                    rows2, page_size=2000
                )