"""
Etki plani kaliciligi — canli PostgreSQL.

IMMUTABLE = "icerigi degistirilemez", "bir kez yazilabilir" DEGIL.
Retry, worker yeniden baslatma ve ayni fact revizyonunun tekrar islenmesi
gereksiz hata URETMEMELI.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.change_impact_detector import (
    KNOWLEDGE_PIT,
    KNOWLEDGE_RESTATE,
    FactChange,
    PeerCandidate,
    detect_change_impact,
)
from src.analytics.change_impact_persistence import (
    ENTRY_COLUMNS,
    PLAN_COLUMNS,
    ImpactPlanConflict,
    load_targeted_tickers,
    persist_impact_plan,
    record_application_attempt,
)

TZ = timezone(timedelta(hours=3))
YAYIN = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)
KESIM = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)


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
            cur.execute("SELECT to_regclass('analytics.impact_plan')")
            if cur.fetchone()[0] is None:
                c.close()
                pytest.skip("sql/032 uygulanmamis")
            # TRUNCATE kullaniliyor cunku immutable trigger DELETE'i
            # (dogru sekilde) engelliyor. TRUNCATE satir trigger'larini
            # atlar ve tablo sahipligi gerektirir; bu bilinen bir
            # PostgreSQL davranisidir ve belgede sinir olarak yazilidir.
            cur.execute("TRUNCATE analytics.reconciliation_finding,"
                        " analytics.reconciliation_run,"
                        " analytics.impact_application_target,"
                        " analytics.impact_application_run,"
                        " analytics.impact_plan_entry, analytics.impact_plan")
    yield c
    if not c.closed:
        c.close()


def degisiklik(**kw) -> FactChange:
    veri = dict(ticker="GARFA", statement_type="BALANCE_SHEET",
                fact_key="total_equity", period_end=date(2025, 12, 31),
                old_value=1000.0, new_value=800.0, published_at=YAYIN,
                source_fact_id="F1", source_statement_id="S1",
                source_version_id="V2", routed_engine="FINANCIAL")
    veri.update(kw)
    return FactChange(**veri)


META = dict(direct_ticker="GARFA", source_fact_id="F1",
            source_statement_id="S1", source_version_id="V2",
            statement_type="BALANCE_SHEET", fact_key="total_equity",
            changed_period_end=date(2025, 12, 31), published_at=YAYIN)


def plan_uret(**kw):
    havuz = {"FINANCIAL": [
        PeerCandidate("GARFA", True, False, 1.2, 0.9),
        PeerCandidate("PEER1", True, True, 1.0, 1.0),
        PeerCandidate("PEER2", True, True, 1.5, 1.5)]}
    args = dict(impact_run_id="R1", peer_candidates=havuz, analysis_at=KESIM)
    args.update(kw)
    degisim = args.pop("change", degisiklik())
    return detect_change_impact(degisim, **args)


def oku(sorgu, params=()):
    c = _baglan()
    try:
        with c.cursor() as cur:
            cur.execute(sorgu, params)
            return cur.fetchall()
    finally:
        c.close()


# ============================================ 1) IDEMPOTENT YAZIM
def test_ilk_yazim_normal(conn):
    p = plan_uret()
    sonuc = persist_impact_plan(conn, p, META)
    assert sonuc["created"] is True
    assert sonuc["entry_rows"] == len(p.entries)
    conn.close()
    assert oku("SELECT count(*) FROM analytics.impact_plan")[0][0] == 1


def test_ayni_plan_ikinci_kez_SATIR_ARTIRMAZ(conn):
    """Retry ve worker yeniden baslatma gereksiz hata URETMEMELI."""
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    ikinci = persist_impact_plan(conn, p, META)
    assert ikinci["created"] is False
    conn.close()
    assert oku("SELECT count(*) FROM analytics.impact_plan")[0][0] == 1
    assert oku("SELECT count(*) FROM analytics.impact_plan_entry")[0][0] == len(p.entries)


def test_idempotent_yazim_created_at_TAZELEMEZ(conn):
    """
    'Idempotent' adi altinda mevcut satiri UPDATE edip zamanini tazelemek,
    planin TARIHSEL KANIT niteligini yok eder.
    """
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    once = oku("SELECT created_at FROM analytics.impact_plan")[0][0]
    import time
    time.sleep(0.05)
    persist_impact_plan(conn, p, META)
    conn.close()
    sonra = oku("SELECT created_at FROM analytics.impact_plan")[0][0]
    assert sonra == once, "created_at tazelendi: provenance kayboldu"


def test_idempotent_yazim_plan_sha_degistirmez(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    sha_once = oku("SELECT plan_sha256 FROM analytics.impact_plan")[0][0]
    persist_impact_plan(conn, p, META)
    conn.close()
    assert oku("SELECT plan_sha256 FROM analytics.impact_plan")[0][0] == sha_once


def test_plan_uretimi_deterministik():
    """Ayni girdi -> ayni kimlik VE ayni icerik ozeti."""
    a, b = plan_uret(), plan_uret()
    assert a.impact_plan_id == b.impact_plan_id
    assert a.plan_sha256() == b.plan_sha256()


# ============================================ 2) CATISMA
def test_ayni_kimlik_farkli_icerik_SERT_RET(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)

    # Ayni kimlik, farkli icerik: elle bozulmus plan.
    from dataclasses import replace
    bozuk = replace(p, entries=p.entries[:1])
    assert bozuk.impact_plan_id == p.impact_plan_id
    assert bozuk.plan_sha256() != p.plan_sha256()
    with pytest.raises(ImpactPlanConflict, match="icerik farkli"):
        persist_impact_plan(conn, bozuk, META)


def test_catisma_hicbir_seyi_OVERWRITE_ETMEZ(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    beklenen = len(p.entries)
    from dataclasses import replace
    bozuk = replace(p, entries=p.entries[:1])
    with pytest.raises(ImpactPlanConflict):
        persist_impact_plan(conn, bozuk, META)
    conn.close()
    assert oku("SELECT count(*) FROM analytics.impact_plan_entry")[0][0] == beklenen
    assert oku("SELECT plan_sha256 FROM analytics.impact_plan")[0][0] == p.plan_sha256()


# ============================================ 3) IMMUTABLE
def test_plan_UPDATE_edilemez(conn):
    persist_impact_plan(conn, plan_uret(), META)
    with pytest.raises(psycopg2.Error, match="immutable"):
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE analytics.impact_plan SET entry_count = 0")


def test_plan_DELETE_edilemez(conn):
    persist_impact_plan(conn, plan_uret(), META)
    with pytest.raises(psycopg2.Error, match="immutable"):
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM analytics.impact_plan")


def test_plan_entry_UPDATE_edilemez(conn):
    persist_impact_plan(conn, plan_uret(), META)
    with pytest.raises(psycopg2.Error, match="immutable"):
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE analytics.impact_plan_entry "
                            "SET impacted_ticker = 'HACK'")


# ============================================ 4) UYGULAMA KOSULARI
def test_ayni_plan_birden_fazla_uygulama_denemesi(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    a1 = record_application_attempt(
        conn, impact_plan_id=p.impact_plan_id, application_run_id="APP-1",
        started_at=KESIM, status="FAILED", finished_at=KESIM,
        error_type="OperationalError", targeted_ticker_count=3)
    a2 = record_application_attempt(
        conn, impact_plan_id=p.impact_plan_id, application_run_id="APP-2",
        started_at=KESIM, status="APPLIED", finished_at=KESIM,
        targeted_ticker_count=3)
    assert (a1, a2) == (1, 2)
    conn.close()
    satirlar = oku("SELECT attempt_no, status FROM analytics"
                   ".impact_application_run ORDER BY attempt_no")
    assert [r[1] for r in satirlar] == ["FAILED", "APPLIED"]


def test_uygulama_denemesi_plani_KIRLETMEZ(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    once = oku("SELECT plan_sha256, created_at FROM analytics.impact_plan")[0]
    record_application_attempt(
        conn, impact_plan_id=p.impact_plan_id, application_run_id="APP-1",
        started_at=KESIM, status="APPLIED", finished_at=KESIM)
    conn.close()
    assert oku("SELECT plan_sha256, created_at FROM analytics.impact_plan")[0] == once


def test_uygulama_kaydi_DELETE_edilemez(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    record_application_attempt(conn, impact_plan_id=p.impact_plan_id,
                               application_run_id="APP-1", started_at=KESIM,
                               status="APPLIED", finished_at=KESIM)
    with pytest.raises(psycopg2.Error, match="immutable"):
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM analytics.impact_application_run")


def test_ayni_attempt_no_iki_kez_olamaz(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    with pytest.raises(psycopg2.Error):
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO analytics.impact_application_run
                    (application_run_id, impact_plan_id, attempt_no, started_at,
                     finished_at, status, targeted_ticker_count, diagnostics)
                    VALUES ('A1',%s,1,%s,%s,'APPLIED',0,'{}'),
                           ('A2',%s,1,%s,%s,'APPLIED',0,'{}')
                """, (p.impact_plan_id, KESIM, KESIM,
                      p.impact_plan_id, KESIM, KESIM))


def test_FAILED_kosu_hata_tipi_tasir(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    with pytest.raises(psycopg2.Error):
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO analytics.impact_application_run
                    (application_run_id, impact_plan_id, attempt_no, started_at,
                     finished_at, status, targeted_ticker_count, diagnostics)
                    VALUES ('A1',%s,1,%s,%s,'FAILED',0,'{}')
                """, (p.impact_plan_id, KESIM, KESIM))


def test_uygulama_hata_mesaji_redakte_edilir(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    record_application_attempt(
        conn, impact_plan_id=p.impact_plan_id, application_run_id="APP-1",
        started_at=KESIM, status="FAILED", finished_at=KESIM,
        error_type="OperationalError",
        error_message="baglanti hatasi password: hunter2")
    conn.close()
    mesaj = oku("SELECT error_message FROM analytics.impact_application_run")[0][0]
    assert "hunter2" not in mesaj and "***" in mesaj


# ============================================ 5) PIT vs RESTATE KIMLIGI
def test_PIT_ve_RESTATE_farkli_plan_kimligi():
    """Ayni fact degisikligi, farkli bilgi tabani -> FARKLI plan."""
    pit = plan_uret(knowledge_basis=KNOWLEDGE_PIT)
    restate = plan_uret(knowledge_basis=KNOWLEDGE_RESTATE,
                        knowledge_cutoff_at=KESIM + timedelta(days=30))
    assert pit.impact_plan_id != restate.impact_plan_id


def test_iki_bilgi_tabani_birlikte_saklanir(conn):
    pit = plan_uret(knowledge_basis=KNOWLEDGE_PIT)
    restate = plan_uret(knowledge_basis=KNOWLEDGE_RESTATE,
                        knowledge_cutoff_at=KESIM + timedelta(days=30))
    persist_impact_plan(conn, pit, META)
    persist_impact_plan(conn, restate, META)
    conn.close()
    satirlar = oku("SELECT knowledge_basis FROM analytics.impact_plan "
                   "ORDER BY knowledge_basis")
    assert [r[0] for r in satirlar] == ["CURRENT_KNOWLEDGE_RESTATE", "PIT_HISTORY"]


def test_RESTATE_plani_cutoff_gerektirir():
    from src.analytics.change_impact_detector import ChangeImpactError
    with pytest.raises(ChangeImpactError, match="knowledge_cutoff_at"):
        plan_uret(knowledge_basis=KNOWLEDGE_RESTATE)


def test_farkli_analysis_at_farkli_plan():
    a = plan_uret(analysis_at=KESIM)
    b = plan_uret(analysis_at=KESIM + timedelta(days=1))
    assert a.impact_plan_id != b.impact_plan_id


def test_farkli_kaynak_surumu_farkli_plan():
    a = plan_uret()
    b = plan_uret(change=degisiklik(source_version_id="V3"))
    assert a.impact_plan_id != b.impact_plan_id


# ============================================ 6) KOPRU
def test_hedef_kume_plandan_okunur(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    hedefler = load_targeted_tickers(conn, p.impact_plan_id)
    assert hedefler == p.targeted_tickers()
    assert "GARFA" in hedefler and "PEER1" in hedefler


def test_kendine_peer_kaydi_veritabanina_giremez(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    with pytest.raises(psycopg2.Error):
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO analytics.impact_plan_entry
                    (impact_plan_id, entry_seq, direct_ticker, impacted_ticker,
                     impact_type, engine_family, module, dependency_edge_id,
                     reason_code, actual_effects, effective_from,
                     affected_anchor_period_ends)
                    VALUES (%s, 9999, 'GARFA','GARFA','PEER_PROPAGATED',
                            'FINANCIAL','M2',%s,'X',ARRAY['A'],%s,
                            ARRAY['2025-12-31'::date])
                """, (p.impact_plan_id, "a" * 64, KESIM))


def test_sutun_sozlesmesi_semayla_eslesir(conn):
    with conn.cursor() as cur:
        for tablo, sutunlar in (("impact_plan", PLAN_COLUMNS),
                                ("impact_plan_entry", ENTRY_COLUMNS)):
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='analytics' AND table_name=%s", (tablo,))
            gercek = {r[0] for r in cur.fetchall()}
            eksik = set(sutunlar) - gercek
            assert not eksik, f"{tablo} semada olmayan sutun: {eksik}"


def test_entry_seq_GIRIS_SIRASINDAN_bagimsiz():
    """
    entry_seq DETERMINISTIK siralamadan gelmeli, giris sirasindan DEGIL.
    Aksi halde ayni mantiksal plan farkli uretim sirasiyla farkli seq
    atar ve plan_sha256 ayni olmasina ragmen satirlar kayar.

    Girdileri TERS cevirerek sinariz: ayni (seq -> ticker/module) eslemesi
    cikmali.
    """
    from dataclasses import replace

    from src.analytics.change_impact_persistence import _entry_rows
    p = plan_uret()
    ters = replace(p, entries=tuple(reversed(p.entries)))
    a = _entry_rows(p)
    b = _entry_rows(ters)
    assert [(r[1], r[3], r[6]) for r in a] == [(r[1], r[3], r[6]) for r in b], \
        "entry_seq giris sirasina bagimli"


def test_entry_seq_kesintisiz_ve_sifirdan(conn):
    p = plan_uret()
    persist_impact_plan(conn, p, META)
    conn.close()
    seqler = [r[0] for r in oku("SELECT entry_seq FROM analytics"
                                ".impact_plan_entry ORDER BY entry_seq")]
    assert seqler == list(range(len(p.entries)))
