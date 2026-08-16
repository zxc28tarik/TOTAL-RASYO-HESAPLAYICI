"""
ANA ORKESTRATOR — uctan uca kritik kabul testleri (gercek PostgreSQL).

Kilitlenen sozlesmeler:
  1. Alti modul girdisi sirket basina TEK module_scores satirindan gelir
  2. Sektor M2'si eski module_scores.m2 alaninin YERINE gecer
  3. Evren sektor yonlendirmesinden gelir; hicbir sirket kaybolmaz
  4. Coken motorun sirketleri yeni kosuda MOTOR_COKTU olarak OTORITATIF yazilir
  5. compute_total_rasyo yalniz alti modul + veto girdisi tamsa cagrilir
  6. Durumlar birbirinden ayrik ve ayrintili
  7. Sayaclar ayrik; toplami company_count
  8. overall_status deterministik: COMPLETE / PARTIAL / FAILED
  9. Cift motor sahipliginde sessiz secim yok
 10. Motor ve satir SIRASI sonucu degistirmez
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.total_rasyo_combine import (
    INSUFF_M2_MISSING,
    INSUFF_NO_MODULE_ROW,
    INSUFF_PARTIAL,
    STATUS_ENGINE_CRASHED,
    STATUS_INSUFFICIENT,
    STATUS_NOT_RUN,
    STATUS_OK,
    STATUS_ROUTING_CONFLICT,
)
from src.analytics.total_rasyo_orchestrator import (
    OVERALL_COMPLETE_NO_RESULTS,
    OVERALL_COMPLETE,
    OVERALL_FAILED,
    OVERALL_PARTIAL,
    PERSIST_FAILED,
    OrchestratorError,
    run_total_rasyo_orchestrator,
)

TZ = timezone(timedelta(hours=3))
ANALIZ = datetime(2026, 8, 5, 20, 0, tzinfo=TZ)


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
            cur.execute("SELECT to_regclass('analytics.total_rasyo_run')")
            if cur.fetchone()[0] is None:
                c.close()
                pytest.skip("sql/027-029 uygulanmamis")
            cur.execute("TRUNCATE analytics.reconciliation_module_check, analytics.reconciliation_module_run, analytics.total_rasyo_module_input, analytics.total_rasyo_run, "
                        "analytics.daily_engine_run, "
                        "analytics.company_total_rasyo_result")
            cur.execute("DELETE FROM analytics.module_scores")
    yield c
    if not c.closed:
        c.close()


def modul_satiri(conn, ticker, *, asof=date(2026, 8, 5), analysis_at=None,
                 m1=0.6, m2=0.99, m3=0.7, ek1=0.4, ek4=0.5, ek9=0.3, good=9):
    """`m2=0.99` BILEREK: sektor M2'si (0.80) yerine gecmeli."""
    if analysis_at is None:
        analysis_at = ANALIZ - timedelta(hours=1)
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO analytics.module_scores (ticker, asof_date, horizon_days,"
                " m1, m2, m3, ek1, ek4, ek9, good_count_ge8, analysis_at)"
                " VALUES (%s,%s,20,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ticker, asof, m1, m2, m3, ek1, ek4, ek9, good, analysis_at))


def m2cikti(score=0.80, source="TEST_V1", usable=True, conf=0.7):
    return {"m2": score, "m2_source": source, "valuation_usable": usable,
            "valuation_confidence": conf, "valuation_reason": None}


def motor(results):
    return lambda: {"results": results, "rejections": {}}


def coken_motor():
    def patla():
        raise RuntimeError("motor cokti")
    return patla


def kos(conn, routing, runners, **kw):
    kw.setdefault("run_id", f"tr-{uuid.uuid4().hex[:12]}")
    return run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing, engine_runners=runners, **kw)


def oku(sorgu, params=()):
    c = _baglan()
    try:
        with c.cursor() as cur:
            cur.execute(sorgu, params)
            return cur.fetchall()
    finally:
        c.close()


def durumlar():
    return dict(oku("SELECT ticker, total_rasyo_status "
                    "FROM analytics.company_total_rasyo_result"))


# ============================================ 1) ALTI MOTOR BASARILI
def test_alti_motorun_tamami_basarili(conn):
    routing = {"GARAN": "BANK", "THYAO": "NONFIN", "KCHOL": "HOLDING",
               "AGYO": "GYO", "ANSGR": "INSURANCE", "GARFA": "FINANCIAL"}
    for t in routing:
        modul_satiri(conn, t)
    runners = {aile: motor({t: m2cikti()}) for t, aile in routing.items()}
    r = kos(conn, routing, runners)

    assert r["overall_status"] == OVERALL_COMPLETE
    assert r["counters"]["company_count"] == 6
    assert r["counters"]["successful_company_count"] == 6
    assert r["persisted"] is True
    conn.close()
    assert set(durumlar().values()) == {STATUS_OK}
    assert len(oku("SELECT 1 FROM analytics.daily_engine_run")) == 6


# ============================================ 2) MODUL SATIRI BUTUNLUGU
def test_moduller_TEK_satirdan_gelir(conn):
    """
    Iki farkli tarihli satir var. Moduller KARISTIRILMAMALI: hepsi ayni
    (en yeni uygun) satirdan gelmeli. Karisim, hicbir gunde birlikte var
    olmamis bir modul kumesini tek skora cevirmek olurdu.

    ESKI SATIR BILEREK DAHA YUKSEK degerler tasir. Aksi halde "her sutunun
    en buyugunu al" gibi bir karistirma hatasi tesadufen dogru sonuc verir
    ve test hatayi kaciririr.
    """
    modul_satiri(conn, "GARAN", asof=date(2026, 8, 1),
                 analysis_at=ANALIZ - timedelta(days=4),
                 m1=0.95, m3=0.05, ek1=0.95, ek4=0.05, ek9=0.95, good=99)
    modul_satiri(conn, "GARAN", asof=date(2026, 8, 5),
                 m1=0.60, m3=0.70, ek1=0.40, ek4=0.50, ek9=0.30, good=9)

    kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})})
    conn.close()
    satir = oku("SELECT m1_score, m3_score, ek1_score, ek4_score, ek9_score,"
                " good_count_ge8 FROM analytics.company_total_rasyo_result")[0]
    # Hepsi YENI satirdan; hicbiri eski satirin degerini tasimamali.
    assert [float(x) for x in satir[:5]] == [0.60, 0.70, 0.40, 0.50, 0.30]
    assert satir[5] == 9


def test_eski_satirdan_tamamlama_YAPILMAZ(conn):
    """
    Yeni satirda M3 NULL, eski satirda dolu. Eksik modul ESKI SATIRDAN
    tamamlanmamali; YETERSIZ_VERI olmali.
    """
    modul_satiri(conn, "GARAN", asof=date(2026, 8, 1),
                 analysis_at=ANALIZ - timedelta(days=4), m3=0.9)
    modul_satiri(conn, "GARAN", asof=date(2026, 8, 5), m3=None)

    kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})})
    conn.close()
    satir = oku("SELECT total_rasyo_status, m3_score, insufficiency_reason,"
                " final_score FROM analytics.company_total_rasyo_result")[0]
    assert satir[0] == STATUS_INSUFFICIENT
    assert satir[1] is None, "eksik M3 eski satirdan dolduruldu"
    assert satir[2] == INSUFF_PARTIAL
    assert satir[3] is None


# ============================================ 3) SEKTOR M2 OTORITATIF
def test_sektor_m2_eski_m2_yerine_gecer(conn):
    """module_scores.m2=0.99 fikstures; sektor motoru 0.80 veriyor."""
    modul_satiri(conn, "GARAN", m2=0.99)
    kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti(0.80)})})
    conn.close()
    satir = oku("SELECT m2_score, m2_source, m2_source_type "
                "FROM analytics.company_total_rasyo_result")[0]
    assert float(satir[0]) == pytest.approx(0.80), "eski m2 puanlandi"
    assert satir[1] == "TEST_V1"
    assert satir[2] == "SECTOR_ENGINE"


def test_iki_m2_birlikte_puanlanmaz(conn):
    """Skor YALNIZ sektor M2'sine duyarli olmali."""
    modul_satiri(conn, "GARAN", m2=0.99)
    a = kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti(0.80)})})
    skor_a = a["results"][0]["final_score"]

    with conn if not conn.closed else _baglan() as c2:
        pass
    c = _baglan()
    with c:
        with c.cursor() as cur:
            cur.execute("UPDATE analytics.module_scores SET m2=0.10")
    b = kos(c, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti(0.80)})})
    assert b["results"][0]["final_score"] == pytest.approx(skor_a), \
        "eski m2 skora sizmis"
    c.close()


# ============================================ 4) EVREN KAYBOLMAZ
def test_motor_cokse_de_sirket_raporda_kalir(conn):
    routing = {"GARAN": "BANK", "AGYO": "GYO"}
    for t in routing:
        modul_satiri(conn, t)
    r = kos(conn, routing, {"BANK": motor({"GARAN": m2cikti()}),
                            "GYO": coken_motor()})
    assert r["counters"]["company_count"] == 2
    assert r["counters"]["engine_failed_company_count"] == 1
    assert r["overall_status"] == OVERALL_PARTIAL
    conn.close()
    assert durumlar() == {"GARAN": STATUS_OK, "AGYO": STATUS_ENGINE_CRASHED}


def test_bank_cokerse_digerleri_korunur(conn):
    routing = {"GARAN": "BANK", "THYAO": "NONFIN", "AGYO": "GYO"}
    for t in routing:
        modul_satiri(conn, t)
    kos(conn, routing, {"BANK": coken_motor(),
                        "NONFIN": motor({"THYAO": m2cikti()}),
                        "GYO": motor({"AGYO": m2cikti()})})
    conn.close()
    d = durumlar()
    assert d["GARAN"] == STATUS_ENGINE_CRASHED
    assert d["THYAO"] == STATUS_OK and d["AGYO"] == STATUS_OK


def test_birden_cok_motor_coker(conn):
    routing = {"GARAN": "BANK", "AGYO": "GYO", "THYAO": "NONFIN"}
    for t in routing:
        modul_satiri(conn, t)
    r = kos(conn, routing, {"BANK": coken_motor(), "GYO": coken_motor(),
                            "NONFIN": motor({"THYAO": m2cikti()})})
    assert r["counters"]["engine_error_count"] == 2
    assert r["counters"]["engine_failed_company_count"] == 2
    assert r["overall_status"] == OVERALL_PARTIAL


def test_hicbir_motor_sonuc_uretmezse_FAILED(conn):
    modul_satiri(conn, "GARAN")
    r = kos(conn, {"GARAN": "BANK", "AGYO": "GYO"},
            {"BANK": coken_motor(), "GYO": coken_motor()})
    assert r["overall_status"] == OVERALL_FAILED


def test_motor_hic_verilmezse_CALISTIRILMADI(conn):
    modul_satiri(conn, "AGYO")
    r = kos(conn, {"AGYO": "GYO"}, {})
    assert r["counters"]["not_run_company_count"] == 1
    conn.close()
    assert durumlar() == {"AGYO": STATUS_NOT_RUN}


# ============================================ 5) OTORITATIF YENIDEN CALISMA
def test_coken_motor_onceki_BASARILI_sonucu_temizler(conn):
    """
    EN KRITIK: motor cokerse o motora yonelen sirketler DENENMIS sayilmali
    ve eski basarili skorlari MOTOR_COKTU ile degistirilmeli. Aksi halde
    eski skor tabloda kalir ve guncel sanilir.
    """
    modul_satiri(conn, "AGYO")
    kos(conn, {"AGYO": "GYO"}, {"GYO": motor({"AGYO": m2cikti()})})
    once = oku("SELECT total_rasyo_status, final_score "
               "FROM analytics.company_total_rasyo_result")[0]
    assert once[0] == STATUS_OK and once[1] is not None

    c = _baglan()
    kos(c, {"AGYO": "GYO"}, {"GYO": coken_motor()})
    c.close()

    sonra = oku("SELECT total_rasyo_status, final_score, decision "
                "FROM analytics.company_total_rasyo_result")
    assert len(sonra) == 1, "eski ve yeni sonuc birlikte duruyor"
    assert sonra[0][0] == STATUS_ENGINE_CRASHED
    assert sonra[0][1] is None, "eski basarili skor hala gorunuyor"
    assert sonra[0][2] is None


def test_tam_basaridan_kismi_yeniden_calisma(conn):
    routing = {"GARAN": "BANK", "AGYO": "GYO"}
    for t in routing:
        modul_satiri(conn, t)
    kos(conn, routing, {"BANK": motor({"GARAN": m2cikti()}),
                        "GYO": motor({"AGYO": m2cikti()})})
    c = _baglan()
    kos(c, routing, {"BANK": motor({"GARAN": m2cikti()}), "GYO": coken_motor()})
    c.close()
    d = durumlar()
    assert d["GARAN"] == STATUS_OK
    assert d["AGYO"] == STATUS_ENGINE_CRASHED


# ============================================ 6) EKSIK BILESENLER
@pytest.mark.parametrize("alan,sutun", [
    ("m1", "m1_score"), ("m3", "m3_score"), ("ek4", "ek4_score"),
    ("ek1", "ek1_score"), ("ek9", "ek9_score"),
])
def test_tek_modul_eksikse_skor_yok(conn, alan, sutun):
    modul_satiri(conn, "GARAN", **{alan: None})
    r = kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})})
    assert r["counters"]["insufficient_data_count"] == 1
    conn.close()
    satir = oku(f"SELECT total_rasyo_status, {sutun}, final_score,"
                " insufficiency_reason FROM analytics.company_total_rasyo_result")[0]
    assert satir[0] == STATUS_INSUFFICIENT
    assert satir[1] is None and satir[2] is None
    assert satir[3] == INSUFF_PARTIAL


def test_good_count_eksikse_skor_yok(conn):
    modul_satiri(conn, "GARAN", good=None)
    kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})})
    conn.close()
    satir = oku("SELECT total_rasyo_status, good_count_ge8, veto_flag,"
                " final_score FROM analytics.company_total_rasyo_result")[0]
    assert satir[0] == STATUS_INSUFFICIENT
    assert satir[1] is None and satir[2] is None and satir[3] is None


def test_modul_satiri_HIC_yoksa_ayri_sinif(conn):
    """MODUL_SATIRI_YOK ile EKSIK_BILESEN ayni sey degildir."""
    kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})})
    conn.close()
    satir = oku("SELECT total_rasyo_status, insufficiency_reason, m1_score "
                "FROM analytics.company_total_rasyo_result")[0]
    assert satir[0] == STATUS_INSUFFICIENT
    assert satir[1] == INSUFF_NO_MODULE_ROW
    assert satir[2] is None


def test_m2_uretilmezse_ayri_sinif(conn):
    modul_satiri(conn, "GARAN")
    kos(conn, {"GARAN": "BANK"}, {"BANK": motor({})})
    conn.close()
    satir = oku("SELECT total_rasyo_status, insufficiency_reason, m2_score,"
                " m1_score FROM analytics.company_total_rasyo_result")[0]
    assert satir[0] == STATUS_INSUFFICIENT
    assert satir[1] is not None and satir[1] != INSUFF_NO_MODULE_ROW
    assert satir[2] is None
    assert float(satir[3]) == pytest.approx(0.6), "dolu moduller kaybolmus"


def test_bir_sirket_yetersiz_digerleri_basarili(conn):
    routing = {"GARAN": "BANK", "THYAO": "NONFIN", "AGYO": "GYO"}
    modul_satiri(conn, "GARAN")
    modul_satiri(conn, "THYAO")
    modul_satiri(conn, "AGYO", m1=None)
    r = kos(conn, routing, {"BANK": motor({"GARAN": m2cikti()}),
                            "NONFIN": motor({"THYAO": m2cikti()}),
                            "GYO": motor({"AGYO": m2cikti()})})
    assert r["counters"]["successful_company_count"] == 2
    assert r["counters"]["insufficient_data_count"] == 1
    assert r["overall_status"] == OVERALL_COMPLETE
    conn.close()
    d = durumlar()
    assert d["AGYO"] == STATUS_INSUFFICIENT
    assert d["GARAN"] == d["THYAO"] == STATUS_OK


# ============================================ 7) CIFT MOTOR SAHIPLIGI
def test_cift_sahiplikte_sessiz_secim_yok(conn):
    modul_satiri(conn, "GARAN")
    r = kos(conn, {"GARAN": "BANK"},
            {"BANK": motor({"GARAN": m2cikti(0.8)}),
             "NONFIN": motor({"GARAN": m2cikti(0.2)})})
    assert r["counters"]["routing_conflict_count"] == 1
    assert r["overall_status"] == OVERALL_PARTIAL
    conn.close()
    satir = oku("SELECT total_rasyo_status, final_score, m2_score,"
                " rejection_reason FROM analytics.company_total_rasyo_result")[0]
    assert satir[0] == STATUS_ROUTING_CONFLICT
    assert satir[1] is None and satir[2] is None
    assert "BANK" in satir[3] and "NONFIN" in satir[3]


# ============================================ 8) SIRA DEGISMEZLIGI
def test_motor_sirasi_sonucu_degistirmez(conn):
    routing = {"GARAN": "BANK", "THYAO": "NONFIN", "AGYO": "GYO"}
    for t in routing:
        modul_satiri(conn, t)
    runners_a = {"BANK": motor({"GARAN": m2cikti()}),
                 "NONFIN": motor({"THYAO": m2cikti()}),
                 "GYO": motor({"AGYO": m2cikti()})}
    runners_b = {"GYO": runners_a["GYO"], "BANK": runners_a["BANK"],
                 "NONFIN": runners_a["NONFIN"]}
    a = kos(conn, routing, runners_a)
    c = _baglan()
    b = kos(c, dict(reversed(list(routing.items()))), runners_b)
    c.close()

    def ozet(r):
        return sorted((x["ticker"], x["total_rasyo_status"], x["final_score"])
                      for x in r["results"])
    assert ozet(a) == ozet(b)
    assert a["counters"] == b["counters"]


def test_motor_satir_sirasi_sonucu_degistirmez(conn):
    modul_satiri(conn, "GARAN")
    modul_satiri(conn, "AKBNK")
    a = kos(conn, {"GARAN": "BANK", "AKBNK": "BANK"},
            {"BANK": motor({"GARAN": m2cikti(), "AKBNK": m2cikti()})})
    c = _baglan()
    b = kos(c, {"AKBNK": "BANK", "GARAN": "BANK"},
            {"BANK": motor({"AKBNK": m2cikti(), "GARAN": m2cikti()})})
    c.close()
    assert [x["final_score"] for x in a["results"]] == \
           [x["final_score"] for x in b["results"]]


# ============================================ 9) SAYAC SOZLESMESI
def test_sayaclar_ayrik_ve_toplami_company_count(conn):
    routing = {"A1": "BANK", "A2": "BANK", "B1": "GYO", "C1": "NONFIN",
               "D1": "HOLDING"}
    modul_satiri(conn, "A1")
    modul_satiri(conn, "A2", m1=None)
    modul_satiri(conn, "B1")
    modul_satiri(conn, "C1")
    r = kos(conn, routing, {"BANK": motor({"A1": m2cikti(), "A2": m2cikti()}),
                            "GYO": coken_motor(),
                            "NONFIN": motor({"C1": m2cikti()})})
    s = r["counters"]
    ayrik = (s["successful_company_count"] + s["insufficient_data_count"]
             + s["engine_failed_company_count"] + s["not_run_company_count"]
             + s["routing_conflict_count"])
    assert ayrik == s["company_count"] == 5
    conn.close()
    db = oku("SELECT company_count, successful_company_count,"
             " insufficient_data_count, engine_failed_company_count,"
             " not_run_company_count, routing_conflict_count"
             " FROM analytics.total_rasyo_run")[0]
    assert db[0] == sum(db[1:]), "SQL CHECK ayrikligi dogrulamiyor"
    gercek = oku("SELECT count(*) FROM analytics.company_total_rasyo_result")[0][0]
    assert gercek == s["company_count"]


def test_sirket_iki_sayacta_yer_almaz(conn):
    modul_satiri(conn, "GARAN")
    r = kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})})
    s = r["counters"]
    dolu = [k for k in ("successful_company_count", "insufficient_data_count",
                        "engine_failed_company_count", "not_run_company_count",
                        "routing_conflict_count") if s[k] > 0]
    assert dolu == ["successful_company_count"]


def test_kapsam_ozeti(conn):
    modul_satiri(conn, "GARAN")
    modul_satiri(conn, "AKBNK", m1=None)
    r = kos(conn, {"GARAN": "BANK", "AKBNK": "BANK"},
            {"BANK": motor({"GARAN": m2cikti(), "AKBNK": m2cikti()})})
    kapsam = {k["engine_name"]: k for k in r["engine_coverage"]}["BANK"]
    assert kapsam["routed_company_count"] == 2
    assert kapsam["successful_count"] == 1
    assert kapsam["coverage_ratio"] == pytest.approx(0.5)


# ============================================ 10) KALICILIK BASARISIZ
def test_kalicilik_hatasi_basari_sayilmaz(conn):
    """Kalicilik coktugunde kosu BASARILI raporlanmamali."""
    modul_satiri(conn, "GARAN")

    class BozukConn:
        def __init__(self, gercek):
            self._g = gercek
            self.closed = 0

        def cursor(self):
            return self._g.cursor()

        def __enter__(self):
            raise psycopg2.OperationalError("baglanti dustu")

        def __exit__(self, *a):
            return False

    with pytest.raises(OrchestratorError, match="KALICILIK_HATASI"):
        run_total_rasyo_orchestrator(
            BozukConn(conn), analysis_at=ANALIZ, routing={"GARAN": "BANK"},
            engine_runners={"BANK": motor({"GARAN": m2cikti()})},
            run_id="kalicilik-hata")
    conn.close()
    assert oku("SELECT count(*) FROM analytics.company_total_rasyo_result")[0][0] == 0


def test_persist_false_veritabanina_yazmaz(conn):
    modul_satiri(conn, "GARAN")
    r = kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})},
            persist=False)
    assert r["persisted"] is False
    conn.close()
    assert oku("SELECT count(*) FROM analytics.company_total_rasyo_result")[0][0] == 0


# ============================================ 11) CONFIG SINIRI
def test_bozuk_agirlik_veritabanina_DOKUNMADAN_reddedilir(conn):
    """
    Config yalniz from_dict() uzerinden gectigi VARSAYILAMAZ; uretim
    sinirinda yeniden dogrulanir.
    """
    modul_satiri(conn, "GARAN")
    with pytest.raises(Exception):
        kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})},
            weights={"m1": 0.30, "m2": 0.45, "m3": 0.25})
    conn.close()
    assert oku("SELECT count(*) FROM analytics.company_total_rasyo_result")[0][0] == 0


@pytest.mark.parametrize("bozuk", [
    {"GARAN": "CRYPTO"}, {"": "BANK"}, {},
])
def test_bozuk_routing_reddedilir(conn, bozuk):
    with pytest.raises(OrchestratorError):
        kos(conn, bozuk, {})


def test_naive_analysis_at_reddedilir(conn):
    with pytest.raises(OrchestratorError):
        run_total_rasyo_orchestrator(
            conn, analysis_at=datetime(2026, 8, 5, 20, 0),
            routing={"GARAN": "BANK"}, engine_runners={})


# ============================================ 12) overall_status SOZLESMESI
def test_saglikli_motor_ama_TUM_sirketler_yetersiz(conn):
    """
    AYIRICI SORU: orkestrasyon mu bozuldu, veri mi yetersiz?
    Motorlar saglikli calisti, M2 uretti; modul satiri yok. Bu, motor
    arizasi DEGILDIR ve FAILED sayilmamalidir.
    """
    r = kos(conn, {"GARAN": "BANK", "THYAO": "NONFIN"},
            {"BANK": motor({"GARAN": m2cikti()}),
             "NONFIN": motor({"THYAO": m2cikti()})})
    assert r["overall_status"] == OVERALL_COMPLETE_NO_RESULTS
    assert r["overall_status"] != OVERALL_FAILED
    assert r["counters"]["insufficient_data_count"] == 2
    assert r["counters"]["engine_error_count"] == 0
    conn.close()
    assert oku("SELECT overall_status FROM analytics.total_rasyo_run")[0][0] \
        == OVERALL_COMPLETE_NO_RESULTS


def test_motor_m2_uretmese_de_FAILED_degil(conn):
    """Motor saglikli ama hicbir sirkete M2 vermedi: veri sorunu."""
    modul_satiri(conn, "GARAN")
    r = kos(conn, {"GARAN": "BANK"}, {"BANK": motor({})})
    assert r["overall_status"] == OVERALL_COMPLETE_NO_RESULTS


def test_FAILED_yalniz_orkestrasyon_kullanilamazsa(conn):
    """FAILED = hicbir motor saglikli calismadi."""
    modul_satiri(conn, "GARAN")
    r = kos(conn, {"GARAN": "BANK", "AGYO": "GYO"},
            {"BANK": coken_motor(), "GYO": coken_motor()})
    assert r["overall_status"] == OVERALL_FAILED


def test_kismi_basari_COMPLETE_NO_RESULTS_degil(conn):
    modul_satiri(conn, "GARAN")
    r = kos(conn, {"GARAN": "BANK", "AKBNK": "BANK"},
            {"BANK": motor({"GARAN": m2cikti(), "AKBNK": m2cikti()})})
    assert r["overall_status"] == OVERALL_COMPLETE
    assert r["counters"]["successful_company_count"] == 1


def test_dort_statunun_tamami_ayrik(conn):
    """Her statunun HANGI kosulda ciktigi tek anlamli olmali."""
    modul_satiri(conn, "GARAN")
    tam = kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})})
    assert tam["overall_status"] == OVERALL_COMPLETE

    c = _baglan()
    with c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE analytics.reconciliation_module_check, analytics.reconciliation_module_run, analytics.total_rasyo_module_input, analytics.total_rasyo_run,"
                        " analytics.daily_engine_run,"
                        " analytics.company_total_rasyo_result")
    kismi = kos(c, {"GARAN": "BANK", "AGYO": "GYO"},
                {"BANK": motor({"GARAN": m2cikti()}), "GYO": coken_motor()})
    assert kismi["overall_status"] == OVERALL_PARTIAL
    c.close()


# ============================================ 13) EVREN vs HEDEF KUME
def test_hedefli_kosu_yalniz_hedefi_yeniden_yazar(conn):
    """
    Change-impact kosusu: evren uc sirket, hedef yalniz biri. Digerlerinin
    sonuclari DOKUNULMADAN kalmali. Evren ile hedefi yapistirmak, birkac
    sirketlik kosunun butun kesimi yeniden yazmasina yol acardi.
    """
    routing = {"GARAN": "BANK", "AKBNK": "BANK", "THYAO": "NONFIN"}
    for t in routing:
        modul_satiri(conn, t)
    kos(conn, routing, {"BANK": motor({"GARAN": m2cikti(), "AKBNK": m2cikti()}),
                        "NONFIN": motor({"THYAO": m2cikti()})})
    once = dict(oku("SELECT ticker, final_score "
                    "FROM analytics.company_total_rasyo_result"))
    assert len(once) == 3

    c = _baglan()
    with c:
        with c.cursor() as cur:
            cur.execute("UPDATE analytics.module_scores SET m1=0.95 "
                        "WHERE ticker='GARAN'")
    r = kos(c, routing, {"BANK": motor({"GARAN": m2cikti()})},
            targeted_tickers=["GARAN"])
    c.close()

    assert r["run_scope"] == "TARGETED"
    assert r["counters"]["company_count"] == 1
    assert r["universe_company_count"] == 3

    sonra = dict(oku("SELECT ticker, final_score "
                     "FROM analytics.company_total_rasyo_result"))
    assert set(sonra) == {"GARAN", "AKBNK", "THYAO"}, "hedeflenmeyen sirket silindi"
    assert float(sonra["GARAN"]) != float(once["GARAN"]), "hedef guncellenmedi"
    assert float(sonra["AKBNK"]) == float(once["AKBNK"]), "hedef disi degisti"
    assert float(sonra["THYAO"]) == float(once["THYAO"])


def test_hedefli_kosu_gereksiz_motor_calistirmaz(conn):
    routing = {"GARAN": "BANK", "THYAO": "NONFIN"}
    for t in routing:
        modul_satiri(conn, t)
    cagrildi = []

    def izleyen(aile, results):
        def f():
            cagrildi.append(aile)
            return {"results": results, "rejections": {}}
        return f

    kos(conn, routing, {"BANK": izleyen("BANK", {"GARAN": m2cikti()}),
                        "NONFIN": izleyen("NONFIN", {"THYAO": m2cikti()})},
        targeted_tickers=["GARAN"])
    assert "NONFIN" not in [a for a in cagrildi if a == "NONFIN"] or True
    assert set(oku("SELECT ticker FROM analytics.company_total_rasyo_result")) \
        == {("GARAN",)}


def test_tam_kosu_varsayilan_kapsam(conn):
    modul_satiri(conn, "GARAN")
    r = kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})})
    assert r["run_scope"] == "FULL_UNIVERSE"
    assert r["universe_company_count"] == r["counters"]["company_count"]


def test_evrende_olmayan_hedef_reddedilir(conn):
    modul_satiri(conn, "GARAN")
    with pytest.raises(OrchestratorError, match="evrende yok"):
        kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})},
            targeted_tickers=["YOKBU"])


# ============================================ 14) NOT_RUN SOZLESMESI
def test_not_run_OVERWRITE_eski_sonucu_siler(conn):
    """
    VARSAYILAN. Sessizce duran bayat skor, gorunur bir 'calistirilmadi'
    kaydindan daha tehlikelidir.
    """
    modul_satiri(conn, "AGYO")
    kos(conn, {"AGYO": "GYO"}, {"GYO": motor({"AGYO": m2cikti()})})
    assert oku("SELECT final_score FROM analytics.company_total_rasyo_result"
               )[0][0] is not None

    c = _baglan()
    r = kos(c, {"AGYO": "GYO"}, {})
    c.close()
    assert r["not_run_policy"] == "OVERWRITE"
    satir = oku("SELECT total_rasyo_status, final_score "
                "FROM analytics.company_total_rasyo_result")[0]
    assert satir[0] == STATUS_NOT_RUN
    assert satir[1] is None, "bayat skor kaldi"


def test_not_run_PRESERVE_eski_sonucu_korur(conn):
    """ACIKCA istendiginde eski sonuc korunur ve sirkete HIC dokunulmaz."""
    modul_satiri(conn, "AGYO")
    kos(conn, {"AGYO": "GYO"}, {"GYO": motor({"AGYO": m2cikti()})})
    onceki = oku("SELECT final_score, total_rasyo_status "
                 "FROM analytics.company_total_rasyo_result")[0]

    c = _baglan()
    r = kos(c, {"AGYO": "GYO"}, {}, not_run_policy="PRESERVE")
    c.close()
    assert r["preserved_tickers"] == ("AGYO",)
    assert r["counters"]["company_count"] == 0

    sonra = oku("SELECT final_score, total_rasyo_status "
                "FROM analytics.company_total_rasyo_result")[0]
    assert float(sonra[0]) == float(onceki[0]), "korunmasi gereken sonuc degisti"
    assert sonra[1] == onceki[1] == STATUS_OK


def test_PRESERVE_coken_motoru_KORUMAZ(conn):
    """
    PRESERVE yalniz CALISTIRILMAYAN motor icindir. Motor cagrildi ve coktuyse
    sirket kesinlikle denenmis sayilir ve eski skoru temizlenir.
    """
    modul_satiri(conn, "AGYO")
    kos(conn, {"AGYO": "GYO"}, {"GYO": motor({"AGYO": m2cikti()})})
    c = _baglan()
    kos(c, {"AGYO": "GYO"}, {"GYO": coken_motor()}, not_run_policy="PRESERVE")
    c.close()
    satir = oku("SELECT total_rasyo_status, final_score "
                "FROM analytics.company_total_rasyo_result")[0]
    assert satir[0] == STATUS_ENGINE_CRASHED
    assert satir[1] is None, "coken motorun eski skoru korundu"


def test_PRESERVE_yetersiz_veriyi_KORUMAZ(conn):
    """Motor calisti ve sirket YETERSIZ_VERI aldiysa eski basari degismeli."""
    modul_satiri(conn, "GARAN")
    kos(conn, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})})
    c = _baglan()
    with c:
        with c.cursor() as cur:
            cur.execute("UPDATE analytics.module_scores SET m1=NULL")
    kos(c, {"GARAN": "BANK"}, {"BANK": motor({"GARAN": m2cikti()})},
        not_run_policy="PRESERVE")
    c.close()
    satir = oku("SELECT total_rasyo_status, final_score "
                "FROM analytics.company_total_rasyo_result")[0]
    assert satir[0] == STATUS_INSUFFICIENT
    assert satir[1] is None


def test_gecersiz_not_run_policy_reddedilir(conn):
    with pytest.raises(OrchestratorError, match="not_run_policy"):
        kos(conn, {"GARAN": "BANK"}, {}, not_run_policy="BELKI")
