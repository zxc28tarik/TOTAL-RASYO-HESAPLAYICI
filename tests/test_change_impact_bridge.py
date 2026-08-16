"""
Readiness bariyeri ve `targeted_tickers` koprusu testleri.

EN KRITIK: eski M1 + yeni M2 gibi sessiz karisim YASAK.
AMA: plan etkilenmedigini soyluyorsa eski M3/Ek4/Ek9 kullanmak DOGRU.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.analytics.change_impact_bridge import (
    APP_PARTIAL,
    APP_READY,
    NOT_READY,
    READY,
    REASON_GROUP_PARTIAL,
    REASON_NO_LINEAGE,
    REASON_STALE_SOURCE,
    REASON_STALE_TIME,
    BridgeError,
    ModuleLineage,
    application_counters,
    assert_no_plan_ticker_lost,
    bridge_targeted_tickers,
    evaluate_readiness,
)
from src.analytics.change_impact_detector import (
    FactChange,
    PeerCandidate,
    detect_change_impact,
)

TZ = timezone(timedelta(hours=3))
YAYIN = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)
KESIM = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
URETIM = datetime(2026, 3, 2, 12, 0, tzinfo=TZ)      # degisiklikten SONRA
ESKI_URETIM = datetime(2026, 2, 1, 12, 0, tzinfo=TZ)  # degisiklikten ONCE
SURUM = "V2"


def degisiklik(**kw) -> FactChange:
    veri = dict(ticker="GARFA", statement_type="BALANCE_SHEET",
                fact_key="total_equity", period_end=date(2025, 12, 31),
                old_value=1000.0, new_value=800.0, published_at=YAYIN,
                source_fact_id="F1", source_statement_id="S1",
                source_version_id=SURUM, routed_engine="FINANCIAL")
    veri.update(kw)
    return FactChange(**veri)


def plan_uret(**kw):
    havuz = {"FINANCIAL": [
        PeerCandidate("GARFA", True, False, 1.2, 0.9),
        PeerCandidate("PEER1", True, True, 1.0, 1.0)]}
    args = dict(impact_run_id="R1", peer_candidates=havuz, analysis_at=KESIM)
    args.update(kw)
    degisim = args.pop("change", degisiklik())
    return detect_change_impact(degisim, **args)


def soy(ticker, modul, *, surum=SURUM, uretim=URETIM, motor=None):
    return ModuleLineage(ticker=ticker, module=modul, engine_family=motor,
                         source_version_id=surum, produced_at=uretim,
                         calculation_profile="V1", calculation_version=1)


def tam_soy(plan, **kw):
    """Planin istedigi butun modulleri hazir gosterir."""
    kayitlar = []
    for ticker in plan.targeted_tickers():
        moduller = {e.module for e in plan.entries if e.impacted_ticker == ticker}
        # Atomik grup genislemesi de karsilansin.
        if moduller & {"M1", "Ek1", "GOOD_COUNT"}:
            moduller |= {"M1", "Ek1", "GOOD_COUNT"}
        for m in moduller:
            kayitlar.append(soy(ticker, m, **kw))
    return kayitlar


def degerlendir(plan, kayitlar, **kw):
    args = dict(expected_source_version_id=SURUM, change_published_at=YAYIN)
    args.update(kw)
    return evaluate_readiness(plan, kayitlar, **args)


# ============================================ 1) TAM HAZIR
def test_butun_girdiler_hazirsa_READY():
    p = plan_uret()
    r = degerlendir(p, tam_soy(p))
    assert r.overall_status() == APP_READY
    assert set(r.ready_tickers()) == set(p.targeted_tickers())
    assert r.not_ready_targets == 0


def test_hazir_ticker_orkestratore_gider():
    p = plan_uret()
    r = degerlendir(p, tam_soy(p))
    assert bridge_targeted_tickers(r) == r.ready_tickers()


# ============================================ 2) SESSIZ KARISIM YASAK
def test_ESKI_M1_YENI_M2_karisimi_engellenir():
    """EN KRITIK SENARYO."""
    p = plan_uret(change=degisiklik(statement_type="INCOME_STATEMENT",
                                    fact_key="net_income"))
    kayitlar = [k for k in tam_soy(p) if k.module != "M1"]
    kayitlar.append(soy("GARFA", "M1", surum="V1"))  # ESKI surum
    r = degerlendir(p, kayitlar)
    assert r.per_ticker["GARFA"].status == NOT_READY
    assert "M1" in r.per_ticker["GARFA"].missing_modules
    assert r.per_ticker["GARFA"].reasons["M1"] == REASON_STALE_SOURCE
    assert "GARFA" not in bridge_targeted_tickers(r)


def test_M1_guncel_Ek1_eski_engellenir():
    p = plan_uret(change=degisiklik(statement_type="INCOME_STATEMENT",
                                    fact_key="net_income"))
    kayitlar = [k for k in tam_soy(p) if k.module != "Ek1"]
    kayitlar.append(soy("GARFA", "Ek1", surum="V1"))
    r = degerlendir(p, kayitlar)
    assert r.per_ticker["GARFA"].status == NOT_READY
    assert "Ek1" in r.per_ticker["GARFA"].missing_modules


def test_GOOD_COUNT_eski_engellenir():
    """Veto girdisi bayat kalirsa skor sessizce yanlis cikar."""
    p = plan_uret(change=degisiklik(statement_type="INCOME_STATEMENT",
                                    fact_key="net_income"))
    kayitlar = [k for k in tam_soy(p) if k.module != "GOOD_COUNT"]
    kayitlar.append(soy("GARFA", "GOOD_COUNT", surum="V1"))
    r = degerlendir(p, kayitlar)
    assert r.per_ticker["GARFA"].status == NOT_READY


def test_lineage_kaydi_hic_yoksa_hazir_degil():
    """
    'module_scores satiri var' YETMEZ. Lineage kaydi yoksa o modulun bu
    revizyondan uretildigi KANITLANAMAZ.
    """
    p = plan_uret()
    r = degerlendir(p, [])
    assert r.ready_tickers() == ()
    assert all(v.status == NOT_READY for v in r.per_ticker.values())
    assert r.per_ticker["GARFA"].reasons["M2"] == REASON_NO_LINEAGE


def test_uretim_degisiklikten_ONCEYSE_hazir_degil():
    """
    Kaynak surumu dogru gorunse bile uretim zamani degisiklikten onceyse,
    o uretim bu revizyonu GOREMEZDI.
    """
    p = plan_uret()
    r = degerlendir(p, tam_soy(p, uretim=ESKI_URETIM))
    assert r.ready_tickers() == ()
    assert r.per_ticker["GARFA"].reasons["M2"] == REASON_STALE_TIME


def test_eski_M2ye_FALLBACK_yok():
    """Yeni M2 uretilemezse eski M2 ile devam EDILMEZ."""
    p = plan_uret()
    kayitlar = [k for k in tam_soy(p) if k.module != "M2"]
    kayitlar.append(soy("GARFA", "M2", surum="V1"))
    r = degerlendir(p, kayitlar)
    assert "GARFA" not in bridge_targeted_tickers(r)


# ============================================ 3) ATOMIK GRUP
def test_atomik_grup_kismi_hazirsa_ticker_calismaz():
    """1/3 veya 2/3 hazirsa INPUTS_NOT_READY."""
    p = plan_uret(change=degisiklik(statement_type="INCOME_STATEMENT",
                                    fact_key="net_income"))
    kayitlar = [k for k in tam_soy(p)
                if not (k.ticker == "GARFA" and k.module == "GOOD_COUNT")]
    r = degerlendir(p, kayitlar)
    assert r.per_ticker["GARFA"].status == NOT_READY
    assert "GOOD_COUNT" in r.per_ticker["GARFA"].missing_modules


def test_grup_uyesi_planda_olmasa_da_gerekli_olur():
    """
    Gruptan biri etkilendiyse UCU DE hazir olmali. Plan yalniz birini
    isaretlese bile digerleri gerekli hale gelir.
    """
    p = plan_uret(change=degisiklik(statement_type="INCOME_STATEMENT",
                                    fact_key="net_income"))
    gerekli = set(r for r in degerlendir(p, tam_soy(p))
                  .per_ticker["GARFA"].required_modules)
    assert {"M1", "Ek1", "GOOD_COUNT"} <= gerekli


def test_grup_eksikligi_ayri_neden_kodu():
    p = plan_uret(change=degisiklik(statement_type="INCOME_STATEMENT",
                                    fact_key="net_income"))
    # Yalniz M1 hazir; grup genislemesi digerlerini gerekli kilar.
    kayitlar = [soy("GARFA", "M1"), soy("GARFA", "M2"),
                soy("PEER1", "M2")]
    r = degerlendir(p, kayitlar)
    nedenler = set(r.per_ticker["GARFA"].reasons.values())
    assert nedenler & {REASON_NO_LINEAGE, REASON_GROUP_PARTIAL}


# ============================================ 4) ETKILENMEYEN MODULLER
def test_M3_Ek4_Ek9_gereksiz_yere_KAPIYA_TAKILMAZ():
    """
    'Eski' olmak tek basina sorun DEGILDIR. Registry bu modullerin finansal
    tablo degisikliginden etkilenmedigini kanitliyor; eski degerleri
    gecerlidir ve readiness onlari ISTEMEMELI.
    """
    p = plan_uret()
    r = degerlendir(p, tam_soy(p))
    for tr in r.per_ticker.values():
        assert "M3" not in tr.required_modules
        assert "Ek4" not in tr.required_modules
        assert "Ek9" not in tr.required_modules
    assert r.overall_status() == APP_READY


def test_fiyat_modulleri_lineage_olmadan_da_READY():
    """M3/Ek4/Ek9 icin hic lineage kaydi olmasa bile ticker hazir olabilir."""
    p = plan_uret()
    kayitlar = [k for k in tam_soy(p) if k.module in ("M2", "M1", "Ek1", "GOOD_COUNT")]
    r = degerlendir(p, kayitlar)
    assert r.overall_status() == APP_READY


# ============================================ 5) PEER-PROPAGATED
def test_peer_hedefin_M2si_yeniden_uretilmeli():
    """
    Hedefin kendi bilancosu degismemis olsa bile peer-dependent M2'si
    yeniden uretilmeli.
    """
    p = plan_uret()
    peer_kayitlari = [e for e in p.entries
                      if e.impacted_ticker == "PEER1" and e.impact_type == "PEER_PROPAGATED"]
    assert peer_kayitlari, "peer etkisi uretilmemis"
    kayitlar = [k for k in tam_soy(p) if k.ticker != "PEER1"]
    kayitlar.append(soy("PEER1", "M2", surum="V1"))  # eski M2
    r = degerlendir(p, kayitlar)
    assert r.per_ticker["PEER1"].status == NOT_READY


def test_peer_hedefte_M1_gereksiz_yere_istenmez():
    """
    M1/Ek1/GOOD_COUNT sirket-ICI bagimliliktir. Baska sirket bilanco
    acikladi diye peer hedefte yeniden calistirilmamalidir.
    """
    p = plan_uret()
    gerekli = set(degerlendir(p, tam_soy(p)).per_ticker["PEER1"].required_modules)
    assert gerekli == {"M2"}, f"peer hedefte fazla modul istendi: {gerekli}"


# ============================================ 6) TICKER BAZINDA ATOMIKLIK
def test_hazir_ticker_baskasi_yuzunden_ENGELLENMEZ():
    p = plan_uret()
    kayitlar = [k for k in tam_soy(p) if k.ticker != "PEER1"]  # PEER1 hazir degil
    r = degerlendir(p, kayitlar)
    assert "GARFA" in r.ready_tickers()
    assert "PEER1" not in r.ready_tickers()
    assert r.overall_status() == APP_PARTIAL


def test_kismi_durumda_hazir_olanlar_gonderilir():
    p = plan_uret()
    kayitlar = [k for k in tam_soy(p) if k.ticker != "PEER1"]
    r = degerlendir(p, kayitlar)
    hedefler = bridge_targeted_tickers(r)
    assert hedefler == ("GARFA",)


# ============================================ 7) ZINCIR KANITI
def test_bariyeri_gecmeyen_ticker_SIZAMAZ():
    p = plan_uret()
    kayitlar = [k for k in tam_soy(p) if k.ticker != "PEER1"]
    r = degerlendir(p, kayitlar)
    with pytest.raises(BridgeError, match="bariyeri gecmeyen"):
        assert_no_plan_ticker_lost(p, r, ["GARFA", "PEER1"])


def test_bariyeri_gecen_ticker_KAYBOLAMAZ():
    """Plandaki bir ticker'in sessizce dusmesi de hatadir."""
    p = plan_uret()
    r = degerlendir(p, tam_soy(p))
    with pytest.raises(BridgeError, match="gonderilmedi"):
        assert_no_plan_ticker_lost(p, r, ["GARFA"])


def test_zincir_dogru_oldugunda_gecer():
    p = plan_uret()
    r = degerlendir(p, tam_soy(p))
    assert_no_plan_ticker_lost(p, r, bridge_targeted_tickers(r))


def test_plan_ticker_raporda_yoksa_hata():
    p = plan_uret()
    r = degerlendir(p, tam_soy(p))
    from dataclasses import replace
    eksik = replace(r, per_ticker={"GARFA": r.per_ticker["GARFA"]})
    with pytest.raises(BridgeError, match="readiness raporunda yok"):
        assert_no_plan_ticker_lost(p, eksik, ["GARFA"])


# ============================================ 8) SAYACLAR
def test_uygulama_sayaclari():
    p = plan_uret()
    kayitlar = [k for k in tam_soy(p) if k.ticker != "PEER1"]
    r = degerlendir(p, kayitlar)
    s = application_counters(r, orchestrated=["GARFA"], failed=[])
    assert s["planned_targets"] == 2
    assert s["refreshed_targets"] == 1
    assert s["not_ready_targets"] == 1
    assert s["orchestrated_tickers"] == 1


def test_bos_plan_READY():
    p = plan_uret(change=degisiklik(fact_key="provisions"))
    r = degerlendir(p, [])
    assert r.overall_status() == APP_READY
    assert bridge_targeted_tickers(r) == ()


def test_gecersiz_girdi_reddedilir():
    p = plan_uret()
    with pytest.raises(BridgeError):
        evaluate_readiness(p, [], expected_source_version_id="")


def test_DISARIDAN_gelen_kismi_grup_genisletilir():
    """
    ATOMIK GRUP GENISLEMESININ ASIL YUKU.

    detect_change_impact() grubu her zaman eksiksiz uretir, bu yuzden
    genisleme normal akista no-op'tur. Ama plan baska bir kaynaktan
    (eski surum, elle kurulmus, farkli detector) gelirse gruptan yalniz
    biri isaretlenmis olabilir. Readiness bu durumda da UCUNU birden
    istemeli; aksi halde M1 tazelenip Ek1/GOOD_COUNT bayat kalirdi.
    """
    from dataclasses import replace

    p = plan_uret(change=degisiklik(statement_type="INCOME_STATEMENT",
                                    fact_key="net_income"))
    yalniz_m1 = tuple(e for e in p.entries
                      if not (e.impacted_ticker == "GARFA"
                              and e.module in ("Ek1", "GOOD_COUNT")))
    kismi = replace(p, entries=yalniz_m1)
    assert {e.module for e in kismi.entries if e.impacted_ticker == "GARFA"} \
        & {"Ek1", "GOOD_COUNT"} == set(), "fikstur kismi grup kurmadi"

    r = evaluate_readiness(kismi, [soy("GARFA", "M1"), soy("GARFA", "M2"),
                                   soy("PEER1", "M2")],
                           expected_source_version_id=SURUM,
                           change_published_at=YAYIN)
    gerekli = set(r.per_ticker["GARFA"].required_modules)
    assert {"M1", "Ek1", "GOOD_COUNT"} <= gerekli, \
        "kismi grup genisletilmedi: bayat Ek1/GOOD_COUNT ile calisilirdi"
    assert r.per_ticker["GARFA"].status == NOT_READY
