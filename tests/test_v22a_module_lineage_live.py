"""
V22-A canli PostgreSQL testleri — uretici fan-out + tuketim-ani snapshot.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.module_producer_lineage import ModuleRow, persist_producer_lineage
from src.analytics.total_rasyo_module_input_snapshot import (
    ModuleInputSnapshotError,
    build_module_input_rows,
    persist_module_input_snapshot,
)

TZ = timezone(timedelta(hours=3))
ANALIZ = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
URETIM = datetime(2026, 3, 2, 20, 5, tzinfo=TZ)


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
            cur.execute("SELECT to_regclass('analytics.total_rasyo_module_input')")
            if cur.fetchone()[0] is None:
                c.close()
                pytest.skip("sql/036 uygulanmamis")
            cur.execute("TRUNCATE analytics.total_rasyo_module_input")
            cur.execute("TRUNCATE analytics.module_production_lineage")
            cur.execute("DELETE FROM analytics.module_scores")
            cur.execute("TRUNCATE analytics.total_rasyo_run CASCADE")
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


def _batch_run_kaydi(conn, run_key, *, analysis_at=ANALIZ):
    """
    module_scores.source_run_key SADECE kap_bank_batch_runs'a FK'lidir --
    genel bir kayit defteri DEGILDIR (BULGU: bu kimlik alani yalniz BANK
    batch akisi icin gercek bir tabloya baglidir). Gecerli bir
    source_run_key kullanmak icin once bu tabloya kayit atilmalidir.
    """
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
            """, (run_key, analysis_at, analysis_at.date(), analysis_at.date(),
                  "a" * 64))


def _modul_satiri(conn, ticker, *, analysis_at=ANALIZ, source_run_key=None):
    if source_run_key is not None:
        _batch_run_kaydi(conn, source_run_key, analysis_at=analysis_at)
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.module_scores
                (ticker, asof_date, horizon_days, m1, m3, ek1, ek4, ek9,
                 good_count_ge8, analysis_at, source_run_key)
                VALUES (%s,%s,20,0.6,0.7,0.4,0.5,0.3,9,%s,%s)
            """, (ticker, analysis_at.date(), analysis_at, source_run_key))


def _sonuc(ticker="GARFA", *, m1_source_at=ANALIZ, m2_source_at=ANALIZ):
    return {"ticker": ticker, "modules": {
        "M2": {"score": 0.8, "missing": False, "source_at": m2_source_at},
        "M1": {"score": 0.6, "missing": False, "source_at": m1_source_at},
        "M3": {"score": 0.7, "missing": False, "source_at": m1_source_at},
        "Ek4": {"score": 0.5, "missing": False, "source_at": m1_source_at},
        "Ek1": {"score": 0.4, "missing": False, "source_at": m1_source_at},
        "Ek9": {"score": 0.3, "missing": False, "source_at": m1_source_at},
    }}


# ============================================ URETICI TARAFI
def test_uretici_lineage_gercekten_commit_edilir(conn):
    n = persist_producer_lineage(
        conn, [ModuleRow("GARFA", date(2025, 12, 31))],
        analysis_at=ANALIZ, produced_at=URETIM, source_run_key="1"*64)
    assert n == 5
    conn.close()
    satirlar = oku("SELECT module FROM analytics.module_production_lineage"
                   " WHERE ticker='GARFA' ORDER BY module")
    assert {r[0] for r in satirlar} == {"Ek1", "Ek4", "Ek9", "M1", "M3"}


def test_uretici_M2_yazmaz(conn):
    persist_producer_lineage(
        conn, [ModuleRow("GARFA", date(2025, 12, 31))],
        analysis_at=ANALIZ, produced_at=URETIM, source_run_key="1"*64)
    conn.close()
    assert oku("SELECT count(*) FROM analytics.module_production_lineage"
              " WHERE ticker='GARFA' AND module='M2'")[0][0] == 0


def test_uretici_upsert_idempotent(conn):
    """Ayni analysis_at ile tekrar cagrilirsa satir COGALMAZ."""
    persist_producer_lineage(conn, [ModuleRow("GARFA", date(2025, 12, 31))],
                             analysis_at=ANALIZ, produced_at=URETIM,
                             source_run_key="1"*64)
    persist_producer_lineage(conn, [ModuleRow("GARFA", date(2025, 12, 31))],
                             analysis_at=ANALIZ, produced_at=URETIM,
                             source_run_key="3"*64)
    conn.close()
    assert oku("SELECT count(*) FROM analytics.module_production_lineage"
              " WHERE ticker='GARFA'")[0][0] == 5
    assert oku("SELECT DISTINCT source_version_id FROM analytics"
              ".module_production_lineage WHERE ticker='GARFA'")[0][0] == "3"*64


# ============================================ TUKETIM-ANI SNAPSHOT
def test_tuketim_ani_identity_known_M1_icin_true(conn):
    _run_kaydi(conn)
    _modul_satiri(conn, "GARFA", source_run_key="1"*64)
    n = persist_module_input_snapshot(
        conn, total_rasyo_run_id="RUN-A", results=[_sonuc()])
    assert n == 6  # alti modul
    conn.close()
    m1 = oku("SELECT identity_known, module_source_run_key FROM analytics"
            ".total_rasyo_module_input WHERE ticker='GARFA' AND module='M1'")[0]
    assert m1 == (True, "1"*64)


def test_tuketim_ani_M2_identity_known_HER_ZAMAN_false(conn):
    _run_kaydi(conn)
    _modul_satiri(conn, "GARFA", source_run_key="1"*64)
    persist_module_input_snapshot(conn, total_rasyo_run_id="RUN-A",
                                  results=[_sonuc()])
    conn.close()
    m2 = oku("SELECT identity_known, module_source_run_key FROM analytics"
            ".total_rasyo_module_input WHERE ticker='GARFA' AND module='M2'")[0]
    assert m2 == (False, None)


def test_tuketim_ani_TOCTOU_eslesmezse_identity_known_false(conn):
    """
    Orkestratör ESKI bir module_source_at kullandi ama module_scores
    o arada BASKA bir analysis_at ile guncellendi (TOCTOU). Bagimsiz sorgu
    ESKI zamanla eslesmedigi icin kimlik YAZILAMAZ.
    """
    _run_kaydi(conn)
    _modul_satiri(conn, "GARFA", analysis_at=ANALIZ + timedelta(hours=1),
                 source_run_key="2"*64)
    # Sonuc nesnesi orkestratörün kullandigi ESKI zamani tasiyor.
    n = persist_module_input_snapshot(
        conn, total_rasyo_run_id="RUN-A", results=[_sonuc(m1_source_at=ANALIZ)])
    assert n == 6
    conn.close()
    m1 = oku("SELECT identity_known, module_source_run_key FROM analytics"
            ".total_rasyo_module_input WHERE ticker='GARFA' AND module='M1'")[0]
    assert m1 == (False, None), "TOCTOU eslesmezken kimlik yazilmamali"


def test_tuketim_ani_eksik_modulde_sorgu_yapilmaz(conn):
    """source_at None ise (modul eksikti) bagimsiz sorgu hic denenmez."""
    _run_kaydi(conn)
    sonuc = _sonuc()
    sonuc["modules"]["M1"] = {"score": None, "missing": True, "source_at": None}
    n = persist_module_input_snapshot(conn, total_rasyo_run_id="RUN-A",
                                      results=[sonuc])
    assert n == 6
    conn.close()
    m1 = oku("SELECT identity_known, module_missing FROM analytics"
            ".total_rasyo_module_input WHERE ticker='GARFA' AND module='M1'")[0]
    assert m1 == (False, True)


def test_tuketim_ani_alti_modulun_hepsi_yazilir(conn):
    _run_kaydi(conn)
    _modul_satiri(conn, "GARFA", source_run_key="1"*64)
    persist_module_input_snapshot(conn, total_rasyo_run_id="RUN-A",
                                  results=[_sonuc()])
    conn.close()
    moduller = {r[0] for r in oku("SELECT module FROM analytics"
                                  ".total_rasyo_module_input WHERE ticker='GARFA'")}
    assert moduller == {"M1", "M2", "M3", "Ek1", "Ek4", "Ek9"}


def test_tuketim_ani_ayni_analysis_at_farkli_deger_identity_known_false(conn):
    """
    GERCEK RISK: bir duzeltme kosusu AYNI analysis_at etiketiyle DEGERI
    sessizce degistirebilir (upsert PK'si ticker+asof_date+horizon_days'tir,
    analysis_at ozgunlugu GARANTI ETMEZ). Orkestratör 0.6 kullandi, ama
    module_scores SIMDI 0.99 tasiyor -- ayni analysis_at etiketiyle.
    """
    _run_kaydi(conn)
    _modul_satiri(conn, "GARFA", source_run_key="1" * 64)
    with conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE analytics.module_scores SET m1=0.99"
                        " WHERE ticker='GARFA'")
    n = persist_module_input_snapshot(
        conn, total_rasyo_run_id="RUN-A", results=[_sonuc()])  # M1 skoru 0.6 idi
    assert n == 6
    conn.close()
    m1 = oku("SELECT identity_known, module_source_run_key FROM analytics"
            ".total_rasyo_module_input WHERE ticker='GARFA' AND module='M1'")[0]
    assert m1 == (False, None), (
        "ayni analysis_at etiketi altinda deger degismis, kimlik gecerli sayilmamali")


def test_tuketim_ani_deger_eslesirse_identity_known_true(conn):
    """Kontrol grubu: deger degismediyse kimlik normal sekilde bulunur."""
    _run_kaydi(conn)
    _modul_satiri(conn, "GARFA", source_run_key="1" * 64)
    persist_module_input_snapshot(conn, total_rasyo_run_id="RUN-A",
                                  results=[_sonuc()])
    conn.close()
    m1 = oku("SELECT identity_known FROM analytics.total_rasyo_module_input"
            " WHERE ticker='GARFA' AND module='M1'")[0]
    assert m1 == (True,)


def test_tuketim_ani_source_run_key_YOKSA_identity_known_false(conn):
    """
    KRITIK AYRIM: 'lineage satiri var' != 'source identity biliniyor'.
    analysis_at ve deger TAM eslesse bile, module_scores.source_run_key
    NULL ise (siradan/BANK-disi gunluk pipeline) GERCEK URETIM KOSUSU
    kimligi kanitlanamaz. Sahte/gecici kimlik UYDURULMAZ.
    """
    _run_kaydi(conn)
    _modul_satiri(conn, "GARFA", source_run_key=None)  # source_run_key YOK
    persist_module_input_snapshot(conn, total_rasyo_run_id="RUN-A",
                                  results=[_sonuc()])
    conn.close()
    m1 = oku("SELECT identity_known, module_source_run_key FROM analytics"
            ".total_rasyo_module_input WHERE ticker='GARFA' AND module='M1'")[0]
    assert m1 == (False, None), (
        "source_run_key yokken identity_known=True olmamali -- "
        "zaman/deger eslesmesi gercek kimlik kanitinin yerine gecemez")


def test_run_id_olmadan_reddedilir(conn):
    with pytest.raises(ModuleInputSnapshotError):
        build_module_input_rows(conn, total_rasyo_run_id="", results=[_sonuc()])


# ============================================ IMMUTABLE + ROL
def test_total_rasyo_module_input_UPDATE_edilemez(conn):
    _run_kaydi(conn)
    _modul_satiri(conn, "GARFA", source_run_key="1"*64)
    persist_module_input_snapshot(conn, total_rasyo_run_id="RUN-A",
                                  results=[_sonuc()])
    with pytest.raises(psycopg2.Error, match="immutable"):
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE analytics.total_rasyo_module_input"
                            " SET identity_known = true")


def test_runtime_TRUNCATE_edemez(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("TRUNCATE analytics.total_rasyo_module_input")


def test_runtime_INSERT_edebilir(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            cur.execute("SELECT count(*) FROM analytics.total_rasyo_module_input")
            assert cur.fetchone()[0] >= 0


# ============================================ SEMA KISITI
def test_identity_known_true_ama_alan_bos_reddedilir(conn):
    _run_kaydi(conn)
    with pytest.raises(psycopg2.Error):
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO analytics.total_rasyo_module_input
                    (total_rasyo_run_id, ticker, module, module_missing,
                     identity_known)
                    VALUES ('RUN-A','GARFA','M1',false,true)
                """)
