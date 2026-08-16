from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

# Test ortamında psycopg2 kurulmamış olabilir; bu dosya yalnız saf birleştirme
# ve SQL parametre sözleşmesini sınar. Üretim bağımlılığı requirements.txt içindedir.
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

from src.analytics.bank_batch_pipeline import daily_price_cutoff_date
from src.analytics.bank_batch_pipeline import BankM2Context, compute_bank_m2_score
from src.analytics.bank_valuation_pipeline import BankValuationInputs, run_bank_valuation
from tests.test_bank_batch_pipeline import canonical
from src.analytics.run_daily_pipeline import (
    _compute_m2_from_period_comparison,
    _resolve_pipeline_clock,
    _upsert_module_scores,
)

TZ = ZoneInfo("Europe/Istanbul")


def test_daily_close_cutoff_prevents_intraday_same_day_lookahead():
    assert daily_price_cutoff_date(datetime(2026, 8, 4, 9, 0, tzinfo=TZ)) == date(2026, 8, 3)
    assert daily_price_cutoff_date(datetime(2026, 8, 4, 18, 29, tzinfo=TZ)) == date(2026, 8, 3)
    assert daily_price_cutoff_date(datetime(2026, 8, 4, 18, 30, tzinfo=TZ)) == date(2026, 8, 4)


def test_daily_close_cutoff_uses_istanbul_even_when_input_is_utc():
    # 15:30 UTC == 18:30 Europe/Istanbul in August.
    utc = ZoneInfo("UTC")
    assert daily_price_cutoff_date(datetime(2026, 8, 4, 15, 30, tzinfo=utc)) == date(2026, 8, 4)


def test_pipeline_clock_applies_one_market_cutoff_to_all_daily_modules():
    analysis, market_asof = _resolve_pipeline_clock(
        date(2026, 8, 4), "2026-08-04T12:00:00+03:00"
    )
    assert analysis is not None
    assert market_asof == date(2026, 8, 3)


def test_pipeline_clock_uses_istanbul_date_for_utc_input():
    analysis, market_asof = _resolve_pipeline_clock(
        date(2026, 8, 4), "2026-08-03T21:30:00Z"
    )
    assert analysis is not None
    assert market_asof == date(2026, 8, 3)


def test_pipeline_clock_rejects_local_date_mismatch():
    with pytest.raises(ValueError, match="asof ile ayni"):
        _resolve_pipeline_clock(date(2026, 8, 3), "2026-08-03T21:30:00Z")


def test_bank_m2_asof_date_is_istanbul_local_date():
    # 21:30 UTC is already the next calendar day in Istanbul.
    valuation = run_bank_valuation(
        canonical(),
        BankValuationInputs(coe=0.3705, macro_cap=0.140135, band_width_shadow_mode=False),
    )
    valuation = dict(valuation)
    valuation["analysis_at"] = datetime(2026, 8, 3, 21, 30, tzinfo=ZoneInfo("UTC"))
    result = compute_bank_m2_score(valuation, BankM2Context(current_price=5.5))
    assert result["asof_date"] == date(2026, 8, 4)


def test_bank_m2_overrides_period_m2_and_keeps_inputs(monkeypatch):
    calls = []

    def fake_read_sql(sql, conn, params=None):
        calls.append((sql, params))
        if "FROM analytics.m2_period_comparison" in sql:
            return pd.DataFrame([
                {"ticker": "GARAN", "m2": 0.41, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
                {"ticker": "THYAO", "m2": 0.63, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
            ])
        if "FROM analytics.bank_m2_scores" in sql:
            return pd.DataFrame([
                {
                    "ticker": "GARAN", "m2": 0.77,
                    "m2_source": "BANK_TWO_AXIS_V47",
                    "m2_score_inputs": {"s_val_effective": 0.8, "s_lag_effective": 0.7},
                }
            ])
        raise AssertionError(sql)

    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(
        object(), date(2026, 8, 4), bank_analysis_at="2026-08-04T12:00:00+03:00"
    ).set_index("ticker")
    assert out.loc["GARAN", "m2"] == pytest.approx(0.77)
    assert out.loc["GARAN", "m2_source"] == "BANK_TWO_AXIS_V47"
    assert out.loc["GARAN", "m2_score_inputs"]["s_val_effective"] == 0.8
    assert out.loc["THYAO", "m2"] == pytest.approx(0.63)
    assert len(calls) == 2
    assert "analysis_at <= %(analysis_at)s" in calls[1][0]


def test_period_m2_uses_market_cutoff_while_bank_m2_uses_analysis_date(monkeypatch):
    calls = []

    def fake_read_sql(sql, conn, params=None):
        calls.append((sql, params))
        return pd.DataFrame(columns=["ticker", "m2", "m2_source", "m2_score_inputs"])

    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    _compute_m2_from_period_comparison(
        object(),
        date(2026, 8, 4),
        period_asof=date(2026, 8, 3),
        bank_analysis_at="2026-08-04T12:00:00+03:00",
    )
    assert calls[0][1]["asof"] == date(2026, 8, 3)
    assert calls[1][1]["asof"] == date(2026, 8, 4)


def test_bank_m2_is_not_used_without_exact_analysis_cutoff(monkeypatch):
    calls = []

    def fake_read_sql(sql, conn, params=None):
        calls.append(sql)
        return pd.DataFrame([{
            "ticker": "GARAN", "m2": 0.41,
            "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None,
        }])

    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(object(), date(2026, 8, 4))
    assert out.iloc[0]["m2_source"] == "PERIOD_M2_V3"
    assert len(calls) == 1


class Cursor:
    def __init__(self):
        self.sql = None
        self.rows = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Conn:
    def __init__(self):
        self.cur = Cursor()

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_module_score_upsert_persists_m2_source_and_score_inputs(monkeypatch):
    captured = {}

    def fake_execute_values(cur, sql, rows, page_size):
        captured["sql"] = sql
        captured["rows"] = rows
        captured["page_size"] = page_size

    monkeypatch.setattr("src.analytics.run_daily_pipeline.execute_values", fake_execute_values)
    df = pd.DataFrame([{
        "ticker": "GARAN", "period_end": date(2026, 6, 30),
        "m1": 0.8, "m2": 0.77, "m3": 0.6,
        "m2_source": "BANK_TWO_AXIS_V47",
        "m2_score_inputs": {"s_val_effective": 0.8, "s_lag_effective": 0.7},
        "ek1": 0.4, "ek4": 0.5, "ek9": 0.6,
        "base_score": 0.7, "final_score": 0.7,
        "good_count_ge8": 7, "decision": "AL", "veto_flag": False,
    }])
    _upsert_module_scores(Conn(), df, date(2026, 8, 4), 63)
    assert "m2_source" in captured["sql"]
    assert "m2_score_inputs" in captured["sql"]
    row = captured["rows"][0]
    assert row[7] == "BANK_TWO_AXIS_V47"
    assert '"s_val_effective": 0.8' in row[8]


def test_module_score_upsert_records_analysis_time_and_rejects_older_overwrite(monkeypatch):
    captured = {}

    def fake_execute_values(cur, sql, rows, page_size):
        captured["sql"] = " ".join(sql.split())
        captured["rows"] = rows

    monkeypatch.setattr("src.analytics.run_daily_pipeline.execute_values", fake_execute_values)

    # V22-A: _upsert_module_scores artik analysis_at doluysa AYRICA
    # module_production_lineage'e fan-out yazar (persist_producer_lineage,
    # src/analytics/module_producer_lineage.py). O yol psycopg2.extras
    # .execute_values'i DOGRUDAN cagirir (run_daily_pipeline.execute_values
    # ile AYNI baglanti degildir); bu yuzden GERCEK psycopg2 kurulu bir
    # ortamda gercek execute_values calisip sahte Cursor'da patlar. Test
    # mock'u bu YENI yan etkiyi de taklit etmelidir -- uretim kodu "gercek
    # baglanti mi" diye SORMAMALIDIR (bu, test double'lari yuzunden uretim
    # davranisini degistirir ve yeni bir sessiz-skip sinifi yaratirdi).
    lineage_captured = {}

    def fake_lineage_execute_values(cur, sql, argslist, template=None,
                                    page_size=100, fetch=False):
        lineage_captured["sql"] = " ".join(sql.split())
        lineage_captured["rows"] = list(argslist)

    monkeypatch.setattr("psycopg2.extras.execute_values",
                        fake_lineage_execute_values)

    analysis_at = datetime(2026, 8, 4, 19, 0, tzinfo=TZ)
    df = pd.DataFrame([{
        "ticker": "GARAN", "period_end": date(2026, 6, 30),
        "m1": 0.8, "m2": 0.77, "m3": 0.6,
        "m2_source": "BANK_TWO_AXIS_V47", "m2_score_inputs": {},
        "ek1": 0.4, "ek4": 0.5, "ek9": 0.6,
        "base_score": 0.7, "final_score": 0.7,
        "good_count_ge8": 7, "decision": "AL", "veto_flag": False,
    }])
    _upsert_module_scores(
        Conn(), df, date(2026, 8, 4), 63,
        analysis_at=analysis_at, source_run_key="a" * 64,
    )
    assert captured["rows"][0][-2:] == (analysis_at, "a" * 64)
    assert "EXCLUDED.analysis_at >= analytics.module_scores.analysis_at" in captured["sql"]

    # Fan-out: GARAN icin BES modul lineage satiri (M2 HARIC), hepsi ayni
    # analysis_at ve source_run_key ile.
    assert len(lineage_captured["rows"]) == 5
    moduller = {r[1] for r in lineage_captured["rows"]}
    assert moduller == {"M1", "M3", "Ek1", "Ek4", "Ek9"}
    assert all(r[0] == "GARAN" for r in lineage_captured["rows"])
    assert all(r[2] == analysis_at for r in lineage_captured["rows"])
    assert all(r[4] == "a" * 64 for r in lineage_captured["rows"])


def test_nonfin_m2_overrides_period_but_bank_has_final_precedence(monkeypatch):
    calls = []

    def fake_read_sql(sql, conn, params=None):
        calls.append((sql, params))
        if "FROM analytics.m2_period_comparison" in sql:
            return pd.DataFrame([
                {"ticker": "THYAO", "m2": 0.40, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
                {"ticker": "GARAN", "m2": 0.40, "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None},
            ])
        if "FROM analytics.nonfin_m2_scores" in sql:
            return pd.DataFrame([
                {"ticker": "THYAO", "m2": 0.72, "m2_source": "NONFIN_RELATIVE_TWO_AXIS_V1", "m2_score_inputs": {"x": 1}},
                {"ticker": "GARAN", "m2": 0.55, "m2_source": "NONFIN_RELATIVE_TWO_AXIS_V1", "m2_score_inputs": {"x": 2}},
            ])
        if "FROM analytics.bank_m2_scores" in sql:
            return pd.DataFrame([
                {"ticker": "GARAN", "m2": 0.80, "m2_source": "BANK_TWO_AXIS_V47", "m2_score_inputs": {"x": 3}},
            ])
        raise AssertionError(sql)

    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(
        object(), date(2026, 8, 4),
        bank_analysis_at="2026-08-04T12:00:00+03:00",
        nonfin_analysis_at="2026-08-04T12:00:00+03:00",
    ).set_index("ticker")
    assert out.loc["THYAO", "m2"] == pytest.approx(0.72)
    assert out.loc["THYAO", "m2_source"] == "NONFIN_RELATIVE_TWO_AXIS_V1"
    assert out.loc["GARAN", "m2"] == pytest.approx(0.80)
    assert out.loc["GARAN", "m2_source"] == "BANK_TWO_AXIS_V47"
    assert len(calls) == 3


def test_nonfin_m2_is_not_used_without_exact_analysis_cutoff(monkeypatch):
    calls = []

    def fake_read_sql(sql, conn, params=None):
        calls.append(sql)
        return pd.DataFrame([{
            "ticker": "THYAO", "m2": 0.41,
            "m2_source": "PERIOD_M2_V3", "m2_score_inputs": None,
        }])

    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    out = _compute_m2_from_period_comparison(object(), date(2026, 8, 4))
    assert out.iloc[0]["m2_source"] == "PERIOD_M2_V3"
    assert len(calls) == 1
