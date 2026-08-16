from datetime import date

import pandas as pd
import pytest

import sys
import types
try:
    import psycopg2.extras  # type: ignore  # noqa: F401
except ModuleNotFoundError:
    psycopg2 = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    extras.execute_values = lambda *args, **kwargs: None
    psycopg2.extras = extras
    sys.modules["psycopg2"] = psycopg2
    sys.modules["psycopg2.extras"] = extras

from src.analytics.run_daily_pipeline import _compute_m2_from_period_comparison


def test_gyo_m2_overrides_period_only_with_exact_timestamp(monkeypatch):
    calls = []
    def fake_read_sql(sql, conn, params=None):
        calls.append((sql, params))
        if "FROM analytics.m2_period_comparison" in sql:
            return pd.DataFrame([
                {"ticker": "AGYO", "m2": 0.41, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
                {"ticker": "THYAO", "m2": 0.63, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
            ])
        if "FROM analytics.gyo_m2_scores" in sql:
            return pd.DataFrame([
                {"ticker": "AGYO", "m2": 0.77, "m2_source": "GYO_PD_NAV_TWO_AXIS_V1", "m2_score_inputs": {"valuation_confidence": .7}},
            ])
        raise AssertionError(sql)
    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(
        object(), date(2026, 8, 5), gyo_analysis_at="2026-08-05T12:00:00+03:00"
    ).set_index("ticker")
    assert out.loc["AGYO", "m2"] == pytest.approx(.77)
    assert out.loc["THYAO", "m2"] == pytest.approx(.63)
    assert "analysis_at <= %(analysis_at)s" in calls[1][0]


def test_gyo_not_read_without_exact_timestamp(monkeypatch):
    calls=[]
    def fake_read_sql(sql, conn, params=None):
        calls.append(sql)
        return pd.DataFrame([{"ticker":"AGYO","m2":.4,"m2_source":"PERIOD_M2_V3","m2_score_inputs":None}])
    monkeypatch.setattr(pd,"read_sql",fake_read_sql)
    out=_compute_m2_from_period_comparison(object(),date(2026,8,5))
    assert out.iloc[0]["m2_source"]=="PERIOD_M2_V3"
    assert len(calls)==1
