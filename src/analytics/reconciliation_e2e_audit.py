#!/usr/bin/env python3
"""
V21 Reconciliation-1 E2E KABUL DENETIMI — gercek zincir, gercek PostgreSQL.

    fact degisikligi -> impact plan -> application target snapshot ->
    readiness -> orchestrator -> total_rasyo_result -> reconciliation

Dort kanit AYRI AYRI dogrulanir:
    tam eslesme          = PASS
    bir hedef eksik      = MISSING
    fazladan islenen     = UNEXPECTED
    yanlis/eski run kimligi = STALE

Kullanim: python3 -m src.analytics.reconciliation_e2e_audit
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import psycopg2

from src.analytics.change_impact_bridge import (
    ModuleLineage,
    bridge_targeted_tickers,
    evaluate_readiness,
)
from src.analytics.change_impact_detector import FactChange, PeerCandidate, detect_change_impact
from src.analytics.change_impact_persistence import persist_impact_plan, record_application_attempt
from src.analytics.reconciliation_impact_orchestrator import (
    STATUS_MISMATCH,
    STATUS_PASS,
    FINDING_MISSING,
    FINDING_STALE,
    FINDING_UNEXPECTED,
    reconcile_impact_vs_actual,
)
from src.analytics.reconciliation_persistence import (
    fetch_actual_rows_full,
    persist_application_targets,
    persist_reconciliation_result,
)
from src.analytics.total_rasyo_orchestrator import run_total_rasyo_orchestrator
import src.analytics.total_rasyo_self_audit as V19

TZ = timezone(timedelta(hours=3))
KESIM = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
YAYIN = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)
URETIM = datetime(2026, 3, 2, 12, 0, tzinfo=TZ)
SURUM = "V2"


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
            cur.execute("DELETE FROM analytics.module_scores")


def fact(ticker="GARFA", **kw):
    d = dict(statement_type="BALANCE_SHEET", fact_key="total_equity",
             period_end=date(2025, 12, 31), old_value=1000.0, new_value=800.0,
             published_at=YAYIN, source_fact_id="F1", source_statement_id="S1",
             source_version_id=SURUM, routed_engine="FINANCIAL")
    d.update(kw)
    return FactChange(ticker=ticker, **d)


def meta(c):
    return dict(direct_ticker=c.ticker, source_fact_id=c.source_fact_id,
                source_statement_id=c.source_statement_id,
                source_version_id=c.source_version_id,
                statement_type=c.statement_type, fact_key=c.fact_key,
                changed_period_end=c.period_end, published_at=c.published_at)


def modul_satiri(conn, ticker):
    d = {"M1": 0.62, "M3": 0.71, "Ek4": 0.55, "Ek1": 0.48, "Ek9": 0.33}
    V19.modul_satiri(conn, ticker, asof=date(2026, 3, 2),
                     analysis_at=KESIM - timedelta(hours=1), degerler=d, good=9)


def kur_zincir(conn, tickers):
    routing = {t: "FINANCIAL" for t in tickers}
    for t in tickers:
        modul_satiri(conn, t)
    c = fact(tickers[0])
    plan = detect_change_impact(
        c, impact_run_id="E2E-REC", analysis_at=KESIM,
        peer_candidates={"FINANCIAL": [PeerCandidate(t, True, True, 1.0, 0.9)
                                       for t in tickers]})
    persist_impact_plan(conn, plan, meta(c))
    lineage = []
    for t in plan.targeted_tickers():
        mods = {e.module for e in plan.entries if e.impacted_ticker == t}
        if mods & {"M1", "Ek1", "GOOD_COUNT"}:
            mods |= {"M1", "Ek1", "GOOD_COUNT"}
        lineage += [ModuleLineage(t, m, None, SURUM, URETIM, "V1", 1) for m in mods]
    rapor = evaluate_readiness(plan, lineage, expected_source_version_id=SURUM,
                               change_published_at=YAYIN)
    hedefler = bridge_targeted_tickers(rapor)
    return plan, routing, hedefler


def bir_reconciliation(conn, plan, app_id, hedefler, *, orch_run_id, gonderilen):
    record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                               application_run_id=app_id, started_at=KESIM,
                               status="APPLIED", finished_at=KESIM,
                               orchestrator_run_id=orch_run_id,
                               targeted_ticker_count=len(hedefler))
    persist_application_targets(conn, application_run_id=app_id, tickers=hedefler)
    actual = fetch_actual_rows_full(conn, analysis_at=KESIM,
                                    expected_tickers=hedefler,
                                    orchestrator_run_id=orch_run_id)
    r = reconcile_impact_vs_actual(
        application_run_id=app_id, impact_plan_id=plan.impact_plan_id,
        analysis_at=KESIM, started_at=KESIM, finished_at=KESIM,
        application_status="APPLIED", expected_tickers=hedefler,
        actual_rows=actual, orchestrator_run_id=orch_run_id)
    persist_reconciliation_result(conn, r)
    return r


# ==================================================================== 1) PASS
def sc_tam_eslesme_PASS(conn):
    temizle(conn)
    plan, routing, hedefler = kur_zincir(conn, ("GARFA",))
    orch_id = "ORCH-PASS"
    runners = {"FINANCIAL": V19.motor({t: V19.m2cikti(0.8) for t in hedefler})}
    run_total_rasyo_orchestrator(conn, analysis_at=KESIM, routing=routing,
                                 engine_runners=runners, run_id=orch_id,
                                 targeted_tickers=list(hedefler))
    r = bir_reconciliation(conn, plan, "APP-PASS", hedefler,
                           orch_run_id=orch_id, gonderilen=hedefler)
    if r.status != STATUS_PASS:
        raise ZincirIhlali(f"tam eslesme PASS vermedi: {r.status} {r.findings}")


# ==================================================================== 2) MISSING
def sc_bir_hedef_eksik_MISSING(conn):
    temizle(conn)
    plan, routing, hedefler = kur_zincir(conn, ("GARFA", "PEER1"))
    if len(hedefler) < 2:
        raise ZincirIhlali("senaryo kurulumu iki hedef gerektirir")
    orch_id = "ORCH-MISSING"
    eksik_hedef = list(hedefler)[:1]  # BILEREK bir tanesi atlaniyor
    runners = {"FINANCIAL": V19.motor({t: V19.m2cikti(0.8) for t in eksik_hedef})}
    run_total_rasyo_orchestrator(conn, analysis_at=KESIM, routing=routing,
                                 engine_runners=runners, run_id=orch_id,
                                 targeted_tickers=eksik_hedef)
    r = bir_reconciliation(conn, plan, "APP-MISSING", hedefler,
                           orch_run_id=orch_id, gonderilen=eksik_hedef)
    if r.status != STATUS_MISMATCH:
        raise ZincirIhlali(f"eksik hedef MISMATCH vermedi: {r.status}")
    if set(r.missing()) != (set(hedefler) - set(eksik_hedef)):
        raise ZincirIhlali(f"MISSING kumesi yanlis: {r.missing()}")


# ==================================================================== 3) UNEXPECTED
def sc_fazladan_islenen_UNEXPECTED(conn):
    """
    Orkestratöre readiness bariyerinden GECMEMIS bir ticker'i dogrudan
    hedef listesine ekleyerek "beklenmeyen isleme" uretilir.
    """
    temizle(conn)
    plan, routing, hedefler = kur_zincir(conn, ("GARFA",))
    orch_id = "ORCH-UNEXPECTED"
    # PEER1 readiness'ten GECMEDI (lineage yok) ama orkestratöre YINE DE
    # gonderiliyor -- bariyer atlanmis gibi bir durum simule ediliyor.
    modul_satiri(conn, "PEER1")
    routing["PEER1"] = "FINANCIAL"
    gonderilen = list(hedefler) + ["PEER1"]
    runners = {"FINANCIAL": V19.motor({t: V19.m2cikti(0.8) for t in gonderilen})}
    run_total_rasyo_orchestrator(conn, analysis_at=KESIM, routing=routing,
                                 engine_runners=runners, run_id=orch_id,
                                 targeted_tickers=gonderilen)
    r = bir_reconciliation(conn, plan, "APP-UNEXPECTED", hedefler,
                           orch_run_id=orch_id, gonderilen=gonderilen)
    if r.status != STATUS_MISMATCH:
        raise ZincirIhlali(f"fazladan isleme MISMATCH vermedi: {r.status}")
    if "PEER1" not in r.unexpected():
        raise ZincirIhlali(f"UNEXPECTED PEER1'i yakalamadi: {r.unexpected()}")


# ==================================================================== 4) STALE
def sc_yanlis_run_kimligi_STALE(conn):
    """
    Bu attempt'in beklediginden FARKLI bir run_id ile yazilmis (yani baska
    bir kosu tarafindan sonradan ezilmis gibi gorunen) kanonik satir.
    """
    temizle(conn)
    plan, routing, hedefler = kur_zincir(conn, ("GARFA",))
    baska_run_id = "ORCH-BASKA-KOSU"
    runners = {"FINANCIAL": V19.motor({t: V19.m2cikti(0.8) for t in hedefler})}
    run_total_rasyo_orchestrator(conn, analysis_at=KESIM, routing=routing,
                                 engine_runners=runners, run_id=baska_run_id,
                                 targeted_tickers=list(hedefler))

    # Bu reconciliation FARKLI bir orkestratör run_id BEKLIYOR (kendi
    # attempt'i), ama satir zaten baska_run_id ile yazilmis durumda.
    beklenen_run_id = "ORCH-BU-ATTEMPT"
    r = bir_reconciliation(conn, plan, "APP-STALE", hedefler,
                           orch_run_id=beklenen_run_id, gonderilen=hedefler)
    if r.status != STATUS_MISMATCH:
        raise ZincirIhlali(f"yanlis run kimligi MISMATCH vermedi: {r.status}")
    if set(r.stale()) != set(hedefler):
        raise ZincirIhlali(f"STALE kumesi yanlis: {r.stale()}")
    if not r.stale_check_performed:
        raise ZincirIhlali("STALE bulundu ama stale_check_performed False")


SCENARIOS = (
    ("1_tam_eslesme_PASS", sc_tam_eslesme_PASS),
    ("2_bir_hedef_eksik_MISSING", sc_bir_hedef_eksik_MISSING),
    ("3_fazladan_islenen_UNEXPECTED", sc_fazladan_islenen_UNEXPECTED),
    ("4_yanlis_run_kimligi_STALE", sc_yanlis_run_kimligi_STALE),
)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="V21 Reconciliation E2E denetimi")
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    conn = baglan()
    if conn is None:
        print("ATLANDI: PostgreSQL erisilemedi.")
        return 2
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('analytics.reconciliation_run')")
        if cur.fetchone()[0] is None:
            print("ATLANDI: sql/035 uygulanmamis.")
            return 2

    sonuclar = []
    for ad, fn in SCENARIOS:
        try:
            fn(conn)
            sonuclar.append({"senaryo": ad, "durum": "PASS"})
        except ZincirIhlali as exc:
            sonuclar.append({"senaryo": ad, "durum": "FAIL", "hata": f"ZINCIR: {exc}"})
        except Exception as exc:  # noqa: BLE001
            import traceback
            izi = traceback.format_exc(limit=3).strip().splitlines()[-1]
            sonuclar.append({"senaryo": ad, "durum": "FAIL",
                             "hata": f"{type(exc).__name__}: {exc} | {izi}"})
    conn.close()

    gecti = sum(1 for s in sonuclar if s["durum"] == "PASS")
    print("\n" + "=" * 62)
    print("V21 RECONCILIATION E2E KABUL DENETIMI")
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
