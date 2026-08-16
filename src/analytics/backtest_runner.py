from __future__ import annotations

import uuid
from datetime import date
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values

from src.utils.calendar import get_trading_days, add_trading_days
from src.analytics.run_daily_pipeline import run_daily_pipeline


def _close(conn, ticker: str, d: date) -> Optional[float]:
    df = pd.read_sql(
        "SELECT COALESCE(adj_close, close) AS px FROM core.prices_daily WHERE ticker=%(t)s AND trade_date=%(d)s",
        conn, params={"t": ticker, "d": d}
    )
    if df.empty:
        return None
    px = df.iloc[0, 0]
    return None if px is None else float(px)


def _idx_close(conn, idx: str, d: date) -> Optional[float]:
    df = pd.read_sql(
        "SELECT close AS px FROM core.index_prices_daily WHERE index_code=%(i)s AND trade_date=%(d)s",
        conn, params={"i": idx, "d": d}
    )
    if df.empty:
        return None
    px = df.iloc[0, 0]
    return None if px is None else float(px)


def _rebalance_dates(trading: List[date], start: date, end: date, mode: str) -> List[date]:
    td = [d for d in trading if start <= d <= end]
    if not td:
        return []
    if mode == "daily":
        return td
    if mode == "step5":
        return td[::5]
    if mode == "step10":
        return td[::10]
    return td[::5]


def _step_trading_days(mode: str) -> int:
    """Rebalance spacing in trading days, used for correct annualisation."""
    return {"daily": 1, "step5": 5, "step10": 10}.get(mode, 5)


def _module_scores_exist(conn, asof: date) -> bool:
    df = pd.read_sql(
        "SELECT 1 FROM analytics.module_scores WHERE asof_date=%(d)s LIMIT 1",
        conn, params={"d": asof}
    )
    return not df.empty


def _get_al_tickers(conn, asof: date) -> List[str]:
    df = pd.read_sql(
        """
        SELECT ticker
        FROM analytics.module_scores
        WHERE asof_date=%(d)s AND horizon_days=63
          AND decision='AL'
          AND (veto_flag IS NULL OR veto_flag=false)
        """,
        conn, params={"d": asof}
    )
    return df["ticker"].astype(str).tolist() if not df.empty else []


def _portfolio_return(conn, tickers: List[str], start: date, end: date) -> Tuple[Optional[float], int]:
    if not tickers:
        return None, 0
    rets = []
    for t in tickers:
        p0 = _close(conn, t, start)
        p1 = _close(conn, t, end)
        if p0 is None or p1 is None or p0 <= 0:
            continue
        rets.append(p1 / p0 - 1.0)
    if not rets:
        return None, 0
    return float(np.mean(np.array(rets, dtype=float))), int(len(rets))


def _bench_return(conn, start: date, end: date, idx: str = "XU100") -> Optional[float]:
    p0 = _idx_close(conn, idx, start)
    p1 = _idx_close(conn, idx, end)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return float(p1 / p0 - 1.0)


EMPTY_KEYS = {
    "cum_return": None, "ann_return": None, "ann_vol": None, "sharpe": None,
    "max_drawdown": None, "bench_cum_return": None, "info_ratio": None,
    "n_invested": 0, "coverage": None, "periods_per_year": None,
}


MIN_OBS_FOR_RISK = 20  # below this, Sharpe/IR are noise and are reported as None


def _metrics(ts: pd.DataFrame, step_trading_days: int = 1, span_years: Optional[float] = None) -> dict:
    """Performance metrics for the rebalance series.

    Important conventions:
    - Periods with no AL ticker (n_al == 0) are EXCLUDED from risk metrics and
      compounding. Treating an empty portfolio as a flat 0% period made the
      equity curve monotonic, drawdown zero and Sharpe meaningless.
      `coverage` reports the share of periods that were actually invested.
    - Annualisation uses the CALENDAR span of the run (span_years), because idle
      periods are real time in which nothing was earned. Volatility is scaled by
      the real rebalance spacing (step_trading_days), not the row count.
    - Sharpe and the information ratio are suppressed below MIN_OBS_FOR_RISK
      invested periods; with a handful of observations they are noise.
    - If hold_days > step_trading_days the holding windows overlap, so the
      compounded curve is optimistic; read it comparatively, not as a live P&L.
    """
    df = ts.copy()
    df = df.dropna(subset=["port_ret"])
    if df.empty:
        return dict(EMPTY_KEYS)

    n_all = df.shape[0]
    if "n_al" in df.columns:
        inv = df[df["n_al"].fillna(0).astype(int) > 0].copy()
    else:
        inv = df.copy()
    n_invested = int(inv.shape[0])
    coverage = float(n_invested / n_all) if n_all else None

    # Benchmark curve uses all periods (it is always invested).
    bench_cum = float((1.0 + df["bench_ret"].fillna(0.0)).cumprod().iloc[-1] - 1.0)

    if n_invested == 0:
        out = dict(EMPTY_KEYS)
        out["bench_cum_return"] = bench_cum
        out["coverage"] = coverage
        return out

    inv["port_cum"] = (1.0 + inv["port_ret"]).cumprod()
    cum_return = float(inv["port_cum"].iloc[-1] - 1.0)

    periods_per_year = 252.0 / max(float(step_trading_days), 1.0)
    years = float(span_years) if span_years and span_years > 0 else (n_invested / periods_per_year)
    ann_return = float(inv["port_cum"].iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else None

    vol = float(np.std(inv["port_ret"].to_numpy(dtype=float), ddof=1)) if n_invested > 1 else 0.0
    ann_vol = float(vol * np.sqrt(periods_per_year))

    enough = n_invested >= MIN_OBS_FOR_RISK
    sharpe = float(ann_return / ann_vol) if enough and ann_vol > 0 and ann_return is not None else None

    peak = inv["port_cum"].cummax()
    dd = inv["port_cum"] / peak - 1.0
    max_dd = float(dd.min())
    df = inv
    n = n_invested

    # Information ratio: per-rebalance excess return vs benchmark, annualised by
    # the same convention as the other metrics. Overlapping holds (step < hold)
    # make this an approximation; treat it as comparative, not absolute.
    excess = (df["port_ret"] - df["bench_ret"].fillna(0.0)).to_numpy(dtype=float)
    ex_std = float(np.std(excess, ddof=1)) if excess.shape[0] > 1 else 0.0
    info_ratio = float(np.mean(excess) / ex_std * np.sqrt(periods_per_year)) if enough and ex_std > 0 else None

    return {
        "cum_return": cum_return, "ann_return": ann_return, "ann_vol": ann_vol,
        "sharpe": sharpe, "max_drawdown": max_dd, "bench_cum_return": bench_cum,
        "info_ratio": info_ratio, "n_invested": n_invested, "coverage": coverage,
        "periods_per_year": periods_per_year,
    }


def _write_db(conn, run_id: str, start: date, end: date, rebalance: str, hold_days: int, ts: pd.DataFrame, turnover_avg: Optional[float] = None, step_trading_days: int = 1) -> None:
    span_years = max((end - start).days / 365.25, 1e-9)
    met = _metrics(ts, step_trading_days=step_trading_days, span_years=span_years)
    avg_n_al = int(ts["n_al"].mean()) if not ts.empty else 0

    run_row = (
        run_id, start, end, rebalance, int(hold_days),
        int(ts.shape[0]), avg_n_al,
        met["cum_return"], met["ann_return"], met["ann_vol"], met["sharpe"], met["max_drawdown"], met["bench_cum_return"],
        turnover_avg, met["info_ratio"]
    )

    ts_rows = [
        (run_id, r.asof_date, r.hold_end, int(r.n_al), float(r.port_ret), float(r.bench_ret), float(r.port_cum), float(r.bench_cum))
        for r in ts.itertuples(index=False)
    ]

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO analytics.backtest_runs
                  (run_id, start_date, end_date, rebalance, hold_days, n_rebalances, avg_n_al,
                   cum_return, ann_return, ann_vol, sharpe, max_drawdown, bench_cum_return,
                   turnover_avg, info_ratio)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                run_row
            )

            if ts_rows:
                sql = """
                INSERT INTO analytics.backtest_timeseries
                  (run_id, asof_date, hold_end, n_al, port_ret, bench_ret, port_cum, bench_cum)
                VALUES %s
                ON CONFLICT (run_id, asof_date)
                DO UPDATE SET
                  hold_end=EXCLUDED.hold_end,
                  n_al=EXCLUDED.n_al,
                  port_ret=EXCLUDED.port_ret,
                  bench_ret=EXCLUDED.bench_ret,
                  port_cum=EXCLUDED.port_cum,
                  bench_cum=EXCLUDED.bench_cum
                """
                execute_values(cur, sql, ts_rows, page_size=2000)


def run_backtest(
    conn,
    start_date: str,
    end_date: str,
    rebalance: str = "step5",
    hold_days: int = 20,
    ensure_scores: bool = False,
    ratios_json_path: str = "config/ratios.json",
    sectors_json_path: str = "config/sectors.json",
    weights_json_path: str | None = None,
) -> str:
    trading = get_trading_days(conn)
    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()

    rdates = _rebalance_dates(trading, start, end, rebalance)
    if not rdates:
        raise ValueError("No trading dates in range.")

    run_id = str(uuid.uuid4())[:8]
    rows = []
    port_cum = 1.0
    bench_cum = 1.0
    prev_tickers: List[str] = []
    turnovers: List[float] = []

    for asof in rdates:
        if ensure_scores and (not _module_scores_exist(conn, asof)):
            run_daily_pipeline(
                conn=conn,
                asof_date=str(asof),
                ratios_json_path=ratios_json_path,
                sectors_json_path=sectors_json_path,
                weights_json_path=weights_json_path
            )

        tickers = _get_al_tickers(conn, asof)
        if prev_tickers or tickers:
            inter = len(set(prev_tickers) & set(tickers))
            base = max(len(prev_tickers), len(tickers), 1)
            if prev_tickers:
                turnovers.append(1.0 - inter / base)
        prev_tickers = tickers
        hold_end = add_trading_days(asof, hold_days, trading)

        pret, n_used = _portfolio_return(conn, tickers, asof, hold_end)
        bret = _bench_return(conn, asof, hold_end)

        pret = 0.0 if pret is None else float(pret)
        bret = 0.0 if bret is None else float(bret)

        port_cum *= (1.0 + pret)
        bench_cum *= (1.0 + bret)

        rows.append((asof, hold_end, n_used, pret, bret, port_cum, bench_cum))

    ts = pd.DataFrame(rows, columns=["asof_date", "hold_end", "n_al", "port_ret", "bench_ret", "port_cum", "bench_cum"])

    # CSV
    import os
    os.makedirs("outputs", exist_ok=True)
    ts.to_csv(f"outputs/backtest_{run_id}.csv", index=False)

    turnover_avg = float(np.mean(turnovers)) if turnovers else None
    step_td = _step_trading_days(rebalance)
    _write_db(conn, run_id, start, end, rebalance, hold_days, ts, turnover_avg=turnover_avg, step_trading_days=step_td)

    met = _metrics(ts, step_trading_days=step_td, span_years=max((end - start).days / 365.25, 1e-9))
    if met["coverage"] is not None and met["coverage"] < 0.99:
        print(f"[uyari] Donemlerin %{(1-met['coverage'])*100:.0f}'inde AL sinyali yok; "
              f"risk metrikleri sadece yatirim yapilan {met['n_invested']} donem uzerinden hesaplandi.")
    if met["n_invested"] and met["n_invested"] < MIN_OBS_FOR_RISK:
        print(f"[uyari] Sadece {met['n_invested']} yatirimli donem var (<{MIN_OBS_FOR_RISK}); "
              f"Sharpe ve bilgi orani guvenilir olmadigi icin bos birakildi.")
    if hold_days > step_td:
        print(f"[uyari] hold ({hold_days}) > rebalance adimi ({step_td}) -> pencereler ortusuyor; "
              f"bilesik egri iyimser okunur, metrikleri karsilastirmali kullanin.")
    return run_id