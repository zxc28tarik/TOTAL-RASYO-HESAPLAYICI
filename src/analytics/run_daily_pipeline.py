from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values

from src.analytics.rsc_scoring import score_quarter, load_sector_config, build_sector_group_map
from src.analytics.betas import estimate_betas_for_date, upsert_betas
from src.analytics.alpha_realized import compute_alpha_realized, upsert_alpha_realized
from src.analytics.decile_map import build_decile_map, upsert_decile_map
from src.analytics.trailing_alpha import compute_trailing_alpha, upsert_trailing_alpha
from src.analytics.period_trend import build_period_8q_comparison, upsert_period_8q_comparison
from src.analytics.expected_band_periods import build_expected_band_periods, upsert_expected_band_periods
from src.analytics.m2_period import compute_m2_period_comparison, upsert_m2_period_comparison
from src.analytics.total_rasyo_score import compute_total_rasyo
from src.analytics.ek4_momentum import compute_ek4_momentum_point
from src.analytics.ek1_quality import compute_ek1_score_from_good_count
from src.analytics.ek9_volatility import compute_ek9_volatility_scores


def _sql_value(x):
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




def _json_text_or_none(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = value
        return json.dumps(parsed, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, default=_sql_value)

def _load_weights(path: Optional[str]) -> Dict[str, float]:
    if not path:
        return {"M2": 0.40, "M1": 0.18, "M3": 0.12, "Ek4": 0.16, "Ek1": 0.08, "Ek9": 0.06}
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    return cfg.get("base_weights", {})


def _resolve_pipeline_clock(
    asof: date,
    bank_analysis_at: Optional[str | datetime],
) -> tuple[datetime | None, date]:
    """Return exact analysis timestamp and the latest usable daily-market date.

    Without an exact timestamp, ``asof`` is treated as an end-of-day run. When
    a timestamp is supplied, every DATE-based market module uses the same
    conservative close cutoff; this prevents one module from seeing a close
    that another module correctly hides.
    """
    if bank_analysis_at is None:
        return None, asof
    if isinstance(bank_analysis_at, datetime):
        analysis_ts = bank_analysis_at
    else:
        try:
            analysis_ts = datetime.fromisoformat(str(bank_analysis_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("bank_analysis_at gecersiz ISO timestamp") from exc
    if analysis_ts.tzinfo is None or analysis_ts.utcoffset() is None:
        raise ValueError("bank_analysis_at timezone bilgisi icermeli")
    local_date = analysis_ts.astimezone(ZoneInfo("Europe/Istanbul")).date()
    if local_date != asof:
        raise ValueError("bank_analysis_at tarihi asof ile ayni olmali")
    from src.analytics.bank_batch_pipeline import daily_price_cutoff_date
    return analysis_ts, daily_price_cutoff_date(analysis_ts)


def _upsert_rsc(conn, df_scores: pd.DataFrame, df_sum: pd.DataFrame) -> None:
    with conn:
        with conn.cursor() as cur:
            if not df_scores.empty:
                rows = [tuple(r) for r in df_scores.itertuples(index=False, name=None)]
                execute_values(
                    cur,
                    """
                    INSERT INTO analytics.rsc_scores_quarterly
                      (ticker,period_end,version_tag,ratio_name,pillar,score_1_10,level_percentile,trend_bonus,is_na)
                    VALUES %s
                    ON CONFLICT (ticker,period_end,version_tag,ratio_name)
                    DO UPDATE SET
                      pillar=EXCLUDED.pillar,
                      score_1_10=EXCLUDED.score_1_10,
                      level_percentile=EXCLUDED.level_percentile,
                      trend_bonus=EXCLUDED.trend_bonus,
                      is_na=EXCLUDED.is_na
                    """,
                    rows, page_size=5000
                )
            if not df_sum.empty:
                rows2 = [tuple(r) for r in df_sum.itertuples(index=False, name=None)]
                execute_values(
                    cur,
                    """
                    INSERT INTO analytics.rsc_summary_quarterly
                      (ticker,period_end,version_tag,rsc_core_norm,rsc_val_norm,good_count_ge8,score_mean,score_std)
                    VALUES %s
                    ON CONFLICT (ticker,period_end,version_tag)
                    DO UPDATE SET
                      rsc_core_norm=EXCLUDED.rsc_core_norm,
                      rsc_val_norm=EXCLUDED.rsc_val_norm,
                      good_count_ge8=EXCLUDED.good_count_ge8,
                      score_mean=EXCLUDED.score_mean,
                      score_std=EXCLUDED.score_std
                    """,
                    rows2, page_size=5000
                )


def _compute_m1_from_period_trend(conn, asof: date) -> pd.DataFrame:
    df = pd.read_sql(
        """
        SELECT ticker, quality_trend_score AS m1, latest_period_end AS period_end,
               good_count_latest AS good_count_ge8
        FROM analytics.period_8q_comparison
        WHERE asof_date=%(asof)s
        """,
        conn, params={"asof": asof}
    )
    if df.empty:
        return pd.DataFrame(columns=["ticker", "m1", "period_end", "good_count_ge8"])
    df["m1"] = pd.to_numeric(df["m1"], errors="coerce").fillna(0.0).clip(0.0, 1.0)
    df["good_count_ge8"] = pd.to_numeric(df["good_count_ge8"], errors="coerce").fillna(0).astype(int)
    return df[["ticker", "m1", "period_end", "good_count_ge8"]]


def _compute_m2_from_period_comparison(
    conn,
    asof: date,
    *,
    period_asof: date | None = None,
    bank_analysis_at: Optional[str | datetime] = None,
    nonfin_analysis_at: Optional[str | datetime] = None,
    holding_analysis_at: Optional[str | datetime] = None,
    gyo_analysis_at: Optional[str | datetime] = None,
    insurance_analysis_at: Optional[str | datetime] = None,
    financial_institution_analysis_at: Optional[str | datetime] = None,
) -> pd.DataFrame:
    period_cutoff = period_asof or asof
    period = pd.read_sql(
        """
        SELECT ticker, m2_final AS m2,
               'PERIOD_M2_V3'::text AS m2_source,
               NULL::jsonb AS m2_score_inputs
        FROM analytics.m2_period_comparison
        WHERE asof_date=%(asof)s
        """,
        conn, params={"asof": period_cutoff}
    )
    bank = pd.DataFrame(columns=["ticker", "m2", "m2_source", "m2_score_inputs"])
    nonfin = pd.DataFrame(columns=["ticker", "m2", "m2_source", "m2_score_inputs"])
    holding = pd.DataFrame(columns=["ticker", "m2", "m2_source", "m2_score_inputs"])
    gyo = pd.DataFrame(columns=["ticker", "m2", "m2_source", "m2_score_inputs"])
    insurance = pd.DataFrame(columns=["ticker", "m2", "m2_source", "m2_score_inputs"])
    financial_institution = pd.DataFrame(columns=["ticker", "m2", "m2_source", "m2_score_inputs"])
    if nonfin_analysis_at is not None:
        nonfin_cutoff, _ = _resolve_pipeline_clock(asof, nonfin_analysis_at)
        assert nonfin_cutoff is not None
        nonfin = pd.read_sql(
            """
            SELECT DISTINCT ON (ticker)
                   ticker, m2_score AS m2,
                   m2_source, score_inputs AS m2_score_inputs
            FROM analytics.nonfin_m2_scores
            WHERE asof_date=%(asof)s
              AND analysis_at <= %(analysis_at)s
            ORDER BY ticker, analysis_at DESC, anchor_period_end DESC
            """,
            conn, params={"asof": asof, "analysis_at": nonfin_cutoff}
        )
    if holding_analysis_at is not None:
        holding_cutoff, _ = _resolve_pipeline_clock(asof, holding_analysis_at)
        assert holding_cutoff is not None
        holding = pd.read_sql(
            """
            SELECT DISTINCT ON (ticker)
                   ticker, m2_score AS m2,
                   m2_source, score_inputs AS m2_score_inputs
            FROM analytics.holding_m2_scores
            WHERE asof_date=%(asof)s
              AND analysis_at <= %(analysis_at)s
            ORDER BY ticker, analysis_at DESC, nav_asof_date DESC
            """,
            conn, params={"asof": asof, "analysis_at": holding_cutoff}
        )
    if gyo_analysis_at is not None:
        gyo_cutoff, _ = _resolve_pipeline_clock(asof, gyo_analysis_at)
        assert gyo_cutoff is not None
        gyo = pd.read_sql(
            """
            SELECT DISTINCT ON (ticker)
                   ticker, m2_score AS m2,
                   m2_source, score_inputs AS m2_score_inputs
            FROM analytics.gyo_m2_scores
            WHERE asof_date=%(asof)s
              AND analysis_at <= %(analysis_at)s
            ORDER BY ticker, analysis_at DESC, nav_asof_date DESC
            """,
            conn, params={"asof": asof, "analysis_at": gyo_cutoff}
        )
    if insurance_analysis_at is not None:
        insurance_cutoff, _ = _resolve_pipeline_clock(asof, insurance_analysis_at)
        assert insurance_cutoff is not None
        insurance = pd.read_sql(
            """
            SELECT DISTINCT ON (ticker)
                   ticker, m2_score AS m2,
                   m2_source, score_inputs AS m2_score_inputs
            FROM analytics.insurance_m2_scores
            WHERE asof_date=%(asof)s
              AND analysis_at <= %(analysis_at)s
            ORDER BY ticker, analysis_at DESC, period_end DESC
            """,
            conn, params={"asof": asof, "analysis_at": insurance_cutoff}
        )
    if financial_institution_analysis_at is not None:
        fi_cutoff, _ = _resolve_pipeline_clock(asof, financial_institution_analysis_at)
        assert fi_cutoff is not None
        financial_institution = pd.read_sql(
            """
            SELECT DISTINCT ON (ticker)
                   ticker, m2_score AS m2,
                   m2_source, score_inputs AS m2_score_inputs
            FROM analytics.financial_institution_m2_scores
            WHERE asof_date=%(asof)s
              AND analysis_at <= %(analysis_at)s
            ORDER BY ticker, analysis_at DESC, period_end DESC
            """,
            conn, params={"asof": asof, "analysis_at": fi_cutoff}
        )
    if bank_analysis_at is not None:
        cutoff, _ = _resolve_pipeline_clock(asof, bank_analysis_at)
        assert cutoff is not None
        bank = pd.read_sql(
            """
            SELECT DISTINCT ON (ticker)
                   ticker, m2_score AS m2,
                   'BANK_TWO_AXIS_V47'::text AS m2_source,
                   score_inputs AS m2_score_inputs
            FROM analytics.bank_m2_scores
            WHERE asof_date=%(asof)s
              AND analysis_at <= %(analysis_at)s
            ORDER BY ticker, analysis_at DESC, anchor_period_end DESC
            """,
            conn, params={"asof": asof, "analysis_at": cutoff}
        )
    frames = []
    if not period.empty:
        frames.append(period)
    for override in (holding, gyo, insurance, financial_institution, nonfin, bank):
        if override.empty:
            continue
        override_tickers = set(override["ticker"].astype(str))
        frames = [frame[~frame["ticker"].astype(str).isin(override_tickers)] for frame in frames]
        frames.append(override)
    if not frames:
        return pd.DataFrame(columns=["ticker", "m2", "m2_source", "m2_score_inputs"])
    df = pd.concat(frames, ignore_index=True)
    df["ticker"] = df["ticker"].astype(str)
    df["m2"] = pd.to_numeric(df["m2"], errors="coerce").fillna(0.5).clip(0.0, 1.0)
    return df[["ticker", "m2", "m2_source", "m2_score_inputs"]]


def _compute_m3_from_trailing_alpha(conn, asof: date, window_days: int = 63) -> pd.DataFrame:
    df = pd.read_sql(
        """
        SELECT ticker, alpha_score AS m3
        FROM analytics.alpha_trailing
        WHERE asof_date=%(asof)s AND window_days=%(w)s
        """,
        conn, params={"asof": asof, "w": int(window_days)}
    )
    if df.empty:
        return pd.DataFrame(columns=["ticker", "m3"])
    df["m3"] = pd.to_numeric(df["m3"], errors="coerce").fillna(0.5).clip(0.0, 1.0)
    return df[["ticker", "m3"]]


def _compute_ek4_momentum(conn, asof: date, lookback: int = 20) -> pd.DataFrame:
    u = pd.read_sql(
        "SELECT ticker, COALESCE(sector_index_code,'XU100') AS sec FROM core.universe_stocks WHERE is_active=true",
        conn
    )
    if u.empty:
        return pd.DataFrame(columns=["ticker","ek4"])
    p = pd.read_sql(
        """
        SELECT ticker, trade_date, COALESCE(adj_close, close) AS px
        FROM core.prices_daily
        WHERE ticker = ANY(%(t)s) AND trade_date <= %(asof)s
        """,
        conn, params={"t": u["ticker"].astype(str).tolist(), "asof": asof}
    )
    if p.empty:
        return pd.DataFrame(columns=["ticker","ek4"])
    p["trade_date"] = pd.to_datetime(p["trade_date"]).dt.date
    spx = p.pivot_table(index="trade_date", columns="ticker", values="px", aggfunc="last").sort_index()
    idx = sorted(set(["XU100"] + u["sec"].astype(str).tolist()))
    ip = pd.read_sql(
        """
        SELECT index_code, trade_date, close AS px
        FROM core.index_prices_daily
        WHERE index_code = ANY(%(i)s) AND trade_date <= %(asof)s
        """,
        conn, params={"i": idx, "asof": asof}
    )
    if ip.empty:
        return pd.DataFrame(columns=["ticker","ek4"])
    ip["trade_date"] = pd.to_datetime(ip["trade_date"]).dt.date
    ipx = ip.pivot_table(index="trade_date", columns="index_code", values="px", aggfunc="last").sort_index()
    dates = spx.index.intersection(ipx.index)
    if len(dates) < lookback + 2:
        return pd.DataFrame(columns=["ticker","ek4"])
    end = dates[-1]
    start = dates[-(lookback + 1)]
    rows = []
    for r in u.itertuples(index=False):
        t = str(r.ticker); sec = str(r.sec)
        if t not in spx.columns or sec not in ipx.columns:
            continue
        p0 = spx.loc[start, t]; p1 = spx.loc[end, t]
        s0 = ipx.loc[start, sec]; s1 = ipx.loc[end, sec]
        if pd.isna(p0) or pd.isna(p1) or pd.isna(s0) or pd.isna(s1) or float(p0) <= 0 or float(s0) <= 0:
            continue
        point = compute_ek4_momentum_point(
            stock_start=float(p0),
            stock_end=float(p1),
            sector_start=float(s0),
            sector_end=float(s1),
        )
        rows.append((t, point.score))
    return pd.DataFrame(rows, columns=["ticker","ek4"])


def _compute_ek1_goodcount(conn, asof: date) -> pd.DataFrame:
    df = pd.read_sql(
        """
        SELECT ticker, good_count_latest AS good_count_ge8
        FROM analytics.period_8q_comparison
        WHERE asof_date=%(asof)s
        """,
        conn, params={"asof": asof}
    )
    if df.empty:
        return pd.DataFrame(columns=["ticker","ek1","good_count_ge8"])
    gc = pd.to_numeric(df["good_count_ge8"], errors="coerce").fillna(0.0)
    df["ek1"] = gc.map(compute_ek1_score_from_good_count)
    return df[["ticker","ek1","good_count_ge8"]]


def _compute_ek9_vol(conn, asof: date, lookback: int = 63) -> pd.DataFrame:
    p = pd.read_sql(
        """
        SELECT ticker, trade_date, COALESCE(adj_close, close) AS px
        FROM core.prices_daily
        WHERE trade_date <= %(asof)s
        """,
        conn, params={"asof": asof}
    )
    if p.empty:
        return pd.DataFrame(columns=["ticker","ek9"])
    p["trade_date"] = pd.to_datetime(p["trade_date"]).dt.date
    spx = p.pivot_table(index="trade_date", columns="ticker", values="px", aggfunc="last").sort_index()
    ret = spx.pct_change()
    if ret.shape[0] < lookback + 2:
        return pd.DataFrame(columns=["ticker","ek9"])
    window = ret.tail(lookback)
    scored = compute_ek9_volatility_scores(window)
    ek9 = scored["ek9"]
    return pd.DataFrame({"ticker": ek9.index.astype(str), "ek9": ek9.values})


def _upsert_module_scores(
    conn, df: pd.DataFrame, asof: date, horizon_days: int,
    *, analysis_at: datetime | None = None, source_run_key: str | None = None,
) -> None:
    if df.empty:
        return
    rows = []
    for r in df.itertuples(index=False):
        rows.append(tuple(_sql_value(v) for v in (
            r.ticker, asof, r.period_end, int(horizon_days),
            r.m1, r.m2, r.m3,
            r.m2_source,
            _json_text_or_none(r.m2_score_inputs),
            r.ek1, None, r.ek4, None, r.ek9,
            r.base_score, r.final_score,
            int(r.good_count_ge8) if r.good_count_ge8 is not None else None,
            r.decision, bool(r.veto_flag), analysis_at, source_run_key
        )))
    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO analytics.module_scores
                  (ticker,asof_date,period_end,horizon_days,m1,m2,m3,m2_source,m2_score_inputs,
                   ek1,ek3,ek4,ek5_dilution,ek9,base_score,final_score,good_count_ge8,decision,veto_flag,
                   analysis_at,source_run_key)
                VALUES %s
                ON CONFLICT (ticker, asof_date, horizon_days)
                DO UPDATE SET
                  period_end=EXCLUDED.period_end,
                  m1=EXCLUDED.m1,
                  m2=EXCLUDED.m2,
                  m3=EXCLUDED.m3,
                  m2_source=EXCLUDED.m2_source,
                  m2_score_inputs=EXCLUDED.m2_score_inputs,
                  ek1=EXCLUDED.ek1,
                  ek3=EXCLUDED.ek3,
                  ek4=EXCLUDED.ek4,
                  ek5_dilution=EXCLUDED.ek5_dilution,
                  ek9=EXCLUDED.ek9,
                  base_score=EXCLUDED.base_score,
                  final_score=EXCLUDED.final_score,
                  good_count_ge8=EXCLUDED.good_count_ge8,
                  decision=EXCLUDED.decision,
                  veto_flag=EXCLUDED.veto_flag,
                  analysis_at=EXCLUDED.analysis_at,
                  source_run_key=EXCLUDED.source_run_key
                WHERE analytics.module_scores.analysis_at IS NULL
                   OR (EXCLUDED.analysis_at IS NOT NULL
                       AND EXCLUDED.analysis_at >= analytics.module_scores.analysis_at)
                """,
                rows, page_size=2000
            )

    # V22-A: URETICI TARAFI lineage. module_scores yazimini DEGISTIRMEZ;
    # ONA EK bir yan etki olarak calisir. analysis_at yoksa (eski cagri
    # sekli) lineage YAZILMAZ -- kimliksiz kayit uretmek yerine sessizce
    # atlanir; identity_known=false zaten V22-A tuketim snapshot'inda
    # bunu yansitir.
    if analysis_at is not None:
        from datetime import timezone as _tz
        from src.analytics.module_producer_lineage import ModuleRow, persist_producer_lineage
        satirlar = [ModuleRow(ticker=r.ticker, period_end=r.period_end)
                   for r in df.itertuples(index=False)]
        persist_producer_lineage(
            conn, satirlar, analysis_at=analysis_at,
            produced_at=datetime.now(_tz.utc),  # GERCEK duvar saati; PIT baglami analysis_at'ten AYRI
            source_run_key=source_run_key)


def run_daily_pipeline(
    conn,
    asof_date: str,
    ratios_json_path: str = "config/ratios.json",
    sectors_json_path: str = "config/sectors.json",
    weights_json_path: Optional[str] = None,
    horizon_days: int = 63,
    *,
    bank_analysis_at: Optional[str] = None,
    bank_anchor_period_end: Optional[str] = None,
) -> None:
    """Run the revised pipeline.

    Main project logic:
    - M1: last 8 financial periods / total ratio score trend.
    - M2: period-based expected band comparison, not a one-day band check.
    - M3: trailing 63 trading day alpha versus BIST + sector.
    """
    asof = pd.to_datetime(asof_date).date()
    analysis_ts, market_asof = _resolve_pipeline_clock(asof, bank_analysis_at)

    # 1) RSC scoring from ratios table. This creates per-period total ratio notes.
    # Sector-neutral mode: percentiles are ranked within sector groups and the
    # sector policies in sectors.json shape allowed ratios + aggregation weights.
    index_to_group, sector_policies = load_sector_config(sectors_json_path)
    universe_sec = pd.read_sql(
        "SELECT ticker, sector_index_code, sector_code FROM core.universe_stocks",
        conn
    )
    sector_group_map = build_sector_group_map(universe_sec, index_to_group)

    ratios = pd.read_sql(
        "SELECT ticker, period_end, version_tag, ratio_name, ratio_value, is_na FROM analytics.ratios_quarterly",
        conn
    )
    if not ratios.empty:
        df_scores, df_sum = score_quarter(
            ratios,
            ratios_json_path=ratios_json_path,
            allowed_ratios=None,
            sector_group_map=sector_group_map,
            sector_policies=sector_policies,
        )
        _upsert_rsc(conn, df_scores, df_sum)

    # 2) Market/sector beta for the analysis date. Used by trailing alpha.
    bet = estimate_betas_for_date(conn, t0_date=str(market_asof))
    upsert_betas(conn, bet)

    # 3) Trailing alpha: asof - 63 trading days -> asof. No forward waiting.
    ta = compute_trailing_alpha(conn, asof_date=str(market_asof), window_days=horizon_days)
    upsert_trailing_alpha(conn, ta)

    # 4) Keep legacy forward alpha + decile map available for calibration/backtest if enough historical data exists.
    # The new period-band builder has a conservative RSC fallback, so the system does not fail on small datasets.
    ar = compute_alpha_realized(conn, horizon_days=horizon_days, t0_max=str(market_asof))
    upsert_alpha_realized(conn, ar)
    map_df, thr_df = build_decile_map(conn, window_end=str(market_asof), horizon_days=horizon_days, bucket_count=10)
    upsert_decile_map(conn, map_df, thr_df)

    # 5) M1 foundation: last 8 period total-ratio/RSC trend.
    period_df = build_period_8q_comparison(conn, asof_date=str(asof))
    upsert_period_8q_comparison(conn, period_df)

    # 6) Expected bands for the last 8 financial periods, not just the latest report.
    band_periods = build_expected_band_periods(conn, asof_date=str(market_asof), horizon_days=horizon_days, bucket_count=10)
    upsert_expected_band_periods(conn, band_periods)

    # 7) M2: current band vs previous band + 8-period band/fiyat takip farkı + alpha support.
    m2_period = compute_m2_period_comparison(conn, asof_date=str(market_asof), horizon_days=horizon_days)
    upsert_m2_period_comparison(conn, m2_period)

    # 7.5) Optional BANK v4.7 valuation batch. Exact timestamp is mandatory;
    # daily date is not silently promoted to a timezone-less analysis moment.
    if analysis_ts is not None:
        from src.analytics.bank_batch_pipeline import run_bank_batch
        anchor = None if bank_anchor_period_end is None else pd.to_datetime(bank_anchor_period_end).date()
        run_bank_batch(
            conn, analysis_at=analysis_ts, anchor_period_end=anchor, persist=True
        )

    # 8) Final modules.
    m1 = _compute_m1_from_period_trend(conn, asof)
    m2 = _compute_m2_from_period_comparison(
        conn,
        asof,
        period_asof=market_asof,
        bank_analysis_at=analysis_ts,
        nonfin_analysis_at=analysis_ts,
        holding_analysis_at=analysis_ts,
        gyo_analysis_at=analysis_ts,
        insurance_analysis_at=analysis_ts,
        financial_institution_analysis_at=analysis_ts,
    )
    m3 = _compute_m3_from_trailing_alpha(conn, market_asof, window_days=horizon_days)
    ek1 = _compute_ek1_goodcount(conn, asof)
    ek4 = _compute_ek4_momentum(conn, market_asof)
    ek9 = _compute_ek9_vol(conn, market_asof)

    tickers = pd.read_sql("SELECT ticker FROM core.universe_stocks WHERE is_active=true", conn)
    df = pd.DataFrame({"ticker": tickers["ticker"].astype(str)}) if not tickers.empty else pd.DataFrame(columns=["ticker"])
    if df.empty:
        return

    df = df.merge(m1, on="ticker", how="left")
    df = df.merge(m2, on="ticker", how="left")
    df = df.merge(m3, on="ticker", how="left")
    df = df.merge(ek1, on="ticker", how="left", suffixes=("", "_ek1"))
    df = df.merge(ek4, on="ticker", how="left")
    df = df.merge(ek9, on="ticker", how="left")
    df = df.fillna({"m1":0.0, "m2":0.5, "m3":0.5, "ek1":0.0, "ek4":0.5, "ek9":0.5, "good_count_ge8":0})
    if "m2_source" not in df.columns:
        df["m2_source"] = "NEUTRAL_FALLBACK"
    else:
        df["m2_source"] = df["m2_source"].fillna("NEUTRAL_FALLBACK")
    if "m2_score_inputs" not in df.columns:
        df["m2_score_inputs"] = None

    # If period_end is missing from M1, fill from financials.
    if "period_end" not in df.columns or df["period_end"].isna().all():
        pe = pd.read_sql("SELECT ticker, MAX(period_end) AS period_end FROM core.financials_quarterly GROUP BY ticker", conn)
        df = df.drop(columns=[c for c in ["period_end"] if c in df.columns]).merge(pe, on="ticker", how="left")

    W = _load_weights(weights_json_path)
    def total_for_row(row: pd.Series) -> pd.Series:
        result = compute_total_rasyo(
            {
                "M2": row["m2"],
                "M1": row["m1"],
                "M3": row["m3"],
                "Ek4": row["ek4"],
                "Ek1": row["ek1"],
                "Ek9": row["ek9"],
            },
            good_count_ge8=row["good_count_ge8"],
            weights=W,
        )
        return pd.Series({
            "base_score": result["base_score"],
            "veto_flag": result["veto_flag"],
            "final_score": result["final_score"],
            "decision": result["decision"],
        })

    totals = df.apply(total_for_row, axis=1)
    df[["base_score", "veto_flag", "final_score", "decision"]] = totals
    _upsert_module_scores(conn, df, asof, horizon_days, analysis_at=analysis_ts)
