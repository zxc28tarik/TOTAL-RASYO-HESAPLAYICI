"""
Banka disi finansal kurulus (faktoring / leasing / tuketici finansmani)
degerleme motoru testleri.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.analytics.financial_institution_valuation import (
    FinancialInstitutionValuationConfig,
    FinancialInstitutionValuationError,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    STATUS_TOO_WIDE,
    SUPPORTED_BUSINESS_TYPES,
    build_financial_institution_snapshot,
    combine_financial_institution_m2,
    evaluate_financial_institution_batch,
    value_financial_institution_snapshot,
)

TZ = ZoneInfo("Europe/Istanbul")
ANALIZ = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)
DONEM = date(2025, 12, 31)


def cfg(**kw):
    data = {
        "valuation_profile": "FI_PB_PE_V1",
        "valuation_version": 1,
        "source_metrics_profile": "FI_METRICS_V1",
        "source_metrics_version": 1,
        "accounting_profile": "TFRS",
        "accounting_version": 1,
        "share_basis": "ADJ",
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
        source_confidence=0.9, source_document_id=f"KAP-{ticker}",
        source_sha256="a" * 64, metrics_profile="FI_METRICS_V1", metrics_version=1,
        accounting_profile="TFRS", accounting_version=1,
        npl_gross=equity * 0.15, provisions=equity * 0.12,
        net_finance_income_ttm=equity * 0.42, operating_expenses_ttm=equity * 0.14,
    )
    data.update(kw)
    return build_financial_institution_snapshot(**data)


def emsaller(tip="FACTORING"):
    return [
        snap("FI2", price=9.0, equity=900.0, net_income=150.0, tip=tip),
        snap("FI3", price=12.0, equity=1100.0, net_income=210.0, tip=tip),
        snap("FI4", price=8.0, equity=950.0, net_income=140.0, tip=tip),
    ]


# ------------------------------------------------ alt grup sozlesmesi
def test_desteklenen_alt_gruplar():
    assert SUPPORTED_BUSINESS_TYPES == {"FACTORING", "LEASING", "CONSUMER_FINANCE"}


@pytest.mark.parametrize("tip", sorted(SUPPORTED_BUSINESS_TYPES))
def test_her_alt_grup_calisir(tip):
    r = value_financial_institution_snapshot(snap("FI1", tip=tip), emsaller(tip), cfg())
    assert r["status"] == STATUS_OK
    assert r["business_type"] == tip


def test_desteklenmeyen_alt_grup_reddedilir():
    with pytest.raises(FinancialInstitutionValuationError):
        snap("FI1", tip="BANK")


def test_farkli_alt_gruplar_ayni_emsal_havuzuna_girmez():
    """Faktoring ile leasing ayni gruba KONMAZ."""
    with pytest.raises(FinancialInstitutionValuationError, match="business_type uyusmuyor"):
        value_financial_institution_snapshot(
            snap("FI1", tip="FACTORING"), emsaller("LEASING"), cfg()
        )


def test_batch_alt_gruplari_ayirir():
    kayitlar = [
        snap("FAK1", tip="FACTORING"), snap("FAK2", price=9.0, equity=900.0, net_income=150.0, tip="FACTORING"),
        snap("FAK3", price=12.0, equity=1100.0, net_income=210.0, tip="FACTORING"),
        snap("LEA1", tip="LEASING"), snap("LEA2", price=9.0, equity=900.0, net_income=150.0, tip="LEASING"),
        snap("LEA3", price=12.0, equity=1100.0, net_income=210.0, tip="LEASING"),
    ]
    out = evaluate_financial_institution_batch(kayitlar, config=cfg(), follow_contexts={})
    assert out["result_count"] == 6
    for row in out["results"]:
        emsal = row["valuation"]["diagnostics"].get("peer_tickers", [])
        onek = row["ticker"][:3]
        assert all(p.startswith(onek) for p in emsal), f"{row['ticker']} emsalleri karismis: {emsal}"


# ------------------------------------------------ leave-one-out
def test_leave_one_out_ihlali_reddedilir():
    hedef = snap("FI1")
    with pytest.raises(FinancialInstitutionValuationError, match="leave-one-out"):
        value_financial_institution_snapshot(hedef, [snap("FI1"), *emsaller()], cfg())


def test_yinelenen_emsal_reddedilir():
    with pytest.raises(FinancialInstitutionValuationError, match="yinelenen peer"):
        value_financial_institution_snapshot(snap("FI1"), [snap("FI2"), snap("FI2")], cfg())


# ------------------------------------------------ ret nedenleri
@pytest.mark.parametrize("kw,neden", [
    ({"currency": "USD"}, "HEDEF_PARA_BIRIMI_UYUSMUYOR"),
    ({"share_basis": "RAW"}, "HEDEF_PAY_BAZI_UYUSMUYOR"),
    ({"accounting_profile": "ESKI"}, "HEDEF_MUHASEBE_PROFILI_UYUSMUYOR"),
    ({"price_trade_date": date(2026, 1, 1)}, "HEDEF_FIYAT_BAYAT"),
    ({"source_confidence": 0.1}, "HEDEF_KAYNAK_GUVENI_DUSUK"),
])
def test_hedef_ret_nedenleri(kw, neden):
    r = value_financial_institution_snapshot(snap("FI1", **kw), emsaller(), cfg())
    assert r["status"] == STATUS_INSUFFICIENT
    assert r["reason"] == neden


def test_bayat_finansal_bilgi_reddedilir():
    eski = date(2024, 12, 31)
    r = value_financial_institution_snapshot(
        snap("FI1", period_end=eski), [snap(f"FI{i}", period_end=eski) for i in (2, 3)], cfg()
    )
    assert r["status"] == STATUS_INSUFFICIENT
    assert r["reason"] == "HEDEF_FINANSAL_BILGI_BAYAT"


def test_ozkaynak_tamponu_yetersizse_reddedilir():
    """Asiri kaldiracli finansal kurulus degerlenmez."""
    r = value_financial_institution_snapshot(
        snap("FI1", total_assets=1000.0 * 40), emsaller(), cfg()
    )
    assert r["status"] == STATUS_INSUFFICIENT
    assert r["reason"] == "HEDEF_OZKAYNAK_TAMPONU_YETERSIZ"


def test_yetersiz_emsal_reddedilir():
    r = value_financial_institution_snapshot(snap("FI1"), [snap("FI2")], cfg())
    assert r["status"] == STATUS_INSUFFICIENT
    assert r["reason"] == "YETERSIZ_FINANSAL_KURULUS_EMSALI"
    assert r["diagnostics"]["peer_count"] == 1


def test_emsal_dislama_nedenleri_kaybolmaz():
    kotu = snap("FI9", currency="USD")
    r = value_financial_institution_snapshot(snap("FI1"), [*emsaller(), kotu], cfg())
    assert r["diagnostics"]["excluded_peers"]["FI9"] == "CURRENCY_MISMATCH"


# ------------------------------------------------ yontem secimi
def test_negatif_karda_fk_devre_disi():
    """Zarar eden sirkette F/K kullanilmaz, yalniz PD/DD kalir."""
    r = value_financial_institution_snapshot(snap("FI1", net_income=-50.0), emsaller(), cfg())
    assert r["status"] == STATUS_OK
    assert r["method_count"] == 1
    assert "PE" not in r["diagnostics"]["method_bands"]
    assert r["target_pe"] is None


def test_dusuk_roe_da_fk_devre_disi():
    """Kar pozitif ama surdurulebilir degilse F/K acilmaz."""
    r = value_financial_institution_snapshot(
        snap("FI1", net_income=5.0), emsaller(), cfg(minimum_pe_roe=0.02)
    )
    assert r["status"] == STATUS_OK
    assert r["method_count"] == 1


def test_pozitif_karda_iki_yontem():
    r = value_financial_institution_snapshot(snap("FI1"), emsaller(), cfg())
    assert r["method_count"] == 2
    assert set(r["diagnostics"]["method_bands"]) == {"PB", "PE"}


# ------------------------------------------------ band ve guven
def test_band_geometrisi_gecerli():
    r = value_financial_institution_snapshot(snap("FI1"), emsaller(), cfg())
    assert 0 < r["V_low"] <= r["V_mid"] <= r["V_high"]
    assert r["lower_halfwidth"] > 0 and r["upper_halfwidth"] > 0


def test_aktif_kalitesi_bandi_DEGISTIRMEZ():
    """
    SOZLESME: aktif kalitesi gostergeleri yalniz v_conf'a girer, fiyat bandini
    keyfi bicimde sismez. Bu, sigorta motorundaki teknik gosterge ilkesiyle ayni.
    """
    iyi = value_financial_institution_snapshot(
        snap("FI1", npl_gross=10.0, provisions=9.0), emsaller(), cfg()
    )
    kotu = value_financial_institution_snapshot(
        snap("FI1", npl_gross=600.0, provisions=60.0), emsaller(), cfg()
    )
    assert iyi["V_mid"] == pytest.approx(kotu["V_mid"])
    assert iyi["V_low"] == pytest.approx(kotu["V_low"])
    assert iyi["V_high"] == pytest.approx(kotu["V_high"])
    assert iyi["v_conf"] > kotu["v_conf"], "kotu aktif kalitesi GUVENI dusurmeli"


def test_eksik_opsiyonel_alan_tahmin_edilmez():
    az = snap("FI1", npl_gross=None, provisions=None,
              net_finance_income_ttm=None, operating_expenses_ttm=None)
    r = value_financial_institution_snapshot(az, emsaller(), cfg())
    assert r["status"] == STATUS_OK
    m = r["diagnostics"]["target_metrics"]
    assert m["npl_ratio"] is None and m["provision_coverage"] is None
    assert m["net_finance_margin"] is None and m["cost_to_income"] is None


def test_roe_ortalama_ozkaynak_uzerinden():
    """Donem ici sermaye artisi ROE'yi sismemeli."""
    s = snap("FI1", equity=1000.0, net_income=190.0)
    assert s.roe_ttm == pytest.approx(190.0 / 950.0)


def test_uc_emsal_carpani_bandi_sismez():
    """maximum_pb kapisi model araligi disindaki emsali eler."""
    uc = snap("FI9", price=40.0, equity=1100.0, net_income=210.0)   # PD/DD ~7.27
    r = value_financial_institution_snapshot(snap("FI1"), [*emsaller(), uc], cfg())
    assert r["diagnostics"]["excluded_peers"]["FI9"] == "PD_DD_MODEL_ARALIGI_DISINDA"


def test_band_cok_genisse_golge_mod_disinda_reddedilir():
    """
    Kullanilabilirlik tavani: gozlenen yari genislik max_halfwidth'i asarsa
    golge modda ISARETLENIR, sert modda REDDEDILIR.
    """
    genis = [snap("FI2", price=2.0, equity=1000.0, net_income=150.0),      # PD/DD 0.4
             snap("FI3", price=10.0, equity=1000.0, net_income=210.0),     # PD/DD 2.0
             snap("FI4", price=27.0, equity=1000.0, net_income=140.0)]     # PD/DD 5.4
    golge = value_financial_institution_snapshot(
        snap("FI1"), genis, cfg(max_halfwidth=0.30, band_width_shadow_mode=True))
    sert = value_financial_institution_snapshot(
        snap("FI1"), genis, cfg(max_halfwidth=0.30, band_width_shadow_mode=False))
    assert golge["diagnostics"]["aggregation"]["shadow_too_wide"] is True
    assert golge["status"] == STATUS_OK, "golge modda band URETILIR"
    assert sert["status"] == STATUS_TOO_WIDE
    assert sert["v_conf"] == 0.0
    assert sert["valuation_score"] == 0.5


# ------------------------------------------------ M2
def test_m2_iki_eksen():
    v = value_financial_institution_snapshot(snap("FI1"), emsaller(), cfg())
    m2 = combine_financial_institution_m2(v, follow_score=0.8, follow_active=True, config=cfg())
    c = cfg()
    beklenen = (c.valuation_axis_weight * (0.5 + (v["valuation_score"] - 0.5) * v["v_conf"])
                + c.follow_axis_weight * 0.8)
    assert m2["m2"] == pytest.approx(beklenen)
    assert m2["m2_source"] == "FINANCIAL_INSTITUTION_PB_PE_TWO_AXIS_V1"


def test_degerleme_kullanilamazsa_eksen_notrlesir():
    v = value_financial_institution_snapshot(snap("FI1", currency="USD"), emsaller(), cfg())
    m2 = combine_financial_institution_m2(v, follow_score=0.9, follow_active=True, config=cfg())
    assert m2["valuation_usable"] is False
    c = cfg()
    assert m2["m2"] == pytest.approx(c.valuation_axis_weight * 0.5 + c.follow_axis_weight * 0.9)


def test_follow_active_bool_olmali():
    v = value_financial_institution_snapshot(snap("FI1"), emsaller(), cfg())
    for bozuk in ("true", 1, None):
        with pytest.raises(FinancialInstitutionValuationError):
            combine_financial_institution_m2(v, follow_score=0.8, follow_active=bozuk, config=cfg())


def test_tani_alanlari_skora_sizmaz():
    v = value_financial_institution_snapshot(snap("FI1"), emsaller(), cfg())
    m2 = combine_financial_institution_m2(v, follow_score=0.5, follow_active=False, config=cfg())
    c = cfg()
    assert m2["m2"] == pytest.approx(
        c.valuation_axis_weight * (0.5 + (v["valuation_score"] - 0.5) * v["v_conf"])
        + c.follow_axis_weight * 0.5
    )


# ------------------------------------------------ config
def test_bilinmeyen_config_alani_reddedilir():
    with pytest.raises(FinancialInstitutionValuationError, match="bilinmeyen"):
        cfg(foo=1)


def test_agirlik_toplami_bir_olmali():
    with pytest.raises(FinancialInstitutionValuationError):
        cfg(pb_weight=0.5, pe_weight=0.2)
    with pytest.raises(FinancialInstitutionValuationError):
        cfg(valuation_axis_weight=0.5, follow_axis_weight=0.2)


def test_shadow_mode_python_bool_olmali():
    with pytest.raises(FinancialInstitutionValuationError):
        cfg(band_width_shadow_mode="false")


def test_config_sha_deterministik():
    assert cfg().config_sha256 == cfg().config_sha256
    assert cfg().config_sha256 != cfg(pb_weight=0.6, pe_weight=0.4).config_sha256


def test_dataclass_dogrudan_kurulumu_dogrulamayi_atlayamaz():
    """Config'i dogrudan kurup gecersiz deger vermek engellenmelidir."""
    bozuk = FinancialInstitutionValuationConfig(
        valuation_profile="X", valuation_version=1, source_metrics_profile="Y",
        source_metrics_version=1, accounting_profile="TFRS", accounting_version=1,
        share_basis="ADJ", pb_weight=0.9, pe_weight=0.9,
    )
    with pytest.raises(FinancialInstitutionValuationError):
        value_financial_institution_snapshot(snap("FI1"), emsaller(), bozuk)


# ------------------------------------------------ eksik deger sozlesmesi
@pytest.mark.parametrize("alan", ["total_equity", "current_price", "shares_out", "total_assets"])
def test_zorunlu_alan_bool_olamaz(alan):
    with pytest.raises(FinancialInstitutionValuationError):
        snap("FI1", **{alan: True})


def test_period_end_ceyrek_sonu_olmali():
    with pytest.raises(FinancialInstitutionValuationError, match="ceyrek sonu"):
        snap("FI1", period_end=date(2025, 11, 30))


def test_takip_alacagi_toplam_alacagi_asamaz():
    with pytest.raises(FinancialInstitutionValuationError):
        snap("FI1", npl_gross=1_000_000.0)


def test_ozkaynak_aktifi_asamaz():
    with pytest.raises(FinancialInstitutionValuationError):
        snap("FI1", equity=1000.0, total_assets=500.0)


# ------------------------------------------------ determinizm
def test_emsal_sirasi_sonucu_degistirmez():
    p = emsaller()
    a = value_financial_institution_snapshot(snap("FI1"), p, cfg())
    b = value_financial_institution_snapshot(snap("FI1"), list(reversed(p)), cfg())
    assert a["V_mid"] == pytest.approx(b["V_mid"])
    assert a["v_conf"] == pytest.approx(b["v_conf"])
    assert a["diagnostics"]["peer_tickers"] == b["diagnostics"]["peer_tickers"]


def test_batch_sonucu_deterministik():
    kayitlar = [snap("FI1"), *emsaller()]
    a = evaluate_financial_institution_batch(kayitlar, config=cfg(), follow_contexts={})
    b = evaluate_financial_institution_batch(list(reversed(kayitlar)), config=cfg(), follow_contexts={})
    assert [r["ticker"] for r in a["results"]] == [r["ticker"] for r in b["results"]]
    assert a["config_sha256"] == b["config_sha256"]


def test_beklenmeyen_follow_context_reddedilir():
    with pytest.raises(FinancialInstitutionValuationError, match="beklenmeyen follow_context"):
        evaluate_financial_institution_batch(
            [snap("FI1"), *emsaller()], config=cfg(), follow_contexts={"YOK": {}}
        )


# ------------------------------------------------ paketlenen config
def test_paketlenen_config_yuklenir():
    from pathlib import Path
    c = FinancialInstitutionValuationConfig.load(
        Path("config/financial_institution_valuation.pb_pe_v1.json")
    )
    assert c.valuation_profile == "FINANCIAL_INSTITUTION_PB_PE"
    assert c.pb_weight + c.pe_weight == pytest.approx(1.0)
    assert c.band_width_shadow_mode is True
    assert len(c.config_sha256) == 64
