"""
Finansal kurulus hattinda dayaniklilik ve karisma sozlesmeleri.

Kapsam:
  - eksik fiyat/metrikli TEK sirket butun batch'i dusurmemeli
  - pd.NA / NaN / sonsuz / bos metin sinir davranislari
  - muhasebe profili, metrik profili, para birimi ve pay bazi emsallerde KARISMAMALI
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from price_level_fixtures import certify_frames

from src.analytics.financial_institution_batch_pipeline import (
    FinancialInstitutionBatchError,
    build_financial_institution_snapshots_from_frames,
)
from src.analytics.financial_institution_valuation import (
    FinancialInstitutionValuationConfig,
    FinancialInstitutionValuationError,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    build_financial_institution_snapshot,
    evaluate_financial_institution_batch,
    value_financial_institution_snapshot,
)

TZ = ZoneInfo("Europe/Istanbul")
ANALIZ = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)
DONEM = date(2025, 12, 31)
SHA = "a" * 64


def cfg(**kw):
    data = {
        "valuation_profile": "FI_PB_PE_V1", "valuation_version": 1,
        "source_metrics_profile": "FI_METRICS_V1", "source_metrics_version": 1,
        "accounting_profile": "TFRS", "accounting_version": 1, "share_basis": "ADJ",
    }
    data.update(kw)
    return FinancialInstitutionValuationConfig.from_dict(data)


def snap(ticker, *, price=10.0, equity=1000.0, net_income=180.0, tip="FACTORING", **kw):
    data = dict(
        ticker=ticker, analysis_at=ANALIZ, business_type=tip, currency="TRY",
        share_basis="ADJ", current_price=price, price_trade_date=date(2026, 2, 27),
        period_end=DONEM, published_at=datetime(2026, 2, 13, tzinfo=TZ),
        total_equity=equity, average_equity=equity * 0.95, net_income_ttm=net_income,
        total_assets=equity * 5, finance_receivables=equity * 4.2, shares_out=200.0,
        source_confidence=0.9, source_document_id=f"KAP-{ticker}", source_sha256=SHA,
        metrics_profile="FI_METRICS_V1", metrics_version=1,
        accounting_profile="TFRS", accounting_version=1,
        npl_gross=equity * 0.15, provisions=equity * 0.12,
        net_finance_income_ttm=equity * 0.42, operating_expenses_ttm=equity * 0.14,
    )
    data.update(kw)
    return build_financial_institution_snapshot(**data)


# ------------------------------------------------ tek sirket batch'i dusurmez
def frames(eksik_fiyat=(), eksik_metrik=()):
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    universe = pd.DataFrame({"ticker": tickers, "sector_family": ["FINANCIAL"] * 4})
    metrics = pd.DataFrame([
        {
            "ticker": t, "period_end": DONEM,
            "published_at": datetime(2026, 2, 13, tzinfo=TZ),
            "business_type": "FACTORING", "accounting_profile": "TFRS",
            "accounting_version": 1, "currency": "TRY", "shares_out": 200,
            "share_basis": "ADJ", "total_equity": 1000, "average_equity": 950,
            "net_income_ttm": 180, "total_assets": 5000, "finance_receivables": 4200,
            "npl_gross": 150, "provisions": 120, "net_finance_income_ttm": 420,
            "funding_cost_ttm": 300, "operating_expenses_ttm": 140,
            "capital_adequacy_ratio": 0.18, "source_confidence": 0.9,
            "source_document_id": f"DOC-{t}", "source_sha256": SHA,
            "metrics_profile": "FI_METRICS_V1", "metrics_version": 1,
        }
        for t in tickers if t not in eksik_metrik
    ])
    prices = pd.DataFrame([
        {"ticker": t, "price_trade_date": date(2026, 2, 27), "current_price": 10.0}
        for t in tickers if t not in eksik_fiyat
    ])
    certify_frames(metrics, prices, ANALIZ, "period_end")
    return universe, metrics, prices


def test_eksik_metrikli_tek_sirket_batchi_dusurmez():
    universe, metrics, prices = frames(eksik_metrik=("BBB",))
    snapshots, rejections = build_financial_institution_snapshots_from_frames(
        universe=universe, metrics=metrics, prices=prices, analysis_at=ANALIZ
    )
    assert [s.ticker for s in snapshots] == ["AAA", "CCC", "DDD"]
    assert rejections == [{"ticker": "BBB", "reason": "FINANSAL_KURULUS_METRIKLERI_YOK"}]


def test_eksik_fiyatli_tek_sirket_batchi_dusurmez():
    universe, metrics, prices = frames(eksik_fiyat=("CCC",))
    snapshots, rejections = build_financial_institution_snapshots_from_frames(
        universe=universe, metrics=metrics, prices=prices, analysis_at=ANALIZ
    )
    kalan = [s.ticker for s in snapshots]
    assert "CCC" not in kalan
    assert len(kalan) == 3
    assert any(r["ticker"] == "CCC" for r in rejections)


def test_bozuk_tek_sirket_digerlerinin_degerlemesini_engellemez():
    """Bir sirket YETERSIZ_VERI alsa bile digerleri OK almaya devam eder."""
    kayitlar = [snap("AAA"), snap("BBB", price=9.0, equity=900.0, net_income=150.0),
                snap("CCC", price=12.0, equity=1100.0, net_income=210.0),
                snap("DDD", currency="USD")]          # bu sirket reddedilecek
    out = evaluate_financial_institution_batch(kayitlar, config=cfg(), follow_contexts={})
    durum = {r["ticker"]: r["valuation"]["status"] for r in out["results"]}
    assert durum["DDD"] == STATUS_INSUFFICIENT
    assert durum["AAA"] == STATUS_OK
    assert out["result_count"] == 4, "reddedilen sirket de raporda kalir"


# ------------------------------------------------ eksik deger sinirlari
@pytest.mark.parametrize("bos", [None, float("nan"), pd.NA])
def test_opsiyonel_alanda_eksik_deger_kabul_edilir(bos):
    """None/NaN/pd.NA opsiyonel alanda 'bilgi yok' demektir, hata degil."""
    s = snap("AAA", npl_gross=None, provisions=None, capital_adequacy_ratio=bos)
    assert s.capital_adequacy_ratio is None
    assert s.npl_ratio is None


@pytest.mark.parametrize("bozuk", [float("inf"), float("-inf")])
def test_sonsuz_deger_reddedilir(bozuk):
    with pytest.raises(FinancialInstitutionValuationError):
        snap("AAA", current_price=bozuk)
    with pytest.raises(FinancialInstitutionValuationError):
        snap("AAA", total_equity=bozuk)


@pytest.mark.parametrize("bozuk", ["", "   "])
def test_bos_metin_reddedilir(bozuk):
    for alan in ("currency", "share_basis", "source_document_id"):
        with pytest.raises(FinancialInstitutionValuationError):
            snap("AAA", **{alan: bozuk})


def test_cok_buyuk_tamsayi_kontrollu_reddedilir():
    with pytest.raises(FinancialInstitutionValuationError):
        snap("AAA", total_equity=10 ** 10000)


def test_numpy_bool_sayi_olarak_gecemez():
    np = pytest.importorskip("numpy")
    for deger in (np.bool_(True), np.bool_(False), True, False):
        with pytest.raises(FinancialInstitutionValuationError):
            snap("AAA", current_price=deger)


def test_numpy_sayisal_tipler_calisir():
    np = pytest.importorskip("numpy")
    s = snap("AAA", current_price=np.float64(10.0), shares_out=np.int64(200))
    assert s.current_price == pytest.approx(10.0)


# ------------------------------------------------ emsal karismasi
@pytest.mark.parametrize("alan,deger,neden", [
    ("currency", "USD", "CURRENCY_MISMATCH"),
    ("share_basis", "RAW", "SHARE_BASIS_MISMATCH"),
    ("accounting_profile", "ESKI", "ACCOUNTING_PROFILE_MISMATCH"),
    ("metrics_profile", "BASKA_V1", "METRICS_PROFILE_MISMATCH"),
    ("accounting_version", 2, "ACCOUNTING_PROFILE_MISMATCH"),
    ("metrics_version", 2, "METRICS_PROFILE_MISMATCH"),
])
def test_emsal_profil_karismasi_dislanir(alan, deger, neden):
    """Farkli muhasebe/metrik profili, para birimi veya pay bazi emsale KARISMAZ."""
    kirli = snap("FI9", **{alan: deger})
    r = value_financial_institution_snapshot(
        snap("AAA"),
        [snap("BBB", price=9.0, equity=900.0, net_income=150.0),
         snap("CCC", price=12.0, equity=1100.0, net_income=210.0), kirli],
        cfg(),
    )
    assert r["diagnostics"]["excluded_peers"]["FI9"] == neden
    assert "FI9" not in r["diagnostics"]["peer_tickers"]


def test_farkli_donem_emsali_dislanir():
    eski = snap("FI9", period_end=date(2025, 9, 30))
    r = value_financial_institution_snapshot(
        snap("AAA"),
        [snap("BBB", price=9.0, equity=900.0, net_income=150.0),
         snap("CCC", price=12.0, equity=1100.0, net_income=210.0), eski],
        cfg(),
    )
    assert r["diagnostics"]["excluded_peers"]["FI9"] == "PERIOD_MISMATCH"


def test_alt_grup_karismasi_SERT_hata():
    """Profil karismasi 'dislama', alt grup karismasi HATA'dir."""
    with pytest.raises(FinancialInstitutionValuationError, match="business_type uyusmuyor"):
        value_financial_institution_snapshot(
            snap("AAA", tip="FACTORING"), [snap("BBB", tip="LEASING")], cfg()
        )


def test_dislama_nedenleri_ret_defterinde_kaybolmaz():
    kirli = [snap("X1", currency="USD"), snap("X2", share_basis="RAW"),
             snap("X3", period_end=date(2025, 9, 30))]
    r = value_financial_institution_snapshot(
        snap("AAA"),
        [snap("BBB", price=9.0, equity=900.0, net_income=150.0),
         snap("CCC", price=12.0, equity=1100.0, net_income=210.0), *kirli],
        cfg(),
    )
    assert set(r["diagnostics"]["excluded_peers"]) == {"X1", "X2", "X3"}
    assert r["status"] == STATUS_OK, "kirli emsaller elenince temizlerle degerleme surer"


# ------------------------------------------------ sistemik eksik-deger regresyonu
@pytest.mark.parametrize("mod,fn", [
    ("src.analytics.financial_institution_valuation", "_optional_finite"),
    ("src.analytics.insurance_valuation", "_optional_finite"),
    ("src.analytics.nonfin_valuation", "_optional_number"),
])
@pytest.mark.parametrize("bos", [None, float("nan")])
def test_tum_motorlarda_opsiyonel_alan_eksik_degeri_tanir(mod, fn, bos):
    """
    SISTEMIK HATA REGRESYONU: opsiyonel alan yardimcilari yalniz `is None`
    kontrol ediyordu. PostgreSQL NULL -> pandas NaN/pd.NA donusumunde bu
    alanlar hata veriyordu.
    """
    import importlib
    fonksiyon = getattr(importlib.import_module(mod), fn)
    assert fonksiyon("x", bos) is None


@pytest.mark.parametrize("mod,fn", [
    ("src.analytics.financial_institution_valuation", "_optional_finite"),
    ("src.analytics.insurance_valuation", "_optional_finite"),
    ("src.analytics.nonfin_valuation", "_optional_number"),
])
def test_tum_motorlarda_pandas_na_taninir(mod, fn):
    import importlib
    fonksiyon = getattr(importlib.import_module(mod), fn)
    assert fonksiyon("x", pd.NA) is None


@pytest.mark.parametrize("mod,fn", [
    ("src.analytics.financial_institution_valuation", "_optional_finite"),
    ("src.analytics.insurance_valuation", "_optional_finite"),
    ("src.analytics.nonfin_valuation", "_optional_number"),
])
def test_eksik_deger_ile_gecersiz_deger_ayrimi_korunur(mod, fn):
    """inf ve bool EKSIK degildir; hata vermeye devam etmeli."""
    import importlib
    fonksiyon = getattr(importlib.import_module(mod), fn)
    for bozuk in (float("inf"), True, "abc"):
        with pytest.raises(Exception):
            fonksiyon("x", bozuk)
