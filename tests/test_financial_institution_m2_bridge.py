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


def test_financial_institution_m2_overrides_period_only_with_exact_timestamp(monkeypatch):
    calls = []
    def fake_read_sql(sql, conn, params=None):
        calls.append((sql, params))
        if "FROM analytics.m2_period_comparison" in sql:
            return pd.DataFrame([
                {"ticker": "FAKTR", "m2": .41, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
                {"ticker": "THYAO", "m2": .63, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
            ])
        if "FROM analytics.financial_institution_m2_scores" in sql:
            return pd.DataFrame([
                {"ticker": "FAKTR", "m2": .78, "m2_source": "FINANCIAL_INSTITUTION_PB_PE_TWO_AXIS_V1", "m2_score_inputs": {"valuation_confidence": .7}},
            ])
        raise AssertionError(sql)
    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(
        object(), date(2026, 8, 5), financial_institution_analysis_at="2026-08-05T12:00:00+03:00"
    ).set_index("ticker")
    assert out.loc["FAKTR", "m2"] == pytest.approx(.78)
    assert out.loc["THYAO", "m2"] == pytest.approx(.63)
    assert "analysis_at <= %(analysis_at)s" in calls[1][0]
    assert "period_end DESC" in calls[1][0]


def test_financial_institution_not_read_without_exact_timestamp(monkeypatch):
    calls = []
    def fake_read_sql(sql, conn, params=None):
        calls.append(sql)
        return pd.DataFrame([{"ticker": "FAKTR", "m2": .4, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None}])
    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(object(), date(2026, 8, 5))
    assert out.iloc[0]["m2_source"] == "PERIOD_M2_V3"
    assert len(calls) == 1


def test_financial_institution_m2_gecmise_sizmaz(monkeypatch):
    """
    Sonradan hesaplanmis kayit GECMIS analiz tarihine sizmamali:
    sorgu `analysis_at <= %(analysis_at)s` ile kesilir.
    """
    calls = []
    def fake_read_sql(sql, conn, params=None):
        calls.append((sql, params))
        if "FROM analytics.m2_period_comparison" in sql:
            return pd.DataFrame([{"ticker": "FAKTR", "m2": .41,
                                  "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None}])
        if "FROM analytics.financial_institution_m2_scores" in sql:
            return pd.DataFrame()          # kesim sonrasi kayit gelmedi
        raise AssertionError(sql)
    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(
        object(), date(2026, 8, 5),
        financial_institution_analysis_at="2026-08-05T12:00:00+03:00",
    ).set_index("ticker")
    assert out.loc["FAKTR", "m2_source"] == "PERIOD_M2_V3", "kesim disi kayit kullanilmamali"
    fi_sql = [s for s, _ in calls if "financial_institution_m2_scores" in s][0]
    assert "analysis_at <= %(analysis_at)s" in fi_sql
    assert "asof_date=%(asof)s" in fi_sql


def test_sektor_ozel_m2_oncelik_sirasi(monkeypatch):
    """Sektor ozel sonuc varsa donemsel M2 fallback'i EZILIR."""
    def fake_read_sql(sql, conn, params=None):
        if "FROM analytics.m2_period_comparison" in sql:
            return pd.DataFrame([
                {"ticker": "FAKTR", "m2": .30, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
                {"ticker": "DIGER", "m2": .55, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
            ])
        if "FROM analytics.financial_institution_m2_scores" in sql:
            return pd.DataFrame([{"ticker": "FAKTR", "m2": .82,
                                  "m2_source": "FINANCIAL_INSTITUTION_PB_PE_TWO_AXIS_V1",
                                  "m2_score_inputs": {"valuation_confidence": .6}}])
        raise AssertionError(sql)
    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(
        object(), date(2026, 8, 5),
        financial_institution_analysis_at="2026-08-05T12:00:00+03:00",
    ).set_index("ticker")
    assert out.loc["FAKTR", "m2"] == pytest.approx(.82)
    assert out.loc["FAKTR", "m2_source"] == "FINANCIAL_INSTITUTION_PB_PE_TWO_AXIS_V1"
    assert out.loc["DIGER", "m2_source"] == "PERIOD_M2_V3", "sektor sonucu olmayan sirket fallback kullanir"
    assert len(out) == 2, "ticker yinelenmemeli"
