from __future__ import annotations

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


def test_holding_m2_overrides_period_only_with_exact_timestamp(monkeypatch):
    calls = []

    def fake_read_sql(sql, conn, params=None):
        calls.append((sql, params))
        if "FROM analytics.m2_period_comparison" in sql:
            return pd.DataFrame([
                {"ticker": "KCHOL", "m2": 0.41, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
                {"ticker": "THYAO", "m2": 0.63, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
            ])
        if "FROM analytics.holding_m2_scores" in sql:
            return pd.DataFrame([
                {"ticker": "KCHOL", "m2": 0.76, "m2_source": "HOLDING_NAV_DISCOUNT_TWO_AXIS_V1", "m2_score_inputs": {"valuation_confidence": 0.7}},
            ])
        raise AssertionError(sql)

    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(
        object(), date(2026, 8, 5), holding_analysis_at="2026-08-05T12:00:00+03:00"
    ).set_index("ticker")
    assert out.loc["KCHOL", "m2"] == pytest.approx(0.76)
    assert out.loc["KCHOL", "m2_source"] == "HOLDING_NAV_DISCOUNT_TWO_AXIS_V1"
    assert out.loc["THYAO", "m2"] == pytest.approx(0.63)
    assert len(calls) == 2
    assert "analysis_at <= %(analysis_at)s" in calls[1][0]
    assert "nav_asof_date DESC" in calls[1][0]


def test_holding_m2_is_not_read_without_exact_cutoff(monkeypatch):
    calls = []

    def fake_read_sql(sql, conn, params=None):
        calls.append(sql)
        return pd.DataFrame([{
            "ticker": "KCHOL", "m2": 0.41,
            "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None,
        }])

    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(object(), date(2026, 8, 5))
    assert out.iloc[0]["m2_source"] == "PERIOD_M2_V3"
    assert len(calls) == 1


def test_family_specific_overrides_are_mutually_independent(monkeypatch):
    def fake_read_sql(sql, conn, params=None):
        if "FROM analytics.m2_period_comparison" in sql:
            return pd.DataFrame([
                {"ticker": "KCHOL", "m2": 0.4, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
                {"ticker": "THYAO", "m2": 0.4, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
                {"ticker": "GARAN", "m2": 0.4, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
            ])
        if "FROM analytics.holding_m2_scores" in sql:
            return pd.DataFrame([{"ticker": "KCHOL", "m2": 0.7, "m2_source": "HOLDING_NAV_DISCOUNT_TWO_AXIS_V1", "m2_score_inputs": {}}])
        if "FROM analytics.nonfin_m2_scores" in sql:
            return pd.DataFrame([{"ticker": "THYAO", "m2": 0.8, "m2_source": "NONFIN_RELATIVE_TWO_AXIS_V1", "m2_score_inputs": {}}])
        if "FROM analytics.bank_m2_scores" in sql:
            return pd.DataFrame([{"ticker": "GARAN", "m2": 0.9, "m2_source": "BANK_TWO_AXIS_V47", "m2_score_inputs": {}}])
        raise AssertionError(sql)

    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(
        object(), date(2026, 8, 5),
        holding_analysis_at="2026-08-05T12:00:00+03:00",
        nonfin_analysis_at="2026-08-05T12:00:00+03:00",
        bank_analysis_at="2026-08-05T12:00:00+03:00",
    ).set_index("ticker")
    assert out.loc["KCHOL", "m2"] == pytest.approx(0.7)
    assert out.loc["THYAO", "m2"] == pytest.approx(0.8)
    assert out.loc["GARAN", "m2"] == pytest.approx(0.9)
