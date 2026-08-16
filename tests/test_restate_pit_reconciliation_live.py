"""
V23-B canli PostgreSQL testleri.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.restate_pit_collector import (
    fetch_pit_snapshots,
    fetch_restate_run_tickers,
    fetch_restate_snapshots,
)
from src.analytics.restate_pit_persistence import (
    RestatePitConflict,
    persist_restate_pit_reconciliation,
)
from src.analytics.restate_pit_reconciliation import (
    STATUS_INCOMPLETE,
    STATUS_MISMATCH,
    STATUS_PASS,
    reconcile_pit_vs_restate,
)

TZ = timezone(timedelta(hours=3))
HEDEF = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
CUTOFF = datetime(2026, 4, 1, 10, 0, tzinfo=TZ)


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
            cur.execute("SELECT to_regclass('analytics.reconciliation_restate_run')")
            if cur.fetchone()[0] is None:
                c.close()
                pytest.skip("sql/039 uygulanmamis")
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
            """, (run_id, HEDEF, "a" * 64, HEDEF, HEDEF))


def _pit_satiri(conn, ticker, *, score=0.62, decision="IZLE", status="OK",
                run_id="RUN-A"):
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.company_total_rasyo_result
                (analysis_at, ticker, run_id, routed_engine, engine_status,
                 m2_score, m2_missing, m1_score, m1_missing, m3_score, m3_missing,
                 ek4_score, ek4_missing, ek1_score, ek1_missing, ek9_score,
                 ek9_missing, good_count_ge8, good_count_missing, base_score,
                 final_score, veto_flag, decision, weights_profile,
                 total_rasyo_status, diagnostics)
                VALUES (%s,%s,%s,'FINANCIAL','OK', 0.8,false, 0.6,false,
                        0.7,false, 0.5,false, 0.4,false, 0.3,false, 9,false,
                        %s,%s,false,%s,'TOTAL_RASYO_SCORE_V1',%s,'{}')
            """, (HEDEF, ticker, run_id, score, score, decision, status))


def _restate_run(conn, rid, *, target=HEDEF, cutoff=CUTOFF):
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.total_rasyo_restate_runs
                (restate_run_id, target_analysis_at, knowledge_cutoff_at,
                 started_at, finished_at, status, restate_contract_version,
                 reader_version, inputs_sha256, results_sha256,
                 calculation_profile, calculation_version, company_count,
                 successful_company_count, diagnostics)
                VALUES (%s,%s,%s,%s,%s,'COMPLETE',1,1,%s,%s,'V1',1,1,0,'{}')
            """, (rid, target, cutoff, cutoff, cutoff, "b" * 64, "c" * 64))


def _restate_satiri(conn, rid, ticker, *, status="YETERSIZ_VERI", score=None,
                    decision=None, reason="NO_RESTATE_SOURCE_FOR_M2"):
    m2_missing = status != "OK"
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.company_total_rasyo_restate_result
                (restate_run_id, ticker, target_analysis_at, knowledge_cutoff_at,
                 engine_family, m2_missing, m1_missing, m3_missing, ek4_missing,
                 ek1_missing, ek9_missing, good_count_missing, base_score,
                 final_score, decision, total_rasyo_status, rejection_reason,
                 insufficiency_reason, diagnostics)
                VALUES (%s,%s,%s,%s,'FINANCIAL',%s,false,false,false,false,
                        false,false,%s,%s,%s,%s,%s,%s,'{}')
            """, (rid, ticker, HEDEF, CUTOFF, m2_missing,
                  score, score, decision, status,
                  (reason if status != "OK" else None), reason))


def hesapla_ve_kalicilastir(conn, rid):
    tickers = fetch_restate_run_tickers(conn, restate_run_id=rid)
    pit = fetch_pit_snapshots(conn, target_analysis_at=HEDEF, tickers=tickers)
    restate = fetch_restate_snapshots(conn, restate_run_id=rid, tickers=tickers)
    r = reconcile_pit_vs_restate(restate_run_id=rid, tickers=tickers,
                                 pit_snapshots=pit, restate_snapshots=restate)
    persist_restate_pit_reconciliation(conn, r, started_at=HEDEF, finished_at=HEDEF)
    return r


# ============================================ M2 NEDENLI INCOMPLETE (bugunku gercek)
def test_m2_nedenli_hepsi_incomplete_gercek_PG_uzerinde_INCOMPLETE(conn):
    """Bugunku V23-A gercekligi: M2 yuzunden HER restate YETERSIZ_VERI ->
    reconciliation status INCOMPLETE olmali, PASS DEGIL."""
    _run_kaydi(conn)
    _pit_satiri(conn, "GARFA")
    _pit_satiri(conn, "PEER1")
    _restate_run(conn, "1" * 64)
    _restate_satiri(conn, "1" * 64, "GARFA")
    _restate_satiri(conn, "1" * 64, "PEER1")
    r = hesapla_ve_kalicilastir(conn, "1" * 64)
    assert r.compared_count == 0
    assert r.mismatch_count == 0
    assert r.status == STATUS_INCOMPLETE
    conn.close()
    kayit = oku("SELECT status, compared_count, mismatch_count FROM analytics"
               ".reconciliation_restate_run WHERE reconciliation_run_id=%s",
               (r.reconciliation_run_id,))[0]
    assert kayit == (STATUS_INCOMPLETE, 0, 0)


def test_incomplete_durumda_bulgu_satirlari_dogru_yazilir(conn):
    _run_kaydi(conn)
    _pit_satiri(conn, "GARFA")
    _restate_run(conn, "2" * 64)
    _restate_satiri(conn, "2" * 64, "GARFA")
    r = hesapla_ve_kalicilastir(conn, "2" * 64)
    conn.close()
    bulgular = oku("SELECT finding_type FROM analytics"
                   ".reconciliation_restate_finding WHERE reconciliation_run_id=%s",
                   (r.reconciliation_run_id,))
    assert bulgular == [("RESTATE_INCOMPLETE",)]


# ============================================ GERCEK KARSILASTIRMA -> PASS/MISMATCH
def test_temiz_karsilastirma_gercek_PG_PASS(conn):
    _run_kaydi(conn)
    _pit_satiri(conn, "GARFA", score=0.62, decision="IZLE")
    _restate_run(conn, "3" * 64)
    _restate_satiri(conn, "3" * 64, "GARFA", status="OK", score=0.62, decision="IZLE")
    r = hesapla_ve_kalicilastir(conn, "3" * 64)
    assert r.status == STATUS_PASS
    assert r.fully_verified is True


def test_deger_farki_gercek_PG_MISMATCH(conn):
    _run_kaydi(conn)
    _pit_satiri(conn, "GARFA", score=0.62, decision="IZLE")
    _restate_run(conn, "4" * 64)
    _restate_satiri(conn, "4" * 64, "GARFA", status="OK", score=0.95, decision="IZLE")
    r = hesapla_ve_kalicilastir(conn, "4" * 64)
    assert r.status == STATUS_MISMATCH
    conn.close()
    bulgular = oku("SELECT finding_type FROM analytics"
                   ".reconciliation_restate_finding WHERE reconciliation_run_id=%s",
                   (r.reconciliation_run_id,))
    assert bulgular == [("VALUE_CHANGED",)]


def test_kismi_durum_bazisi_incomplete_bazisi_temiz_PASS(conn):
    """Kullanicinin ornegi: 7'si temiz karsilastirilmis, 3'u INCOMPLETE."""
    _run_kaydi(conn)
    _restate_run(conn, "5" * 64)
    for i in range(10):
        t = f"T{i}"
        _pit_satiri(conn, t, score=0.5, decision="IZLE")
        if i < 3:
            _restate_satiri(conn, "5" * 64, t, status="YETERSIZ_VERI")
        else:
            _restate_satiri(conn, "5" * 64, t, status="OK", score=0.5, decision="IZLE")
    r = hesapla_ve_kalicilastir(conn, "5" * 64)
    assert r.compared_count == 7
    assert r.status == STATUS_PASS
    assert r.fully_verified is False


# ============================================ VIEW KORUNMASI (canli kanit)
def test_view_hukum_kaynagi_DEGIL_sahte_fark_YOK(conn):
    """
    restate_vs_pit_comparison view'i decision_changed=TRUE dondurse bile,
    bu reconciliation dogru sonucu (INCOMPLETE, mismatch yok) uretmelidir.
    """
    _run_kaydi(conn)
    _pit_satiri(conn, "GARFA", decision="AL")
    _restate_run(conn, "6" * 64)
    _restate_satiri(conn, "6" * 64, "GARFA", status="YETERSIZ_VERI")
    with conn.cursor() as cur:
        cur.execute("SELECT decision_changed FROM analytics"
                    ".restate_vs_pit_comparison WHERE ticker='GARFA'")
        view_sonucu = cur.fetchone()
    assert view_sonucu == (True,), "view'in sahte fark urettigi dogrulanamadi"
    r = hesapla_ve_kalicilastir(conn, "6" * 64)
    assert r.mismatch_count == 0
    assert r.status == STATUS_INCOMPLETE


# ============================================ IDEMPOTENCY + IMMUTABLE
def test_ayni_reconciliation_ikinci_kez_satir_artirmaz(conn):
    _run_kaydi(conn)
    _pit_satiri(conn, "GARFA")
    _restate_run(conn, "7" * 64)
    _restate_satiri(conn, "7" * 64, "GARFA")
    r = hesapla_ve_kalicilastir(conn, "7" * 64)
    once = oku("SELECT created_at FROM analytics.reconciliation_restate_run")[0]
    import time
    time.sleep(0.05)
    tickers = fetch_restate_run_tickers(conn, restate_run_id="7" * 64)
    pit = fetch_pit_snapshots(conn, target_analysis_at=HEDEF, tickers=tickers)
    restate = fetch_restate_snapshots(conn, restate_run_id="7" * 64, tickers=tickers)
    r2 = reconcile_pit_vs_restate(restate_run_id="7" * 64, tickers=tickers,
                                  pit_snapshots=pit, restate_snapshots=restate)
    sonuc = persist_restate_pit_reconciliation(conn, r2, started_at=HEDEF,
                                               finished_at=HEDEF)
    assert sonuc["created"] is False
    conn.close()
    assert oku("SELECT count(*) FROM analytics.reconciliation_restate_run")[0][0] == 1
    assert oku("SELECT created_at FROM analytics.reconciliation_restate_run")[0] == once


def test_reconciliation_run_UPDATE_edilemez(conn):
    _run_kaydi(conn)
    _pit_satiri(conn, "GARFA")
    _restate_run(conn, "8" * 64)
    _restate_satiri(conn, "8" * 64, "GARFA")
    hesapla_ve_kalicilastir(conn, "8" * 64)
    with pytest.raises(psycopg2.Error, match="immutable"):
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE analytics.reconciliation_restate_run SET status='PASS'")


def test_runtime_TRUNCATE_edemez(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("TRUNCATE analytics.reconciliation_restate_run")


# ============================================ HATALI KULLANIM
def test_restate_satiri_olmayan_ticker_gonderilirse_hata(conn):
    from src.analytics.restate_pit_reconciliation import RestatePitReconciliationError

    _run_kaydi(conn)
    _pit_satiri(conn, "GARFA")
    _restate_run(conn, "9" * 64)
    _restate_satiri(conn, "9" * 64, "GARFA")
    pit = fetch_pit_snapshots(conn, target_analysis_at=HEDEF,
                              tickers=["GARFA", "HAYALI"])
    restate = fetch_restate_snapshots(conn, restate_run_id="9" * 64,
                                      tickers=["GARFA", "HAYALI"])
    with pytest.raises(RestatePitReconciliationError):
        reconcile_pit_vs_restate(restate_run_id="9" * 64,
                                 tickers=["GARFA", "HAYALI"],
                                 pit_snapshots=pit, restate_snapshots=restate)
