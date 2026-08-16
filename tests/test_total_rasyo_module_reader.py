"""
Point-in-time modul okuma sozlesmesi testleri.

Kilitlenen davranislar:
  - m2 SELECT edilmez (cift sayim yasagi)
  - gelecek kayit sizmaz
  - eksik modul tahmin EDILMEZ, None kalir
  - good_count eksikse SIFIR VARSAYILMAZ
  - inf/bool GECERSIZ, NaN/pd.NA/None EKSIK
  - kaynakta fillna YOKTUR
"""
from __future__ import annotations

import ast
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.analytics.total_rasyo_module_reader import (
    DEFAULT_HORIZON_DAYS,
    MODULE_SOURCE_TYPE,
    READ_MODULE_KEYS,
    CompanyModuleContext,
    ModuleReadError,
    absent_module_context,
    daily_price_cutoff_date,
    fetch_module_context,
)

TZ = ZoneInfo("Europe/Istanbul")
ANALIZ = datetime(2026, 8, 5, 20, 0, tzinfo=TZ)
KAYNAK = Path("src/analytics/total_rasyo_module_reader.py")


class SahteCursor:
    def __init__(self, rows, gunluk):
        self._rows = rows
        self._gunluk = gunluk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._gunluk.append((sql, params))

    def fetchall(self):
        return list(self._rows)


class SahteConn:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.gunluk: list = []

    def cursor(self):
        return SahteCursor(self.rows, self.gunluk)


def satir(ticker="GARAN", *, asof=date(2026, 8, 5), analysis_at=None,
          m1=0.6, m3=0.7, ek4=0.5, ek1=0.4, ek9=0.3, good=9):
    if analysis_at is None:
        analysis_at = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
    return (ticker, asof, analysis_at, m1, m3, ek4, ek1, ek9, good)


# ===================================================== cift sayim yasagi
def test_m2_hic_select_edilmez():
    """
    M2'nin otoritatif kaynagi SEKTOR MOTORUDUR. module_scores.m2 ayni sinyali
    ikinci kez tasir; SELECT listesine girerse cift sayim riski dogar.
    """
    kaynak = KAYNAK.read_text(encoding="utf-8")
    govde = kaynak[kaynak.index("MODULE_SCORES_PIT_SQL"):]
    sql = govde[:govde.index('"""', govde.index('"""') + 3)]
    assert not re.search(r"\bms\.m2\b", sql), "SQL m2 sutununu okumamali"
    assert not re.search(r"^\s*m2\s*,", sql, re.M)
    assert "M2" not in READ_MODULE_KEYS


def test_okunan_modul_kumesi_besli():
    assert READ_MODULE_KEYS == ("M1", "M3", "Ek4", "Ek1", "Ek9")


# ===================================================== tahmin uretmeme
def test_kaynak_fillna_kullanmaz():
    """
    run_daily_pipeline.py m1=0.0 / m3=0.5 / ek1=0.0 / ek4=0.5 / ek9=0.5
    ile dolduruyor -- bu skor UYDURMAKTIR. Okuyucu bunu yapmamali.
    """
    agac = ast.parse(KAYNAK.read_text(encoding="utf-8"))
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.Attribute) and dugum.attr == "fillna":
            pytest.fail("modul okuyucu fillna kullanmamali")


def test_eksik_modul_none_kalir():
    conn = SahteConn([satir(m1=None, ek9=None)])
    ctx = fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)["GARAN"]
    assert ctx.components["M1"].score is None
    assert ctx.components["M1"].missing is True
    assert ctx.components["M1"].reason == "M1_KAYNAGI_YOK"
    assert ctx.components["Ek9"].missing is True
    assert ctx.components["M3"].score == pytest.approx(0.7)
    assert ctx.components["M3"].missing is False
    assert set(ctx.missing_keys()) == {"M1", "Ek9"}


def test_eksik_modulun_kaynagi_da_bos():
    conn = SahteConn([satir(ek4=None)])
    ctx = fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)["GARAN"]
    assert ctx.components["Ek4"].source_at is None
    assert ctx.components["Ek4"].source_type is None
    assert ctx.components["M1"].source_type == MODULE_SOURCE_TYPE


def test_kaydi_olmayan_sirket_tum_moduller_eksik():
    ctx = absent_module_context("xyz")
    assert ctx.ticker == "XYZ"
    assert all(ctx.components[k].missing for k in READ_MODULE_KEYS)
    assert ctx.good_count_missing is True
    assert set(ctx.missing_keys()) == set(READ_MODULE_KEYS) | {"good_count_ge8"}


# ===================================================== veto girdisi
def test_good_count_eksikse_sifir_varsayilmaz():
    """
    Sifir varsaymak good_count < esik kosulunu dogurur ve skoru 0.60 ile
    carpar; yani eksik veri SESSIZCE CEZAYA donusur.
    """
    conn = SahteConn([satir(good=None)])
    ctx = fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)["GARAN"]
    assert ctx.good_count_ge8 is None
    assert ctx.good_count_missing is True
    assert ctx.good_count_reason == "GOOD_COUNT_KAYNAGI_YOK"
    assert "good_count_ge8" in ctx.missing_keys()


def test_good_count_sifir_eksik_degildir():
    conn = SahteConn([satir(good=0)])
    ctx = fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)["GARAN"]
    assert ctx.good_count_ge8 == 0
    assert ctx.good_count_missing is False


@pytest.mark.parametrize("bozuk", [True, False, 2.5, -1])
def test_good_count_gecersiz_degerler(bozuk):
    conn = SahteConn([satir(good=bozuk)])
    with pytest.raises(ModuleReadError):
        fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)


# ===================================================== eksik / gecersiz siniri
def test_nan_ve_pdna_eksik_sayilir():
    import numpy as np
    import pandas as pd
    conn = SahteConn([satir(m1=np.nan, m3=pd.NA)])
    ctx = fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)["GARAN"]
    assert ctx.components["M1"].missing is True
    assert ctx.components["M3"].missing is True


@pytest.mark.parametrize("bozuk", [float("inf"), float("-inf"), True, False, "", "abc", 1.5, -0.1])
def test_gecersiz_deger_hata(bozuk):
    """inf ve bool EKSIK DEGILDIR; gecersizdir ve reddedilir."""
    conn = SahteConn([satir(m1=bozuk)])
    with pytest.raises(ModuleReadError):
        fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)


# ===================================================== zaman kurali
def test_sql_gelecek_kaydi_filtreler():
    conn = SahteConn([satir()])
    fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)
    sql, params = conn.gunluk[0]
    assert "ms.analysis_at <= %(analysis_at)s" in sql
    assert "ms.asof_date <= %(context_asof)s" in sql
    assert params["analysis_at"] == ANALIZ
    assert params["context_asof"] == date(2026, 8, 5)
    assert params["horizon_days"] == DEFAULT_HORIZON_DAYS


def test_gelecek_analysis_at_ikinci_katmanda_yakalanir():
    """SQL atlansa bile Python katmani gelecek kaydi reddetmeli."""
    gelecek = ANALIZ + timedelta(hours=1)
    conn = SahteConn([satir(analysis_at=gelecek)])
    with pytest.raises(ModuleReadError, match="gelecekteki module_scores"):
        fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)


def test_gelecek_asof_date_reddedilir():
    conn = SahteConn([satir(asof=date(2026, 8, 6))])
    with pytest.raises(ModuleReadError, match="gelecekteki asof_date"):
        fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)


def test_bayatlik_penceresi_parametreye_baglanir():
    conn = SahteConn([satir()])
    fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ,
                         max_context_age_days=30)
    _, params = conn.gunluk[0]
    assert params["min_asof"] == date(2026, 8, 5) - timedelta(days=30)


def test_kesim_esitligi_ARANMAZ():
    """
    SOZLESME 3: birebir esitlik degil, kaynak_zamani <= analysis_at.
    Bir gun onceki kayit gecerlidir.
    """
    onceki = datetime(2026, 8, 4, 19, 0, tzinfo=TZ)
    conn = SahteConn([satir(asof=date(2026, 8, 4), analysis_at=onceki)])
    ctx = fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)["GARAN"]
    assert ctx.components["M1"].score == pytest.approx(0.6)
    assert ctx.components["M1"].source_at == onceki


def test_gercek_kaynak_zamani_gorunur():
    """SOZLESME 4: secilen bilesenin gercek kaynak zamani raporlanmali."""
    kaynak_at = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)
    conn = SahteConn([satir(asof=date(2026, 8, 3), analysis_at=kaynak_at)])
    ctx = fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)["GARAN"]
    for key in READ_MODULE_KEYS:
        assert ctx.components[key].source_at == kaynak_at
        assert ctx.components[key].source_type == MODULE_SOURCE_TYPE
    assert ctx.asof_date == date(2026, 8, 3)


# ===================================================== girdi dogrulama
def test_naive_datetime_reddedilir():
    with pytest.raises(ModuleReadError):
        fetch_module_context(SahteConn(), tickers=["X"],
                             analysis_at=datetime(2026, 8, 5, 20, 0))


def test_bos_ticker_listesi_sorgu_yapmaz():
    conn = SahteConn([satir()])
    assert fetch_module_context(conn, tickers=[], analysis_at=ANALIZ) == {}
    assert conn.gunluk == []


def test_ticker_normalize_ve_tekillestirilir():
    conn = SahteConn([satir()])
    fetch_module_context(conn, tickers=[" garan ", "GARAN", "akbnk"],
                         analysis_at=ANALIZ)
    _, params = conn.gunluk[0]
    assert params["tickers"] == ["GARAN", "AKBNK"]


def test_yinelenen_pit_satiri_reddedilir():
    conn = SahteConn([satir(), satir()])
    with pytest.raises(ModuleReadError, match="birden fazla point-in-time"):
        fetch_module_context(conn, tickers=["GARAN"], analysis_at=ANALIZ)


@pytest.mark.parametrize("bozuk", [0, -1, True, 1.5, "20"])
def test_gecersiz_horizon_reddedilir(bozuk):
    with pytest.raises(ModuleReadError):
        fetch_module_context(SahteConn(), tickers=["X"], analysis_at=ANALIZ,
                             horizon_days=bozuk)


def test_cutoff_istanbul_yerel_tarihi():
    utc_gec = datetime(2026, 8, 5, 22, 30, tzinfo=ZoneInfo("UTC"))
    assert daily_price_cutoff_date(utc_gec) == date(2026, 8, 6)
