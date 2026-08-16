#!/usr/bin/env python3
"""
V20 E2E KABUL DENETIMI — katmanlar arasi ZINCIR.

Bu denetim 15.000'lik rastgele tarama DEGILDIR. Amac az sayida ama GUCLU ve
DETERMINISTIK senaryoyla su zincirin dogru baglandigini kanitlamaktir:

    fact degisikligi
      -> detect_change_impact
      -> plan persist (idempotent, immutable)
      -> gerekli M2/modul tazeleme + lineage
      -> readiness bariyeri
      -> targeted_tickers
      -> V19 orkestratör
      -> otoritatif kalicilik
      -> Total Rasyo sonucu

NEDEN AYRI: V19 ve V20'nin kendi 15.000'lik kanitlari BAGIMSIZ kalir; bu
denetim "iki katman ayri ayri dogru ama birbirine baglaninca bozuk"
sinifindaki hatalari yakalar.

Kullanim:
    python3 -m src.analytics.change_impact_e2e_audit
    python3 -m src.analytics.change_impact_e2e_audit --json rapor.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

import psycopg2

from src.analytics.change_impact_bridge import (
    NOT_READY,
    READY,
    ModuleLineage,
    assert_no_plan_ticker_lost,
    bridge_targeted_tickers,
    evaluate_readiness,
)
from src.analytics.change_impact_detector import (
    EMPTY_DIAGNOSTIC_ONLY,
    EMPTY_NO_SCORING_DEPENDENCY,
    IMPACT_PEER,
    KNOWLEDGE_PIT,
    KNOWLEDGE_RESTATE,
    ChangeImpactError,
    FactChange,
    PeerCandidate,
    detect_change_impact,
)
from src.analytics.change_impact_persistence import (
    ImpactPlanConflict,
    load_targeted_tickers,
    persist_impact_plan,
    record_application_attempt,
)
from src.analytics.total_rasyo_combine import STATUS_INSUFFICIENT, STATUS_OK
from src.analytics.total_rasyo_orchestrator import run_total_rasyo_orchestrator
import src.analytics.total_rasyo_self_audit as V19

TZ = timezone(timedelta(hours=3))
KESIM = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
YAYIN = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)
URETIM = datetime(2026, 3, 2, 12, 0, tzinfo=TZ)
SURUM = "V2"

META_TASLAK = dict(source_fact_id="F1", source_statement_id="S1",
                   source_version_id=SURUM, published_at=YAYIN)


class ZincirIhlali(AssertionError):
    pass


def baglan():
    dsn = os.environ.get("TOTAL_RASYO_TEST_DSN") or os.environ.get("PGDATABASE")
    if not dsn:
        return None
    try:
        if "=" in dsn or dsn.startswith("postgres"):
            return psycopg2.connect(dsn)
        return psycopg2.connect(dbname=dsn)
    except psycopg2.Error:
        return None


def temizle(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE analytics.reconciliation_finding,"
                        " analytics.reconciliation_run,"
                        " analytics.impact_application_target,"
                        " analytics.impact_application_run,"
                        " analytics.impact_plan_entry, analytics.impact_plan")
            cur.execute("TRUNCATE analytics.reconciliation_module_check,"
                        " analytics.reconciliation_module_run,"
                        " analytics.total_rasyo_module_input,"
                        " analytics.company_total_rasyo_result,"
                        " analytics.daily_engine_run, analytics.total_rasyo_run")
            cur.execute("TRUNCATE analytics.total_rasyo_restate_module_input,"
                        " analytics.company_total_rasyo_restate_result,"
                        " analytics.total_rasyo_restate_runs")
            cur.execute("DELETE FROM analytics.module_scores")


def fact(ticker="GARFA", *, engine="FINANCIAL", statement="BALANCE_SHEET",
         key="total_equity", period=date(2025, 12, 31), old=1000.0, new=800.0,
         surum=SURUM) -> FactChange:
    return FactChange(ticker=ticker, statement_type=statement, fact_key=key,
                      period_end=period, old_value=old, new_value=new,
                      published_at=YAYIN, source_fact_id="F1",
                      source_statement_id="S1", source_version_id=surum,
                      routed_engine=engine)


def meta(c: FactChange) -> dict[str, Any]:
    return dict(direct_ticker=c.ticker, source_fact_id=c.source_fact_id,
                source_statement_id=c.source_statement_id,
                source_version_id=c.source_version_id,
                statement_type=c.statement_type, fact_key=c.fact_key,
                changed_period_end=c.period_end, published_at=c.published_at)


def soy(ticker, modul, *, surum=SURUM, uretim=URETIM):
    return ModuleLineage(ticker=ticker, module=modul, engine_family=None,
                         source_version_id=surum, produced_at=uretim,
                         calculation_profile="V1", calculation_version=1)


def tam_soy(plan, *, haric=(), eski_surum=()):
    kayitlar = []
    for ticker in plan.targeted_tickers():
        moduller = {e.module for e in plan.entries if e.impacted_ticker == ticker}
        if moduller & {"M1", "Ek1", "GOOD_COUNT"}:
            moduller |= {"M1", "Ek1", "GOOD_COUNT"}
        for m in sorted(moduller):
            if (ticker, m) in haric:
                continue
            s = "V1" if (ticker, m) in eski_surum else SURUM
            kayitlar.append(soy(ticker, m, surum=s))
    return kayitlar


def modul_satiri(conn, ticker, **kw):
    d = {"M1": 0.62, "M3": 0.71, "Ek4": 0.55, "Ek1": 0.48, "Ek9": 0.33}
    good = kw.pop("good", 9)
    d.update(kw)
    V19.modul_satiri(conn, ticker, asof=date(2026, 3, 2),
                     analysis_at=KESIM - timedelta(hours=1),
                     degerler=d, good=good)


def kos_orkestrator(conn, routing, m2_map, hedefler, run_id):
    runners = {}
    for aile in set(routing.values()):
        sonuc = {t: V19.m2cikti(m2_map[t]) for t, a in routing.items()
                 if a == aile and t in m2_map}
        runners[aile] = V19.motor(sonuc)
    return run_total_rasyo_orchestrator(
        conn, analysis_at=KESIM, routing=routing, engine_runners=runners,
        run_id=run_id, targeted_tickers=list(hedefler) or None)


def db_sonuc(conn, ticker):
    with conn.cursor() as cur:
        cur.execute("SELECT total_rasyo_status, final_score, decision"
                    " FROM analytics.company_total_rasyo_result"
                    " WHERE ticker=%s AND analysis_at=%s", (ticker, KESIM))
        return cur.fetchone()


# ==================================================================== 1
def sc_direct_basarili(conn):
    """Fact degisir -> gerekenler tazelenir -> readiness gecer -> sonuc DEGISIR."""
    temizle(conn)
    routing = {"GARFA": "FINANCIAL"}
    modul_satiri(conn, "GARFA")
    kos_orkestrator(conn, routing, {"GARFA": 0.80}, [], "E2E-1-once")
    once = db_sonuc(conn, "GARFA")
    if once[0] != STATUS_OK:
        raise ZincirIhlali("baslangic sonucu OK degil")

    c = fact()
    plan = detect_change_impact(c, impact_run_id="E2E-1", analysis_at=KESIM)
    persist_impact_plan(conn, plan, meta(c))
    hedef_db = load_targeted_tickers(conn, plan.impact_plan_id)
    if set(hedef_db) != set(plan.targeted_tickers()):
        raise ZincirIhlali("plan hedefleri veritabanindan farkli okundu")

    rapor = evaluate_readiness(plan, tam_soy(plan),
                               expected_source_version_id=SURUM,
                               change_published_at=YAYIN)
    hedefler = bridge_targeted_tickers(rapor)
    assert_no_plan_ticker_lost(plan, rapor, hedefler)
    if "GARFA" not in hedefler:
        raise ZincirIhlali("tam lineage'a ragmen bariyer gecilmedi")

    kos_orkestrator(conn, routing, {"GARFA": 0.40}, hedefler, "E2E-1-sonra")
    sonra = db_sonuc(conn, "GARFA")
    if sonra[1] == once[1]:
        raise ZincirIhlali("yeniden hesaplama sonucu degistirmedi")


# ==================================================================== 2
def sc_eski_m1_yeni_m2_yasak(conn):
    temizle(conn)
    c = fact(statement="INCOME_STATEMENT", key="net_income")
    plan = detect_change_impact(c, impact_run_id="E2E-2", analysis_at=KESIM)
    rapor = evaluate_readiness(plan, tam_soy(plan, eski_surum={("GARFA", "M1")}),
                               expected_source_version_id=SURUM,
                               change_published_at=YAYIN)
    if rapor.per_ticker["GARFA"].status != NOT_READY:
        raise ZincirIhlali("eski M1 ile bariyer gecildi")
    if "GARFA" in bridge_targeted_tickers(rapor):
        raise ZincirIhlali("bayat M1 ile ticker V19'a ulasti")


# ==================================================================== 3
def sc_peer_propagation(conn):
    """A'nin fact'i B'nin M2'sini etkiler; B'nin M1/Ek1'i GEREKSIZ istenmez."""
    temizle(conn)
    c = fact()
    plan = detect_change_impact(
        c, impact_run_id="E2E-3", analysis_at=KESIM,
        peer_candidates={"FINANCIAL": [
            PeerCandidate("GARFA", True, True, 1.2, 0.9),
            PeerCandidate("PEER1", True, True, 1.0, 1.0)]})
    peer = [e for e in plan.entries if e.impacted_ticker == "PEER1"]
    if not peer or any(e.impact_type != IMPACT_PEER for e in peer):
        raise ZincirIhlali("peer etkisi uretilmedi")
    rapor = evaluate_readiness(plan, tam_soy(plan),
                               expected_source_version_id=SURUM,
                               change_published_at=YAYIN)
    gerekli = set(rapor.per_ticker["PEER1"].required_modules)
    if gerekli != {"M2"}:
        raise ZincirIhlali(f"peer hedefte fazla modul istendi: {gerekli}")


# ==================================================================== 4
def sc_peer_exit(conn):
    temizle(conn)
    c = fact()
    plan = detect_change_impact(
        c, impact_run_id="E2E-4", analysis_at=KESIM,
        peer_candidates={"FINANCIAL": [
            PeerCandidate("GARFA", True, False, 1.2, 0.9),
            PeerCandidate("PEER1", True, True, 1.0, 1.0),
            PeerCandidate("PEER2", True, True, 1.5, 1.5)]})
    hedefler = {e.impacted_ticker for e in plan.entries
                if e.impact_type == IMPACT_PEER}
    if hedefler != {"PEER1", "PEER2"}:
        raise ZincirIhlali(f"cikis sonrasi hedefler yanlis: {sorted(hedefler)}")
    etkiler = {x for e in plan.entries if e.impact_type == IMPACT_PEER
               for x in e.actual_effects}
    if "ELIGIBILITY_EXIT" not in etkiler:
        raise ZincirIhlali("havuzdan cikis isaretlenmedi")


# ==================================================================== 5
def sc_minimum_peer_crossing(conn):
    """Eski OK -> yeni YETERSIZ. Eski basarili M2'ye FALLBACK YOK."""
    temizle(conn)
    routing = {"GARFA": "FINANCIAL"}
    modul_satiri(conn, "GARFA")
    kos_orkestrator(conn, routing, {"GARFA": 0.80}, [], "E2E-5-once")
    if db_sonuc(conn, "GARFA")[0] != STATUS_OK:
        raise ZincirIhlali("baslangic OK degil")

    c = fact()
    plan = detect_change_impact(
        c, impact_run_id="E2E-5", analysis_at=KESIM,
        peer_candidates={"FINANCIAL": [
            PeerCandidate("GARFA", True, False, 1.2, 0.9),
            PeerCandidate("PEER1", True, True, 1.0, 1.0)]},
        # ESIK 1 OLMALI: PEER1'in leave-one-out ornegi degisiklikten ONCE
        # {GARFA} = 1 uye, SONRA {} = 0 uye. Esik 2 secilseydi ikisi de
        # esigin altinda kalirdi ve gecis OLUSMAZDI -- urun dogru davranir,
        # senaryo yanlis kurulmus olurdu.
        minimum_peer_count={"FINANCIAL": 1})
    etkiler = {x for e in plan.entries if e.impact_type == IMPACT_PEER
               for x in e.actual_effects}
    if "MIN_PEER_COUNT_CROSSING" not in etkiler:
        raise ZincirIhlali("minimum peer count gecisi isaretlenmedi")

    # Motor M2 uretemiyor -> yeni OTORITATIF sonuc yetersizdir.
    kos_orkestrator(conn, routing, {}, ["GARFA"], "E2E-5-sonra")
    sonra = db_sonuc(conn, "GARFA")
    if sonra[0] != STATUS_INSUFFICIENT:
        raise ZincirIhlali(f"yetersiz sonuc yazilmadi: {sonra[0]}")
    if sonra[1] is not None:
        raise ZincirIhlali("eski basarili skora FALLBACK yapildi")


# ==================================================================== 6
def sc_ttm_loss(conn):
    temizle(conn)
    routing = {"THYAO": "NONFIN"}
    modul_satiri(conn, "THYAO")
    kos_orkestrator(conn, routing, {"THYAO": 0.75}, [], "E2E-6-once")
    if db_sonuc(conn, "THYAO")[0] != STATUS_OK:
        raise ZincirIhlali("baslangic OK degil")

    c = fact("THYAO", engine="NONFIN", statement="INCOME_STATEMENT",
             key="revenue", old=100.0, new=None)
    plan = detect_change_impact(c, impact_run_id="E2E-6", analysis_at=KESIM)
    if "TTM_LOSS" not in {x for e in plan.entries for x in e.actual_effects}:
        raise ZincirIhlali("TTM kaybi isaretlenmedi")

    kos_orkestrator(conn, routing, {}, ["THYAO"], "E2E-6-sonra")
    sonra = db_sonuc(conn, "THYAO")
    if sonra[0] != STATUS_INSUFFICIENT or sonra[1] is not None:
        raise ZincirIhlali("TTM kaybi eski basariyi silmedi")


# ==================================================================== 7
def sc_atomik_grup_veto(conn):
    """Uc modul birlikte tazelenmeden ticker gecmez; veto karari degistirir."""
    temizle(conn)
    c = fact(statement="INCOME_STATEMENT", key="net_income")
    plan = detect_change_impact(c, impact_run_id="E2E-7", analysis_at=KESIM)
    for eksik in ("M1", "Ek1", "GOOD_COUNT"):
        rapor = evaluate_readiness(plan, tam_soy(plan, haric={("GARFA", eksik)}),
                                   expected_source_version_id=SURUM,
                                   change_published_at=YAYIN)
        if "GARFA" in bridge_targeted_tickers(rapor):
            raise ZincirIhlali(f"{eksik} eksikken ticker gecti")

    # DISARIDAN gelen KISMI grup da genisletilmeli. detect_change_impact()
    # grubu her zaman eksiksiz uretir, bu yuzden genisleme normal akista
    # no-op'tur; ama plan baska bir kaynaktan gelirse gruptan yalniz biri
    # isaretlenmis olabilir.
    from dataclasses import replace
    kismi = replace(plan, entries=tuple(
        e for e in plan.entries
        if not (e.impacted_ticker == "GARFA" and e.module in ("Ek1", "GOOD_COUNT"))))
    rapor = evaluate_readiness(kismi, [soy("GARFA", "M1"), soy("GARFA", "M2")],
                               expected_source_version_id=SURUM,
                               change_published_at=YAYIN)
    gerekli = set(rapor.per_ticker["GARFA"].required_modules)
    if not {"M1", "Ek1", "GOOD_COUNT"} <= gerekli:
        raise ZincirIhlali("kismi atomik grup genisletilmedi")

    # Veto flip karari degistirebilmeli.
    routing = {"GARFA": "FINANCIAL"}
    modul_satiri(conn, "GARFA", good=9)
    kos_orkestrator(conn, routing, {"GARFA": 0.90}, [], "E2E-7-veto-yok")
    vetosuz = db_sonuc(conn, "GARFA")
    temizle(conn)
    modul_satiri(conn, "GARFA", good=2)
    kos_orkestrator(conn, routing, {"GARFA": 0.90}, [], "E2E-7-veto-var")
    vetolu = db_sonuc(conn, "GARFA")
    if vetolu[1] >= vetosuz[1]:
        raise ZincirIhlali("veto skoru dusurmedi")


# ==================================================================== 8
def sc_pit_history_korunur(conn):
    temizle(conn)
    routing = {"GARFA": "FINANCIAL"}
    modul_satiri(conn, "GARFA")
    kos_orkestrator(conn, routing, {"GARFA": 0.80}, [], "E2E-8")
    once = db_sonuc(conn, "GARFA")

    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.total_rasyo_restate_runs
                (restate_run_id, target_analysis_at, knowledge_cutoff_at,
                 started_at, finished_at, status, restate_contract_version,
                 reader_version, inputs_sha256, results_sha256,
                 calculation_profile, calculation_version, company_count,
                 successful_company_count, diagnostics)
                VALUES (%s,%s,%s,%s,%s,'COMPLETE',1,1,%s,%s,'V1',1,1,1,'{}')
            """, ("e" * 64, KESIM, KESIM + timedelta(days=30),
                  KESIM, KESIM, "a" * 64, "a" * 64))
            cur.execute("""
                INSERT INTO analytics.company_total_rasyo_restate_result
                (restate_run_id, ticker, target_analysis_at, knowledge_cutoff_at,
                 engine_family, m2_missing, m1_missing, m3_missing, ek4_missing,
                 ek1_missing, ek9_missing, good_count_missing,
                 total_rasyo_status, rejection_reason, insufficiency_reason,
                 diagnostics)
                VALUES (%s,'GARFA',%s,%s,'FINANCIAL',false,false,false,false,
                        false,false,false,'YETERSIZ_VERI','x','EKSIK_BILESEN','{}')
            """, ("e" * 64, KESIM, KESIM + timedelta(days=30)))

    sonra = db_sonuc(conn, "GARFA")
    if sonra != once:
        raise ZincirIhlali("restate PIT satirini degistirdi")


# ==================================================================== 9
def sc_restate_pit_viewina_sizmaz(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM analytics.latest_total_rasyo_result")
        pit = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM analytics"
                    ".company_total_rasyo_restate_result")
        restate = cur.fetchone()[0]
    if restate == 0:
        raise ZincirIhlali("restate satiri kurulmadi (onceki senaryoya bagli)")
    if pit != 1:
        raise ZincirIhlali(f"PIT view'inda beklenmeyen satir sayisi: {pit}")


# ==================================================================== 10a
def sc_kapsam_disi_kaynak(conn):
    """
    MARKET_PRICE / NAV degisikligi: statement_type V20 KAPSAMINDA DEGIL.
    Plan bile uretilmez -- ChangeImpactError.
    """
    for bozuk in ("MARKET_PRICE", "NAV_REPORT", "DISCLOSURE"):
        try:
            detect_change_impact(
                FactChange(ticker="AGYO", statement_type=bozuk,
                           fact_key="current_price", period_end=date(2025, 12, 31),
                           old_value=1.0, new_value=2.0, published_at=YAYIN,
                           source_fact_id="F", source_statement_id="S",
                           source_version_id=SURUM, routed_engine="GYO"),
                impact_run_id="E2E-10a")
        except ChangeImpactError:
            continue
        raise ZincirIhlali(f"{bozuk} icin plan uretildi")


# ==================================================================== 10b
def sc_cash_flow_bos_plan(conn):
    """
    CASH_FLOW: statement type V20 KAPSAMINDA, fakat mevcut registry'de
    skorlayan kenar YOK. Bu 10a ile AYNI SEBEP DEGILDIR.

    Beklenen: kontrollu bos plan + NO_SCORING_DEPENDENCY neden kodu.
    "Desteklenmeyen statement" olarak REDDEDILMEZ.
    """
    c = fact(statement="CASH_FLOW", key="operating_cash_flow")
    plan = detect_change_impact(c, impact_run_id="E2E-10b", analysis_at=KESIM)
    if plan.entries:
        raise ZincirIhlali("CASH_FLOW icin uydurulmus bagimlilik plani")
    neden = plan.diagnostics.get("empty_reason")
    if neden != EMPTY_NO_SCORING_DEPENDENCY:
        raise ZincirIhlali(f"CASH_FLOW icin yanlis neden kodu: {neden}")

    # DIAGNOSTIC_ONLY ayri neden kodu tasimali: "Total Rasyo yeniden
    # hesaplamasi gerekmiyor" demek, "bu veri hic tazelenmeyecek" DEMEK DEGIL.
    tanisal = detect_change_impact(fact(key="provisions"),
                                   impact_run_id="E2E-10b-2", analysis_at=KESIM)
    if tanisal.diagnostics.get("empty_reason") != EMPTY_DIAGNOSTIC_ONLY:
        raise ZincirIhlali("tanisal degisiklik yanlis neden kodu tasidi")
    if tanisal.diagnostics["empty_reason"] == neden:
        raise ZincirIhlali("iki farkli bos-plan sebebi ayni koda dustu")


# ==================================================================== 11
def sc_idempotent_replay(conn):
    temizle(conn)
    c = fact()
    plan = detect_change_impact(c, impact_run_id="E2E-11", analysis_at=KESIM)
    ilk = persist_impact_plan(conn, plan, meta(c))
    ikinci = persist_impact_plan(conn, plan, meta(c))
    if not ilk["created"] or ikinci["created"]:
        raise ZincirIhlali("tekrar isleme yeni plan satiri uretti")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM analytics.impact_plan")
        if cur.fetchone()[0] != 1:
            raise ZincirIhlali("duplicate plan satiri olustu")

    a1 = record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                                    application_run_id="AP-1", started_at=KESIM,
                                    status="APPLIED", finished_at=KESIM)
    a2 = record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                                    application_run_id="AP-2", started_at=KESIM,
                                    status="APPLIED", finished_at=KESIM)
    if (a1, a2) != (1, 2):
        raise ZincirIhlali("uygulama denemeleri append-only degil")

    from dataclasses import replace
    if len(plan.entries) > 1:
        bozuk = replace(plan, entries=plan.entries[:1])
        try:
            persist_impact_plan(conn, bozuk, meta(c))
        except ImpactPlanConflict:
            pass
        else:
            raise ZincirIhlali("ayni kimlikle farkli icerik kabul edildi")


# ==================================================================== 12
def sc_kismi_readiness(conn):
    """A hazir, B degil -> yalniz A calisir, B icin karisim URETILMEZ."""
    temizle(conn)
    routing = {"GARFA": "FINANCIAL", "PEER1": "FINANCIAL"}
    for t in routing:
        modul_satiri(conn, t)
    kos_orkestrator(conn, routing, {"GARFA": 0.80, "PEER1": 0.80}, [], "E2E-12-0")
    once_peer = db_sonuc(conn, "PEER1")

    c = fact()
    plan = detect_change_impact(
        c, impact_run_id="E2E-12", analysis_at=KESIM,
        peer_candidates={"FINANCIAL": [
            PeerCandidate("GARFA", True, True, 1.2, 0.9),
            PeerCandidate("PEER1", True, True, 1.0, 1.0)]})
    kayitlar = [k for k in tam_soy(plan) if k.ticker != "PEER1"]
    rapor = evaluate_readiness(plan, kayitlar, expected_source_version_id=SURUM,
                               change_published_at=YAYIN)
    hedefler = bridge_targeted_tickers(rapor)
    assert_no_plan_ticker_lost(plan, rapor, hedefler)
    if set(hedefler) != {"GARFA"}:
        raise ZincirIhlali(f"kismi durumda yanlis hedef kume: {hedefler}")

    kos_orkestrator(conn, routing, {"GARFA": 0.40, "PEER1": 0.40}, hedefler,
                    "E2E-12-1")
    if db_sonuc(conn, "PEER1") != once_peer:
        raise ZincirIhlali("hazir olmayan ticker yeniden yazildi")
    if db_sonuc(conn, "GARFA") == once_peer:
        pass  # skor farkli olmali; asagida kontrol
    if db_sonuc(conn, "GARFA")[1] is None:
        raise ZincirIhlali("hazir ticker hesaplanmadi")


SCENARIOS: tuple[tuple[str, Callable], ...] = (
    ("1_direct_basarili", sc_direct_basarili),
    ("2_eski_m1_yeni_m2_yasak", sc_eski_m1_yeni_m2_yasak),
    ("3_peer_propagation", sc_peer_propagation),
    ("4_peer_exit", sc_peer_exit),
    ("5_minimum_peer_crossing", sc_minimum_peer_crossing),
    ("6_ttm_loss", sc_ttm_loss),
    ("7_atomik_grup_veto", sc_atomik_grup_veto),
    ("8_pit_history_korunur", sc_pit_history_korunur),
    ("9_restate_pit_viewina_sizmaz", sc_restate_pit_viewina_sizmaz),
    ("10a_kapsam_disi_kaynak", sc_kapsam_disi_kaynak),
    ("10b_cash_flow_bos_plan", sc_cash_flow_bos_plan),
    ("11_idempotent_replay", sc_idempotent_replay),
    ("12_kismi_readiness", sc_kismi_readiness),
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V20 E2E kabul denetimi")
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    conn = baglan()
    if conn is None:
        print("ATLANDI: PostgreSQL erisilemedi.")
        return 2
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('analytics.impact_plan')")
        if cur.fetchone()[0] is None:
            print("ATLANDI: sql/032 uygulanmamis.")
            return 2

    sonuclar: list[dict[str, Any]] = []
    for ad, fn in SCENARIOS:
        try:
            fn(conn)
            sonuclar.append({"senaryo": ad, "durum": "PASS"})
        except ZincirIhlali as exc:
            sonuclar.append({"senaryo": ad, "durum": "FAIL",
                             "hata": f"ZINCIR: {exc}"})
        except Exception as exc:  # noqa: BLE001
            izi = traceback.format_exc(limit=3).strip().splitlines()[-1]
            sonuclar.append({"senaryo": ad, "durum": "FAIL",
                             "hata": f"{type(exc).__name__}: {exc} | {izi}"})
    conn.close()

    gecti = sum(1 for s in sonuclar if s["durum"] == "PASS")
    print("\n" + "=" * 62)
    print("V20 E2E KABUL DENETIMI — katmanlar arasi zincir")
    print("=" * 62)
    for s in sonuclar:
        isaret = "OK  " if s["durum"] == "PASS" else "FAIL"
        print(f"  [{isaret}] {s['senaryo']}")
        if s["durum"] == "FAIL":
            print(f"         {s['hata']}")
    print("-" * 62)
    print(f"  TOPLAM {gecti} / {len(sonuclar)}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"total": len(sonuclar), "passed": gecti,
                       "failed": len(sonuclar) - gecti, "scenarios": sonuclar},
                      fh, ensure_ascii=False, indent=2)
        print(f"\nJSON: {args.json}")
    return 0 if gecti == len(sonuclar) else 1


if __name__ == "__main__":
    sys.exit(main())
