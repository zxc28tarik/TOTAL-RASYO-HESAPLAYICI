from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values


def _sql_value(x):
    """Convert pandas/numpy missing values and scalar types into DB-friendly values."""
    try:
        if pd.isna(x):
            return None
    except Exception:
        pass
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def _records_for_sql(df: pd.DataFrame):
    return [tuple(_sql_value(v) for v in row) for row in df.itertuples(index=False, name=None)]

from src.utils.calendar import get_trading_days


def _latest_trading_day_on_or_before(trading_days: list[date], d: date) -> Optional[date]:
    vals = [x for x in trading_days if x <= d]
    return vals[-1] if vals else None


def _window_start(trading_days: list[date], end: date, window_days: int) -> Optional[date]:
    if end not in trading_days:
        return None
    i = trading_days.index(end)
    j = i - int(window_days)
    if j < 0:
        return None
    return trading_days[j]


def _score_alpha(alpha: Optional[float]) -> float:
    if alpha is None or not np.isfinite(float(alpha)):
        return 0.5
    # map roughly -20%..+20% alpha to 0..1, with 0 alpha = 0.5
    return float(np.clip((float(alpha) + 0.20) / 0.40, 0.0, 1.0))


def _label_alpha(alpha: Optional[float]) -> str:
    if alpha is None or not np.isfinite(float(alpha)):
        return "UNKNOWN"
    a = float(alpha)
    if a >= 0.10:
        return "STRONG_POSITIVE"
    if a >= 0.03:
        return "POSITIVE"
    if a > -0.03:
        return "NEUTRAL"
    if a > -0.10:
        return "WEAK_NEGATIVE"
    return "STRONG_NEGATIVE"


ALPHA_COLUMNS = [
    "ticker", "asof_date", "window_days", "start_date", "end_date", "stock_ret", "mkt_ret",
    "sec_ret", "beta_mkt", "beta_sec", "alpha_trailing", "alpha_score", "alpha_label",
]


def compute_trailing_alpha_from_frames(
    *,
    universe: pd.DataFrame,
    prices: pd.DataFrame,
    index_prices: pd.DataFrame,
    betas: pd.DataFrame,
    asof_date: str | date,
    start_date: str | date,
    end_date: str | date,
    window_days: int = 63,
    market_index: str = "XU100",
) -> pd.DataFrame:
    """Run production trailing-alpha math on explicit, pre-cut frames.

    This pure calculation is shared by the live database path and historical
    replay.  Historical PIT/cutoff and coverage checks belong to the adapter.
    """

    if universe.empty or prices.empty or index_prices.empty:
        return pd.DataFrame(columns=ALPHA_COLUMNS)

    asof_raw = pd.to_datetime(asof_date).date()
    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()
    tickers = universe["ticker"].astype(str).tolist()
    sec_map = dict(
        zip(universe["ticker"].astype(str), universe["sector_index_code"].astype(str))
    )

    prices = prices.copy()
    index_prices = index_prices.copy()
    prices["trade_date"] = pd.to_datetime(prices["trade_date"]).dt.date
    index_prices["trade_date"] = pd.to_datetime(index_prices["trade_date"]).dt.date
    px = prices.pivot_table(index="trade_date", columns="ticker", values="px", aggfunc="last")
    ipx = index_prices.pivot_table(
        index="trade_date", columns="index_code", values="px", aggfunc="last"
    )

    beta_map = {}
    if not betas.empty:
        for row in betas.itertuples(index=False):
            beta_map[str(row.ticker)] = (
                None if row.beta_mkt is None else float(row.beta_mkt),
                None if row.beta_sec is None else float(row.beta_sec),
            )

    m0 = ipx.loc[start, market_index] if market_index in ipx.columns and start in ipx.index else None
    m1 = ipx.loc[end, market_index] if market_index in ipx.columns and end in ipx.index else None
    if m0 is None or m1 is None or pd.isna(m0) or pd.isna(m1) or float(m0) <= 0:
        return pd.DataFrame(columns=ALPHA_COLUMNS)
    mkt_ret = float(m1) / float(m0) - 1.0

    rows = []
    for ticker in tickers:
        if ticker not in px.columns or start not in px.index or end not in px.index:
            continue
        p0 = px.loc[start, ticker]
        p1 = px.loc[end, ticker]
        if pd.isna(p0) or pd.isna(p1) or float(p0) <= 0:
            continue
        stock_ret = float(p1) / float(p0) - 1.0

        sector = sec_map.get(ticker, market_index)
        if sector not in ipx.columns:
            sector = market_index
        s0 = ipx.loc[start, sector]
        s1 = ipx.loc[end, sector]
        if pd.isna(s0) or pd.isna(s1) or float(s0) <= 0:
            continue
        sec_ret = float(s1) / float(s0) - 1.0

        beta_mkt, beta_sec = beta_map.get(ticker, (1.0, 0.0))
        beta_mkt = 1.0 if beta_mkt is None or not np.isfinite(beta_mkt) else beta_mkt
        beta_sec = 0.0 if beta_sec is None or not np.isfinite(beta_sec) else beta_sec
        alpha = stock_ret - beta_mkt * mkt_ret - beta_sec * (sec_ret - mkt_ret)
        rows.append(
            (
                ticker,
                asof_raw,
                int(window_days),
                start,
                end,
                float(stock_ret),
                float(mkt_ret),
                float(sec_ret),
                float(beta_mkt),
                float(beta_sec),
                float(alpha),
                float(_score_alpha(alpha)),
                _label_alpha(alpha),
            )
        )

    return pd.DataFrame(rows, columns=ALPHA_COLUMNS)


def compute_trailing_alpha(
    conn,
    asof_date: str,
    window_days: int = 63,
    market_index: str = "XU100",
) -> pd.DataFrame:
    """Compute trailing alpha from asof-window_days to asof.

    This is the live alpha concept for the project: no forward waiting.
    It answers: in the last N trading days, did the stock outperform/underperform
    after removing market and sector sensitivity?
    """
    asof_raw = pd.to_datetime(asof_date).date()
    trading_days = get_trading_days(conn)
    if not trading_days:
        return pd.DataFrame(columns=ALPHA_COLUMNS)
    end = _latest_trading_day_on_or_before(trading_days, asof_raw)
    if end is None:
        return pd.DataFrame()
    start = _window_start(trading_days, end, int(window_days))
    if start is None:
        return pd.DataFrame()

    universe = pd.read_sql(
        """
        SELECT ticker, COALESCE(sector_index_code,'XU100') AS sector_index_code
        FROM core.universe_stocks
        WHERE is_active=true
        """,
        conn,
    )
    if universe.empty:
        return pd.DataFrame()
    tickers = universe["ticker"].astype(str).tolist()
    sec_map = dict(zip(universe["ticker"].astype(str), universe["sector_index_code"].astype(str)))
    indices = sorted(set([market_index] + list(sec_map.values())))

    prices = pd.read_sql(
        """
        SELECT ticker, trade_date, COALESCE(adj_close, close) AS px
        FROM core.prices_daily
        WHERE ticker = ANY(%(tickers)s)
          AND trade_date IN (%(start)s, %(end)s)
        """,
        conn,
        params={"tickers": tickers, "start": start, "end": end},
    )
    idx_prices = pd.read_sql(
        """
        SELECT index_code, trade_date, close AS px
        FROM core.index_prices_daily
        WHERE index_code = ANY(%(indices)s)
          AND trade_date IN (%(start)s, %(end)s)
        """,
        conn,
        params={"indices": indices, "start": start, "end": end},
    )
    if prices.empty or idx_prices.empty:
        return pd.DataFrame()

    betas = pd.read_sql(
        """
        SELECT DISTINCT ON (ticker) ticker, beta_mkt, beta_sec
        FROM analytics.beta_estimates
        WHERE t0_date <= %(asof)s
        ORDER BY ticker, t0_date DESC
        """,
        conn,
        params={"asof": end},
    )
    return compute_trailing_alpha_from_frames(
        universe=universe,
        prices=prices,
        index_prices=idx_prices,
        betas=betas,
        asof_date=asof_raw,
        start_date=start,
        end_date=end,
        window_days=window_days,
        market_index=market_index,
    )


def upsert_trailing_alpha(conn, df: pd.DataFrame) -> None:
    if df.empty:
        return
    rows = _records_for_sql(df)
    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO analytics.alpha_trailing
                  (ticker, asof_date, window_days, start_date, end_date, stock_ret, mkt_ret,
                   sec_ret, beta_mkt, beta_sec, alpha_trailing, alpha_score, alpha_label)
                VALUES %s
                ON CONFLICT (ticker, asof_date, window_days)
                DO UPDATE SET
                  start_date=EXCLUDED.start_date,
                  end_date=EXCLUDED.end_date,
                  stock_ret=EXCLUDED.stock_ret,
                  mkt_ret=EXCLUDED.mkt_ret,
                  sec_ret=EXCLUDED.sec_ret,
                  beta_mkt=EXCLUDED.beta_mkt,
                  beta_sec=EXCLUDED.beta_sec,
                  alpha_trailing=EXCLUDED.alpha_trailing,
                  alpha_score=EXCLUDED.alpha_score,
                  alpha_label=EXCLUDED.alpha_label
                """,
                rows,
                page_size=2000,
            )
