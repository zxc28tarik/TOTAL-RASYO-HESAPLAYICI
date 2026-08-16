from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values

from src.utils.calendar import get_trading_days, add_trading_days


def _px(conn, ticker: str, d: date) -> Optional[float]:
    df = pd.read_sql(
        "SELECT COALESCE(adj_close, close) AS px FROM core.prices_daily WHERE ticker=%(t)s AND trade_date=%(d)s",
        conn, params={"t": ticker, "d": d}
    )
    if df.empty:
        return None
    v = df.iloc[0, 0]
    return None if v is None else float(v)


def _ipx(conn, index_code: str, d: date) -> Optional[float]:
    df = pd.read_sql(
        "SELECT close AS px FROM core.index_prices_daily WHERE index_code=%(i)s AND trade_date=%(d)s",
        conn, params={"i": index_code, "d": d}
    )
    if df.empty:
        return None
    v = df.iloc[0, 0]
    return None if v is None else float(v)


def compute_alpha_realized(
    conn,
    horizon_days: int = 63,
    market_index: str = "XU100",
    t0_min: Optional[str] = None,
    t0_max: Optional[str] = None,
) -> pd.DataFrame:
    """
    alpha_real = stock_ret - beta_mkt*mkt_ret - beta_sec*(sec_ret - mkt_ret)
    Uses beta_estimates at t0_date (sector-excess factor, same as betas.py).
    """
    # t0 list from financials (you load quarterlies)
    q = """
    SELECT f.ticker, f.t0_date, COALESCE(u.sector_index_code,'XU100') AS sector_index_code
    FROM core.financials_quarterly f
    LEFT JOIN core.universe_stocks u ON u.ticker=f.ticker
    WHERE f.t0_date IS NOT NULL
    """
    params = {}
    if t0_min:
        q += " AND f.t0_date >= %(mn)s"
        params["mn"] = t0_min
    if t0_max:
        q += " AND f.t0_date <= %(mx)s"
        params["mx"] = t0_max

    df = pd.read_sql(q, conn, params=params)
    if df.empty:
        return pd.DataFrame(columns=["ticker","t0_date","horizon_days","stock_ret","mkt_ret","sec_ret","alpha_real"])

    trading_days = get_trading_days(conn)
    max_allowed_end = pd.to_datetime(t0_max).date() if t0_max else None
    out_rows = []

    for r in df.itertuples(index=False):
        t = str(r.ticker)
        t0 = pd.to_datetime(r.t0_date).date()
        sec = str(r.sector_index_code) if r.sector_index_code else market_index

        if t0 not in trading_days:
            # skip if calendar mismatch
            continue
        t1 = add_trading_days(t0, horizon_days, trading_days)
        # Guard against look-ahead leakage in historical/backtest runs.
        # If a maximum as-of date is provided, only completed forward windows are allowed.
        if max_allowed_end is not None and t1 > max_allowed_end:
            continue

        p0 = _px(conn, t, t0)
        p1 = _px(conn, t, t1)
        if p0 is None or p1 is None or p0 <= 0:
            continue
        stock_ret = p1 / p0 - 1.0

        m0 = _ipx(conn, market_index, t0)
        m1 = _ipx(conn, market_index, t1)
        if m0 is None or m1 is None or m0 <= 0:
            continue
        mkt_ret = m1 / m0 - 1.0

        s0 = _ipx(conn, sec, t0)
        s1 = _ipx(conn, sec, t1)
        if s0 is None or s1 is None or s0 <= 0:
            continue
        sec_ret = s1 / s0 - 1.0

        b = pd.read_sql(
            "SELECT beta_mkt, beta_sec FROM analytics.beta_estimates WHERE ticker=%(t)s AND t0_date=%(d)s",
            conn, params={"t": t, "d": t0}
        )
        if b.empty:
            continue
        beta_mkt = b.iloc[0, 0]
        beta_sec = b.iloc[0, 1]
        if beta_mkt is None or beta_sec is None:
            continue

        # Same decomposition as beta estimation: sector factor is the excess over the market.
        alpha = stock_ret - float(beta_mkt) * mkt_ret - float(beta_sec) * (sec_ret - mkt_ret)
        out_rows.append((t, t0, int(horizon_days), float(stock_ret), float(mkt_ret), float(sec_ret), float(alpha)))

    return pd.DataFrame(out_rows, columns=["ticker","t0_date","horizon_days","stock_ret","mkt_ret","sec_ret","alpha_real"])


def upsert_alpha_realized(conn, df: pd.DataFrame) -> None:
    if df.empty:
        return
    rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO analytics.alpha_realized
                  (ticker, t0_date, horizon_days, stock_ret, mkt_ret, sec_ret, alpha_real)
                VALUES %s
                ON CONFLICT (ticker, t0_date, horizon_days)
                DO UPDATE SET
                  stock_ret=EXCLUDED.stock_ret,
                  mkt_ret=EXCLUDED.mkt_ret,
                  sec_ret=EXCLUDED.sec_ret,
                  alpha_real=EXCLUDED.alpha_real
                """,
                rows, page_size=2000
            )