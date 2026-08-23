from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values


def _pct_ret(px: pd.Series) -> pd.Series:
    return px.astype(float).pct_change()


SHRINK_HALF_N = 126  # shrinkage weight = n / (n + SHRINK_HALF_N)


def _ols_2f(y: np.ndarray, x_mkt: np.ndarray, x_sec: np.ndarray) -> Tuple[float, float, float, int]:
    """
    Two-factor model with a sector-EXCESS second factor:

        y = a + b1 * mkt + b2 * (sec - mkt)

    Using the sector excess instead of the raw sector return removes most of the
    collinearity between XU100 and the sector index, so b1/b2 are stable. The
    trailing-alpha consumer applies the exact same decomposition.

    Estimates are shrunk toward the priors b1 -> 1.0, b2 -> 0.0 with weight
    w = n / (n + SHRINK_HALF_N), which stabilises short samples.

    returns (b1, b2, r2, n)
    """
    m = np.isfinite(y) & np.isfinite(x_mkt) & np.isfinite(x_sec)
    y = y[m]; x_mkt = x_mkt[m]; x_sec = x_sec[m]
    n = int(y.shape[0])
    if n < 60:
        return (np.nan, np.nan, np.nan, n)

    x_sec_ex = x_sec - x_mkt
    X = np.column_stack([np.ones(n), x_mkt, x_sec_ex])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    y_hat = X @ beta
    ss_res = float(((y - y_hat) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = np.nan if ss_tot == 0 else (1.0 - ss_res / ss_tot)

    w = n / (n + SHRINK_HALF_N)
    b1 = w * float(beta[1]) + (1.0 - w) * 1.0
    b2 = w * float(beta[2])
    return (float(b1), float(b2), float(r2), n)


def estimate_betas_from_frames(
    *,
    universe: pd.DataFrame,
    prices: pd.DataFrame,
    index_prices: pd.DataFrame,
    t0_date: str | date,
    market_index: str = "XU100",
) -> pd.DataFrame:
    """Run the production two-factor beta math on explicit, pre-cut frames.

    The database-backed live path and the database-free historical replay both
    call this function.  Window selection and PIT validation remain the caller's
    responsibility; this function owns only the production return/OLS/shrinkage
    semantics.
    """

    columns = ["ticker", "t0_date", "beta_mkt", "beta_sec", "r2", "n_obs"]
    if universe.empty:
        return pd.DataFrame(columns=columns)

    tickers = universe["ticker"].astype(str).tolist()
    sec_map = dict(
        zip(universe["ticker"].astype(str), universe["sector_index_code"].astype(str))
    )
    t0 = pd.to_datetime(t0_date).date()
    if prices.empty or index_prices.empty:
        return pd.DataFrame(
            [(ticker, t0, np.nan, np.nan, np.nan, 0) for ticker in tickers],
            columns=columns,
        )

    p = prices.copy()
    ip = index_prices.copy()
    p["trade_date"] = pd.to_datetime(p["trade_date"]).dt.date
    ip["trade_date"] = pd.to_datetime(ip["trade_date"]).dt.date
    spx = p.pivot_table(index="trade_date", columns="ticker", values="px", aggfunc="last").sort_index()
    ipx = ip.pivot_table(index="trade_date", columns="index_code", values="px", aggfunc="last").sort_index()

    # Put both families on one date axis before converting to numpy.  Missing
    # observations stay missing and are removed by _ols_2f's finite mask.
    date_axis = spx.index.union(ipx.index).sort_values()
    sret = spx.reindex(date_axis).apply(_pct_ret, axis=0)
    iret = ipx.reindex(date_axis).apply(_pct_ret, axis=0)
    mret = iret[market_index] if market_index in iret.columns else None

    rows = []
    for ticker in tickers:
        sector = sec_map.get(ticker, market_index)
        if mret is None or sector not in iret.columns or ticker not in sret.columns:
            rows.append((ticker, t0, np.nan, np.nan, np.nan, 0))
            continue
        b1, b2, r2, n = _ols_2f(
            sret[ticker].to_numpy(dtype=float),
            mret.to_numpy(dtype=float),
            iret[sector].to_numpy(dtype=float),
        )
        rows.append((ticker, t0, b1, b2, r2, n))

    return pd.DataFrame(rows, columns=columns)


def estimate_betas_for_date(
    conn,
    t0_date: str,
    lookback_days: int = 252,
    market_index: str = "XU100",
) -> pd.DataFrame:
    """
    Produces betas for tickers in core.universe_stocks at a given t0_date.
    Uses rolling lookback in TRADING DAYS (approx) based on index trading calendar.
    """
    t0 = pd.to_datetime(t0_date).date()

    # universe (ticker -> sector_index_code)
    u = pd.read_sql(
        "SELECT ticker, COALESCE(sector_index_code,'XU100') AS sector_index_code "
        "FROM core.universe_stocks WHERE is_active=true",
        conn
    )
    if u.empty:
        return pd.DataFrame(columns=["ticker","t0_date","beta_mkt","beta_sec","r2","n_obs"])

    tickers = u["ticker"].astype(str).tolist()
    sec_map = dict(zip(u["ticker"].astype(str), u["sector_index_code"].astype(str)))

    # trading days from market index
    td = pd.read_sql(
        "SELECT trade_date FROM core.index_prices_daily WHERE index_code=%(i)s ORDER BY trade_date ASC",
        conn, params={"i": market_index}
    )
    if td.empty:
        return pd.DataFrame(columns=["ticker","t0_date","beta_mkt","beta_sec","r2","n_obs"])

    trading_days = list(pd.to_datetime(td["trade_date"]).dt.date)
    if t0 not in trading_days:
        # if t0 not trading day, use previous available
        t0 = max([d for d in trading_days if d <= t0], default=trading_days[-1])

    i0 = trading_days.index(t0)
    i1 = max(i0 - lookback_days, 0)
    start = trading_days[i1]
    end = t0

    # pull stock prices
    p = pd.read_sql(
        """
        SELECT ticker, trade_date, COALESCE(adj_close, close) AS px
        FROM core.prices_daily
        WHERE ticker = ANY(%(t)s) AND trade_date >= %(s)s AND trade_date <= %(e)s
        """,
        conn, params={"t": tickers, "s": start, "e": end}
    )
    if p.empty:
        return pd.DataFrame(columns=["ticker","t0_date","beta_mkt","beta_sec","r2","n_obs"])

    p["trade_date"] = pd.to_datetime(p["trade_date"]).dt.date

    # pull index prices (market + all sector indices present)
    indices = sorted(set([market_index] + [sec_map[t] for t in tickers]))
    ip = pd.read_sql(
        """
        SELECT index_code, trade_date, close AS px
        FROM core.index_prices_daily
        WHERE index_code = ANY(%(i)s) AND trade_date >= %(s)s AND trade_date <= %(e)s
        """,
        conn, params={"i": indices, "s": start, "e": end}
    )
    if ip.empty:
        return pd.DataFrame(columns=["ticker","t0_date","beta_mkt","beta_sec","r2","n_obs"])

    ip["trade_date"] = pd.to_datetime(ip["trade_date"]).dt.date

    return estimate_betas_from_frames(
        universe=u,
        prices=p,
        index_prices=ip,
        t0_date=t0,
        market_index=market_index,
    )


def upsert_betas(conn, df: pd.DataFrame) -> None:
    if df.empty:
        return
    rows = []
    for r in df.itertuples(index=False):
        rows.append((r.ticker, r.t0_date, r.beta_mkt, r.beta_sec, r.r2, int(r.n_obs)))

    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO analytics.beta_estimates
                  (ticker, t0_date, beta_mkt, beta_sec, r2, n_obs)
                VALUES %s
                ON CONFLICT (ticker, t0_date)
                DO UPDATE SET
                  beta_mkt=EXCLUDED.beta_mkt,
                  beta_sec=EXCLUDED.beta_sec,
                  r2=EXCLUDED.r2,
                  n_obs=EXCLUDED.n_obs
                """,
                rows, page_size=2000
            )
