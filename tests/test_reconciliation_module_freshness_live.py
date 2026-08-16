"""
V22-B canli PostgreSQL testleri — toplayici + kalicilik.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

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
from src.analytics.reconciliation_module_persistence import (
    ModuleReconciliationConflict,
    persist_module_reconciliation,
)
from src.analytics.total_rasyo_module_input_snapshot import persist_module_input_snapshot

TZ = timezone(timedelta(hours=3))
ANALIZ = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
KAYNAK = ANALIZ - timedelta(hours=1)
URETIM = ANALIZ - timedelta(minutes=30)


def _baglan():
    dsn = os.environ.get("TOTAL_RASYO_TEST_DSN") or os.environ.get("PGDATABASE")
    if not dsn:
        pytest.skip("TOTAL_RASYO_TEST_DSN / PGDATABASE tanimli degil")
    try:
        if "=" in dsn or dsn.startswith("postgres"):
            return psycopg2.connect(dsn)
        return psycopg2.connect(dbname=dsn)
    except psycopg2.Error as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL erisilemedi: {exc}")


@pytest.fixture()
def conn():
    c = _baglan()
    with c:
        with c.cursor() as cur:
            cur.execute("SELECT to_regclass('analytics.reconciliation_module_run')")
            if cur.fetchone()[0] is None:
                c.close()
                pytest.skip("sql/037 uygulanmamis")
            cur.execute("TRUNCATE analytics.reconciliation_module_check,"
                        " analytics.reconciliation_module_run,"
                        " analytics.total_rasyo_module_input,"
                        " analytics.total_rasyo_run")
            cur.execute("TRUNCATE analytics.module_production_lineage")
            cur.execute("DELETE FROM analytics.module_scores")
            cur.execute("DELETE FROM analytics.kap_bank_batch_runs")
            cur.execute("TRUNCATE analytics.company_total_rasyo_result,"
                        " analytics.daily_engine_run")
    yield c
    if not c.closed:
        c.close()


def oku(sorgu, params=()):
    c = _baglan()
    try:
        with c.cursor() as cur:
            cur.execute(sorgu, params)
            return cur.fetchall()
    finally:
        c.close()


def _run_kaydi(conn, run_id="RUN-A"):
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.total_rasyo_run
                (run_id, analysis_at, payload_sha256, started_at, finished_at,
                 overall_status, engine_error_count, company_count,
                 successful_company_count, insufficient_data_count,
                 engine_failed_company_count, not_run_company_count,
                 routing_conflict_count, missing_m1_count, missing_m2_count,
                 missing_m3_count, missing_ek4_count, missing_ek1_count,
                 missing_ek9_count, missing_good_count, weights_profile,
                 diagnostics)
                VALUES (%s,%s,%s,%s,%s,'COMPLETE',0,1,1,0,0,0,0,0,0,0,0,0,0,0,
                        'TOTAL_RASYO_SCORE_V1','{}')
            """, (run_id, ANALIZ, "a" * 64, ANALIZ, ANALIZ))
    return run_id


def _canonical_result(conn, ticker="GARFA", *, m2_source_at=ANALIZ):
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.company_total_rasyo_result
                (analysis_at, ticker, routed_engine, engine_status,
                 m2_score, m2_missing, m2_source_at,
                 m1_score, m1_missing, m3_score, m3_missing,
                 ek4_score, ek4_missing, ek1_score, ek1_missing,
                 ek9_score, ek9_missing, good_count_ge8, good_count_missing,
                 base_score, final_score, veto_flag, decision, weights_profile,
                 total_rasyo_status, diagnostics)
                VALUES (%s,%s,'FINANCIAL','OK', 0.8,false,%s, 0.6,false,
                        0.7,false, 0.5,false, 0.4,false, 0.3,false, 9,false,
                        0.62,0.62,false,'IZLE','TOTAL_RASYO_SCORE_V1','OK','{}')
            """, (ANALIZ, ticker, m2_source_at))


def _sonuc(ticker="GARFA"):
    return {"ticker": ticker, "modules": {
        "M2": {"score": 0.8, "missing": False, "source_at": ANALIZ},
        "M1": {"score": 0.6, "missing": False, "source_at": KAYNAK},
        "M3": {"score": 0.7, "missing": False, "source_at": KAYNAK},
        "Ek4": {"score": 0.5, "missing": False, "source_at": KAYNAK},
        "Ek1": {"score": 0.4, "missing": False, "source_at": KAYNAK},
        "Ek9": {"score": 0.3, "missing": False, "source_at": KAYNAK},
    }}


def _modul_ve_lineage(conn, ticker, *, run_key_hex="1" * 64, analysis_at=KAYNAK):
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.kap_bank_batch_runs
                (run_key, analysis_at, asof_date, anchor_period_end,
                 horizon_days, pipeline_version, source, status,
                 requested_count, prepared_count, result_count,
                 rejected_count, sector_scale_eligible_count,
                 valuation_ok_count, report_sha256)
                VALUES (%s,%s,%s,%s,20,'TEST_V1','TEST','COMPLETE',
                        1,1,1,0,0,1,%s)
                ON CONFLICT (run_key) DO NOTHING
            """, (run_key_hex, analysis_at, analysis_at.date(), analysis_at.date(),
                  "a" * 64))
            cur.execute("""
                INSERT INTO analytics.module_scores
                (ticker, asof_date, horizon_days, m1, m3, ek1, ek4, ek9,
                 good_count_ge8, analysis_at, source_run_key)
                VALUES (%s,%s,20,0.6,0.7,0.4,0.5,0.3,9,%s,%s)
            """, (ticker, analysis_at.date(), analysis_at, run_key_hex))
    persist_producer_lineage(conn, [ModuleRow(ticker, date(2025, 12, 31))],
                             analysis_at=analysis_at, produced_at=URETIM,
                             source_run_key=run_key_hex)


def tam_kurulum(conn, ticker="GARFA"):
    _run_kaydi(conn)
    _modul_ve_lineage(conn, ticker)
    _canonical_result(conn, ticker)
    persist_module_input_snapshot(conn, total_rasyo_run_id="RUN-A",
                                  results=[_sonuc(ticker)])


def hesapla_ve_kalicilastir(conn, ticker="GARFA"):
    consumed = fetch_consumed_modules(conn, total_rasyo_run_id="RUN-A", ticker=ticker)
    successors = fetch_successors(conn, ticker=ticker, total_rasyo_analysis_at=ANALIZ,
                                  consumed_modules=consumed)
    r = reconcile_module_freshness(
        total_rasyo_run_id="RUN-A", ticker=ticker, analysis_at=ANALIZ,
        started_at=ANALIZ, finished_at=ANALIZ, consumed_modules=consumed,
        successors=successors, evidence_available=consumed is not None)
    persist_module_reconciliation(conn, r)
    return r


# ============================================ HAPPY PATH
def test_tam_kurulum_gercek_PG_uzerinde_PASS(conn):
    tam_kurulum(conn)
    r = hesapla_ve_kalicilastir(conn)
    assert r.status == STATUS_PASS
    conn.close()
    kayit = oku("SELECT status, fully_verified FROM analytics"
               ".reconciliation_module_run WHERE reconciliation_run_id=%s",
               (r.reconciliation_run_id,))[0]
    assert kayit == (STATUS_PASS, True)


# ============================================ 1) TOTAL_STALE canli
def test_daha_yeni_lineage_satiri_TOTAL_STALE_canli_yakalanir(conn):
    tam_kurulum(conn)
    # KAYNAK'tan YENI bir uretici satiri: hala ANALIZ sinirinda (look-ahead
    # degil). Yalniz module_production_lineage'e yazilir -- TOTAL_STALE
    # kontrolu SADECE bu tabloyu sorgular.
    yeni_analiz = KAYNAK + timedelta(minutes=10)
    persist_producer_lineage(conn, [ModuleRow("GARFA", date(2025, 12, 31))],
                             analysis_at=yeni_analiz, produced_at=URETIM,
                             source_run_key="2" * 64)
    r = hesapla_ve_kalicilastir(conn)
    assert r.status == STATUS_MISMATCH
    assert "M1" in r.total_stale_modules()


def test_gelecekteki_satir_TOTAL_STALE_SAYILMAZ_look_ahead_korumasi(conn):
    """HALEF KURALI: total_rasyo.analysis_at SINIRINI ASAN satir SAYILMAZ."""
    tam_kurulum(conn)
    gelecek = ANALIZ + timedelta(days=1)
    persist_producer_lineage(conn, [ModuleRow("GARFA", date(2025, 12, 31))],
                             analysis_at=gelecek, produced_at=URETIM,
                             source_run_key="3" * 64)
    r = hesapla_ve_kalicilastir(conn)
    assert "M1" not in r.total_stale_modules(), (
        "gelecekteki satir look-ahead korumasini ihlal ederek stale sayildi")


# ============================================ 2) MODULE_LINEAGE_STALE canli
def test_ayni_etikette_kimlik_degisimi_LINEAGE_STALE_canli_yakalanir(conn):
    tam_kurulum(conn)
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.kap_bank_batch_runs
                (run_key, analysis_at, asof_date, anchor_period_end,
                 horizon_days, pipeline_version, source, status,
                 requested_count, prepared_count, result_count,
                 rejected_count, sector_scale_eligible_count,
                 valuation_ok_count, report_sha256)
                VALUES ('4'||repeat('4',63),%s,%s,%s,20,'TEST_V2','TEST',
                        'COMPLETE',1,1,1,0,0,1,%s)
            """, (KAYNAK, KAYNAK.date(), KAYNAK.date(), "b" * 64))
    persist_producer_lineage(conn, [ModuleRow("GARFA", date(2025, 12, 31))],
                             analysis_at=KAYNAK, produced_at=URETIM,
                             source_run_key="4" * 64)  # AYNI etiket, FARKLI kimlik
    r = hesapla_ve_kalicilastir(conn)
    assert r.status == STATUS_MISMATCH
    assert "M1" in r.lineage_stale_modules()


# ============================================ 3) identity_known=false canli
def test_identity_bilinmeyen_modulde_lineage_HUKUM_VERMEZ_canli(conn):
    _run_kaydi(conn)
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.module_scores
                (ticker, asof_date, horizon_days, m1, m3, ek1, ek4, ek9,
                 good_count_ge8, analysis_at, source_run_key)
                VALUES ('GARFA',%s,20,0.6,0.7,0.4,0.5,0.3,9,%s,NULL)
            """, (KAYNAK.date(), KAYNAK))
    _canonical_result(conn)
    persist_module_input_snapshot(conn, total_rasyo_run_id="RUN-A",
                                  results=[_sonuc()])
    r = hesapla_ve_kalicilastir(conn)
    c = r.checks["M1"]
    assert c.lineage_performed is False
    assert c.lineage_stale is None
    assert r.fully_verified is False  # kapsam eksik ama status MISMATCH degil bulgu yoksa
    assert r.status == STATUS_PASS


# ============================================ INCOMPLETE canli
def test_snapshot_hic_yazilmamissa_INCOMPLETE_canli(conn):
    _run_kaydi(conn)
    consumed = fetch_consumed_modules(conn, total_rasyo_run_id="RUN-A", ticker="GARFA")
    assert consumed is None
    r = reconcile_module_freshness(
        total_rasyo_run_id="RUN-A", ticker="GARFA", analysis_at=ANALIZ,
        started_at=ANALIZ, finished_at=ANALIZ, consumed_modules={},
        successors={}, evidence_available=False)
    persist_module_reconciliation(conn, r)
    conn.close()
    kayit = oku("SELECT status FROM analytics.reconciliation_module_run"
               " WHERE reconciliation_run_id=%s", (r.reconciliation_run_id,))[0]
    assert kayit == (STATUS_INCOMPLETE,)


# ============================================ IDEMPOTENCY + IMMUTABLE
def test_ayni_reconciliation_ikinci_kez_satir_artirmaz(conn):
    tam_kurulum(conn)
    r = hesapla_ve_kalicilastir(conn)
    once = oku("SELECT created_at FROM analytics.reconciliation_module_run")[0]
    import time
    time.sleep(0.05)
    ikinci = persist_module_reconciliation(conn, r)
    assert ikinci["created"] is False
    conn.close()
    assert oku("SELECT count(*) FROM analytics.reconciliation_module_run")[0][0] == 1
    assert oku("SELECT created_at FROM analytics.reconciliation_module_run")[0] == once


def test_ayni_kimlik_farkli_icerik_reddedilir(conn):
    tam_kurulum(conn)
    r = hesapla_ve_kalicilastir(conn)
    from dataclasses import replace
    bozuk = replace(r, status=STATUS_PASS,
                    checks={k: replace(v, total_stale=False) for k, v in r.checks.items()})
    # ayni kimlik farkli olmasi icin bulgu ekleyelim
    from src.analytics.reconciliation_module_freshness import ModuleCheck
    bozuk_checks = dict(r.checks)
    bozuk_checks["M1"] = ModuleCheck(
        module="M1", missing=False, freshness_performed=True,
        freshness_reason=None, total_stale=True, lineage_performed=True,
        lineage_reason=None, lineage_stale=False)
    bozuk2 = replace(r, status=STATUS_MISMATCH, fully_verified=True,
                     checks=bozuk_checks)
    with pytest.raises(ModuleReconciliationConflict):
        persist_module_reconciliation(conn, bozuk2)


def test_reconciliation_module_run_UPDATE_edilemez(conn):
    tam_kurulum(conn)
    r = hesapla_ve_kalicilastir(conn)
    with pytest.raises(psycopg2.Error, match="immutable"):
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE analytics.reconciliation_module_run"
                            " SET status='PASS'")


def test_runtime_TRUNCATE_edemez(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("TRUNCATE analytics.reconciliation_module_run")
