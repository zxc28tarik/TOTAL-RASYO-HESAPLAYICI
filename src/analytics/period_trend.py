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
    xs = [float(v) for v in values if v is not None and np.isfinite(float(v))]
    if len(xs) < 3:
        return None
    y = np.array(xs, dtype=float)
    x = np.arange(len(y), dtype=float)
    x -= x.mean()
    denom = float((x * x).sum())
    if denom == 0:
        return None
    return float((x * (y - y.mean())).sum() / denom)


def _trend_score(latest, avg8, change1q, change4q, slope8) -> float:
    # All parts are normalized to 0..1; this represents the M1 quality trend score.
    latest_part = 0.5 if latest is None else float(np.clip((float(latest) - 1.0) / 9.0, 0.0, 1.0))
    c1 = 0.5 if change1q is None else float(np.clip(0.5 + float(change1q) / 2.0, 0.0, 1.0))
    c4 = 0.5 if change4q is None else float(np.clip(0.5 + float(change4q) / 3.0, 0.0, 1.0))
    sl = 0.5 if slope8 is None else float(np.clip(0.5 + float(slope8) / 0.8, 0.0, 1.0))
    avg_part = 0.5 if avg8 is None else float(np.clip((float(avg8) - 1.0) / 9.0, 0.0, 1.0))
    return float(np.clip(0.35 * latest_part + 0.15 * avg_part + 0.20 * c1 + 0.15 * c4 + 0.15 * sl, 0.0, 1.0))


def _trend_label(score, slope8, change1q) -> str:
    slope8 = 0.0 if slope8 is None or not np.isfinite(float(slope8)) else float(slope8)
    change1q = 0.0 if change1q is None or not np.isfinite(float(change1q)) else float(change1q)
    score = 0.0 if score is None else float(score)
    if slope8 > 0.15 and change1q >= -0.20 and score >= 0.62:
        return "IMPROVING"
    if slope8 < -0.15 and change1q <= 0.10:
        return "DETERIORATING"
    if score >= 0.70 and abs(slope8) <= 0.15:
        return "STABLE_HIGH"
    if score <= 0.40 and abs(slope8) <= 0.15:
        return "STABLE_LOW"
    return "MIXED"


def build_period_8q_comparison(conn, asof_date: str) -> pd.DataFrame:
    asof = pd.to_datetime(asof_date).date()
    df = pd.read_sql(
        """
        SELECT s.ticker, s.period_end, s.version_tag,
               s.rsc_core_norm, s.good_count_ge8, s.score_mean, s.score_std
        FROM analytics.rsc_summary_quarterly s
        JOIN core.financials_quarterly f
          ON f.ticker=s.ticker AND f.period_end=s.period_end AND f.version_tag=s.version_tag
        WHERE f.t0_date IS NOT NULL
          AND f.t0_date <= %(asof)s
        ORDER BY s.ticker, s.period_end
        """,
        conn,
        params={"asof": asof},
    )
    if df.empty:
        return pd.DataFrame(columns=[
            "ticker", "asof_date", "latest_period_end", "period_count", "score_latest", "score_prev",
            "score_avg_8q", "score_min_8q", "score_max_8q", "score_change_1q", "score_change_4q",
            "score_slope_8q", "rsc_latest", "rsc_prev", "rsc_change_1q", "good_count_latest",
            "good_count_prev", "good_count_change_1q", "quality_trend_score", "quality_trend_label"
        ])
    df["period_end"] = pd.to_datetime(df["period_end"]).dt.date
    df["score_mean"] = pd.to_numeric(df["score_mean"], errors="coerce")
    df["rsc_core_norm"] = pd.to_numeric(df["rsc_core_norm"], errors="coerce")
    df["good_count_ge8"] = pd.to_numeric(df["good_count_ge8"], errors="coerce").fillna(0).astype(int)

    rows = []
    for ticker, g in df.groupby("ticker", sort=False):
        g = g.sort_values("period_end").tail(8).reset_index(drop=True)
        period_count = int(g.shape[0])
        scores = g["score_mean"].tolist()
        rscs = g["rsc_core_norm"].tolist()
        goods = g["good_count_ge8"].tolist()

        latest = scores[-1] if period_count >= 1 and pd.notna(scores[-1]) else None
        prev = scores[-2] if period_count >= 2 and pd.notna(scores[-2]) else None
        clean_scores = [float(v) for v in scores if v is not None and pd.notna(v) and np.isfinite(float(v))]
        avg8 = float(np.mean(clean_scores)) if clean_scores else None
        min8 = float(np.min(clean_scores)) if clean_scores else None
        max8 = float(np.max(clean_scores)) if clean_scores else None
        change1 = (float(latest) - float(prev)) if latest is not None and prev is not None else None
        base4 = scores[-5] if period_count >= 5 and pd.notna(scores[-5]) else None
        change4 = (float(latest) - float(base4)) if latest is not None and base4 is not None else None
        slope8 = _slope([v for v in scores if pd.notna(v)])

        rsc_latest = rscs[-1] if period_count >= 1 and pd.notna(rscs[-1]) else None
        rsc_prev = rscs[-2] if period_count >= 2 and pd.notna(rscs[-2]) else None
        rsc_change1 = (float(rsc_latest) - float(rsc_prev)) if rsc_latest is not None and rsc_prev is not None else None
        good_latest = int(goods[-1]) if period_count >= 1 else None
        good_prev = int(goods[-2]) if period_count >= 2 else None
        good_change1 = (good_latest - good_prev) if good_latest is not None and good_prev is not None else None
        qscore = _trend_score(latest, avg8, change1, change4, slope8)
        qlabel = _trend_label(qscore, slope8, change1)

        rows.append((
            str(ticker), asof, g["period_end"].iloc[-1], period_count,
            latest, prev, avg8, min8, max8, change1, change4, slope8,
            rsc_latest, rsc_prev, rsc_change1, good_latest, good_prev, good_change1,
            qscore, qlabel,
        ))

    return pd.DataFrame(rows, columns=[
        "ticker", "asof_date", "latest_period_end", "period_count", "score_latest", "score_prev",
        "score_avg_8q", "score_min_8q", "score_max_8q", "score_change_1q", "score_change_4q",
        "score_slope_8q", "rsc_latest", "rsc_prev", "rsc_change_1q", "good_count_latest",
        "good_count_prev", "good_count_change_1q", "quality_trend_score", "quality_trend_label"
    ])


def upsert_period_8q_comparison(conn, df: pd.DataFrame) -> None:
    if df.empty:
        return
    rows = _records_for_sql(df)
    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO analytics.period_8q_comparison
                  (ticker, asof_date, latest_period_end, period_count, score_latest, score_prev,
                   score_avg_8q, score_min_8q, score_max_8q, score_change_1q, score_change_4q,
                   score_slope_8q, rsc_latest, rsc_prev, rsc_change_1q, good_count_latest,
                   good_count_prev, good_count_change_1q, quality_trend_score, quality_trend_label)
                VALUES %s
                ON CONFLICT (ticker, asof_date)
                DO UPDATE SET
                  latest_period_end=EXCLUDED.latest_period_end,
                  period_count=EXCLUDED.period_count,
                  score_latest=EXCLUDED.score_latest,
                  score_prev=EXCLUDED.score_prev,
                  score_avg_8q=EXCLUDED.score_avg_8q,
                  score_min_8q=EXCLUDED.score_min_8q,
                  score_max_8q=EXCLUDED.score_max_8q,
                  score_change_1q=EXCLUDED.score_change_1q,
                  score_change_4q=EXCLUDED.score_change_4q,
                  score_slope_8q=EXCLUDED.score_slope_8q,
                  rsc_latest=EXCLUDED.rsc_latest,
                  rsc_prev=EXCLUDED.rsc_prev,
                  rsc_change_1q=EXCLUDED.rsc_change_1q,
                  good_count_latest=EXCLUDED.good_count_latest,
                  good_count_prev=EXCLUDED.good_count_prev,
                  good_count_change_1q=EXCLUDED.good_count_change_1q,
                  quality_trend_score=EXCLUDED.quality_trend_score,
                  quality_trend_label=EXCLUDED.quality_trend_label
                """,
                rows,
                page_size=2000,
            )
