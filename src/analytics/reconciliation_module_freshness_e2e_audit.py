#!/usr/bin/env python3
"""
V22-B E2E KABUL DENETIMI — gercek zincir, gercek PostgreSQL.

    modul uretimi (module_scores + fan-out lineage)
      -> V19 orkestratörü (Total Rasyo hesabi)
      -> V22-A tuketim-ani snapshot
      -> V22-B reconciliation (freshness + lineage)

Kullanim: python3 -m src.analytics.reconciliation_module_freshness_e2e_audit
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import psycopg2

from src.analytics.module_producer_lineage import ModuleRow, persist_producer_lineage
from src.analytics.reconciliation_module_collector import (
    fetch_consumed_modules,
    fetch_successors,
)
from src.analytics.reconciliation_module_freshness import (
    STATUS_INCOMPLETE,
    STATUS_MISMATCH,
    STATUS_PASS,
    reconcile_module_freshness,
)
from src.analytics.reconciliation_module_persistence import persist_module_reconciliation
from src.analytics.total_rasyo_module_input_snapshot import persist_module_input_snapshot
from src.analytics.total_rasyo_orchestrator import run_total_rasyo_orchestrator
import src.analytics.total_rasyo_self_audit as V19

TZ = timezone(timedelta(hours=3))
ANALIZ = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
KAYNAK = ANALIZ - timedelta(hours=1)
URETIM = ANALIZ - timedelta(minutes=30)


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
            cur.execute("TRUNCATE analytics.reconciliation_module_check,"
                        " analytics.reconciliation_module_run,"
                        " analytics.total_rasyo_module_input,"
                        " analytics.company_total_rasyo_result,"
                        " analytics.daily_engine_run, analytics.total_rasyo_run")
            cur.execute("TRUNCATE analytics.module_production_lineage")
            cur.execute("DELETE FROM analytics.module_scores")
            cur.execute("DELETE FROM analytics.kap_bank_batch_runs")


def modul_ve_lineage(conn, ticker, *, run_key_hex, analysis_at=KAYNAK,
                     pipeline_version="TEST_V1"):
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.kap_bank_batch_runs
                (run_key, analysis_at, asof_date, anchor_period_end,
                 horizon_days, pipeline_version, source, status,
                 requested_count, prepared_count, result_count,
                 rejected_count, sector_scale_eligible_count,
                 valuation_ok_count, report_sha256)
                VALUES (%s,%s,%s,%s,20,%s,'TEST','COMPLETE',1,1,1,0,0,1,%s)
                ON CONFLICT (run_key) DO NOTHING
            """, (run_key_hex, analysis_at, analysis_at.date(), analysis_at.date(),
                  pipeline_version, "a" * 64))
            cur.execute("""
                INSERT INTO analytics.module_scores
                (ticker, asof_date, horizon_days, m1, m3, ek1, ek4, ek9,
                 good_count_ge8, analysis_at, source_run_key)
                VALUES (%s,%s,20,0.6,0.7,0.4,0.5,0.3,9,%s,%s)
                ON CONFLICT (ticker, asof_date, horizon_days) DO UPDATE SET
                  analysis_at=EXCLUDED.analysis_at,
                  source_run_key=EXCLUDED.source_run_key
            """, (ticker, analysis_at.date(), analysis_at, run_key_hex))
    persist_producer_lineage(conn, [ModuleRow(ticker, date(2025, 12, 31))],
                             analysis_at=analysis_at, produced_at=URETIM,
                             source_run_key=run_key_hex)


def orkestratör_kos(conn, ticker, run_id, *, m2_source_at=None):
    routing = {ticker: "FINANCIAL"}
    m2_payload = {"m2": 0.8, "m2_source": "AUDIT_V1", "valuation_usable": True,
                 "valuation_confidence": 0.7, "valuation_reason": None,
                 "m2_source_at": m2_source_at or ANALIZ}
    runners = {"FINANCIAL": lambda: {"results": {ticker: m2_payload}, "rejections": {}}}
    return run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing, engine_runners=runners,
        run_id=run_id)


def reconcile_et(conn, ticker, run_id):
    consumed = fetch_consumed_modules(conn, total_rasyo_run_id=run_id, ticker=ticker)
    successors = fetch_successors(conn, ticker=ticker, total_rasyo_analysis_at=ANALIZ,
                                  consumed_modules=consumed or {})
    r = reconcile_module_freshness(
        total_rasyo_run_id=run_id, ticker=ticker, analysis_at=ANALIZ,
        started_at=ANALIZ, finished_at=ANALIZ, consumed_modules=consumed or {},
        successors=successors, evidence_available=consumed is not None)
    persist_module_reconciliation(conn, r)
    return r


# ==================================================================== 1
def sc_uctan_uca_temiz_PASS(conn):
    temizle(conn)
    modul_ve_lineage(conn, "GARFA", run_key_hex="1" * 64)
    rapor = orkestratör_kos(conn, "GARFA", "ORCH-1")
    persist_module_input_snapshot(conn, total_rasyo_run_id="ORCH-1",
                                  results=rapor["results"])
    r = reconcile_et(conn, "GARFA", "ORCH-1")
    if r.status != STATUS_PASS:
        raise ZincirIhlali(f"uctan uca temiz zincir PASS vermedi: {r.status}")
    if r.fully_verified is not True:
        raise ZincirIhlali("BANK/FINANCIAL kimligi bilinirken fully_verified False")


# ==================================================================== 2
def sc_daha_sonra_gelen_duzeltme_TOTAL_STALE(conn):
    temizle(conn)
    modul_ve_lineage(conn, "GARFA", run_key_hex="2" * 64)
    rapor = orkestratör_kos(conn, "GARFA", "ORCH-2")
    persist_module_input_snapshot(conn, total_rasyo_run_id="ORCH-2",
                                  results=rapor["results"])
    # Tuketimden SONRA, hala ANALIZ sinirinda, YENI bir uretici satiri.
    persist_producer_lineage(conn, [ModuleRow("GARFA", date(2025, 12, 31))],
                             analysis_at=KAYNAK + timedelta(minutes=15),
                             produced_at=URETIM + timedelta(minutes=20),
                             source_run_key="9" * 64)
    r = reconcile_et(conn, "GARFA", "ORCH-2")
    if r.status != STATUS_MISMATCH:
        raise ZincirIhlali(f"gec gelen duzeltme MISMATCH vermedi: {r.status}")
    if "M1" not in r.total_stale_modules():
        raise ZincirIhlali("TOTAL_STALE M1'de yakalanmadi")


# ==================================================================== 3
def sc_look_ahead_korumasi_uctan_uca(conn):
    temizle(conn)
    modul_ve_lineage(conn, "GARFA", run_key_hex="3" * 64)
    rapor = orkestratör_kos(conn, "GARFA", "ORCH-3")
    persist_module_input_snapshot(conn, total_rasyo_run_id="ORCH-3",
                                  results=rapor["results"])
    # GELECEKTEKI (analysis_at siniri disinda) bir uretici satiri.
    persist_producer_lineage(conn, [ModuleRow("GARFA", date(2025, 12, 31))],
                             analysis_at=ANALIZ + timedelta(days=1),
                             produced_at=URETIM, source_run_key="8" * 64)
    r = reconcile_et(conn, "GARFA", "ORCH-3")
    if "M1" in r.total_stale_modules():
        raise ZincirIhlali("gelecekteki satir look-ahead korumasini ihlal etti")
    if r.status != STATUS_PASS:
        raise ZincirIhlali(f"look-ahead korumali durum PASS olmali: {r.status}")


# ==================================================================== 4
def sc_kimlik_degisimi_LINEAGE_STALE(conn):
    temizle(conn)
    modul_ve_lineage(conn, "GARFA", run_key_hex="4" * 64)
    rapor = orkestratör_kos(conn, "GARFA", "ORCH-4")
    persist_module_input_snapshot(conn, total_rasyo_run_id="ORCH-4",
                                  results=rapor["results"])
    # AYNI etikette (KAYNAK) FARKLI bir kimlikle yeniden uretim (duzeltme).
    persist_producer_lineage(conn, [ModuleRow("GARFA", date(2025, 12, 31))],
                             analysis_at=KAYNAK, produced_at=URETIM,
                             source_run_key="5" * 64)
    r = reconcile_et(conn, "GARFA", "ORCH-4")
    if r.status != STATUS_MISMATCH:
        raise ZincirIhlali(f"kimlik degisimi MISMATCH vermedi: {r.status}")
    if "M1" not in r.lineage_stale_modules():
        raise ZincirIhlali("MODULE_LINEAGE_STALE yakalanmadi")


# ==================================================================== 5
def sc_snapshot_yoksa_INCOMPLETE(conn):
    temizle(conn)
    modul_ve_lineage(conn, "GARFA", run_key_hex="6" * 64)
    orkestratör_kos(conn, "GARFA", "ORCH-5")
    # V22-A snapshot BILEREK yazilmadi.
    r = reconcile_et(conn, "GARFA", "ORCH-5")
    if r.status != STATUS_INCOMPLETE:
        raise ZincirIhlali(f"kanit yokken INCOMPLETE vermedi: {r.status}")


# ==================================================================== 6
def sc_m2_lineage_hicbir_zaman_hukum_vermez(conn):
    temizle(conn)
    modul_ve_lineage(conn, "GARFA", run_key_hex="7" * 64)
    rapor = orkestratör_kos(conn, "GARFA", "ORCH-6")
    persist_module_input_snapshot(conn, total_rasyo_run_id="ORCH-6",
                                  results=rapor["results"])
    r = reconcile_et(conn, "GARFA", "ORCH-6")
    if r.checks["M2"].lineage_performed:
        raise ZincirIhlali("M2 lineage YAPILDI (mimari olarak imkansiz olmali)")
    if r.status == STATUS_PASS and r.fully_verified is not True:
        raise ZincirIhlali("M2 lineage eksikligi fully_verified'i yanlis etkiledi")


SCENARIOS = (
    ("1_uctan_uca_temiz_PASS", sc_uctan_uca_temiz_PASS),
    ("2_daha_sonra_gelen_duzeltme_TOTAL_STALE", sc_daha_sonra_gelen_duzeltme_TOTAL_STALE),
    ("3_look_ahead_korumasi", sc_look_ahead_korumasi_uctan_uca),
    ("4_kimlik_degisimi_LINEAGE_STALE", sc_kimlik_degisimi_LINEAGE_STALE),
    ("5_snapshot_yoksa_INCOMPLETE", sc_snapshot_yoksa_INCOMPLETE),
    ("6_m2_lineage_hicbir_zaman", sc_m2_lineage_hicbir_zaman_hukum_vermez),
)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="V22-B E2E kabul denetimi")
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    conn = baglan()
    if conn is None:
        print("ATLANDI: PostgreSQL erisilemedi.")
        return 2
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('analytics.reconciliation_module_run')")
        if cur.fetchone()[0] is None:
            print("ATLANDI: sql/037 uygulanmamis.")
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
    print("V22-B E2E KABUL DENETIMI")
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
