from datetime import date
import json
import os
import uuid

import numpy as np
import pandas as pd
import pytest

from src.analytics import run_daily_pipeline as daily
from src.analytics.total_rasyo_score import DEFAULT_WEIGHTS, compute_total_rasyo
from tests.test_daily_pipeline_fail_closed import Conn


@pytest.mark.parametrize("value", [None, np.nan, pd.NA, "bad", np.inf, -np.inf, -0.1, 1.1, True])
@pytest.mark.parametrize("module", ["m2", "m3"])
def test_source_missing_invalid_never_becomes_score(monkeypatch, module, value):
    frame = pd.DataFrame([{"ticker": "W1", module: value, "m2_source": "PERIOD_M2_V3",
                           "m2_score_inputs": None}])
    monkeypatch.setattr(daily.pd, "read_sql", lambda *a, **k: frame.copy())
    reader = daily._compute_m2_from_period_comparison if module == "m2" else daily._compute_m3_from_trailing_alpha
    result = reader(object(), date(2026, 9, 8))
    assert len(result) == 1
    assert pd.isna(result.iloc[0][module])


@pytest.mark.parametrize("module", ["m2", "m3"])
def test_valid_score_including_real_neutral_unchanged(monkeypatch, module):
    frame = pd.DataFrame([{"ticker": str(value), module: value, "m2_source": "PERIOD_M2_V3",
                           "m2_score_inputs": None} for value in [0., .5, 1.]])
    monkeypatch.setattr(daily.pd, "read_sql", lambda *a, **k: frame.copy())
    reader = daily._compute_m2_from_period_comparison if module == "m2" else daily._compute_m3_from_trailing_alpha
    assert reader(object(), date(2026, 9, 8))[module].tolist() == [0., .5, 1.]


def test_invalid_specialized_override_does_not_resurrect_period_score(monkeypatch):
    def read(sql, *a, **k):
        bank = "bank_m2_scores" in sql
        return pd.DataFrame([{"ticker": "W1", "m2": None if bank else .8,
                              "m2_source": "BANK_TWO_AXIS_V47" if bank else "PERIOD_M2_V3",
                              "m2_score_inputs": {"source": "kept"} if bank else None}])
    monkeypatch.setattr(daily.pd, "read_sql", read)
    out = daily._compute_m2_from_period_comparison(
        object(), date(2026, 9, 8), bank_analysis_at="2026-09-08T19:00:00+03:00").iloc[0]
    assert pd.isna(out.m2)
    assert out.m2_source == "BANK_TWO_AXIS_V47"
    assert out.m2_score_inputs == {"source": "kept"}


def module_frame():
    return pd.DataFrame([dict(ticker="W1", period_end=date(2026, 6, 30),
                             m1=.8, m2=.5, m3=.6, ek1=.4, ek4=.5, ek9=.6,
                             m2_source="PERIOD_M2_V3", m2_score_inputs=None, good_count_ge8=7)])


@pytest.mark.parametrize("module", ["m2", "m3", "ek9"])
def test_rejection_reaches_total_and_persistence(monkeypatch, module):
    frame = module_frame()
    frame[module] = np.nan
    result = daily._finalize_module_scores(frame, DEFAULT_WEIGHTS)
    assert result.iloc[0].decision == "YETERSIZ_VERI"
    assert pd.isna(result.iloc[0].final_score)
    assert module.upper() in {k.upper() for k in result.iloc[0].module_rejections}
    captured = {}
    monkeypatch.setattr(daily, "execute_values", lambda cur, sql, rows, **kw: captured.update(sql=sql, rows=rows))
    daily._upsert_module_scores(Conn(), result, date(2026, 9, 8), 63)
    row = captured["rows"][0]
    assert row[14] is None and row[15] is None
    assert row[17] == "YETERSIZ_VERI"
    assert "module_rejections=EXCLUDED.module_rejections" in captured["sql"]
    assert json.loads(row[19]) == result.iloc[0].module_rejections


def test_complete_total_preserves_math_and_clears_rejections():
    frame = module_frame()
    frame["module_rejections"] = [{"M2": "OLD_REJECTION"}]
    result = daily._finalize_module_scores(frame, DEFAULT_WEIGHTS).iloc[0]
    expected = compute_total_rasyo({k: frame.iloc[0][k.lower()] for k in DEFAULT_WEIGHTS}, good_count_ge8=7)
    assert result.final_score == expected["final_score"]
    assert result.module_rejections == {}


def test_nan_good_count_persists_as_null(monkeypatch):
    frame = module_frame()
    frame["good_count_ge8"] = np.nan
    result = daily._finalize_module_scores(frame, DEFAULT_WEIGHTS)
    captured = []
    monkeypatch.setattr(daily, "execute_values", lambda cur, sql, rows, **kw: captured.extend(rows))
    daily._upsert_module_scores(Conn(), result, date(2026, 9, 8), 63)
    assert captured[0][16] is None
    assert captured[0][17] == "YETERSIZ_VERI"


@pytest.mark.parametrize("missing", ["m2", "m3", "ek9"])
def test_actual_daily_orchestration_preserves_rejected_row(monkeypatch, missing):
    """Keep actual M2/M3 readers, finalizer and persistence; stub unrelated producers."""
    for name in ("estimate_betas_for_date", "upsert_betas", "compute_trailing_alpha",
                 "upsert_trailing_alpha", "compute_alpha_realized", "upsert_alpha_realized",
                 "upsert_decile_map", "build_period_8q_comparison", "upsert_period_8q_comparison",
                 "build_expected_band_periods", "upsert_expected_band_periods",
                 "compute_m2_period_comparison", "upsert_m2_period_comparison"):
        monkeypatch.setattr(daily, name, lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(daily, "build_decile_map", lambda *a, **k: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(daily, "_compute_m1_from_period_trend", lambda *a, **k: module_frame()[["ticker", "m1", "period_end", "good_count_ge8"]])
    for key in ("ek1", "ek4", "ek9"):
        name = {"ek1": "_compute_ek1_goodcount", "ek4": "_compute_ek4_momentum", "ek9": "_compute_ek9_vol"}[key]
        monkeypatch.setattr(daily, name, lambda *a, key=key, **k: pd.DataFrame([{
            "ticker": "W1", key: np.nan if key == missing else .6}]))
    def read(sql, *a, **k):
        if "m2_period_comparison" in sql:
            return pd.DataFrame([{"ticker": "W1", "m2": None if missing == "m2" else .5,
                                  "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None}])
        if "alpha_trailing" in sql:
            return pd.DataFrame([{"ticker": "W1", "m3": None if missing == "m3" else .6}])
        if "ratios_quarterly" in sql:
            return pd.DataFrame()
        return pd.DataFrame([{"ticker": "W1", "sector_code": "NONFIN", "sector_index_code": "XUSIN"}])
    monkeypatch.setattr(daily.pd, "read_sql", read)
    captured = []
    monkeypatch.setattr(daily, "execute_values", lambda cur, sql, rows, **kw: captured.extend(rows))
    daily.run_daily_pipeline(Conn(), "2026-09-08")
    assert len(captured) == 1
    assert captured[0][15] is None
    assert captured[0][17] == "YETERSIZ_VERI"
    assert {k.lower() for k in json.loads(captured[0][19])} == {missing}


@pytest.mark.skipif(not os.getenv("TOTAL_RASYO_TEST_DSN"), reason="requires CI PostgreSQL")
def test_nullable_db_sources_and_rejection_roundtrip():
    import psycopg2
    ticker = "W1_" + uuid.uuid4().hex
    asof = date(2026, 9, 8)
    with psycopg2.connect(os.environ["TOTAL_RASYO_TEST_DSN"]) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO analytics.m2_period_comparison(ticker,asof_date,m2_final) VALUES (%s,%s,NULL)", (ticker, asof))
                cur.execute("INSERT INTO analytics.alpha_trailing(ticker,asof_date,window_days,alpha_score) VALUES (%s,%s,63,NULL)", (ticker, asof))
            m2 = daily._compute_m2_from_period_comparison(conn, asof).set_index("ticker").loc[ticker]
            m3 = daily._compute_m3_from_trailing_alpha(conn, asof).set_index("ticker").loc[ticker]
            assert pd.isna(m2.m2) and pd.isna(m3.m3)
            frame = module_frame()
            frame["ticker"] = ticker
            frame["m2"] = m2.m2
            frame["m3"] = m3.m3
            result = daily._finalize_module_scores(frame, DEFAULT_WEIGHTS)
            daily._upsert_module_scores(conn, result, asof, 63)
            with conn.cursor() as cur:
                cur.execute("SELECT m2,m3,final_score,decision,module_rejections FROM analytics.module_scores WHERE ticker=%s", (ticker,))
                row = cur.fetchone()
            assert row[:4] == (None, None, None, "YETERSIZ_VERI")
            assert set(row[4]) == {"M2", "M3"}
            # Exercise actual ON CONFLICT both ways: recovery clears diagnostics,
            # then a new Ek9 rejection replaces them without retaining a score.
            frame["m2"], frame["m3"] = .5, .6
            daily._upsert_module_scores(conn, daily._finalize_module_scores(frame, DEFAULT_WEIGHTS), asof, 63)
            with conn.cursor() as cur:
                cur.execute("SELECT final_score,module_rejections FROM analytics.module_scores WHERE ticker=%s", (ticker,))
                recovered = cur.fetchone()
            assert recovered[0] is not None and recovered[1] == {}
            frame["ek9"] = np.nan
            frame["ek9_rejection_reason"] = "STOCK_WINDOW_PRICE_MISSING"
            daily._upsert_module_scores(conn, daily._finalize_module_scores(frame, DEFAULT_WEIGHTS), asof, 63)
            with conn.cursor() as cur:
                cur.execute("SELECT ek9,final_score,module_rejections FROM analytics.module_scores WHERE ticker=%s", (ticker,))
                rejected_again = cur.fetchone()
            assert rejected_again == (None, None, {"Ek9": "STOCK_WINDOW_PRICE_MISSING"})
        finally:
            conn.rollback()
            with conn.cursor() as cur:
                for table in ("module_scores", "m2_period_comparison", "alpha_trailing"):
                    cur.execute(f"DELETE FROM analytics.{table} WHERE ticker=%s", (ticker,))
            conn.commit()
