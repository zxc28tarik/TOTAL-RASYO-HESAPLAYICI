#!/usr/bin/env python3
"""
CHANGE-IMPACT OZ DENETIMI — 15.000 senaryo.

KAPSAM: yalniz V20 katmanlari.

    financial fact change
      -> dependency registry
      -> zaman yayilimi
      -> DIRECT / PEER_PROPAGATED
      -> impact plan
      -> persistence / idempotency
      -> readiness gereksinimleri

V19 Total Rasyo HESAPLAMASI bu denetime KARISMAZ. Orkestratörün kendi
15.000'lik kaniti ayri durur ve bu dosya ona DOKUNMAZ. Iki katmanin
birbirine baglanmasi ayri bir E2E denetiminin isidir; boylece bir hata
ciktiginda HANGI katmanin bozuldugu ayirt edilebilir.

"Exception olmadi" BASARI SAYILMAZ; her senaryoda invariant'lar dogrulanir.

Kullanim:
    python3 -m src.analytics.change_impact_self_audit
    python3 -m src.analytics.change_impact_self_audit --count 500
    python3 -m src.analytics.change_impact_self_audit --replay C00042
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import traceback
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional

from src.analytics.change_impact_bridge import (
    NOT_READY,
    READY,
    ModuleLineage,
    assert_no_plan_ticker_lost,
    bridge_targeted_tickers,
    evaluate_readiness,
)
from src.analytics.change_impact_detector import (
    DETECTOR_VERSION,
    EFFECT_ENTER,
    EFFECT_EXIT,
    EFFECT_MIN_PEER,
    EFFECT_TTM_LOSS,
    IMPACT_DIRECT,
    IMPACT_MODULE,
    IMPACT_PEER,
    KNOWLEDGE_PIT,
    KNOWLEDGE_RESTATE,
    ChangeImpactError,
    FactChange,
    PeerCandidate,
    detect_change_impact,
)
from src.analytics.change_impact_periods import (
    affected_anchor_period_ends,
    period_ordinal,
    shift_quarters,
)
from src.analytics.change_impact_registry import (
    DEPENDENCY_EDGES,
    PERIOD_8Q_GROUP,
    STATEMENT_TYPES,
    atomic_groups,
    edges_for_fact,
    registry_sha256,
)

TZ = timezone(timedelta(hours=3))
YAYIN = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)
KESIM = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
URETIM = datetime(2026, 3, 2, 12, 0, tzinfo=TZ)
SURUM = "V2"

CEYREKLER = (date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30),
             date(2025, 12, 31))

# Motor -> (statement, fact) ornekleri. UYDURULMAZ; registry'den turetilir.
def _fact_havuzu() -> dict[str, list[tuple[str, str]]]:
    havuz: dict[str, set[tuple[str, str]]] = {}
    for e in DEPENDENCY_EDGES:
        if not e.v20_triggers:
            continue
        if e.statement_type is None:
            # V20 tetikleyicisi statement_type TASIMAK ZORUNDA. None ise
            # registry sozlesmesi bozulmus demektir; sessizce atlamak yerine
            # denetim baslamadan hata verilir.
            raise RuntimeError(
                f"v20_triggers=True ama statement_type=None: "
                f"{e.engine_family}/{e.source_fact_key} -- registry bozuk")
        havuz.setdefault(e.engine_family, set()).add(
            (e.statement_type, e.source_fact_key))
    return {k: sorted(v) for k, v in havuz.items()}


FACT_HAVUZU = _fact_havuzu()
SEKTOR_MOTORLARI = tuple(k for k in FACT_HAVUZU if k != "MODULE_PIPELINE")

DISTRIBUTION: tuple[tuple[str, int], ...] = (
    ("dogrudan_bilanco", 1600),
    ("gelir_tablosu_ttm", 1600),
    ("nakit_akim", 500),
    ("sekiz_donem_penceresi", 1000),
    ("latest_only_alan", 1000),
    ("off_by_one_sinirlari", 1100),
    ("ttm_loss", 800),
    ("peer_statistic", 1100),
    ("peer_eligibility_enter_exit", 1100),
    ("leave_one_out", 800),
    ("minimum_peer_count", 500),
    ("carpan_bazli_nonfin", 500),
    ("bank_peer_yok", 400),
    ("holding_gyo_tetiklenmez", 400),
    ("atomik_grup", 500),
    ("pit_restate_ayrimi", 400),
    ("plan_idempotency", 400),
    ("readiness", 900),
    ("karma_coklu_fact", 400),
)

TOTAL = sum(n for _, n in DISTRIBUTION)
assert TOTAL == 15000, f"dagilim toplami 15000 olmali, {TOTAL} bulundu"


class InvariantIhlali(AssertionError):
    pass


# ==================================================================== yardimci
def fact_degisikligi(rng, *, engine=None, statement=None, fact=None,
                     period_end=None, old=1000.0, new=800.0, **kw) -> FactChange:
    aile = engine or rng.choice(SEKTOR_MOTORLARI)
    if statement is None or fact is None:
        statement, fact = rng.choice(FACT_HAVUZU[aile])
    veri = dict(
        ticker=kw.pop("ticker", "AAA"), statement_type=statement, fact_key=fact,
        period_end=period_end or rng.choice(CEYREKLER),
        old_value=old, new_value=new, published_at=YAYIN,
        source_fact_id="F1", source_statement_id="S1",
        source_version_id=SURUM, routed_engine=aile)
    veri.update(kw)
    return FactChange(**veri)


def havuz(engine, *adaylar):
    return {engine: list(adaylar)}


def plan_uret(change, **kw):
    args = dict(impact_run_id="AUDIT", analysis_at=KESIM)
    args.update(kw)
    return detect_change_impact(change, **args)


def soy(ticker, modul, *, surum=SURUM, uretim=URETIM, motor=None):
    return ModuleLineage(ticker=ticker, module=modul, engine_family=motor,
                         source_version_id=surum, produced_at=uretim,
                         calculation_profile="V1", calculation_version=1)


def tam_soy(plan):
    kayitlar = []
    for ticker in plan.targeted_tickers():
        moduller = {e.module for e in plan.entries if e.impacted_ticker == ticker}
        if moduller & {"M1", "Ek1", "GOOD_COUNT"}:
            moduller |= {"M1", "Ek1", "GOOD_COUNT"}
        kayitlar += [soy(ticker, m) for m in moduller]
    return kayitlar


# ================================================================= invariants
def inv_plan_temel(plan, change):
    if plan.registry_sha256 != registry_sha256():
        raise InvariantIhlali("plan registry SHA'si uyusmuyor")
    if plan.detector_version != DETECTOR_VERSION:
        raise InvariantIhlali("detector surumu uyusmuyor")
    kod = change.ticker.strip().upper()
    for e in plan.entries:
        if e.direct_ticker != kod:
            raise InvariantIhlali("direct_ticker degisen sirket degil")
        if e.impact_type == IMPACT_PEER and e.impacted_ticker == kod:
            raise InvariantIhlali("kendine PEER_PROPAGATED yazilmis (cift sayim)")
        if not e.actual_effects:
            raise InvariantIhlali("etki listesi bos")
        if not e.affected_anchor_period_ends:
            raise InvariantIhlali("anchor listesi bos")


def inv_tek_motor(plan, change):
    aile = change.routed_engine.strip().upper()
    for e in plan.entries:
        if e.engine_family not in (aile, "MODULE_PIPELINE"):
            raise InvariantIhlali(
                f"baska motorun kenari uretildi: {e.engine_family}")


def inv_anchor_sinirlari(plan, change):
    """OFF-BY-ONE: n anchor -> 0..n-1 offset. Fazlasi gereksiz hesap demektir."""
    baslangic = period_ordinal(change.period_end)
    for e in plan.entries:
        anchors = e.affected_anchor_period_ends
        ordinaller = [period_ordinal(a) for a in anchors]
        if ordinaller[0] != baslangic:
            raise InvariantIhlali("ilk anchor degisen donem degil")
        if ordinaller != list(range(baslangic, baslangic + len(anchors))):
            raise InvariantIhlali("anchor dizisi kesintili")
        if len(anchors) > 8:
            raise InvariantIhlali(f"anchor sayisi 8'i asti: {len(anchors)}")


def inv_atomik_grup(plan):
    for grup, kenarlar in atomic_groups().items():
        beklenen = {e.downstream_target for e in kenarlar}
        for ticker in {e.impacted_ticker for e in plan.entries
                       if e.dependency_group_key == grup}:
            gorulen = {e.module for e in plan.entries
                       if e.dependency_group_key == grup
                       and e.impacted_ticker == ticker}
            if gorulen != beklenen:
                raise InvariantIhlali(
                    f"{ticker}: atomik grup eksik {sorted(gorulen)}")


def inv_readiness_zinciri(plan, rapor, hedefler):
    if set(rapor.per_ticker) != set(plan.targeted_tickers()):
        raise InvariantIhlali("readiness raporu plan hedefleriyle uyusmuyor")
    hazir = set(rapor.ready_tickers())
    if set(hedefler) != hazir:
        raise InvariantIhlali("targeted_tickers bariyer sonucuyla uyusmuyor")
    for ticker in hedefler:
        tr = rapor.per_ticker[ticker]
        if tr.missing_modules:
            raise InvariantIhlali(f"{ticker}: eksik modulle gonderildi")


def ortak(plan, change):
    inv_plan_temel(plan, change)
    inv_tek_motor(plan, change)
    inv_anchor_sinirlari(plan, change)
    inv_atomik_grup(plan)


# ================================================================== senaryolar
def sc_dogrudan_bilanco(rng, sid):
    aile = rng.choice(SEKTOR_MOTORLARI)
    adaylar = [(s, f) for s, f in FACT_HAVUZU[aile] if s == "BALANCE_SHEET"]
    if not adaylar:
        return
    statement, fact = rng.choice(adaylar)
    c = fact_degisikligi(rng, engine=aile, statement=statement, fact=fact)
    p = plan_uret(c)
    ortak(p, c)

    # TANISAL ALAN skoru veya degerleme bandini DEGISTIRMEZ; plan uretmemesi
    # DOGRU davranistir. npl_gross / provisions / finance_receivables gibi
    # alanlar DIAGNOSTIC_ONLY rolundedir. Bunlari "plan uretmeli" saymak,
    # gereksiz yeniden hesaplama talep etmek olurdu.
    skorlayan = [e for e in edges_for_fact(statement_type=statement,
                                           fact_key=fact, engine_family=aile)
                 if e.dependency_role != "DIAGNOSTIC_ONLY"]
    modul = [e for e in edges_for_fact(statement_type=statement, fact_key=fact,
                                       engine_family="MODULE_PIPELINE")
             if e.dependency_role != "DIAGNOSTIC_ONLY"]
    if skorlayan or modul:
        if not p.entries:
            raise InvariantIhlali(f"{aile}/{fact}: skorlayan kenar var ama plan bos")
        if "AAA" not in p.targeted_tickers():
            raise InvariantIhlali("degisen sirket hedef kumede yok")
    else:
        if p.entries:
            raise InvariantIhlali(
                f"{aile}/{fact}: yalniz tanisal alan icin plan uretildi")


def sc_gelir_tablosu_ttm(rng, sid):
    aile = rng.choice(("NONFIN", "FINANCIAL", "INSURANCE"))
    adaylar = [(s, f) for s, f in FACT_HAVUZU[aile] if s == "INCOME_STATEMENT"]
    statement, fact = rng.choice(adaylar)
    c = fact_degisikligi(rng, engine=aile, statement=statement, fact=fact)
    p = plan_uret(c)
    ortak(p, c)
    ttm = [e for e in p.entries if "TTM" in e.reason_code]
    for e in ttm:
        if len(e.affected_anchor_period_ends) != 4:
            raise InvariantIhlali(
                f"TTM kenari {len(e.affected_anchor_period_ends)} anchor uretti")


def sc_nakit_akim(rng, sid):
    """
    CASH_FLOW V20 kapsamindadir ama SU AN hicbir motor nakit akim kalemi
    TUKETMIYOR. Bu bir eksiklik degil, kaynak kodun gercegi. Uydurulmus
    bagimlilik EKLENMEZ; denetim bos plan uretildigini DOGRULAR.
    """
    c = fact_degisikligi(rng, statement="CASH_FLOW",
                         fact=rng.choice(("operating_cash_flow", "capex",
                                          "free_cash_flow")))
    p = plan_uret(c)
    ortak(p, c)
    if p.entries:
        raise InvariantIhlali(
            "nakit akim kenari yokken plan uretildi -- uydurulmus bagimlilik")
    if edges_for_fact(statement_type="CASH_FLOW", fact_key="operating_cash_flow"):
        raise InvariantIhlali("registry'de beklenmeyen CASH_FLOW kenari")


def sc_sekiz_donem_penceresi(rng, sid):
    c = fact_degisikligi(rng, engine="BANK", statement="INCOME_STATEMENT",
                         fact="net_income")
    p = plan_uret(c)
    ortak(p, c)
    seri = [e for e in p.entries if "SERIES_8Q" in e.reason_code
            or "PERIOD_8Q" in e.reason_code]
    if not seri:
        raise InvariantIhlali("8 donem kenari uretilmedi")
    for e in seri:
        if len(e.affected_anchor_period_ends) != 8:
            raise InvariantIhlali(
                f"8Q kenari {len(e.affected_anchor_period_ends)} anchor uretti")


def sc_latest_only_alan(rng, sid):
    """
    LATEST_ONLY kenari, degisen ceyrek SON donem degilse yayilmamali.
    Iki ceyrek eski bir bilanco duzeltmesi son bilanco metrigini etkilemez.
    """
    c = fact_degisikligi(rng, engine="FINANCIAL", statement="BALANCE_SHEET",
                         fact="total_assets", period_end=date(2025, 6, 30))
    guncel = plan_uret(c, latest_period_end=date(2025, 6, 30))
    eski = plan_uret(c, latest_period_end=date(2025, 12, 31))
    ortak(guncel, c)
    ortak(eski, c)
    if not guncel.entries:
        raise InvariantIhlali("son donem degisikligi plan uretmedi")
    if eski.entries:
        raise InvariantIhlali("eski donem LATEST_ONLY kenarini tetikledi")
    if eski.diagnostics.get("skipped_latest_only", 0) < 1:
        raise InvariantIhlali("atlanan latest_only kenari raporlanmadi")


def sc_off_by_one_sinirlari(rng, sid):
    n = rng.choice((1, 4, 8))
    baslangic = rng.choice(CEYREKLER)
    anchors = affected_anchor_period_ends(
        baslangic, affected_anchor_count=n, max_forward_period_offset=n - 1)
    if len(anchors) != n:
        raise InvariantIhlali(f"{n} anchor beklenirken {len(anchors)}")
    if anchors[-1] != shift_quarters(baslangic, n - 1):
        raise InvariantIhlali("son anchor yanlis offset")
    try:
        affected_anchor_period_ends(baslangic, affected_anchor_count=n,
                                    max_forward_period_offset=n)
    except Exception:
        pass
    else:
        raise InvariantIhlali("tutarsiz offset kabul edildi")


def sc_ttm_loss(rng, sid):
    aile = rng.choice(("NONFIN", "FINANCIAL", "INSURANCE"))
    adaylar = [(s, f) for s, f in FACT_HAVUZU[aile] if s == "INCOME_STATEMENT"]
    statement, fact = rng.choice(adaylar)
    c = fact_degisikligi(rng, engine=aile, statement=statement, fact=fact,
                         old=100.0, new=None)
    p = plan_uret(c)
    ortak(p, c)
    if aile == "NONFIN":
        etkiler = {x for e in p.entries for x in e.actual_effects}
        if EFFECT_TTM_LOSS not in etkiler:
            raise InvariantIhlali("NONFIN'de TTM kaybi isaretlenmedi")


def sc_peer_statistic(rng, sid):
    aile = rng.choice(("FINANCIAL", "INSURANCE"))
    c = fact_degisikligi(rng, engine=aile, statement="BALANCE_SHEET",
                         fact="total_equity")
    adaylar = havuz(aile,
                    PeerCandidate("AAA", True, True, 1.2, 0.9),
                    PeerCandidate("BBB", True, True, 1.0, 1.0),
                    PeerCandidate("CCC", True, True, 1.5, 1.5))
    p = plan_uret(c, peer_candidates=adaylar)
    ortak(p, c)
    peer = {e.impacted_ticker for e in p.entries if e.impact_type == IMPACT_PEER}
    if peer != {"BBB", "CCC"}:
        raise InvariantIhlali(f"peer hedefleri yanlis: {sorted(peer)}")


def sc_peer_eligibility_enter_exit(rng, sid):
    aile = rng.choice(("FINANCIAL", "INSURANCE"))
    cikis = rng.random() < 0.5
    once, sonra = (True, False) if cikis else (False, True)
    c = fact_degisikligi(rng, engine=aile, statement="BALANCE_SHEET",
                         fact="total_equity")
    adaylar = havuz(aile,
                    PeerCandidate("AAA", once, sonra, 1.2, 0.9),
                    PeerCandidate("BBB", True, True, 1.0, 1.0))
    p = plan_uret(c, peer_candidates=adaylar)
    ortak(p, c)
    etkiler = {x for e in p.entries if e.impact_type == IMPACT_PEER
               for x in e.actual_effects}
    beklenen = EFFECT_EXIT if cikis else EFFECT_ENTER
    if beklenen not in etkiler:
        raise InvariantIhlali(f"{beklenen} uretilmedi")


def sc_leave_one_out(rng, sid):
    """
    Hedef kendi ornegine GIRMEZ ve KENDI degisimi kendi medyanini etkilemez.
    Ayrica havuzdan CIKAN hedef de incelenmeli (eski ∪ yeni evren).
    """
    aile = "FINANCIAL"
    c = fact_degisikligi(rng, engine=aile, statement="BALANCE_SHEET",
                         fact="total_equity")
    adaylar = havuz(aile,
                    PeerCandidate("AAA", True, False, 1.2, 0.9),
                    PeerCandidate("BBB", True, False, 1.0, 1.0),
                    PeerCandidate("CCC", True, True, 1.5, 1.5))
    p = plan_uret(c, peer_candidates=adaylar)
    ortak(p, c)
    hedefler = {e.impacted_ticker for e in p.entries if e.impact_type == IMPACT_PEER}
    if "BBB" not in hedefler:
        raise InvariantIhlali("havuzdan cikan HEDEF incelenmedi")
    if "AAA" in hedefler:
        raise InvariantIhlali("degisen sirkete peer etkisi yazildi")


def sc_minimum_peer_count(rng, sid):
    aile = "FINANCIAL"
    c = fact_degisikligi(rng, engine=aile, statement="BALANCE_SHEET",
                         fact="total_equity")
    adaylar = havuz(aile,
                    PeerCandidate("AAA", True, False, 1.2, 0.9),
                    PeerCandidate("BBB", True, True, 1.0, 1.0))
    p = plan_uret(c, peer_candidates=adaylar, minimum_peer_count={aile: 1})
    ortak(p, c)
    etkiler = {x for e in p.entries if e.impact_type == IMPACT_PEER
               for x in e.actual_effects}
    if EFFECT_MIN_PEER not in etkiler:
        raise InvariantIhlali("minimum_peer_count gecisi isaretlenmedi")


def sc_carpan_bazli_nonfin(rng, sid):
    """NONFIN'de emsal TEK carpandan duser, digerlerinde KALIR."""
    c = fact_degisikligi(rng, engine="NONFIN", statement="INCOME_STATEMENT",
                         fact="net_income")
    p = plan_uret(c, peer_candidates=havuz(
        "NONFIN", PeerCandidate("AAA", True, False, 12.0, None),
        PeerCandidate("BBB", True, True, 10.0, 10.0)))
    ortak(p, c)
    kapsamlar = {e.eligibility_scope for e in p.entries
                 if e.impact_type == IMPACT_PEER}
    if not any(k and k.startswith("PER_MULTIPLE:") for k in kapsamlar):
        raise InvariantIhlali("carpan bazli kapsam uretilmedi")


def sc_bank_peer_yok(rng, sid):
    """BANK finansal tablo degisikliginde KESITSEL yayilim uretmez."""
    statement, fact = rng.choice(FACT_HAVUZU["BANK"])
    c = fact_degisikligi(rng, engine="BANK", statement=statement, fact=fact)
    p = plan_uret(c, peer_candidates=havuz(
        "BANK", PeerCandidate("AAA", True, True, 1.0, 0.8),
        PeerCandidate("BBB", True, True, 1.0, 1.0)))
    ortak(p, c)
    if any(e.impact_type == IMPACT_PEER for e in p.entries):
        raise InvariantIhlali("BANK kesitsel peer yayilimi uretti")


def sc_holding_gyo_tetiklenmez(rng, sid):
    """HOLDING/GYO M2'si NAV+fiyat kaynakli; V20 finansal fact tetiklemez."""
    aile = rng.choice(("HOLDING", "GYO"))
    c = fact_degisikligi(rng, engine=aile, statement="BALANCE_SHEET",
                         fact=rng.choice(("total_equity", "shares_out",
                                          "total_assets")))
    p = plan_uret(c)
    ortak(p, c)
    sektor = [e for e in p.entries if e.engine_family == aile]
    if sektor:
        raise InvariantIhlali(f"{aile} icin V20 kenari uretildi")
    if any(e.engine_family in ("HOLDING", "GYO") and e.v20_triggers
           for e in DEPENDENCY_EDGES):
        raise InvariantIhlali("registry'de HOLDING/GYO V20 kenari var")


def sc_atomik_grup(rng, sid):
    aile = rng.choice(SEKTOR_MOTORLARI)
    c = fact_degisikligi(rng, engine=aile, statement="INCOME_STATEMENT",
                         fact="net_income")
    p = plan_uret(c)
    ortak(p, c)
    grup = {e.module for e in p.entries
            if e.dependency_group_key == PERIOD_8Q_GROUP}
    if grup and grup != {"M1", "Ek1", "GOOD_COUNT"}:
        raise InvariantIhlali(f"atomik grup kismi: {sorted(grup)}")
    # Kismi grup readiness tarafindan GENISLETILMELI.
    if grup:
        kismi = replace(p, entries=tuple(
            e for e in p.entries if e.module not in ("Ek1", "GOOD_COUNT")))
        rapor = evaluate_readiness(kismi, [soy("AAA", "M1")],
                                   expected_source_version_id=SURUM,
                                   change_published_at=YAYIN)
        gerekli = set(rapor.per_ticker["AAA"].required_modules)
        if not {"M1", "Ek1", "GOOD_COUNT"} <= gerekli:
            raise InvariantIhlali("kismi atomik grup genisletilmedi")


def sc_pit_restate_ayrimi(rng, sid):
    c = fact_degisikligi(rng)
    pit = plan_uret(c, knowledge_basis=KNOWLEDGE_PIT)
    restate = plan_uret(c, knowledge_basis=KNOWLEDGE_RESTATE,
                        knowledge_cutoff_at=KESIM + timedelta(days=30))
    if pit.impact_plan_id == restate.impact_plan_id:
        raise InvariantIhlali("PIT ve RESTATE ayni plan kimligini paylasti")
    if pit.knowledge_basis == restate.knowledge_basis:
        raise InvariantIhlali("bilgi tabani ayrilmadi")
    try:
        plan_uret(c, knowledge_basis=KNOWLEDGE_RESTATE)
    except ChangeImpactError:
        pass
    else:
        raise InvariantIhlali("cutoff'suz RESTATE plani kabul edildi")


def sc_plan_idempotency(rng, sid):
    c = fact_degisikligi(rng)
    a = plan_uret(c)
    b = plan_uret(c)
    if a.impact_plan_id != b.impact_plan_id:
        raise InvariantIhlali("ayni girdi farkli plan kimligi uretti")
    if a.plan_sha256() != b.plan_sha256():
        raise InvariantIhlali("ayni girdi farkli icerik ozeti uretti")
    farkli = plan_uret(replace(c, source_version_id="V9"))
    if farkli.impact_plan_id == a.impact_plan_id:
        raise InvariantIhlali("farkli kaynak surumu ayni kimlik uretti")
    bozuk = replace(a, entries=a.entries[:1]) if len(a.entries) > 1 else None
    if bozuk is not None and bozuk.plan_sha256() == a.plan_sha256():
        raise InvariantIhlali("farkli icerik ayni SHA uretti")


def sc_readiness(rng, sid):
    aile = rng.choice(("FINANCIAL", "INSURANCE", "NONFIN"))
    statement, fact = rng.choice(FACT_HAVUZU[aile])
    c = fact_degisikligi(rng, engine=aile, statement=statement, fact=fact)
    p = plan_uret(c, peer_candidates=havuz(
        aile, PeerCandidate("AAA", True, True, 1.2, 0.9),
        PeerCandidate("BBB", True, True, 1.0, 1.0)))
    if not p.entries:
        return
    ortak(p, c)

    mod = rng.choice(("tam", "eksik_lineage", "eski_surum", "eski_uretim"))
    if mod == "tam":
        kayitlar = tam_soy(p)
    elif mod == "eksik_lineage":
        kayitlar = [k for k in tam_soy(p) if k.module != "M2"]
    elif mod == "eski_surum":
        kayitlar = [k if k.module != "M2" else soy(k.ticker, "M2", surum="V1")
                    for k in tam_soy(p)]
    else:
        kayitlar = [k if k.module != "M2"
                    else soy(k.ticker, "M2", uretim=YAYIN - timedelta(days=5))
                    for k in tam_soy(p)]

    rapor = evaluate_readiness(p, kayitlar, expected_source_version_id=SURUM,
                               change_published_at=YAYIN)
    hedefler = bridge_targeted_tickers(rapor)
    inv_readiness_zinciri(p, rapor, hedefler)
    assert_no_plan_ticker_lost(p, rapor, hedefler)

    if mod == "tam":
        if set(hedefler) != set(p.targeted_tickers()):
            raise InvariantIhlali("tam lineage'da bariyer gecilmedi")
    else:
        if hedefler:
            raise InvariantIhlali(f"{mod}: bayat girdiyle ticker gonderildi")


def sc_karma_coklu_fact(rng, sid):
    """Birden fazla fact degisikligi UST USTE gelir; planlar birbirine karismaz."""
    aile = rng.choice(("FINANCIAL", "INSURANCE", "NONFIN"))
    adaylar = FACT_HAVUZU[aile]
    n = rng.randint(2, min(4, len(adaylar)))
    secilen = rng.sample(adaylar, n)
    kimlikler = set()
    for i, (statement, fact) in enumerate(secilen):
        c = fact_degisikligi(rng, engine=aile, statement=statement, fact=fact,
                             source_version_id=f"V{i + 2}")
        p = plan_uret(c, peer_candidates=havuz(
            aile, PeerCandidate("AAA", True, True, 1.2, 0.9),
            PeerCandidate("BBB", True, True, 1.0, 1.0)))
        ortak(p, c)
        kimlikler.add(p.impact_plan_id)
    if len(kimlikler) != n:
        raise InvariantIhlali("farkli fact degisiklikleri ayni plan uretti")


SCENARIOS: Mapping[str, Callable] = {
    "dogrudan_bilanco": sc_dogrudan_bilanco,
    "gelir_tablosu_ttm": sc_gelir_tablosu_ttm,
    "nakit_akim": sc_nakit_akim,
    "sekiz_donem_penceresi": sc_sekiz_donem_penceresi,
    "latest_only_alan": sc_latest_only_alan,
    "off_by_one_sinirlari": sc_off_by_one_sinirlari,
    "ttm_loss": sc_ttm_loss,
    "peer_statistic": sc_peer_statistic,
    "peer_eligibility_enter_exit": sc_peer_eligibility_enter_exit,
    "leave_one_out": sc_leave_one_out,
    "minimum_peer_count": sc_minimum_peer_count,
    "carpan_bazli_nonfin": sc_carpan_bazli_nonfin,
    "bank_peer_yok": sc_bank_peer_yok,
    "holding_gyo_tetiklenmez": sc_holding_gyo_tetiklenmez,
    "atomik_grup": sc_atomik_grup,
    "pit_restate_ayrimi": sc_pit_restate_ayrimi,
    "plan_idempotency": sc_plan_idempotency,
    "readiness": sc_readiness,
    "karma_coklu_fact": sc_karma_coklu_fact,
}


def _tam_plan() -> list[tuple[str, str, int]]:
    """
    Kanonik 15.000'lik plan. Senaryo kimligi -> (tur, tohum) eslemesi BURADA
    ve YALNIZ burada belirlenir; hicbir CLI secenegi onu degistirmez.
    Aksi halde --replay baska bir senaryoyu calistirir.
    """
    isler: list[tuple[str, str, int]] = []
    sira = 0
    for tur, adet in DISTRIBUTION:
        for _ in range(adet):
            isler.append((f"C{sira:05d}", tur, 2_000_003 + sira * 6151))
            sira += 1
    return isler


def plan(count: int) -> list[tuple[str, str, int]]:
    tam = _tam_plan()
    if count >= TOTAL:
        return tam
    oran = count / TOTAL
    gruplar: dict[str, list] = {}
    for is_ in tam:
        gruplar.setdefault(is_[1], []).append(is_)
    secilen: list[tuple[str, str, int]] = []
    for tur, adet in DISTRIBUTION:
        secilen.extend(gruplar[tur][:max(1, round(adet * oran))])
    return sorted(secilen)


def calistir(sid: str, tur: str, tohum: int) -> Optional[str]:
    rng = random.Random(tohum)
    try:
        SCENARIOS[tur](rng, sid)
        return None
    except InvariantIhlali as exc:
        return f"INVARIANT: {exc}"
    except Exception as exc:  # noqa: BLE001
        izi = traceback.format_exc(limit=3).strip().splitlines()[-1]
        return f"BEKLENMEYEN {type(exc).__name__}: {exc} | {izi}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Change-impact oz denetimi")
    ap.add_argument("--count", type=int, default=TOTAL)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--replay", type=str, default=None)
    ap.add_argument("--fail-fast", action="store_true")
    args = ap.parse_args(argv)

    isler = plan(args.count)
    if args.replay:
        isler = [i for i in isler if i[0] == args.replay]
        if not isler:
            print(f"senaryo bulunamadi: {args.replay}")
            return 2

    tur_sayaci: dict[str, dict[str, int]] = {}
    hatalar: list[dict[str, Any]] = []
    for sid, tur, tohum in isler:
        girdi = tur_sayaci.setdefault(tur, {"gecti": 0, "kaldi": 0})
        hata = calistir(sid, tur, tohum)
        if hata is None:
            girdi["gecti"] += 1
        else:
            girdi["kaldi"] += 1
            hatalar.append({"senaryo": sid, "tur": tur, "tohum": tohum,
                            "hata": hata})
            if args.fail_fast:
                break

    gecti = sum(g["gecti"] for g in tur_sayaci.values())
    kaldi = sum(g["kaldi"] for g in tur_sayaci.values())
    print("\n" + "=" * 62)
    print("CHANGE-IMPACT OZ DENETIMI (V20)")
    print("=" * 62)
    for tur, _ in DISTRIBUTION:
        g = tur_sayaci.get(tur, {"gecti": 0, "kaldi": 0})
        print(f"  {tur:32s} {g['gecti']:6d} / {g['gecti'] + g['kaldi']:6d}")
    print("-" * 62)
    print(f"  {'TOPLAM':32s} {gecti:6d} / {gecti + kaldi:6d}")
    if hatalar:
        print(f"\nBASARISIZ {len(hatalar)} senaryo (ilk 15):")
        for h in hatalar[:15]:
            print(f"  {h['senaryo']} [{h['tur']}] {h['hata']}")
        print("\nYeniden uretmek icin:")
        print(f"  python3 -m src.analytics.change_impact_self_audit "
              f"--replay {hatalar[0]['senaryo']}")

    if args.json:
        # DAGILIM, GERCEKTEN CALISTIRILAN plani gostermeli. Sabit DISTRIBUTION
        # tablosunu yazmak, --count 1500 ile kosuldugunda total=1500 iken
        # sum(distribution)=15000 gibi kendi icinde CELISKILI bir kanit
        # uretirdi. Kanit dosyasinin makine-okunur metadata'si, insan
        # okunur ozetten daha az dogru olamaz.
        calisan_dagilim = {tur: g["gecti"] + g["kaldi"]
                           for tur, g in tur_sayaci.items()}
        toplam = gecti + kaldi
        if sum(calisan_dagilim.values()) != toplam:
            raise RuntimeError(
                f"dagilim tutarsiz: sum={sum(calisan_dagilim.values())} "
                f"total={toplam}")
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"total": toplam, "passed": gecti, "failed": kaldi,
                       "distribution": calisan_dagilim,
                       "planned_distribution": {k: v for k, v in DISTRIBUTION},
                       "is_full_run": toplam == TOTAL,
                       "by_type": tur_sayaci,
                       "registry_sha256": registry_sha256(),
                       "detector_version": DETECTOR_VERSION,
                       "failures": hatalar[:200]},
                      fh, ensure_ascii=False, indent=2)
        print(f"\nJSON: {args.json}")

    return 0 if kaldi == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
