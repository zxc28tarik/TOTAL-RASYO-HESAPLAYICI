"""
URETIM ROL SOZLESMESI — canli PostgreSQL dogrulamasi.

Satir-seviyesi immutable trigger TEK BASINA guvenlik siniri DEGILDIR:
TRUNCATE satir trigger'larini ATLAR. Bu yuzden koruma iki katmanlidir ve
ikinci katman (YETKI) burada dogrulanir.

Runtime rolu: UPDATE / DELETE / TRUNCATE YAPAMAMALI,
              ama idempotent plan INSERT akisi CALISMALI.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

TZ = timezone(timedelta(hours=3))
KESIM = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)

IMPACT_TABLOLARI = ("impact_plan", "impact_plan_entry", "impact_application_run")


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
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname='total_rasyo_runtime'")
            if cur.fetchone() is None:
                c.close()
                pytest.skip("sql/033 uygulanmamis")
    yield c
    if not c.closed:
        c.close()


def yetki(conn, tablo: str, ayricalik: str, rol: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege(%s, %s, %s)",
                    (rol, f"analytics.{tablo}", ayricalik))
        return bool(cur.fetchone()[0])


# ============================================ RUNTIME KISITLARI
@pytest.mark.parametrize("tablo", IMPACT_TABLOLARI)
def test_runtime_DELETE_yapamaz(conn, tablo):
    assert not yetki(conn, tablo, "DELETE", "total_rasyo_runtime")


@pytest.mark.parametrize("tablo", IMPACT_TABLOLARI)
def test_runtime_TRUNCATE_yapamaz(conn, tablo):
    """
    EN KRITIK: TRUNCATE immutable trigger'i atlar. Tek savunma trigger
    olsaydi, runtime rolu plan gecmisini tamamen silebilirdi.
    """
    assert not yetki(conn, tablo, "TRUNCATE", "total_rasyo_runtime")


@pytest.mark.parametrize("tablo", ("impact_plan", "impact_plan_entry"))
def test_runtime_plan_tablosunu_UPDATE_edemez(conn, tablo):
    assert not yetki(conn, tablo, "UPDATE", "total_rasyo_runtime")


def test_runtime_uygulama_kosusunu_UPDATE_edebilir(conn):
    """Kosu ilerledikce status/finished_at guncellenebilmeli."""
    assert yetki(conn, "impact_application_run", "UPDATE", "total_rasyo_runtime")


@pytest.mark.parametrize("tablo", IMPACT_TABLOLARI)
def test_runtime_INSERT_ve_SELECT_yapabilir(conn, tablo):
    """Kisitlar akisi kirmamali: idempotent plan yazimi calismali."""
    assert yetki(conn, tablo, "INSERT", "total_rasyo_runtime")
    assert yetki(conn, tablo, "SELECT", "total_rasyo_runtime")


# ============================================ SAHIPLIK AYRIMI
@pytest.mark.parametrize("tablo", IMPACT_TABLOLARI)
def test_plan_tablolarinin_sahibi_migration_rolu(conn, tablo):
    """Runtime rolu OWNER OLMAMALI; owner butun kisitlari asabilir."""
    with conn.cursor() as cur:
        cur.execute("SELECT tableowner FROM pg_tables "
                    "WHERE schemaname='analytics' AND tablename=%s", (tablo,))
        sahip = cur.fetchone()[0]
    assert sahip == "total_rasyo_migration"
    assert sahip != "total_rasyo_runtime"


def test_runtime_sema_uzerinde_CREATE_yapamaz(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT has_schema_privilege('total_rasyo_runtime',"
                    " 'analytics', 'CREATE')")
        assert cur.fetchone()[0] is False


def test_runtime_migration_rolunun_uyesi_degil(conn):
    """Uyelik olsaydi runtime dolayli olarak owner yetkisi kazanirdi."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT pg_has_role('total_rasyo_runtime',
                               'total_rasyo_migration', 'USAGE')
        """)
        assert cur.fetchone()[0] is False


# ============================================ TEST ROLU AYRI
def test_test_rolu_uretim_runtime_ile_KARISTIRILMAZ(conn):
    """
    Fikstur temizligi icin TRUNCATE gerekiyor; bu yetki AYRI role verilir.
    Uretimde total_rasyo_test olusturulmamalidir.
    """
    assert yetki(conn, "impact_plan", "TRUNCATE", "total_rasyo_test")
    assert not yetki(conn, "impact_plan", "TRUNCATE", "total_rasyo_runtime")


# ============================================ GERCEK DAVRANIS
def test_runtime_rolu_gercekten_DELETE_edemez(conn):
    """
    Yetki tablosuna bakmak yetmez; gercek bir oturumda denenmelidir.
    SET ROLE ile runtime kimligine gecilir.
    """
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("DELETE FROM analytics.impact_plan")


def test_runtime_rolu_gercekten_TRUNCATE_edemez(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("TRUNCATE analytics.impact_plan")


def test_runtime_rolu_gercekten_UPDATE_edemez(conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("UPDATE analytics.impact_plan SET entry_count=0")


def test_runtime_rolu_SELECT_yapabilir(conn):
    """Kisitlar okuma akisini kirmamali."""
    with conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL ROLE total_rasyo_runtime")
            cur.execute("SELECT count(*) FROM analytics.impact_plan")
            assert cur.fetchone()[0] >= 0


def test_lineage_tablosu_runtime_tarafindan_yazilabilir(conn):
    """Readiness kaniti runtime tarafindan uretilir."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('analytics.module_production_lineage')")
        if cur.fetchone()[0] is None:
            pytest.skip("sql/034 uygulanmamis")
    assert yetki(conn, "module_production_lineage", "INSERT", "total_rasyo_runtime")
    assert not yetki(conn, "module_production_lineage", "TRUNCATE",
                     "total_rasyo_runtime")
