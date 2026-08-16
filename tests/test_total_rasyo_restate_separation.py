"""
PIT_HISTORY ile CURRENT_KNOWLEDGE_RESTATE ayrimi — canli PostgreSQL.

KESIN KURAL:
  PIT_HISTORY               -> o analysis_at aninda GERCEKTEN bilinen veriyle
                               ne sonuc cikiyordu? GERIYE DONUK DEGISTIRILMEZ.
  CURRENT_KNOWLEDGE_RESTATE -> sonradan ogrendigimiz duzeltmelerle bugun ne
                               hesapliyoruz? AYRI tabloda, ayri bilgi urunu.

En kritik invariant: normal PIT sorgulari ve V19 latest_* view'lari restate
tablolarina HIC BAKMAZ. Tek bir filtre unutulursa look-ahead sizar.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

TZ = timezone(timedelta(hours=3))
HEDEF_KESIM = datetime(2025, 12, 31, 20, 0, tzinfo=TZ)   # 2025Q4 PIT kosusu
CUTOFF_2026 = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)     # duzeltme sonrasi
CUTOFF_2026_HAZIRAN = datetime(2026, 6, 1, 10, 0, tzinfo=TZ)


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
            cur.execute("SELECT to_regclass('analytics.total_rasyo_restate_runs')")
            if cur.fetchone()[0] is None:
                c.close()
                pytest.skip("sql/031 uygulanmamis")
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


def run_id(target, cutoff, profile="V1", version=1, sha="a" * 64):
    ham = f"{target.isoformat()}|{cutoff.isoformat()}|{sha}|{profile}|{version}"
    return hashlib.sha256(ham.encode()).hexdigest()


def pit_satiri(conn, ticker="GARAN", *, analysis_at=HEDEF_KESIM, skor=0.62,
               karar="IZLE"):
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.company_total_rasyo_result
                (analysis_at, ticker, routed_engine, engine_status,
                 m2_score, m2_missing, m1_score, m1_missing, m3_score, m3_missing,
                 ek4_score, ek4_missing, ek1_score, ek1_missing,
                 ek9_score, ek9_missing, good_count_ge8, good_count_missing,
                 base_score, final_score, veto_flag, decision, weights_profile,
                 total_rasyo_status, diagnostics)
                VALUES (%s,%s,'BANK','OK', 0.80,false, 0.60,false, 0.70,false,
                        0.50,false, 0.40,false, 0.30,false, 9,false,
                        %s,%s,false,%s,'TOTAL_RASYO_SCORE_V1','OK','{}')
            """, (analysis_at, ticker, skor, skor, karar))


def restate_kosusu(conn, *, target=HEDEF_KESIM, cutoff=CUTOFF_2026,
                   profile="V1", version=1, status="COMPLETE"):
    rid = run_id(target, cutoff, profile, version)
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.total_rasyo_restate_runs
                (restate_run_id, target_analysis_at, knowledge_cutoff_at,
                 started_at, finished_at, status, restate_contract_version,
                 reader_version, inputs_sha256, results_sha256,
                 calculation_profile, calculation_version, company_count,
                 successful_company_count, diagnostics)
                VALUES (%s,%s,%s,%s,%s,%s,1,1,%s,%s,%s,%s,1,1,'{}')
            """, (rid, target, cutoff, cutoff, cutoff, status, "b" * 64, "c" * 64,
                  profile, version))
    return rid


def restate_satiri(conn, rid, ticker="GARAN", *, target=HEDEF_KESIM,
                   cutoff=CUTOFF_2026, skor=0.48, karar="UZAK",
                   m1_source_at=None):
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO analytics.company_total_rasyo_restate_result
                (restate_run_id, ticker, target_analysis_at, knowledge_cutoff_at,
                 engine_family, m2_score, m2_missing, m1_score, m1_source_at,
                 m1_missing, m3_score, m3_missing, ek4_score, ek4_missing,
                 ek1_score, ek1_missing, ek9_score, ek9_missing,
                 good_count_ge8, good_count_missing, base_score, final_score,
                 veto_flag, decision, total_rasyo_status, diagnostics)
                VALUES (%s,%s,%s,%s,'BANK', 0.80,false, 0.20,%s,false,
                        0.70,false, 0.50,false, 0.40,false, 0.30,false,
                        9,false, %s,%s,false,%s,'OK','{}')
            """, (rid, ticker, target, cutoff, m1_source_at, skor, skor, karar))


def oku(sorgu, params=()):
    c = _baglan()
    try:
        with c.cursor() as cur:
            cur.execute(sorgu, params)
            return cur.fetchall()
    finally:
        c.close()


# ============================================ 1) PIT GERIYE DONUK DEGISMEZ
def test_duzeltme_eski_PIT_satirini_degistirmez(conn):
    """
    2025Q4 verisi 2026'da duzeltildi. 2025 PIT satiri TARIHSEL GERCEKLIK
    olarak korunmali.
    """
    pit_satiri(conn, skor=0.62, karar="IZLE")
    rid = restate_kosusu(conn)
    restate_satiri(conn, rid, skor=0.48, karar="UZAK")
    conn.close()

    pit = oku("SELECT final_score, decision FROM analytics"
              ".company_total_rasyo_result WHERE ticker='GARAN'")
    assert len(pit) == 1
    assert float(pit[0][0]) == pytest.approx(0.62), "PIT gecmisi overwrite edildi"
    assert pit[0][1] == "IZLE"


def test_restate_ayri_tabloda_olusur(conn):
    pit_satiri(conn)
    rid = restate_kosusu(conn)
    restate_satiri(conn, rid)
    conn.close()
    assert len(oku("SELECT 1 FROM analytics.company_total_rasyo_result")) == 1
    assert len(oku("SELECT 1 FROM analytics"
                   ".company_total_rasyo_restate_result")) == 1


def test_PIT_ve_restate_farkli_olabilir_ve_IKISI_de_korunur(conn):
    pit_satiri(conn, skor=0.62, karar="IZLE")
    rid = restate_kosusu(conn)
    restate_satiri(conn, rid, skor=0.48, karar="UZAK")
    conn.close()
    kars = oku("SELECT pit_final_score, restate_final_score, decision_changed "
               "FROM analytics.restate_vs_pit_comparison WHERE ticker='GARAN'")
    assert len(kars) == 1
    assert float(kars[0][0]) == pytest.approx(0.62)
    assert float(kars[0][1]) == pytest.approx(0.48)
    assert kars[0][2] is True


# ============================================ 2) CUTOFF AYRIMI
def test_iki_farkli_cutoff_iki_farkli_run(conn):
    """
    Ayni tarihsel kesim, farkli bilgi kesimleri -> ayri restate kosulari.
    Ikisi de korunur; biri digerini ezmez.
    """
    r1 = restate_kosusu(conn, cutoff=CUTOFF_2026)
    r2 = restate_kosusu(conn, cutoff=CUTOFF_2026_HAZIRAN)
    assert r1 != r2
    restate_satiri(conn, r1, cutoff=CUTOFF_2026, skor=0.48)
    restate_satiri(conn, r2, cutoff=CUTOFF_2026_HAZIRAN, skor=0.55)
    conn.close()
    satirlar = oku("SELECT knowledge_cutoff_at, final_score FROM analytics"
                   ".company_total_rasyo_restate_result WHERE ticker='GARAN'"
                   " ORDER BY knowledge_cutoff_at")
    assert len(satirlar) == 2
    assert float(satirlar[0][1]) == pytest.approx(0.48)
    assert float(satirlar[1][1]) == pytest.approx(0.55)


def test_target_ve_cutoff_ayri_kavram(conn):
    """knowledge_cutoff_at, target_analysis_at'ten ONCE olamaz."""
    rid = "b" * 64
    with pytest.raises(psycopg2.Error):
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO analytics.total_rasyo_restate_runs
                    (restate_run_id, target_analysis_at, knowledge_cutoff_at,
                     started_at, finished_at, status, restate_contract_version,
                     reader_version, inputs_sha256, results_sha256,
                     calculation_profile, calculation_version, company_count,
                     successful_company_count, diagnostics)
                    VALUES (%s,%s,%s,%s,%s,'COMPLETE',1,1,%s,%s,'V1',1,0,0,'{}')
                """, (rid, CUTOFF_2026, HEDEF_KESIM, CUTOFF_2026, CUTOFF_2026,
                      "a" * 64, "a" * 64))


def test_ayni_kesim_ayni_cutoff_idempotent_kimlik(conn):
    a = run_id(HEDEF_KESIM, CUTOFF_2026)
    b = run_id(HEDEF_KESIM, CUTOFF_2026)
    assert a == b
    assert run_id(HEDEF_KESIM, CUTOFF_2026, version=2) != a


# ============================================ 3) LOOK-AHEAD KORUMASI
def test_cutoff_sonrasi_kaynak_REDDEDILIR(conn):
    """
    'Bugunku bilgi' SINIRSIZ bilgi demek degildir. Restate sonucunda
    kullanilan hicbir bilesenin kaynak zamani cutoff'u ASAMAZ.
    """
    rid = restate_kosusu(conn, cutoff=CUTOFF_2026)
    with pytest.raises(psycopg2.Error):
        restate_satiri(conn, rid, m1_source_at=CUTOFF_2026 + timedelta(days=1))


def test_cutoff_oncesi_kaynak_kabul(conn):
    rid = restate_kosusu(conn, cutoff=CUTOFF_2026)
    restate_satiri(conn, rid, m1_source_at=CUTOFF_2026 - timedelta(days=1))
    conn.close()
    assert len(oku("SELECT 1 FROM analytics"
                   ".company_total_rasyo_restate_result")) == 1


# ============================================ 4) PIT SORGULARI KIRLENMEZ
def test_latest_PIT_viewi_restate_satirini_GORMEZ(conn):
    """
    EN ONEMLI INVARIANT. Restate satiri gunluk siralamaya sizarsa,
    gecmisten gelen bilgiyle bugunku karar verilmis olur.
    """
    pit_satiri(conn, skor=0.62)
    rid = restate_kosusu(conn)
    restate_satiri(conn, rid, skor=0.48)
    conn.close()
    latest = oku("SELECT ticker, final_score FROM analytics"
                 ".latest_total_rasyo_result")
    assert len(latest) == 1
    assert float(latest[0][1]) == pytest.approx(0.62), "restate PIT view'ina sizdi"


def test_V19_viewlari_restate_tablosuna_BAKMAZ():
    """
    Kaynak duzeyinde kilit: sql/027-030 icindeki view tanimlari restate
    tablolarina referans VEREMEZ.
    """
    for dosya in ("027_total_rasyo_orchestrator.sql",
                  "028_total_rasyo_run_registry.sql",
                  "029_total_rasyo_status_taxonomy.sql",
                  "030_total_rasyo_run_scope.sql"):
        metin = Path("sql") / dosya
        icerik = metin.read_text(encoding="utf-8")
        assert "restate" not in icerik.lower(), f"{dosya} restate'e referans veriyor"


def test_restate_migrationi_V19_viewlarini_DEGISTIRMEZ():
    """sql/031 V19 view'larini yeniden tanimlamamali."""
    icerik = Path("sql/031_total_rasyo_restate.sql").read_text(encoding="utf-8")
    for yasak in ("latest_total_rasyo_result AS", "latest_engine_run AS",
                  "total_rasyo_engine_coverage AS"):
        assert yasak not in icerik, f"031 V19 view'ini yeniden tanimliyor: {yasak}"
    # V19 sonuc tablosunu ALTER etmemeli.
    assert not re.search(r"ALTER TABLE\s+analytics\.company_total_rasyo_result",
                         icerik), "031 V19 tablosunu degistiriyor"


def test_V19_tablosunun_anahtari_DEGISMEDI(conn):
    """(analysis_at, ticker) korunmali; knowledge_basis sutunu eklenmemeli."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.attname FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid
                               AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'analytics.company_total_rasyo_result'::regclass
              AND i.indisprimary ORDER BY a.attname
        """)
        pk = [r[0] for r in cur.fetchall()]
        assert pk == ["analysis_at", "ticker"]
        cur.execute("""
            SELECT count(*) FROM information_schema.columns
            WHERE table_name='company_total_rasyo_result'
              AND column_name='knowledge_basis'
        """)
        assert cur.fetchone()[0] == 0


# ============================================ 5) YANLIS TABLOYA YAZIM
def test_restate_satiri_PIT_tablosuna_yazilamaz(conn):
    """
    Restate satirinin PIT tablosuna INSERT edilmesi engellenmeli. PIT
    tablosunda knowledge_cutoff_at sutunu YOKTUR; deneme hata verir.
    """
    with pytest.raises(psycopg2.Error):
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO analytics.company_total_rasyo_result
                    (analysis_at, ticker, knowledge_cutoff_at)
                    VALUES (%s,'GARAN',%s)
                """, (HEDEF_KESIM, CUTOFF_2026))


def test_restate_run_olmadan_sonuc_yazilamaz(conn):
    """Yabanci anahtar: kosu defterine kaydedilmemis restate sonucu olamaz."""
    with pytest.raises(psycopg2.Error):
        restate_satiri(conn, "c" * 64)


# ============================================ 6) BASARISIZ RESTATE
def test_basarisiz_restate_PIT_sonucunu_etkilemez(conn):
    pit_satiri(conn, skor=0.62)
    restate_kosusu(conn, status="FAILED")
    conn.close()
    pit = oku("SELECT final_score FROM analytics.company_total_rasyo_result")
    assert len(pit) == 1 and float(pit[0][0]) == pytest.approx(0.62)
    assert oku("SELECT count(*) FROM analytics"
               ".company_total_rasyo_restate_result")[0][0] == 0


def test_restate_OK_satiri_alti_modul_tam_ister(conn):
    rid = restate_kosusu(conn)
    with pytest.raises(psycopg2.Error):
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO analytics.company_total_rasyo_restate_result
                    (restate_run_id, ticker, target_analysis_at,
                     knowledge_cutoff_at, engine_family, m2_missing, m1_missing,
                     m3_missing, ek4_missing, ek1_missing, ek9_missing,
                     good_count_missing, total_rasyo_status, diagnostics)
                    VALUES (%s,'GARAN',%s,%s,'BANK',true,false,false,false,
                            false,false,false,'OK','{}')
                """, (rid, HEDEF_KESIM, CUTOFF_2026))
