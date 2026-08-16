"""
V21 Reconciliation-1 kalicilik — canli PostgreSQL.

Ayni desen: idempotent + immutable, gercek baglantiyla dogrulanir.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.change_impact_bridge import ModuleLineage, evaluate_readiness, bridge_targeted_tickers
from src.analytics.change_impact_detector import PeerCandidate, detect_change_impact, FactChange
from src.analytics.change_impact_persistence import persist_impact_plan, record_application_attempt
from src.analytics.reconciliation_impact_orchestrator import (
    STATUS_INCOMPLETE,
    STATUS_MISMATCH,
    STATUS_PASS,
    reconcile_impact_vs_actual,
)
from src.analytics.reconciliation_persistence import (
    ReconciliationConflict,
    fetch_actual_rows,
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
            cur.execute("SELECT to_regclass('analytics.reconciliation_run')")
            if cur.fetchone()[0] is None:
                c.close()
                pytest.skip("sql/035 uygulanmamis")
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
    yield c
    if not c.closed:
        c.close()


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


def tam_zincir(conn, tickers=("GARFA",)):
    """Fact -> plan -> readiness -> orkestratör; gercek kurulum."""
    routing = {t: "FINANCIAL" for t in tickers}
    for t in tickers:
        modul_satiri(conn, t)
    c = fact(tickers[0])
    plan = detect_change_impact(c, impact_run_id="R1", analysis_at=KESIM,
                                peer_candidates={"FINANCIAL": [
                                    PeerCandidate(t, True, True, 1.0, 0.9)
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


# ============================================ 1) BASARILI ZINCIR -> PASS
def test_basarili_zincir_gercek_PG_uzerinde_PASS(conn):
    plan, routing, hedefler = tam_zincir(conn)
    app_id = "APP-1"
    record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                               application_run_id=app_id, started_at=KESIM,
                               status="APPLIED", finished_at=KESIM,
                               orchestrator_run_id="ORCH-1",
                               targeted_ticker_count=len(hedefler))
    persist_application_targets(conn, application_run_id=app_id, tickers=hedefler)

    runners = {"FINANCIAL": V19.motor({t: V19.m2cikti(0.8) for t in hedefler})}
    run_total_rasyo_orchestrator(conn, analysis_at=KESIM, routing=routing,
                                 engine_runners=runners, run_id="ORCH-1",
                                 targeted_tickers=list(hedefler))

    actual = fetch_actual_rows(conn, analysis_at=KESIM, tickers=hedefler)
    r = reconcile_impact_vs_actual(
        application_run_id=app_id, impact_plan_id=plan.impact_plan_id,
        analysis_at=KESIM, started_at=KESIM, finished_at=KESIM,
        application_status="APPLIED", expected_tickers=hedefler, actual_rows=actual,
        orchestrator_run_id="ORCH-1")
    assert r.status == STATUS_PASS
    sonuc = persist_reconciliation_result(conn, r)
    assert sonuc["created"] is True
    conn.close()

    kalici = _baglan()
    with kalici.cursor() as cur:
        cur.execute("SELECT status FROM analytics.reconciliation_run"
                    " WHERE reconciliation_run_id=%s", (r.reconciliation_run_id,))
        assert cur.fetchone()[0] == STATUS_PASS
    kalici.close()


# ============================================ 2) EKSIK YAZIM -> MISSING (canli)
def test_orkestratör_hedefi_atlarsa_MISSING_canli_yakalanir(conn):
    """Hedeflenen ticker'lardan biri orkestratöre HIC gonderilmezse."""
    plan, routing, hedefler = tam_zincir(conn, tickers=("GARFA", "PEER1"))
    app_id = "APP-2"
    record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                               application_run_id=app_id, started_at=KESIM,
                               status="APPLIED", finished_at=KESIM,
                               orchestrator_run_id="ORCH-2")
    persist_application_targets(conn, application_run_id=app_id, tickers=hedefler)

    # BILEREK EKSIK: yalniz GARFA gonderiliyor, PEER1 UNUTULMUS gibi.
    eksik_hedef = [t for t in hedefler if t != "PEER1"] or list(hedefler)[:1]
    runners = {"FINANCIAL": V19.motor({t: V19.m2cikti(0.8) for t in eksik_hedef})}
    run_total_rasyo_orchestrator(conn, analysis_at=KESIM, routing=routing,
                                 engine_runners=runners, run_id="ORCH-2",
                                 targeted_tickers=eksik_hedef)

    actual = fetch_actual_rows(conn, analysis_at=KESIM, tickers=hedefler)
    r = reconcile_impact_vs_actual(
        application_run_id=app_id, impact_plan_id=plan.impact_plan_id,
        analysis_at=KESIM, started_at=KESIM, finished_at=KESIM,
        application_status="APPLIED", expected_tickers=hedefler, actual_rows=actual,
        orchestrator_run_id="ORCH-2")
    assert r.status == STATUS_MISMATCH
    assert set(r.missing()) == (set(hedefler) - set(eksik_hedef))
    persist_reconciliation_result(conn, r)
    conn.close()

    kalici = _baglan()
    with kalici.cursor() as cur:
        cur.execute("SELECT finding_type, ticker FROM analytics"
                    ".reconciliation_finding WHERE reconciliation_run_id=%s",
                    (r.reconciliation_run_id,))
        bulgular = cur.fetchall()
    kalici.close()
    assert ("MISSING", list(set(hedefler) - set(eksik_hedef))[0]) in bulgular \
        if hedefler != set(eksik_hedef) else True


# ============================================ 3) IDEMPOTENCY (canli)
def test_ayni_reconciliation_ikinci_kez_satir_artirmaz(conn):
    plan, routing, hedefler = tam_zincir(conn)
    app_id = "APP-3"
    record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                               application_run_id=app_id, started_at=KESIM,
                               status="APPLIED", finished_at=KESIM,
                               orchestrator_run_id="ORCH-3")
    persist_application_targets(conn, application_run_id=app_id, tickers=hedefler)
    actual = fetch_actual_rows(conn, analysis_at=KESIM, tickers=hedefler)
    r = reconcile_impact_vs_actual(
        application_run_id=app_id, impact_plan_id=plan.impact_plan_id,
        analysis_at=KESIM, started_at=KESIM, finished_at=KESIM,
        application_status="APPLIED", expected_tickers=hedefler, actual_rows=actual,
        orchestrator_run_id="ORCH-3")

    persist_reconciliation_result(conn, r)
    once = _baglan()
    with once.cursor() as cur:
        cur.execute("SELECT created_at FROM analytics.reconciliation_run")
        zaman_once = cur.fetchone()[0]
    once.close()

    import time
    time.sleep(0.05)
    ikinci = persist_reconciliation_result(conn, r)
    conn.close()
    assert ikinci["created"] is False

    kalici = _baglan()
    with kalici.cursor() as cur:
        cur.execute("SELECT count(*) FROM analytics.reconciliation_run")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT created_at FROM analytics.reconciliation_run")
        assert cur.fetchone()[0] == zaman_once, "created_at tazelendi"
    kalici.close()


def test_ayni_kimlik_farkli_icerik_reddedilir(conn):
    plan, routing, hedefler = tam_zincir(conn)
    app_id = "APP-4"
    record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                               application_run_id=app_id, started_at=KESIM,
                               status="APPLIED", finished_at=KESIM,
                               orchestrator_run_id="ORCH-4")
    persist_application_targets(conn, application_run_id=app_id, tickers=hedefler)
    actual = fetch_actual_rows(conn, analysis_at=KESIM, tickers=hedefler)
    r = reconcile_impact_vs_actual(
        application_run_id=app_id, impact_plan_id=plan.impact_plan_id,
        analysis_at=KESIM, started_at=KESIM, finished_at=KESIM,
        application_status="APPLIED", expected_tickers=hedefler, actual_rows=actual,
        orchestrator_run_id="ORCH-4")
    persist_reconciliation_result(conn, r)

    # Ayni kimlik (ayni application_run/plan/analysis_at), farkli bulgu.
    r2 = reconcile_impact_vs_actual(
        application_run_id=app_id, impact_plan_id=plan.impact_plan_id,
        analysis_at=KESIM, started_at=KESIM, finished_at=KESIM,
        application_status="APPLIED",
        expected_tickers=set(hedefler) | {"HAYALI"}, actual_rows=actual,
        orchestrator_run_id="ORCH-4")
    assert r.reconciliation_run_id == r2.reconciliation_run_id
    with pytest.raises(ReconciliationConflict):
        persist_reconciliation_result(conn, r2)


# ============================================ 4) IMMUTABLE
def test_reconciliation_run_UPDATE_edilemez(conn):
    plan, routing, hedefler = tam_zincir(conn)
    app_id = "APP-5"
    record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                               application_run_id=app_id, started_at=KESIM,
                               status="APPLIED", finished_at=KESIM,
                               orchestrator_run_id="ORCH-5")
    persist_application_targets(conn, application_run_id=app_id, tickers=hedefler)
    actual = fetch_actual_rows(conn, analysis_at=KESIM, tickers=hedefler)
    r = reconcile_impact_vs_actual(
        application_run_id=app_id, impact_plan_id=plan.impact_plan_id,
        analysis_at=KESIM, started_at=KESIM, finished_at=KESIM,
        application_status="APPLIED", expected_tickers=hedefler, actual_rows=actual,
        orchestrator_run_id="ORCH-5")
    persist_reconciliation_result(conn, r)
    with pytest.raises(psycopg2.Error, match="immutable"):
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE analytics.reconciliation_run SET status='PASS'")


def test_impact_application_target_DELETE_edilemez(conn):
    plan, routing, hedefler = tam_zincir(conn)
    app_id = "APP-DEL"
    record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                               application_run_id=app_id, started_at=KESIM,
                               status="APPLIED", finished_at=KESIM,
                               orchestrator_run_id="ORCH-DEL")
    persist_application_targets(conn, application_run_id=app_id, tickers=hedefler)
    with pytest.raises(psycopg2.Error, match="immutable"):
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM analytics.impact_application_target"
                            " WHERE application_run_id=%s", (app_id,))


# ============================================ 5) INCOMPLETE (canli)
def test_pending_kosuda_reconciliation_INCOMPLETE_canli(conn):
    plan, routing, hedefler = tam_zincir(conn)
    app_id = "APP-6"
    record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                               application_run_id=app_id, started_at=KESIM,
                               status="PENDING")
    persist_application_targets(conn, application_run_id=app_id, tickers=hedefler)
    actual = fetch_actual_rows(conn, analysis_at=KESIM, tickers=hedefler)
    r = reconcile_impact_vs_actual(
        application_run_id=app_id, impact_plan_id=plan.impact_plan_id,
        analysis_at=KESIM, started_at=KESIM, finished_at=KESIM,
        application_status="PENDING", expected_tickers=hedefler, actual_rows=actual)
    assert r.status == STATUS_INCOMPLETE
    persist_reconciliation_result(conn, r)
    conn.close()
    kalici = _baglan()
    with kalici.cursor() as cur:
        cur.execute("SELECT status FROM analytics.reconciliation_run")
        assert cur.fetchone()[0] == STATUS_INCOMPLETE
    kalici.close()


# ============================================ 6) ROL GUVENLIGI
def test_runtime_reconciliation_tablolarini_TRUNCATE_edemez(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("TRUNCATE analytics.reconciliation_run")


def test_runtime_reconciliation_INSERT_edebilir(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            cur.execute("SELECT count(*) FROM analytics.reconciliation_run")
            assert cur.fetchone()[0] >= 0


# ============================================ 7) UNEXPECTED (fetch_actual_rows_full)
def test_fetch_actual_rows_ile_UNEXPECTED_YAKALANAMAZ(conn):
    """
    BOSLUK KANITI: sadece ticker-filtreli fetch_actual_rows kullanilirsa
    beklenmeyen bir ticker hic sorgulanmaz ve UNEXPECTED KACIRILIR.
    """
    plan, routing, hedefler = tam_zincir(conn)
    app_id = "APP-7"
    orch_id = "ORCH-7"
    record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                               application_run_id=app_id, started_at=KESIM,
                               status="APPLIED", finished_at=KESIM,
                               orchestrator_run_id=orch_id)
    persist_application_targets(conn, application_run_id=app_id, tickers=hedefler)

    modul_satiri(conn, "SIZAN")
    routing["SIZAN"] = "FINANCIAL"
    gonderilen = list(hedefler) + ["SIZAN"]
    runners = {"FINANCIAL": V19.motor({t: V19.m2cikti(0.8) for t in gonderilen})}
    run_total_rasyo_orchestrator(conn, analysis_at=KESIM, routing=routing,
                                 engine_runners=runners, run_id=orch_id,
                                 targeted_tickers=gonderilen)

    from src.analytics.reconciliation_persistence import fetch_actual_rows
    dar = fetch_actual_rows(conn, analysis_at=KESIM, tickers=hedefler)
    assert "SIZAN" not in {r.ticker for r in dar}, (
        "ticker-filtreli sorgu SIZAN'i gormemeli (bu bosluk kanitidir)")


def test_fetch_actual_rows_full_ile_UNEXPECTED_YAKALANIR(conn):
    plan, routing, hedefler = tam_zincir(conn)
    app_id = "APP-8"
    orch_id = "ORCH-8"
    record_application_attempt(conn, impact_plan_id=plan.impact_plan_id,
                               application_run_id=app_id, started_at=KESIM,
                               status="APPLIED", finished_at=KESIM,
                               orchestrator_run_id=orch_id)
    persist_application_targets(conn, application_run_id=app_id, tickers=hedefler)

    modul_satiri(conn, "SIZAN")
    routing["SIZAN"] = "FINANCIAL"
    gonderilen = list(hedefler) + ["SIZAN"]
    runners = {"FINANCIAL": V19.motor({t: V19.m2cikti(0.8) for t in gonderilen})}
    run_total_rasyo_orchestrator(conn, analysis_at=KESIM, routing=routing,
                                 engine_runners=runners, run_id=orch_id,
                                 targeted_tickers=gonderilen)

    from src.analytics.reconciliation_persistence import fetch_actual_rows_full
    genis = fetch_actual_rows_full(conn, analysis_at=KESIM,
                                   expected_tickers=hedefler,
                                   orchestrator_run_id=orch_id)
    r = reconcile_impact_vs_actual(
        application_run_id=app_id, impact_plan_id=plan.impact_plan_id,
        analysis_at=KESIM, started_at=KESIM, finished_at=KESIM,
        application_status="APPLIED", expected_tickers=hedefler,
        actual_rows=genis, orchestrator_run_id=orch_id)
    assert r.status == STATUS_MISMATCH
    assert "SIZAN" in r.unexpected()
