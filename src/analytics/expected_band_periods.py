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


def _bucket_from_breakpoints(v: float, breakpoints: list[float]) -> int:
    k = 0
    for bp in breakpoints:
        if v > float(bp):
            k += 1
    return k + 1


SIGMA_MIN = 0.08
SIGMA_MAX = 0.45
SIGMA_DEFAULT = 0.125
FALLBACK_ALPHA_SLOPE = 0.40


def _realized_sigma_for_horizon(px_series: pd.Series, horizon_days: int, lookback: int = 63) -> Optional[float]:
    """Estimate a per-ticker band half-width from realized volatility.

    Uses the daily return std of the last `lookback` observations up to the band's
    t0 price and scales it to the horizon (sqrt-of-time). Clipped to a sane range
    so illiquid names cannot explode the band and stable names cannot collapse it.
    """
    if px_series is None or len(px_series) < 20:
        return None
    tail = pd.to_numeric(px_series, errors="coerce").dropna().tail(lookback + 1)
    if len(tail) < 20:
        return None
    rets = tail.pct_change().dropna()
    if rets.empty:
        return None
    sd = float(rets.std(ddof=1))
    if not np.isfinite(sd) or sd <= 0:
        return None
    sigma_h = sd * float(np.sqrt(max(int(horizon_days), 1)))
    return float(np.clip(sigma_h, SIGMA_MIN, SIGMA_MAX))


def _fallback_expected_alpha(rsc: Optional[float], sigma: Optional[float] = None) -> tuple[float, float, float]:
    """Fallback expected alpha when the historical decile map is not available.

    Conservative mapping: RSC 0.50 -> 0 alpha, RSC 1.00 -> +20%, RSC 0.00 -> -20%.
    Band half-width comes from the ticker's own realized volatility when available;
    otherwise a fixed proxy is used. This keeps the pipeline usable with small
    early datasets while sizing bands per name instead of one-size-fits-all.
    """
    if rsc is None or not np.isfinite(float(rsc)):
        mid = 0.0
    else:
        mid = (float(rsc) - 0.50) * FALLBACK_ALPHA_SLOPE
    s = SIGMA_DEFAULT if sigma is None or not np.isfinite(float(sigma)) else float(sigma)
    return float(mid), float(mid - s), float(mid + s)


def build_expected_band_periods(
    conn,
    asof_date: str,
    horizon_days: int = 63,
    bucket_count: int = 10,
) -> pd.DataFrame:
    """Build expected bands for the last 8 financial periods of each ticker.

    This is the period-based M2 foundation: M2 compares current vs previous band
    and also the 8-period band path.
    """
    asof = pd.to_datetime(asof_date).date()

    reports = pd.read_sql(
        """
        SELECT f.ticker, f.period_end, f.version_tag, f.t0_date,
               COALESCE(u.sector_code,'NONFIN') AS sector_code
        FROM core.financials_quarterly f
        LEFT JOIN core.universe_stocks u ON u.ticker=f.ticker
        WHERE f.t0_date IS NOT NULL
          AND f.t0_date <= %(asof)s
        ORDER BY f.ticker, f.period_end
        """,
        conn,
        params={"asof": asof},
    )
    if reports.empty:
        return pd.DataFrame()
    reports["period_end"] = pd.to_datetime(reports["period_end"]).dt.date
    reports["t0_date"] = pd.to_datetime(reports["t0_date"]).dt.date

    # Keep last 8 periods per ticker.
    reports = reports.groupby("ticker", group_keys=False).tail(8).reset_index(drop=True)

    rsc = pd.read_sql(
        """
        SELECT ticker, period_end, version_tag, rsc_core_norm, score_mean
        FROM analytics.rsc_summary_quarterly
        """,
        conn,
    ).copy()
    if not rsc.empty:
        rsc["period_end"] = pd.to_datetime(rsc["period_end"]).dt.date
        rsc["rsc_core_norm"] = pd.to_numeric(rsc["rsc_core_norm"], errors="coerce")
        rsc["score_mean"] = pd.to_numeric(rsc["score_mean"], errors="coerce")
        reports = reports.merge(rsc, on=["ticker", "period_end", "version_tag"], how="left")
    else:
        reports["rsc_core_norm"] = np.nan
        reports["score_mean"] = np.nan

    # p0 at t0_date. If there is no exact price at t0, use the last available before t0.
    tickers = reports["ticker"].astype(str).unique().tolist()
    max_t0 = max(reports["t0_date"])
    prices = pd.read_sql(
        """
        SELECT ticker, trade_date, COALESCE(adj_close, close) AS px
        FROM core.prices_daily
        WHERE ticker = ANY(%(tickers)s)
          AND trade_date <= %(max_t0)s
        """,
        conn,
        params={"tickers": tickers, "max_t0": max_t0},
    )
    if prices.empty:
        return pd.DataFrame()
    prices["trade_date"] = pd.to_datetime(prices["trade_date"]).dt.date
    p_by_ticker = {t: g.sort_values("trade_date") for t, g in prices.groupby("ticker")}

    # Optional decile map / thresholds. If unavailable, fallback model is used.
    thr = pd.read_sql(
        """
        SELECT DISTINCT ON (sector_code) sector_code, breakpoints
        FROM analytics.decile_thresholds
        WHERE horizon_days=%(h)s AND bucket_count=%(b)s AND window_end <= %(asof)s
        ORDER BY sector_code, window_end DESC
        """,
        conn,
        params={"h": int(horizon_days), "b": int(bucket_count), "asof": asof},
    )
    thr_map = {str(r.sector_code): list(r.breakpoints) for r in thr.itertuples(index=False)} if not thr.empty else {}
    dm = pd.read_sql(
        """
        SELECT DISTINCT ON (sector_code, bucket_id)
          sector_code, bucket_id, mu_alpha, sigma_alpha
        FROM analytics.decile_map
        WHERE horizon_days=%(h)s AND bucket_count=%(b)s AND window_end <= %(asof)s
        ORDER BY sector_code, bucket_id, window_end DESC
        """,
        conn,
        params={"h": int(horizon_days), "b": int(bucket_count), "asof": asof},
    )
    dm_map = {}
    if not dm.empty:
        for r in dm.itertuples(index=False):
            dm_map[(str(r.sector_code), int(r.bucket_id))] = (
                None if r.mu_alpha is None else float(r.mu_alpha),
                None if r.sigma_alpha is None else float(r.sigma_alpha),
            )

    out = []
    for r in reports.itertuples(index=False):
        ticker = str(r.ticker)
        gpx = p_by_ticker.get(ticker)
        if gpx is None or gpx.empty:
            continue
        eligible = gpx[gpx["trade_date"] <= r.t0_date]
        if eligible.empty:
            continue
        p0 = eligible.iloc[-1]["px"]
        if p0 is None or pd.isna(p0) or float(p0) <= 0:
            continue
        p0 = float(p0)
        rsc_val = None if pd.isna(r.rsc_core_norm) else float(r.rsc_core_norm)
        total_score = None if pd.isna(r.score_mean) else float(r.score_mean)
        sec = str(r.sector_code)
        bps = thr_map.get(sec)
        bucket_id = None
        model_source = "fallback_rsc"

        # Per-name band half-width from realized volatility up to t0.
        dyn_sigma = _realized_sigma_for_horizon(eligible["px"], horizon_days)

        if bps and rsc_val is not None:
            bucket_id = _bucket_from_breakpoints(rsc_val, bps)
            mu_sig = dm_map.get((sec, bucket_id))
            if mu_sig and mu_sig[0] is not None:
                mu = float(mu_sig[0])
                sig = mu_sig[1]
                if sig is None or not np.isfinite(float(sig)) or float(sig) <= 0:
                    # Decile map has a mean but a degenerate sigma: size the band
                    # from the ticker's own volatility instead of collapsing it.
                    sig = SIGMA_DEFAULT if dyn_sigma is None else dyn_sigma
                else:
                    sig = float(np.clip(float(sig), SIGMA_MIN, SIGMA_MAX))
                exp_mid, exp_low, exp_high = mu, mu - sig, mu + sig
                model_source = "decile_map"
            else:
                exp_mid, exp_low, exp_high = _fallback_expected_alpha(rsc_val, sigma=dyn_sigma)
        else:
            exp_mid, exp_low, exp_high = _fallback_expected_alpha(rsc_val, sigma=dyn_sigma)

        p_mid = p0 * (1.0 + exp_mid)
        p_low = p0 * (1.0 + exp_low)
        p_high = p0 * (1.0 + exp_high)
        # Keep the band ordered even if the fallback/decile combination creates an odd result.
        low, mid, high = sorted([p_low, p_mid, p_high])
        out.append((
            ticker, r.period_end, str(r.version_tag), r.t0_date, int(horizon_days),
            p0, total_score, rsc_val, exp_mid, exp_low, exp_high,
            low, mid, high, int(bucket_count), bucket_id, model_source, asof,
        ))

    return pd.DataFrame(out, columns=[
        "ticker", "period_end", "version_tag", "t0_date", "horizon_days", "p0",
        "total_ratio_score", "rsc_core_norm", "exp_alpha_mid", "exp_alpha_low", "exp_alpha_high",
        "p_exp_low", "p_exp_mid", "p_exp_high", "bucket_count", "bucket_id", "model_source", "asof_date"
    ])


def upsert_expected_band_periods(conn, df: pd.DataFrame) -> None:
    if df.empty:
        return
    rows = _records_for_sql(df)
    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO analytics.expected_band_periods
                  (ticker, period_end, version_tag, t0_date, horizon_days, p0,
                   total_ratio_score, rsc_core_norm, exp_alpha_mid, exp_alpha_low, exp_alpha_high,
                   p_exp_low, p_exp_mid, p_exp_high, bucket_count, bucket_id, model_source, asof_date)
                VALUES %s
                ON CONFLICT (ticker, period_end, version_tag, horizon_days, asof_date)
                DO UPDATE SET
                  t0_date=EXCLUDED.t0_date,
                  p0=EXCLUDED.p0,
                  total_ratio_score=EXCLUDED.total_ratio_score,
                  rsc_core_norm=EXCLUDED.rsc_core_norm,
                  exp_alpha_mid=EXCLUDED.exp_alpha_mid,
                  exp_alpha_low=EXCLUDED.exp_alpha_low,
                  exp_alpha_high=EXCLUDED.exp_alpha_high,
                  p_exp_low=EXCLUDED.p_exp_low,
                  p_exp_mid=EXCLUDED.p_exp_mid,
                  p_exp_high=EXCLUDED.p_exp_high,
                  bucket_count=EXCLUDED.bucket_count,
                  bucket_id=EXCLUDED.bucket_id,
                  model_source=EXCLUDED.model_source
                """,
                rows,
                page_size=3000,
            )
