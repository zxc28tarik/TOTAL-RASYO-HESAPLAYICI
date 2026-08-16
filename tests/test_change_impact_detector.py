"""
Change-impact saf tespit katmani testleri.

En kritik iki kilit:
  1. 4Q/8Q OFF-BY-ONE  -> yanlissa gereksiz donem yeniden hesaplanir
  2. HEDEF BAZLI leave-one-out -> yanlissa bayat emsal skoru kalir
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.analytics.change_impact_detector import (
    DETECTOR_VERSION,
    EFFECT_ENTER,
    EFFECT_EXIT,
    EFFECT_MIN_PEER,
    EFFECT_TTM_LOSS,
    EFFECT_VALUE,
    EFFECT_VETO,
    IMPACT_DIRECT,
    IMPACT_MODULE,
    IMPACT_PEER,
    ChangeImpactError,
    EngineContractError,
    FactChange,
    PeerCandidate,
    detect_change_impact,
)
from src.analytics.change_impact_periods import (
    PeriodError,
    affected_anchor_period_ends,
    ordinal_to_period_end,
    period_ordinal,
    shift_quarters,
)
from src.analytics.change_impact_registry import (
    DEPENDENCY_EDGES,
    PERIOD_8Q_GROUP,
    DependencyEdge,
    RegistryError,
    atomic_groups,
    edges_for_fact,
    registry_sha256,
)

TZ = timezone(timedelta(hours=3))
YAYIN = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)


def degisiklik(**kw) -> FactChange:
    veri = dict(
        ticker="GARFA", statement_type="BALANCE_SHEET", fact_key="total_equity",
        period_end=date(2025, 12, 31), old_value=1000.0, new_value=800.0,
        published_at=YAYIN, source_fact_id="F1", source_statement_id="S1",
        source_version_id="V2", routed_engine="FINANCIAL")
    veri.update(kw)
    return FactChange(**veri)


# ==================================================== 1) OFF-BY-ONE
def test_ttm_4q_TAM_DORT_anchor():
    """
    Degisen ceyrek Q yalniz Q, Q+1, Q+2, Q+3 TTM penceresine girer.
    [Q, Q+4] yazmak BES anchor uretir ve fazladan bir donem hesaplatir.
    """
    anchors = affected_anchor_period_ends(
        date(2025, 3, 31), affected_anchor_count=4, max_forward_period_offset=3)
    assert anchors == [date(2025, 3, 31), date(2025, 6, 30),
                       date(2025, 9, 30), date(2025, 12, 31)]
    assert len(anchors) == 4


def test_series_8q_TAM_SEKIZ_anchor():
    anchors = affected_anchor_period_ends(
        date(2025, 3, 31), affected_anchor_count=8, max_forward_period_offset=7)
    assert len(anchors) == 8
    assert anchors[-1] == date(2026, 12, 31)


def test_latest_only_TEK_anchor():
    anchors = affected_anchor_period_ends(
        date(2025, 12, 31), affected_anchor_count=1, max_forward_period_offset=0)
    assert anchors == [date(2025, 12, 31)]


def test_offset_ile_anchor_sayisi_birbirini_dogrular():
    """n anchor, 0..n-1 offset demektir. Tutarsizlik reddedilir."""
    with pytest.raises(PeriodError, match="affected_anchor_count - 1"):
        affected_anchor_period_ends(date(2025, 3, 31),
                                    affected_anchor_count=4,
                                    max_forward_period_offset=4)


def test_ceyrek_ilerlemesi_takvim_gunu_kullanmaz():
    """
    period_end + 90 gun bir sonraki ceyrek sonunu VERMEZ (ceyrekler
    90/91/92 gun) ve artik yillarda kayar.
    """
    assert shift_quarters(date(2025, 12, 31), 1) == date(2026, 3, 31)
    assert shift_quarters(date(2024, 3, 31), 1) == date(2024, 6, 30)  # artik yil
    assert shift_quarters(date(2026, 3, 31), -1) == date(2025, 12, 31)
    # Takvim aritmetigi olsaydi 2025-12-31 + 90 gun = 2026-03-31 tesadufen
    # dogru, ama 2025-03-31 + 90 gun = 2025-06-29 YANLIS olurdu.
    assert shift_quarters(date(2025, 3, 31), 1) == date(2025, 6, 30)


def test_ordinal_gidis_donus():
    for gun in (date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30),
                date(2025, 12, 31)):
        assert ordinal_to_period_end(period_ordinal(gun)) == gun


def test_ordinal_monoton():
    assert period_ordinal(date(2026, 3, 31)) - period_ordinal(date(2025, 12, 31)) == 1


@pytest.mark.parametrize("bozuk", [date(2025, 3, 30), date(2025, 5, 31), date(2025, 1, 1)])
def test_gercek_ceyrek_sonu_olmayan_reddedilir(bozuk):
    with pytest.raises(PeriodError):
        period_ordinal(bozuk)


# ==================================================== 2) REGISTRY
def test_holding_gyo_V20_tetikleyicisi_YOK():
    """
    HOLDING/GYO M2'si NAV + fiyat kaynakli. shares_out bile NAV raporundan
    geliyor; finansal tablo change-impact kapsaminda DEGIL.
    """
    tetikleyen = [e for e in DEPENDENCY_EDGES
                  if e.engine_family in ("HOLDING", "GYO") and e.v20_triggers]
    assert tetikleyen == []


def test_nav_ve_fiyat_kenarlari_registryde_KALIR():
    """Bagimlilik != tetikleyici. Gunluk DAG ayni registry'yi kullanacak."""
    nav = [e for e in DEPENDENCY_EDGES if e.source_domain == "NAV_REPORT"]
    fiyat = [e for e in DEPENDENCY_EDGES if e.source_domain == "MARKET_PRICE"]
    assert nav and fiyat
    assert all(not e.v20_triggers for e in nav + fiyat)


def test_shares_out_provenance_ayrisir():
    """Ayni metrik, iki farkli kaynak -> IKI AYRI kenar."""
    kenarlar = [e for e in DEPENDENCY_EDGES if e.source_fact_key == "shares_out"]
    alanlar = {(e.engine_family, e.source_domain, e.trigger_enabled)
               for e in kenarlar}
    assert ("NONFIN", "FINANCIAL_STATEMENT", True) in alanlar
    assert ("HOLDING", "NAV_REPORT", False) in alanlar
    assert ("GYO", "NAV_REPORT", False) in alanlar


def test_total_equity_IKI_AYRI_yol():
    """Biri istatistigi, digeri havuz uyeligini degistirir."""
    kenarlar = edges_for_fact(statement_type="BALANCE_SHEET",
                              fact_key="total_equity", engine_family="FINANCIAL")
    yollar = {(e.transform_key, e.dependency_role, e.failure_mode) for e in kenarlar}
    assert ("MARKET_CAP_TO_BOOK", "PEER_STATISTIC_INPUT", "VALUE_SHIFT") in yollar
    assert ("EQUITY_BUFFER", "PEER_ELIGIBILITY_INPUT", "POOL_DROP") in yollar


def test_atomik_grup_uc_downstream():
    gruplar = atomic_groups()
    hedefler = {e.downstream_target for e in gruplar[PERIOD_8Q_GROUP]}
    assert hedefler == {"M1", "Ek1", "GOOD_COUNT"}


def test_peer_propagates_TURETILMIS():
    """Saklanan bagimsiz boolean yok; rolle celisemez."""
    assert not any(hasattr(e, "_peer_propagates") for e in DEPENDENCY_EDGES)
    for e in DEPENDENCY_EDGES:
        assert e.peer_propagates == (e.dependency_role in
                                     ("PEER_STATISTIC_INPUT", "PEER_ELIGIBILITY_INPUT"))


def test_ENGINE_FAMILY_blast_radius_YOK():
    """HARD_ERROR/ENGINE_FAMILY normal fact propagation sonucu OLAMAZ."""
    assert all(e.impact_blast_radius in ("COMPANY", "PEER_POOL")
               for e in DEPENDENCY_EDGES)
    assert all(e.failure_mode != "HARD_ERROR" for e in DEPENDENCY_EDGES)


def test_edge_id_deterministik_ve_tanima_duyarli():
    e = DEPENDENCY_EDGES[0]
    assert e.dependency_edge_id == e.dependency_edge_id
    from dataclasses import replace
    assert replace(e, metric_key="baska").dependency_edge_id != e.dependency_edge_id


def test_registry_sha_kararli():
    assert registry_sha256() == registry_sha256()
    assert len(registry_sha256()) == 64


def test_statement_type_yalniz_finansal_tabloda():
    for e in DEPENDENCY_EDGES:
        if e.source_domain == "FINANCIAL_STATEMENT":
            assert e.statement_type in ("BALANCE_SHEET", "INCOME_STATEMENT", "CASH_FLOW")
        else:
            assert e.statement_type is None


def test_gecersiz_blast_radius_reddedilir():
    from dataclasses import replace
    from src.analytics.change_impact_registry import _validate
    with pytest.raises(RegistryError, match="ENGINE_FAMILY"):
        _validate(replace(DEPENDENCY_EDGES[0], impact_blast_radius="ENGINE_FAMILY"))


# ==================================================== 3) LEAVE-ONE-OUT
def havuz(*uyeler):
    return {"FINANCIAL": list(uyeler)}


def test_kendine_PEER_PROPAGATED_yazilmaz():
    """
    Degisen sirket DIRECT alir. Kendi degisimi kendi emsal medyanini
    etkilemez -- kendini emsal olarak kullanamaz. Cift sayim yasak.
    """
    p = detect_change_impact(
        degisiklik(), impact_run_id="R1",
        peer_candidates=havuz(
            PeerCandidate("GARFA", True, True, 1.2, 0.9),
            PeerCandidate("PEER1", True, True, 1.0, 1.0)))
    kendi = [e for e in p.entries if e.impacted_ticker == "GARFA"]
    assert kendi, "degisen sirket kendi etkisini almali"
    # DIRECT (kendi motoru) ve MODULE_DEPENDENCY (modul hatti) mesru; asil
    # sozlesme kendine PEER_PROPAGATED YAZILMAMASIDIR -- cift sayim olurdu.
    assert all(e.impact_type in (IMPACT_DIRECT, IMPACT_MODULE) for e in kendi)
    assert not any(e.impact_type == IMPACT_PEER for e in kendi)
    assert any(e.impact_type == IMPACT_DIRECT for e in kendi)


def test_havuzdan_CIKAN_emsal_digerlerini_etkiler():
    """
    EN KRITIK: yalniz eligible_after'a bakilsaydi, cikan emsalin diger
    sirketlerin ESKI medyaninda hala bulundugu KACIRILIRDI ve o skorlar
    sessizce bayat kalirdi.
    """
    p = detect_change_impact(
        degisiklik(), impact_run_id="R1",
        peer_candidates=havuz(
            PeerCandidate("GARFA", True, False, 1.2, 0.9),   # havuzdan CIKTI
            PeerCandidate("PEER1", True, True, 1.0, 1.0),
            PeerCandidate("PEER2", True, True, 1.5, 1.5)))
    etkilenen = {e.impacted_ticker for e in p.entries if e.impact_type == IMPACT_PEER}
    assert etkilenen == {"PEER1", "PEER2"}
    efektler = {e for x in p.entries if x.impact_type == IMPACT_PEER
                for e in x.actual_effects}
    assert EFFECT_EXIT in efektler


def test_havuza_GIREN_emsal_etki_uretir():
    p = detect_change_impact(
        degisiklik(old_value=800.0, new_value=1000.0), impact_run_id="R1",
        peer_candidates=havuz(
            PeerCandidate("GARFA", False, True, 0.9, 1.2),   # havuza GIRDI
            PeerCandidate("PEER1", True, True, 1.0, 1.0)))
    peer = [e for e in p.entries if e.impact_type == IMPACT_PEER]
    assert {e.impacted_ticker for e in peer} == {"PEER1"}
    assert EFFECT_ENTER in {e for x in peer for e in x.actual_effects}


def test_hic_uygun_olmayan_emsal_etkilenmez():
    """Ne once ne sonra havuzda olan sirket hedef degildir."""
    p = detect_change_impact(
        degisiklik(), impact_run_id="R1",
        peer_candidates=havuz(
            PeerCandidate("GARFA", True, False, 1.2, 0.9),
            PeerCandidate("PEER1", True, True, 1.0, 1.0),
            PeerCandidate("DISARDA", False, False, None, None)))
    assert "DISARDA" not in {e.impacted_ticker for e in p.entries}


def test_degismeyen_havuzda_peer_etkisi_YOK():
    """Uygunluk ve degerler ayniysa gereksiz yeniden hesaplama uretme."""
    p = detect_change_impact(
        degisiklik(fact_key="npl_gross"), impact_run_id="R1",
        peer_candidates=havuz(
            PeerCandidate("GARFA", True, True, 1.0, 1.0),
            PeerCandidate("PEER1", True, True, 1.0, 1.0)))
    assert not [e for e in p.entries if e.impact_type == IMPACT_PEER]


def test_minimum_peer_count_kapisi_ayri_etki():
    """Deger kaymasi degil SONUC KAYBI: hedef YETERSIZ_EMSAL'e duser."""
    p = detect_change_impact(
        degisiklik(), impact_run_id="R1",
        peer_candidates=havuz(
            PeerCandidate("GARFA", True, False, 1.2, 0.9),
            PeerCandidate("PEER1", True, True, 1.0, 1.0)),
        minimum_peer_count={"FINANCIAL": 1})
    peer = [e for e in p.entries if e.impact_type == IMPACT_PEER]
    assert EFFECT_MIN_PEER in {e for x in peer for e in x.actual_effects}


# ==================================================== 4) TEK MOTOR SAHIPLIGI
def test_yalniz_kendi_motorunun_kenarlari():
    """
    V19 ilkesi: sirket TEK sektor motoruna aittir. FINANCIAL bir sirket icin
    INSURANCE/NONFIN/BANK kenarlari URETILMEZ.
    """
    p = detect_change_impact(degisiklik(), impact_run_id="R1")
    assert p.engines() == ("FINANCIAL",)


def test_routed_engine_zorunlu():
    with pytest.raises(ChangeImpactError, match="routed_engine"):
        detect_change_impact(degisiklik(routed_engine=""), impact_run_id="R1")


def test_gecersiz_routed_engine_reddedilir():
    with pytest.raises(ChangeImpactError):
        detect_change_impact(degisiklik(routed_engine="CRYPTO"), impact_run_id="R1")


# ==================================================== 5) KAPSAM
@pytest.mark.parametrize("bozuk", ["NEWS", "DISCLOSURE", "DIVIDEND", ""])
def test_finansal_tablo_disi_statement_reddedilir(bozuk):
    """V20 yalniz BALANCE_SHEET / INCOME_STATEMENT / CASH_FLOW."""
    with pytest.raises(ChangeImpactError):
        detect_change_impact(degisiklik(statement_type=bozuk), impact_run_id="R1")


def test_degisiklik_yoksa_reddedilir():
    with pytest.raises(ChangeImpactError, match="degisiklik yok"):
        detect_change_impact(degisiklik(old_value=100.0, new_value=100.0),
                             impact_run_id="R1")


def test_naive_published_at_reddedilir():
    with pytest.raises(ChangeImpactError):
        detect_change_impact(degisiklik(published_at=datetime(2026, 3, 1, 10, 0)),
                             impact_run_id="R1")


# ==================================================== 6) PLAN
def test_plan_idempotent():
    """Ayni revizyon + ayni detector + ayni registry -> AYNI plan kimligi."""
    a = detect_change_impact(degisiklik(), impact_run_id="R1")
    b = detect_change_impact(degisiklik(), impact_run_id="R2")
    assert a.impact_plan_id == b.impact_plan_id


def test_plan_kaynak_surumune_duyarli():
    a = detect_change_impact(degisiklik(), impact_run_id="R1")
    b = detect_change_impact(degisiklik(new_value=750.0), impact_run_id="R1")
    assert a.impact_plan_id != b.impact_plan_id


def test_plan_registry_sha_tasir():
    p = detect_change_impact(degisiklik(), impact_run_id="R1")
    assert p.registry_sha256 == registry_sha256()
    assert p.detector_version == DETECTOR_VERSION
    assert all(e.registry_sha256 == registry_sha256() for e in p.entries)


def test_plan_veritabanina_dokunmaz():
    """Saf katman: import edilen modulde psycopg2 YOK."""
    import ast
    from pathlib import Path
    agac = ast.parse(Path("src/analytics/change_impact_detector.py").read_text(encoding="utf-8"))
    for dugum in ast.walk(agac):
        if isinstance(dugum, (ast.Import, ast.ImportFrom)):
            adlar = [a.name for a in getattr(dugum, "names", [])]
            modul = getattr(dugum, "module", "") or ""
            assert "psycopg2" not in modul
            assert not any("psycopg2" in a for a in adlar)


def test_latest_only_eski_donemde_yayilmaz():
    """Iki ceyrek eski bilanco duzeltmesi son bilanco metrigini etkilemez."""
    p = detect_change_impact(
        degisiklik(period_end=date(2025, 6, 30)), impact_run_id="R1",
        latest_period_end=date(2025, 12, 31))
    latest_kenar = [e for e in p.entries
                    if e.reason_code.startswith("EQUITY_BUFFER")]
    assert latest_kenar == []
    assert p.diagnostics["skipped_latest_only"] > 0


def test_ttm_kenari_eski_donemde_YAYILIR():
    p = detect_change_impact(
        degisiklik(statement_type="INCOME_STATEMENT", fact_key="net_income",
                   period_end=date(2025, 6, 30)),
        impact_run_id="R1", latest_period_end=date(2025, 12, 31))
    ttm = [e for e in p.entries if e.reason_code.startswith("TTM_SUM_4Q")]
    assert ttm
    assert len(ttm[0].affected_anchor_period_ends) == 4


def test_ttm_null_kaybi_ayri_etki():
    """sum_if_complete: tek ceyrek null -> TTM tamamen kaybolur."""
    p = detect_change_impact(
        degisiklik(ticker="THYAO", routed_engine="NONFIN",
                   statement_type="INCOME_STATEMENT", fact_key="revenue",
                   old_value=100.0, new_value=None),
        impact_run_id="R1")
    assert EFFECT_TTM_LOSS in {x for e in p.entries for x in e.actual_effects}


def test_veto_etkisi_isaretlenir():
    p = detect_change_impact(
        degisiklik(ticker="GARAN", routed_engine="BANK",
                   statement_type="INCOME_STATEMENT",
                   fact_key="net_income"),
        impact_run_id="R1")
    veto = [e for e in p.entries if e.module == "GOOD_COUNT"]
    assert veto and EFFECT_VETO in veto[0].actual_effects


def test_atomik_grup_birlikte_tazelenir():
    """M1 + Ek1 + GOOD_COUNT ucu birden planda olmali."""
    p = detect_change_impact(
        degisiklik(ticker="GARAN", routed_engine="BANK",
                   statement_type="INCOME_STATEMENT",
                   fact_key="net_income"),
        impact_run_id="R1")
    grup = {e.module for e in p.entries
            if e.dependency_group_key == PERIOD_8Q_GROUP}
    assert grup == {"M1", "Ek1", "GOOD_COUNT"}, "grup kismen tazelenemez"


def test_tanisal_alan_plan_uretmez():
    """DIAGNOSTIC_ONLY skoru veya bandi degistirmez."""
    p = detect_change_impact(degisiklik(fact_key="provisions"), impact_run_id="R1")
    assert all(not e.reason_code.startswith("PROVISION_COVERAGE")
               for e in p.entries)


def test_kendisi_havuzdan_CIKAN_hedef_de_incelenir():
    """
    BIRLESIM KURALININ ASIL YUKU. Yalniz `eligible_after`'a bakilsaydi,
    havuzdan cikmis bir HEDEF hic incelenmez ve onun emsal orneginin
    degistigi gozden kacardi -- skoru sessizce bayat kalirdi.

    Burada PEER1 kendi nedeniyle havuzdan cikmis durumda; yine de hedef
    olarak degerlendirilmeli, cunku GARFA'nin cikisi PEER1'in ornegini
    degistiriyor.
    """
    p = detect_change_impact(
        degisiklik(), impact_run_id="R1",
        peer_candidates=havuz(
            PeerCandidate("GARFA", True, False, 1.2, 0.9),
            PeerCandidate("PEER1", True, False, 1.0, 1.0),   # kendisi de CIKTI
            PeerCandidate("PEER2", True, True, 1.5, 1.5)))
    hedefler = {e.impacted_ticker for e in p.entries if e.impact_type == IMPACT_PEER}
    assert "PEER1" in hedefler, "havuzdan cikan hedef incelenmedi"
    assert "PEER2" in hedefler


def test_yalniz_eski_havuzda_olan_hedef_incelenir():
    """Once uygundu, sonra degil: eski degerlemesi hala duzeltilmeli."""
    p = detect_change_impact(
        degisiklik(), impact_run_id="R1",
        peer_candidates=havuz(
            PeerCandidate("GARFA", True, False, 1.2, 0.9),
            PeerCandidate("ESKI", True, False, 1.1, 1.1),
            PeerCandidate("PEER2", True, True, 1.5, 1.5)))
    assert "ESKI" in {e.impacted_ticker for e in p.entries
                      if e.impact_type == IMPACT_PEER}
