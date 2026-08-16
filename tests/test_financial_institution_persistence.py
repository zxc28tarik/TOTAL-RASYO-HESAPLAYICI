"""
Finansal kurulus kalicilik sozlesmesi.

EN KRITIK MADDE: `with conn:` psycopg2'de islemi COMMIT eden yapidir.
Yalnizca `with conn.cursor()` kullanilirsa satirlar yazilmis GORUNUR
(persisted_count doner) ama commit edilmez ve baglanti kapaninca kaybolur --
sessizce yanlis basari raporu. Ayrica hepsi-ya-hicbiri atomikligi de oradan gelir.
"""
import ast
import re
from pathlib import Path

import pytest

from src.ingest.financial_institution_metrics import (
    FinancialInstitutionMetricsIngestError,
    FinancialInstitutionMetricsRecord,
    persist_financial_institution_metrics_records,
)

SHA = "a" * 64


def kayit(ticker="FAKT1", **kw):
    data = {
        "ticker": ticker, "period_end": "2025-12-31",
        "published_at": "2026-02-13T10:00:00+03:00",
        "business_type": "FACTORING", "accounting_profile": "TFRS",
        "accounting_version": 1, "currency": "TRY", "shares_out": "200",
        "share_basis": "ADJ", "total_equity": "1000", "average_equity": "950",
        "net_income_ttm": "180", "total_assets": "5000",
        "finance_receivables": "4200", "npl_gross": "150", "provisions": "120",
        "source_type": "KAP", "source_document_id": f"KAP-{ticker}",
        "source_sha256": SHA, "metrics_profile": "FI_METRICS_V1", "metrics_version": 1,
    }
    data.update(kw)
    return FinancialInstitutionMetricsRecord.from_mapping(data)


class Cursor:
    def __init__(self, gunluk): self.gunluk = gunluk
    def execute(self, sql, params=None): self.gunluk.append(("execute", sql, params))
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Conn:
    """psycopg2 benzeri: `with conn:` girisi/cikisi kaydedilir."""
    def __init__(self): self.gunluk = []
    def cursor(self): return Cursor(self.gunluk)
    def __enter__(self): self.gunluk.append(("conn_enter", None, None)); return self
    def __exit__(self, *a): self.gunluk.append(("conn_exit", None, None)); return False


# ------------------------------------------------ commit/atomiklik sozlesmesi
def test_kalicilik_islem_baglaminda_calisir():
    """`with conn:` OLMADAN satirlar commit edilmez; bu test onu kilitler."""
    conn = Conn()
    sayi = persist_financial_institution_metrics_records(conn, [kayit("A"), kayit("B")])
    assert sayi == 2
    olaylar = [ad for ad, _, _ in conn.gunluk]
    assert olaylar[0] == "conn_enter", "islem baglami ACILMALI (commit buradan gelir)"
    assert olaylar[-1] == "conn_exit", "islem baglami KAPANMALI"
    assert olaylar.count("execute") == 2


def test_kaynak_kodunda_with_conn_var():
    """
    Kaynak duzeyinde de kilitlenir: `with conn.cursor()` tek basina yeterli
    degildir ve gozle fark edilmesi zordur.
    """
    kaynak = Path("src/ingest/financial_institution_metrics.py").read_text(encoding="utf-8")
    govde = kaynak[kaynak.index("def persist_financial_institution_metrics_records"):]
    assert re.search(r"^\s{4}with conn:\s*$", govde, re.M), (
        "persist fonksiyonu `with conn:` islem baglami kullanmali"
    )


def test_bos_liste_islem_acmaz():
    conn = Conn()
    assert persist_financial_institution_metrics_records(conn, []) == 0
    assert conn.gunluk == []


def test_yanlis_tip_reddedilir():
    with pytest.raises(FinancialInstitutionMetricsIngestError):
        persist_financial_institution_metrics_records(Conn(), [{"ticker": "X"}])


def test_kanonik_olmayan_kayit_reddedilir():
    """Yeniden kurulum ayni nesneyi vermeli; vermiyorsa kayit bozuktur."""
    bozuk = kayit("A")
    object.__setattr__(bozuk, "ticker", "a-kucuk-harf")
    with pytest.raises(FinancialInstitutionMetricsIngestError, match="kanonik"):
        persist_financial_institution_metrics_records(Conn(), [bozuk])


def test_idempotent_insert_kullanilir():
    kaynak = Path("src/ingest/financial_institution_metrics.py").read_text(encoding="utf-8")
    assert "ON CONFLICT (metrics_id) DO NOTHING" in kaynak


# ------------------------------------------------ diger hatlarla tutarlilik
@pytest.mark.parametrize("modul,fonksiyon", [
    ("src/ingest/financial_institution_metrics.py", "persist_financial_institution_metrics_records"),
    ("src/ingest/insurance_metrics.py", "persist_insurance_metrics_records"),
])
def test_tum_alim_hatlari_islem_baglami_kullanir(modul, fonksiyon):
    """Yeni bir alim hatti eklendiginde ayni sozlesme aranir."""
    kaynak = Path(modul).read_text(encoding="utf-8")
    govde = kaynak[kaynak.index(f"def {fonksiyon}"):]
    assert re.search(r"^\s{4}with conn:\s*$", govde, re.M), f"{fonksiyon} `with conn:` kullanmali"


def test_batch_hatti_da_islem_baglami_kullanir():
    kaynak = Path("src/analytics/financial_institution_batch_pipeline.py").read_text(encoding="utf-8")
    govde = kaynak[kaynak.index("def persist_financial_institution_batch"):]
    assert re.search(r"^\s{4}with conn:\s*$", govde, re.M)
