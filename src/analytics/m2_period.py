from __future__ import annotations

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


def _slope(values: list[float]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None and np.isfinite(float(v))]
    if len(vals) < 3:
        return None
    y = np.array(vals, dtype=float)
    x = np.arange(len(y), dtype=float)
    x -= x.mean()
    denom = float((x * x).sum())
    if denom == 0:
        return None
    return float((x * (y - y.mean())).sum() / denom)


def _is_good_number(x) -> bool:
    try:
        return x is not None and np.isfinite(float(x))
    except Exception:
        return False


def _band_pos(px: Optional[float], low: Optional[float], mid: Optional[float], high: Optional[float]) -> str:
    if not all(_is_good_number(x) for x in [px, low, mid, high]):
        return "UNKNOWN"
    px, low, mid, high = float(px), float(low), float(mid), float(high)
    if px < low:
        return "BELOW_LOW"
    if px < mid:
        return "LOWER_HALF"
    if px <= high:
        return "UPPER_HALF"
    return "ABOVE_HIGH"


def _discount_score(px: Optional[float], low: Optional[float], mid: Optional[float], high: Optional[float]) -> float:
    if not all(_is_good_number(x) for x in [px, low, mid, high]):
        return 0.5
    px, low, mid, high = float(px), float(low), float(mid), float(high)
    if high <= low or mid <= 0:
        return 0.5
    if px < low:
        return float(np.clip(0.68 + (low - px) / max(low, 1e-9), 0.68, 1.0))
    if px <= mid:
        # still attractive inside the lower half
        return float(np.clip(0.52 + (mid - px) / max(mid - low, 1e-9) * 0.16, 0.52, 0.68))
    if px <= high:
        return float(np.clip(0.25 + (high - px) / max(high - mid, 1e-9) * 0.27, 0.25, 0.52))
    return float(np.clip(0.25 - (px - high) / max(high, 1e-9), 0.0, 0.25))


def _follow_score(gap: Optional[float]) -> float:
    if gap is None or not np.isfinite(float(gap)):
        return 0.5
    # gap = band_mid_change - price_change. Positive means price lags expectation.
    return float(np.clip(0.5 + float(gap) / 0.50, 0.0, 1.0))


def _alpha_support_score(alpha: Optional[float]) -> float:
    if alpha is None or not np.isfinite(float(alpha)):
        return 0.5
    # Strong negative alpha means the opportunity may need confirmation.
    return float(np.clip((float(alpha) + 0.20) / 0.40, 0.0, 1.0))


def _fmt_band(low, high) -> str:
    if low is None or high is None or pd.isna(low) or pd.isna(high):
        return "bilinmiyor"
    return f"{float(low):.2f}-{float(high):.2f}"


def _fmt_num(x) -> str:
    if x is None or pd.isna(x):
        return "bilinmiyor"
    return f"{float(x):.2f}"


def _fmt_pct(x) -> str:
    if x is None or pd.isna(x):
        return "bilinmiyor"
    return f"%{float(x)*100:.1f}"


def _position_tr(pos: str) -> str:
    return {
        "BELOW_LOW": "bandın altında",
        "LOWER_HALF": "bandın alt/orta kısmında",
        "UPPER_HALF": "bandın orta/üst kısmında",
        "ABOVE_HIGH": "bandın üstünde",
        "UNKNOWN": "konumu hesaplanamadı",
    }.get(pos, "konumu hesaplanamadı")


def _label(m2: float, current_pos: str, follow_gap: Optional[float], quality_score: Optional[float]) -> str:
    q = 0.5 if quality_score is None or not np.isfinite(float(quality_score)) else float(quality_score)
    gap = 0.0 if follow_gap is None or not np.isfinite(float(follow_gap)) else float(follow_gap)
    if current_pos == "ABOVE_HIGH":
        return "PRICED_OR_OVERPRICED"
    if current_pos == "BELOW_LOW" and gap > 0.08 and q >= 0.55:
        return "QUALITY_UP_PRICE_LAGGING"
    if m2 >= 0.70:
        return "UNDERPRICED_AND_LAGGING"
    if m2 >= 0.52:
        return "WATCH_MISPRICING"
    return "FAIR_OR_WEAK"


def _commentary(
    current_low, current_mid, current_high,
    prev_low, prev_mid, prev_high,
    current_px,
    prev_pos, current_pos,
    score_slope_8q,
    band_slope_8q,
    follow_gap,
    alpha,
    valuation_support=None,
) -> str:
    parts = []
    parts.append(f"Bu dönem beklenen band {_fmt_band(current_low, current_high)}.")
    parts.append(f"Önceki dönem band {_fmt_band(prev_low, prev_high)}.")
    parts.append(f"Band ortası {_fmt_num(prev_mid)}'dan {_fmt_num(current_mid)}'a çıktı." if prev_mid is not None and current_mid is not None and not pd.isna(prev_mid) and not pd.isna(current_mid) and float(current_mid) >= float(prev_mid)
                 else f"Band ortası {_fmt_num(prev_mid)}'dan {_fmt_num(current_mid)}'a geldi.")
    parts.append(f"Bugünkü fiyat {_fmt_num(current_px)}.")
    parts.append(f"Fiyat önceki banda göre {_position_tr(prev_pos)}, yeni banda göre {_position_tr(current_pos)}.")

    if score_slope_8q is not None and np.isfinite(float(score_slope_8q)):
        if float(score_slope_8q) > 0.10:
            parts.append("Son 8 dönemde total rasyo notu yükseliyor.")
        elif float(score_slope_8q) < -0.10:
            parts.append("Son 8 dönemde total rasyo notu zayıflıyor.")
        else:
            parts.append("Son 8 dönemde total rasyo notu yatay/karışık ilerliyor.")

    if band_slope_8q is not None and np.isfinite(float(band_slope_8q)):
        if float(band_slope_8q) > 0:
            parts.append("Beklenen bandlar dönem dönem yukarı gidiyor.")
        elif float(band_slope_8q) < 0:
            parts.append("Beklenen bandlar dönem dönem aşağı geliyor.")
        else:
            parts.append("Beklenen bandlarda belirgin yön yok.")

    if follow_gap is not None and np.isfinite(float(follow_gap)):
        if float(follow_gap) > 0.08:
            parts.append("Fiyat bu band yükselişini takip etmiyor, geride kalıyor.")
        elif float(follow_gap) < -0.08:
            parts.append("Fiyat beklentiden daha hızlı hareket etmiş görünüyor.")
        else:
            parts.append("Fiyat beklenti değişimini büyük ölçüde takip ediyor.")

    if valuation_support is not None and np.isfinite(float(valuation_support)):
        v = float(valuation_support)
        if v >= 0.70:
            parts.append("Değerleme çarpanları evrene göre ucuz tarafta.")
        elif v <= 0.30:
            parts.append("Değerleme çarpanları evrene göre pahalı tarafta; iskonto sinyali temkinli okunmalı.")
        else:
            parts.append("Değerleme çarpanları evren ortalamasına yakın.")

    if alpha is not None and np.isfinite(float(alpha)):
        if float(alpha) < -0.03:
            parts.append("Son 63 günlük alpha hâlâ zayıf ama toparlanma varsa fırsat güçlenir.")
        elif float(alpha) > 0.03:
            parts.append("Son 63 günlük alpha pozitif; fiyatlama toparlanması başlamış olabilir.")
        else:
            parts.append("Son 63 günlük alpha nötr; fiyatlama teyidi henüz sınırlı.")
    else:
        parts.append("Son 63 günlük alpha hesaplanamadı; fiyatlama teyidi eksik.")

    return " ".join(parts)


def compute_m2_period_comparison(conn, asof_date: str, horizon_days: int = 63) -> pd.DataFrame:
    asof = pd.to_datetime(asof_date).date()
    bands = pd.read_sql(
        """
        SELECT *
        FROM analytics.expected_band_periods
        WHERE asof_date=%(asof)s AND horizon_days=%(h)s
        ORDER BY ticker, period_end
        """,
        conn,
        params={"asof": asof, "h": int(horizon_days)},
    )
    if bands.empty:
        return pd.DataFrame()
    bands["period_end"] = pd.to_datetime(bands["period_end"]).dt.date
    bands["t0_date"] = pd.to_datetime(bands["t0_date"]).dt.date
    for c in ["p_exp_low", "p_exp_mid", "p_exp_high", "p0", "total_ratio_score", "rsc_core_norm"]:
        bands[c] = pd.to_numeric(bands[c], errors="coerce")

    tickers = bands["ticker"].astype(str).unique().tolist()
    px = pd.read_sql(
        """
        SELECT DISTINCT ON (ticker) ticker, COALESCE(adj_close, close) AS current_px
        FROM core.prices_daily
        WHERE ticker = ANY(%(tickers)s) AND trade_date <= %(asof)s
        ORDER BY ticker, trade_date DESC
        """,
        conn,
        params={"tickers": tickers, "asof": asof},
    )
    px_map = {str(r.ticker): None if r.current_px is None else float(r.current_px) for r in px.itertuples(index=False)} if not px.empty else {}

    trend = pd.read_sql(
        "SELECT * FROM analytics.period_8q_comparison WHERE asof_date=%(asof)s",
        conn,
        params={"asof": asof},
    )
    trend_map = {str(r.ticker): r for r in trend.itertuples(index=False)} if not trend.empty else {}

    alpha = pd.read_sql(
        """
        SELECT ticker, alpha_trailing, alpha_score
        FROM analytics.alpha_trailing
        WHERE asof_date=%(asof)s AND window_days=%(h)s
        """,
        conn,
        params={"asof": asof, "h": int(horizon_days)},
    )
    alpha_map = {str(r.ticker): (None if r.alpha_trailing is None else float(r.alpha_trailing), None if r.alpha_score is None else float(r.alpha_score)) for r in alpha.itertuples(index=False)} if not alpha.empty else {}

    # Valuation axis: latest reported-period rsc_val_norm per ticker (cheapness percentile).
    val = pd.read_sql(
        """
        SELECT DISTINCT ON (s.ticker) s.ticker, s.rsc_val_norm
        FROM analytics.rsc_summary_quarterly s
        JOIN core.financials_quarterly f
          ON f.ticker=s.ticker AND f.period_end=s.period_end AND f.version_tag=s.version_tag
        WHERE f.t0_date IS NOT NULL AND f.t0_date <= %(asof)s
        ORDER BY s.ticker, s.period_end DESC
        """,
        conn,
        params={"asof": asof},
    )
    val_map = {str(r.ticker): (None if r.rsc_val_norm is None or pd.isna(r.rsc_val_norm) else float(r.rsc_val_norm)) for r in val.itertuples(index=False)} if not val.empty else {}

    rows = []
    for ticker, g in bands.groupby("ticker", sort=False):
        g = g.sort_values("period_end").tail(8).reset_index(drop=True)
        if g.shape[0] < 1:
            continue
        cur = g.iloc[-1]
        prev = g.iloc[-2] if g.shape[0] >= 2 else cur
        current_px = px_map.get(str(ticker))
        if current_px is None:
            continue

        c_low, c_mid, c_high = float(cur.p_exp_low), float(cur.p_exp_mid), float(cur.p_exp_high)
        p_low, p_mid, p_high = float(prev.p_exp_low), float(prev.p_exp_mid), float(prev.p_exp_high)
        prev_pos = _band_pos(current_px, p_low, p_mid, p_high)
        current_pos = _band_pos(current_px, c_low, c_mid, c_high)

        band_mid_change = (c_mid / p_mid - 1.0) if p_mid and p_mid > 0 else None
        prev_period_price = float(prev.p0) if prev.p0 is not None and not pd.isna(prev.p0) and float(prev.p0) > 0 else None
        price_change = (current_px / prev_period_price - 1.0) if prev_period_price else None
        follow_gap = (band_mid_change - price_change) if band_mid_change is not None and price_change is not None else None

        band_mids = g["p_exp_mid"].astype(float).tolist()
        period_prices = g["p0"].astype(float).tolist()
        band_slope = _slope(band_mids)
        price_to_mid = [p / m for p, m in zip(period_prices, band_mids) if m and np.isfinite(m) and m > 0 and np.isfinite(p)]
        price_to_mid_latest = (current_px / c_mid) if c_mid > 0 else None
        price_to_mid_avg = float(np.mean(price_to_mid)) if price_to_mid else None
        price_to_mid_slope = _slope(price_to_mid)

        # Persistence: band rises while price/band ratio stays low or worsens.
        persistence = 0.5
        if band_slope is not None and price_to_mid_latest is not None:
            if band_slope > 0:
                lag_part = float(np.clip((1.0 - price_to_mid_latest) / 0.35, 0.0, 1.0))
                worsen_part = 0.5 if price_to_mid_slope is None else float(np.clip(0.5 - price_to_mid_slope / 0.10, 0.0, 1.0))
                persistence = float(np.clip(0.70 * lag_part + 0.30 * worsen_part, 0.0, 1.0))
            else:
                persistence = float(np.clip((1.0 - price_to_mid_latest) / 0.50, 0.0, 0.7))

        trend_row = trend_map.get(str(ticker))
        quality_score = None if trend_row is None or trend_row.quality_trend_score is None else float(trend_row.quality_trend_score)
        score_slope_8q = None if trend_row is None or trend_row.score_slope_8q is None else float(trend_row.score_slope_8q)
        alpha_val, alpha_score = alpha_map.get(str(ticker), (None, None))
        alpha_support = _alpha_support_score(alpha_val) if alpha_score is None else float(alpha_score)

        band_score = _discount_score(current_px, c_low, c_mid, c_high)
        follow = _follow_score(follow_gap)
        quality_support = 0.5 if quality_score is None else float(np.clip(quality_score, 0.0, 1.0))
        val_norm = val_map.get(str(ticker))
        valuation_support = 0.5 if val_norm is None or not np.isfinite(float(val_norm)) else float(np.clip(val_norm, 0.0, 1.0))
        m2_final = float(np.clip(
            0.30 * band_score +
            0.22 * follow +
            0.18 * persistence +
            0.10 * alpha_support +
            0.10 * quality_support +
            0.10 * valuation_support,
            0.0, 1.0,
        ))
        label = _label(m2_final, current_pos, follow_gap, quality_score)
        comment = _commentary(
            c_low, c_mid, c_high,
            p_low, p_mid, p_high,
            current_px,
            prev_pos, current_pos,
            score_slope_8q,
            band_slope,
            follow_gap,
            alpha_val,
            valuation_support=None if val_norm is None else valuation_support,
        )

        rows.append((
            str(ticker), asof, cur.period_end, prev.period_end,
            c_low, c_mid, c_high,
            p_low, p_mid, p_high,
            current_px,
            prev_pos, current_pos,
            band_mid_change, price_change, follow_gap,
            band_slope, price_to_mid_latest, price_to_mid_avg,
            alpha_val,
            band_score, follow, persistence, alpha_support, quality_support, valuation_support,
            m2_final, label, comment,
        ))

    return pd.DataFrame(rows, columns=[
        "ticker", "asof_date", "latest_period_end", "prev_period_end",
        "current_p_exp_low", "current_p_exp_mid", "current_p_exp_high",
        "prev_p_exp_low", "prev_p_exp_mid", "prev_p_exp_high",
        "current_px", "price_pos_prev_band", "price_pos_current_band",
        "band_mid_change_1q", "price_change_since_prev_period", "follow_gap_1q",
        "band_mid_slope_8q", "price_to_band_mid_latest", "price_to_band_mid_avg_8q",
        "alpha_trailing_63d", "m2_band_score", "m2_follow_score", "m2_persistence_score",
        "m2_alpha_support_score", "m2_quality_support_score", "m2_valuation_support_score",
        "m2_final", "m2_label", "m2_commentary"
    ])


def upsert_m2_period_comparison(conn, df: pd.DataFrame) -> None:
    if df.empty:
        return
    rows = _records_for_sql(df)
    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO analytics.m2_period_comparison
                  (ticker, asof_date, latest_period_end, prev_period_end,
                   current_p_exp_low, current_p_exp_mid, current_p_exp_high,
                   prev_p_exp_low, prev_p_exp_mid, prev_p_exp_high,
                   current_px, price_pos_prev_band, price_pos_current_band,
                   band_mid_change_1q, price_change_since_prev_period, follow_gap_1q,
                   band_mid_slope_8q, price_to_band_mid_latest, price_to_band_mid_avg_8q,
                   alpha_trailing_63d, m2_band_score, m2_follow_score, m2_persistence_score,
                   m2_alpha_support_score, m2_quality_support_score, m2_valuation_support_score,
                   m2_final, m2_label, m2_commentary)
                VALUES %s
                ON CONFLICT (ticker, asof_date)
                DO UPDATE SET
                  latest_period_end=EXCLUDED.latest_period_end,
                  prev_period_end=EXCLUDED.prev_period_end,
                  current_p_exp_low=EXCLUDED.current_p_exp_low,
                  current_p_exp_mid=EXCLUDED.current_p_exp_mid,
                  current_p_exp_high=EXCLUDED.current_p_exp_high,
                  prev_p_exp_low=EXCLUDED.prev_p_exp_low,
                  prev_p_exp_mid=EXCLUDED.prev_p_exp_mid,
                  prev_p_exp_high=EXCLUDED.prev_p_exp_high,
                  current_px=EXCLUDED.current_px,
                  price_pos_prev_band=EXCLUDED.price_pos_prev_band,
                  price_pos_current_band=EXCLUDED.price_pos_current_band,
                  band_mid_change_1q=EXCLUDED.band_mid_change_1q,
                  price_change_since_prev_period=EXCLUDED.price_change_since_prev_period,
                  follow_gap_1q=EXCLUDED.follow_gap_1q,
                  band_mid_slope_8q=EXCLUDED.band_mid_slope_8q,
                  price_to_band_mid_latest=EXCLUDED.price_to_band_mid_latest,
                  price_to_band_mid_avg_8q=EXCLUDED.price_to_band_mid_avg_8q,
                  alpha_trailing_63d=EXCLUDED.alpha_trailing_63d,
                  m2_band_score=EXCLUDED.m2_band_score,
                  m2_follow_score=EXCLUDED.m2_follow_score,
                  m2_persistence_score=EXCLUDED.m2_persistence_score,
                  m2_alpha_support_score=EXCLUDED.m2_alpha_support_score,
                  m2_quality_support_score=EXCLUDED.m2_quality_support_score,
                  m2_valuation_support_score=EXCLUDED.m2_valuation_support_score,
                  m2_final=EXCLUDED.m2_final,
                  m2_label=EXCLUDED.m2_label,
                  m2_commentary=EXCLUDED.m2_commentary
                """,
                rows,
                page_size=2000,
            )
