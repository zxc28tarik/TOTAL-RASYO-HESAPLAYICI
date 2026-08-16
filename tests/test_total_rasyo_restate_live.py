"""
V23-A canli PostgreSQL testleri — okuyucu + kalicilik.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.total_rasyo_restate_calculator import (
    REASON_NO_RESTATE_SOURCE_M2,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    compute_restate,
)
from src.analytics.total_rasyo_restate_persistence import (
    RestateConflict,
    persist_restate,
)
from src.analytics.total_rasyo_restate_reader import fetch_restate_module_context

TZ = timezone(timedelta(hours=3))
HEDEF = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
CUTOFF = datetime(2026, 4, 1, 10, 0, tzinfo=TZ)
KAYNAK = HEDEF - timedelta(hours=1)
KAYNAK_ARA = HEDEF + timedelta(days=10)  # target ile cutoff arasi -- RESTATE'e ozgu


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
            cur.execute("SELECT to_regclass('analytics.total_rasyo_restate_module_input')")
            if cur.fetchone()[0] is None:
                c.close()
                pytest.skip("sql/038 uygulanmamis")
            cur.execute("TRUNCATE analytics.reconciliation_restate_finding,"
                        " analytics.reconciliation_restate_run,"
                        " analytics.total_rasyo_restate_module_input,"
                        " analytics.company_total_rasyo_restate_result,"
                        " analytics.total_rasyo_restate_runs")
            cur.execute("DELETE FROM analytics.module_scores")
            cur.execute("DELETE FROM analytics.kap_bank_batch_runs")
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


def _batch_run(conn, run_key_hex, *, analysis_at):
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


def _modul_satiri(conn, ticker, *, analysis_at, run_key_hex=None):
    if run_key_hex is not None:
        _batch_run(conn, run_key_hex, analysis_at=analysis_at)
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.module_scores
                (ticker, asof_date, horizon_days, m1, m3, ek1, ek4, ek9,
                 good_count_ge8, analysis_at, source_run_key)
                VALUES (%s,%s,20,0.6,0.7,0.4,0.5,0.3,9,%s,%s)
            """, (ticker, analysis_at.date(), analysis_at, run_key_hex))


# ============================================ OKUYUCU: TARGET/CUTOFF AYRIMI
def _modul_satiri_donem(conn, ticker, *, analysis_at, period_end, run_key_hex=None):
    """
    asof_date HER ZAMAN analysis_at'in takvim gunudur (ck_module_scores_
    analysis_asof kisiti). period_end AYRI bir sutundur -- RESTATE'in
    "ayni doneme ait duzeltme" mantigi bunun uzerinden calisir.
    """
    if run_key_hex is not None:
        _batch_run(conn, run_key_hex, analysis_at=analysis_at)
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.module_scores
                (ticker, asof_date, period_end, horizon_days, m1, m3, ek1, ek4, ek9,
                 good_count_ge8, analysis_at, source_run_key)
                VALUES (%s,%s,%s,20,0.6,0.7,0.4,0.5,0.3,9,%s,%s)
            """, (ticker, analysis_at.date(), period_end, analysis_at, run_key_hex))


def test_hedef_gunune_gore_referans_donem_belirlenir(conn):
    """
    ADIM 1: reference CTE, target_analysis_at'e gore hangi donemin (period_end)
    guncel oldugunu belirler -- knowledge_cutoff_at'e GORE DEGIL. Hedeften
    SONRA hesaplanmis ayri bir donem, referans olarak SECILMEMELI.
    """
    _modul_satiri_donem(conn, "GARFA", analysis_at=KAYNAK,
                        period_end=date(2025, 12, 31), run_key_hex="1" * 64)
    gelecek_donem_analiz = HEDEF + timedelta(days=5)
    _modul_satiri_donem(conn, "GARFA", analysis_at=gelecek_donem_analiz,
                        period_end=date(2026, 3, 31), run_key_hex="2" * 64)
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    # KAYNAK satiri (eski donem) secilmeli; gelecekteki donem DEGIL.
    assert ctx["GARFA"].analysis_at == KAYNAK


def test_cutoffa_kadar_ayni_donem_icin_gelen_duzeltme_kullanilir(conn):
    """
    RESTATE'in tum amaci bu: hedef gun icin GECERLI OLAN AYNI DONEME
    (period_end), cutoff'a kadar gelen bir DUZELTME (farkli asof_date/
    analysis_at, ama AYNI period_end) varsa, o kullanilmali.
    """
    donem = date(2025, 12, 31)
    _modul_satiri_donem(conn, "GARFA", analysis_at=KAYNAK, period_end=donem,
                        run_key_hex="1" * 64)
    # AYNI DONEM icin GEC GELEN duzeltme (target'tan SONRA, cutoff'tan ONCE).
    _batch_run(conn, "3" * 64, analysis_at=KAYNAK_ARA)
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.module_scores
                (ticker, asof_date, period_end, horizon_days, m1, m3, ek1, ek4, ek9,
                 good_count_ge8, analysis_at, source_run_key)
                VALUES ('GARFA',%s,%s,20,0.99,0.7,0.4,0.5,0.3,9,%s,%s)
            """, (KAYNAK_ARA.date(), donem, KAYNAK_ARA, "3" * 64))
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    assert ctx["GARFA"].components["M1"].score == 0.99
    assert ctx["GARFA"].components["M1"].source_run_key == "3" * 64


def test_cutoffu_asan_duzeltme_KULLANILMAZ(conn):
    """Look-ahead korumasi: knowledge_cutoff_at'i ASAN bir duzeltme ASLA kullanilmaz."""
    donem = date(2025, 12, 31)
    _modul_satiri_donem(conn, "GARFA", analysis_at=KAYNAK, period_end=donem,
                        run_key_hex="1" * 64)
    gelecek = CUTOFF + timedelta(days=30)
    _modul_satiri_donem(conn, "GARFA", analysis_at=gelecek, period_end=donem,
                        run_key_hex="4" * 64)
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    # Cutoff'u asan duzeltme DEGIL, KAYNAK satiri kullanilmali.
    assert ctx["GARFA"].analysis_at == KAYNAK
    assert ctx["GARFA"].components["M1"].source_run_key == "1" * 64


def test_kimliksiz_satir_genisletilmis_pencereden_YARARLANAMAZ(conn):
    """
    analysis_at NULL olan bir satir, target gunun asof_date sinirinin
    OTESINDE (yani yalniz cutoff'un tanidigi genisletilmis pencerede)
    ASLA secilemez -- kanitlanamayan kimlige uydurma imtiyaz verilmez.
    """
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.module_scores
                (ticker, asof_date, horizon_days, m1, m3, ek1, ek4, ek9,
                 good_count_ge8, analysis_at, source_run_key)
                VALUES ('GARFA',%s,20,0.5,0.6,0.3,0.4,0.2,8,NULL,NULL)
            """, (HEDEF.date() + timedelta(days=3),))  # target GUNUNDEN SONRA, analysis_at YOK
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    assert "GARFA" not in ctx or ctx["GARFA"].components["M1"].missing


def test_identity_known_source_run_key_varliginca_belirlenir(conn):
    _modul_satiri(conn, "GARFA", analysis_at=KAYNAK, run_key_hex="5" * 64)
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    assert ctx["GARFA"].components["M1"].identity_known is True
    assert ctx["GARFA"].components["M1"].source_run_key == "5" * 64


def test_source_run_key_yoksa_identity_known_false(conn):
    _modul_satiri(conn, "GARFA", analysis_at=KAYNAK, run_key_hex=None)
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    assert ctx["GARFA"].components["M1"].identity_known is False


# ============================================ UCTAN UCA HESAPLAMA + KALICILIK
def test_uctan_uca_M2_yoksa_INCOMPLETE_gercek_PG(conn):
    _modul_satiri(conn, "GARFA", analysis_at=KAYNAK, run_key_hex="6" * 64)
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    comp = compute_restate(target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF,
                           tickers=["GARFA"], module_contexts=ctx)
    assert comp.company_results["GARFA"].total_rasyo_status == STATUS_INSUFFICIENT
    assert comp.company_results["GARFA"].insufficiency_reason == REASON_NO_RESTATE_SOURCE_M2

    sonuc = persist_restate(conn, comp, started_at=HEDEF, finished_at=HEDEF)
    assert sonuc["created"] is True
    conn.close()

    kayit = oku("SELECT total_rasyo_status, insufficiency_reason FROM analytics"
               ".company_total_rasyo_restate_result WHERE ticker='GARFA'")[0]
    assert kayit == (STATUS_INSUFFICIENT, REASON_NO_RESTATE_SOURCE_M2)


def test_m2_input_satiri_identity_known_false_kalici(conn):
    _modul_satiri(conn, "GARFA", analysis_at=KAYNAK, run_key_hex="7" * 64)
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    comp = compute_restate(target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF,
                           tickers=["GARFA"], module_contexts=ctx)
    persist_restate(conn, comp, started_at=HEDEF, finished_at=HEDEF)
    conn.close()
    m2 = oku("SELECT identity_known, module_missing FROM analytics"
            ".total_rasyo_restate_module_input WHERE ticker='GARFA' AND module='M2'")[0]
    assert m2 == (False, True)


# ============================================ IDEMPOTENCY + CONFLICT
def test_ayni_restate_ikinci_kez_satir_artirmaz(conn):
    _modul_satiri(conn, "GARFA", analysis_at=KAYNAK, run_key_hex="8" * 64)
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    comp = compute_restate(target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF,
                           tickers=["GARFA"], module_contexts=ctx)
    persist_restate(conn, comp, started_at=HEDEF, finished_at=HEDEF)
    once = oku("SELECT inserted_at FROM analytics.total_rasyo_restate_runs")[0]
    import time
    time.sleep(0.05)
    ikinci = persist_restate(conn, comp, started_at=HEDEF, finished_at=HEDEF)
    assert ikinci["created"] is False
    conn.close()
    assert oku("SELECT count(*) FROM analytics.total_rasyo_restate_runs")[0][0] == 1
    assert oku("SELECT inserted_at FROM analytics.total_rasyo_restate_runs")[0] == once


def test_ayni_kimlik_farkli_icerik_reddedilir(conn):
    _modul_satiri(conn, "GARFA", analysis_at=KAYNAK, run_key_hex="9" * 64)
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    comp = compute_restate(target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF,
                           tickers=["GARFA"], module_contexts=ctx)
    persist_restate(conn, comp, started_at=HEDEF, finished_at=HEDEF)

    from dataclasses import replace
    bozuk = replace(comp, results_sha256="f" * 64)
    with pytest.raises(RestateConflict):
        persist_restate(conn, bozuk, started_at=HEDEF, finished_at=HEDEF)


# ============================================ IMMUTABLE + ROL
def test_restate_run_UPDATE_edilemez(conn):
    _modul_satiri(conn, "GARFA", analysis_at=KAYNAK, run_key_hex="a" * 64)
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    comp = compute_restate(target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF,
                           tickers=["GARFA"], module_contexts=ctx)
    persist_restate(conn, comp, started_at=HEDEF, finished_at=HEDEF)
    with pytest.raises(psycopg2.Error, match="immutable"):
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE analytics.total_rasyo_restate_runs SET status='PARTIAL'")


def test_restate_result_DELETE_edilemez(conn):
    _modul_satiri(conn, "GARFA", analysis_at=KAYNAK, run_key_hex="b" * 64)
    ctx = fetch_restate_module_context(
        conn, tickers=["GARFA"], target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF)
    comp = compute_restate(target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF,
                           tickers=["GARFA"], module_contexts=ctx)
    persist_restate(conn, comp, started_at=HEDEF, finished_at=HEDEF)
    with pytest.raises(psycopg2.Error, match="immutable"):
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM analytics.company_total_rasyo_restate_result")


def test_runtime_restate_TRUNCATE_edemez(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("TRUNCATE analytics.total_rasyo_restate_runs")


def test_runtime_restate_INSERT_edebilir(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            cur.execute("SELECT count(*) FROM analytics.total_rasyo_restate_runs")
            assert cur.fetchone()[0] >= 0
