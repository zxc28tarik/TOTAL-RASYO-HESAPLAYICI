#!/usr/bin/env python3
"""
V23-B E2E KABUL DENETIMI — gercek zincir, gercek PostgreSQL.

    V19 orkestratör (PIT sonucu)
      -> V23-A restate okuyucu/hesaplayici (M2 nedeniyle YETERSIZ_VERI)
      -> V23-B reconciliation (PIT <-> RESTATE)

Kullanim: python3 -m src.analytics.restate_pit_reconciliation_e2e_audit
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import psycopg2

from src.analytics.restate_pit_collector import (
    fetch_pit_snapshots,
    fetch_restate_run_tickers,
    fetch_restate_snapshots,
)
from src.analytics.restate_pit_persistence import persist_restate_pit_reconciliation
from src.analytics.restate_pit_reconciliation import (
    STATUS_INCOMPLETE,
    STATUS_MISMATCH,
    STATUS_PASS,
    reconcile_pit_vs_restate,
)
from src.analytics.total_rasyo_orchestrator import run_total_rasyo_orchestrator
from src.analytics.total_rasyo_restate_calculator import compute_restate
from src.analytics.total_rasyo_restate_persistence import persist_restate
from src.analytics.total_rasyo_restate_reader import fetch_restate_module_context
import src.analytics.total_rasyo_self_audit as V19

TZ = timezone(timedelta(hours=3))
ANALIZ = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
CUTOFF = datetime(2026, 4, 1, 10, 0, tzinfo=TZ)
KAYNAK = ANALIZ - timedelta(hours=1)


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
            cur.execute("TRUNCATE analytics.reconciliation_restate_finding,"
                        " analytics.reconciliation_restate_run,"
                        " analytics.total_rasyo_restate_module_input,"
                        " analytics.company_total_rasyo_restate_result,"
                        " analytics.total_rasyo_restate_runs")
            cur.execute("TRUNCATE analytics.reconciliation_module_check,"
                        " analytics.reconciliation_module_run,"
                        " analytics.total_rasyo_module_input,"
                        " analytics.company_total_rasyo_result,"
                        " analytics.daily_engine_run, analytics.total_rasyo_run")
            cur.execute("TRUNCATE analytics.module_production_lineage")
            cur.execute("DELETE FROM analytics.module_scores")
            cur.execute("DELETE FROM analytics.kap_bank_batch_runs")


def batch_run(conn, run_key_hex, *, analysis_at):
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.kap_bank_batch_runs
                (run_key, analysis_at, asof_date, anchor_period_end,
                 horizon_days, pipeline_version, source, status,
                 requested_count, prepared_count, result_count,
                 rejected_count, sector_scale_eligible_count,
                 valuation_ok_count, report_sha256)
                VALUES (%s,%s,%s,%s,20,'TEST_V1','TEST','COMPLETE',1,1,1,0,0,1,%s)
                ON CONFLICT (run_key) DO NOTHING
            """, (run_key_hex, analysis_at, analysis_at.date(), analysis_at.date(),
                  "a" * 64))


def modul_satiri(conn, ticker, *, analysis_at=KAYNAK, run_key_hex="1" * 64,
                 period_end=date(2025, 12, 31)):
    batch_run(conn, run_key_hex, analysis_at=analysis_at)
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.module_scores
                (ticker, asof_date, period_end, horizon_days, m1, m3, ek1, ek4, ek9,
                 good_count_ge8, analysis_at, source_run_key)
                VALUES (%s,%s,%s,20,0.6,0.7,0.4,0.5,0.3,9,%s,%s)
            """, (ticker, analysis_at.date(), period_end, analysis_at, run_key_hex))


def orkestratör_kos(conn, ticker, run_id):
    routing = {ticker: "FINANCIAL"}
    m2_payload = {"m2": 0.8, "m2_source": "AUDIT_V1", "valuation_usable": True,
                 "valuation_confidence": 0.7, "valuation_reason": None,
                 "m2_source_at": ANALIZ}
    runners = {"FINANCIAL": lambda: {"results": {ticker: m2_payload}, "rejections": {}}}
    return run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing, engine_runners=runners, run_id=run_id)


def restate_kos(conn, tickers):
    ctx = fetch_restate_module_context(
        conn, tickers=tickers, target_analysis_at=ANALIZ, knowledge_cutoff_at=CUTOFF)
    comp = compute_restate(target_analysis_at=ANALIZ, knowledge_cutoff_at=CUTOFF,
                           tickers=tickers, module_contexts=ctx)
    persist_restate(conn, comp, started_at=ANALIZ, finished_at=ANALIZ)
    return comp


def reconcile_et(conn, restate_run_id):
    tickers = fetch_restate_run_tickers(conn, restate_run_id=restate_run_id)
    pit = fetch_pit_snapshots(conn, target_analysis_at=ANALIZ, tickers=tickers)
    restate = fetch_restate_snapshots(conn, restate_run_id=restate_run_id, tickers=tickers)
    r = reconcile_pit_vs_restate(restate_run_id=restate_run_id, tickers=tickers,
                                 pit_snapshots=pit, restate_snapshots=restate)
    persist_restate_pit_reconciliation(conn, r, started_at=ANALIZ, finished_at=ANALIZ)
    return r


# ==================================================================== 1
def sc_m2_incomplete_gercek_zincir(conn):
    temizle(conn)
    modul_satiri(conn, "GARFA")
    orkestratör_kos(conn, "GARFA", "ORCH-1")
    comp = restate_kos(conn, ["GARFA"])
    r = reconcile_et(conn, comp.restate_run_id)
    if r.status != STATUS_INCOMPLETE:
        raise ZincirIhlali(f"M2 nedenli zincirde INCOMPLETE beklenirdi: {r.status}")
    if r.mismatch_count != 0:
        raise ZincirIhlali("sahte mismatch uretildi")


# ==================================================================== 2
def sc_view_hukum_kaynagi_degil_gercek_zincir(conn):
    """restate_vs_pit_comparison view'i sahte fark uretse bile reconciliation
    dogru sonucu vermeli."""
    temizle(conn)
    modul_satiri(conn, "GARFA")
    orkestratör_kos(conn, "GARFA", "ORCH-2")
    comp = restate_kos(conn, ["GARFA"])
    with conn.cursor() as cur:
        cur.execute("SELECT decision_changed FROM analytics"
                    ".restate_vs_pit_comparison WHERE ticker='GARFA'")
        view_sonucu = cur.fetchone()
    if view_sonucu != (True,):
        raise ZincirIhlali(f"view'in sahte fark davranisi degisti: {view_sonucu}")
    r = reconcile_et(conn, comp.restate_run_id)
    if r.mismatch_count != 0:
        raise ZincirIhlali("view'in sahte farki reconciliation'a sizdi")


# ==================================================================== 3
def sc_pit_eksik_gercek_zincir(conn):
    """RESTATE hesaplanir ama PIT hic yoktur (orkestratör hic calismamis)."""
    temizle(conn)
    modul_satiri(conn, "GARFA")
    comp = restate_kos(conn, ["GARFA"])  # PIT YOK, orkestratör CALISTIRILMADI
    r = reconcile_et(conn, comp.restate_run_id)
    if r.status != STATUS_INCOMPLETE:
        raise ZincirIhlali(f"PIT yokken INCOMPLETE beklenirdi: {r.status}")
    if "GARFA" not in r.pit_missing_tickers():
        raise ZincirIhlali("PIT_MISSING isaretlenmedi")


# ==================================================================== 4
def sc_idempotent_replay_gercek_zincir(conn):
    temizle(conn)
    modul_satiri(conn, "GARFA")
    orkestratör_kos(conn, "GARFA", "ORCH-4")
    comp = restate_kos(conn, ["GARFA"])
    r1 = reconcile_et(conn, comp.restate_run_id)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM analytics.reconciliation_restate_run")
        once = cur.fetchone()[0]
    r2 = reconcile_et(conn, comp.restate_run_id)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM analytics.reconciliation_restate_run")
        sonra = cur.fetchone()[0]
    if once != sonra:
        raise ZincirIhlali("tekrar calistirma satir sayisini artirdi")
    if r1.reconciliation_run_id != r2.reconciliation_run_id:
        raise ZincirIhlali("ayni girdi farkli kimlik uretti")


SCENARIOS = (
    ("1_m2_nedenli_INCOMPLETE_gercek_zincir", sc_m2_incomplete_gercek_zincir),
    ("2_view_hukum_kaynagi_degil", sc_view_hukum_kaynagi_degil_gercek_zincir),
    ("3_pit_eksik_gercek_zincir", sc_pit_eksik_gercek_zincir),
    ("4_idempotent_replay_gercek_zincir", sc_idempotent_replay_gercek_zincir),
)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="V23-B E2E kabul denetimi")
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    conn = baglan()
    if conn is None:
        print("ATLANDI: PostgreSQL erisilemedi.")
        return 2
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('analytics.reconciliation_restate_run')")
        if cur.fetchone()[0] is None:
            print("ATLANDI: sql/039 uygulanmamis.")
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
    print("V23-B E2E KABUL DENETIMI")
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
