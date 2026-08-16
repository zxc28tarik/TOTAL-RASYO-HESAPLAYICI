"""
Kalicilik sozlesmesi — GERCEK PostgreSQL testleri.

NEDEN SAHTE BAGLANTI YETMEZ: V18'de kalicilik fonksiyonunda `with conn:`
eksikti. INSERT'ler calisti, sayac 9 dedi, hicbir istisna atilmadi ve
baglanti kapaninca tabloda 0 satir kaldi. Sahte baglanti kullanan butun
birim testleri geciyordu. Bu dosya yazdiktan sonra BAGLANTIYI KAPATIP
YENI BAGLANTI ile geri okur.

Veritabani yoksa testler atlanir; sessizce "gecti" DEMEZ.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.total_rasyo_persistence import (
    COMPANY_RESULT_COLUMNS,
    ENGINE_RUN_COLUMNS,
    RUN_COLUMNS,
    PersistenceError,
    count_persisted,
    persist_total_rasyo_report,
    run_payload_fingerprint,
)

TZ = timezone(timedelta(hours=3))
ANALIZ = datetime(2026, 8, 5, 20, 0, tzinfo=TZ)


def _dsn() -> str | None:
    return os.environ.get("TOTAL_RASYO_TEST_DSN") or os.environ.get("PGDATABASE")


def _baglan():
    dsn = _dsn()
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
                pytest.skip("sql/027-028 uygulanmamis")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def temizle():
    c = _baglan()
    with c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE analytics.reconciliation_module_check, analytics.reconciliation_module_run, analytics.total_rasyo_module_input, analytics.total_rasyo_run, "
                        "analytics.daily_engine_run, "
                        "analytics.company_total_rasyo_result")
    c.close()


# --------------------------------------------------------------- fikstur
def modul(score=0.6, missing=False):
    return {"score": None if missing else score,
            "source_at": None if missing else ANALIZ - timedelta(hours=1),
            "source_type": None if missing else "ANALYTICS_MODULE_SCORES",
            "missing": missing, "reason": "X_KAYNAGI_YOK" if missing else None}


def basarili(ticker="GARAN", engine="BANK", score=0.62):
    return {
        "ticker": ticker, "routed_engine": engine, "engine_status": "OK",
        "engine_reason": None, "m2_score": 0.8, "m2_source": "TEST_V1",
        "m2_source_at": ANALIZ - timedelta(hours=1), "m2_source_type": "SECTOR_ENGINE",
        "m2_missing": False, "valuation_confidence": 0.7,
        "modules": {"M2": modul(0.8), "M1": modul(0.6), "M3": modul(0.7),
                    "Ek4": modul(0.5), "Ek1": modul(0.4), "Ek9": modul(0.3)},
        "good_count_ge8": 9, "good_count_missing": False,
        "base_score": score, "final_score": score, "total_rasyo_100": score * 100,
        "veto_flag": False, "decision": "IZLE",
        "weights_profile": "TOTAL_RASYO_SCORE_V1",
        "total_rasyo_status": "OK", "rejection_reason": None,
        "insufficiency_reason": None,
        "missing_modules": (), "data_confidence": 0.7, "diagnostics": {},
    }


def yetersiz(ticker="AKBNK", engine="NONFIN", neden="EKSIK_BILESEN: M1"):
    return {
        "ticker": ticker, "routed_engine": engine, "engine_status": "OK",
        "engine_reason": None, "m2_score": 0.8, "m2_source": "TEST_V1",
        "m2_source_at": None, "m2_source_type": "SECTOR_ENGINE",
        "m2_missing": False, "valuation_confidence": 0.7,
        "modules": {"M2": modul(0.8), "M1": modul(missing=True), "M3": modul(0.7),
                    "Ek4": modul(0.5), "Ek1": modul(0.4), "Ek9": modul(0.3)},
        "good_count_ge8": 9, "good_count_missing": False,
        "base_score": None, "final_score": None, "total_rasyo_100": None,
        "veto_flag": None, "decision": None, "weights_profile": None,
        "total_rasyo_status": "YETERSIZ_VERI", "rejection_reason": neden,
        "insufficiency_reason": "EKSIK_BILESEN", "missing_modules": ("M1",), "data_confidence": 0.6, "diagnostics": {},
    }


def motor(engine="BANK", status="OK", routed=1, results=1, hata=None):
    return {"engine": engine, "status": status, "result_count": results,
            "rejection_count": 0, "routed_company_count": routed,
            "error_type": hata, "error_message": None if hata is None else "patladi",
            "duration_ms": 12, "config_sha256": None, "diagnostics": {}}


def rapor(results, engines, *, run_id=None, analysis_at=ANALIZ, status="OK"):
    results = list(results)
    sayaclar = {
        "engine_error_count": sum(1 for e in engines if e["status"] == "FAILED"),
        "company_count": len(results),
        "successful_company_count": sum(1 for r in results if r["total_rasyo_status"] == "OK"),
        "insufficient_data_count": sum(1 for r in results if r["total_rasyo_status"] == "YETERSIZ_VERI"),
        "engine_failed_company_count": sum(1 for r in results if r["total_rasyo_status"] == "MOTOR_COKTU"),
        "not_run_company_count": sum(1 for r in results if r["total_rasyo_status"] == "CALISTIRILMADI"),
        "routing_conflict_count": sum(1 for r in results if r["total_rasyo_status"] == "YONLENDIRME_CAKISMASI"),
        "missing_m1_count": sum(1 for r in results if r["modules"]["M1"]["missing"]),
        "missing_m2_count": sum(1 for r in results if r["m2_missing"]),
        "missing_m3_count": sum(1 for r in results if r["modules"]["M3"]["missing"]),
        "missing_ek4_count": sum(1 for r in results if r["modules"]["Ek4"]["missing"]),
        "missing_ek1_count": sum(1 for r in results if r["modules"]["Ek1"]["missing"]),
        "missing_ek9_count": sum(1 for r in results if r["modules"]["Ek9"]["missing"]),
        "missing_good_count": sum(1 for r in results if r["good_count_missing"]),
    }
    return {
        "run_id": run_id or f"test-{uuid.uuid4().hex[:12]}",
        "analysis_at": analysis_at, "started_at": analysis_at,
        "finished_at": analysis_at + timedelta(seconds=5),
        "overall_status": status, "weights_profile": "TOTAL_RASYO_SCORE_V1",
        "engine_runs": list(engines), "results": results,
        "counters": sayaclar, "diagnostics": {},
    }


def yeni_baglantiyla_oku(sorgu, params=()):
    """HER ZAMAN YENI baglanti: commit edilmemis veri gorunmez."""
    c = _baglan()
    try:
        with c.cursor() as cur:
            cur.execute(sorgu, params)
            return cur.fetchall()
    finally:
        c.close()


# ============================================ 1) COMMIT GERCEKTEN OLUYOR
def test_yazimdan_sonra_YENI_BAGLANTI_ile_okunabiliyor(conn):
    """
    V18 HATASININ DOGRUDAN TESTI: `with conn:` olmadan sayac dogru cikar
    ama baglanti kapaninca satirlar kaybolur.
    """
    r = rapor([basarili()], [motor()])
    sayim = persist_total_rasyo_report(conn, r)
    assert sayim == {"run_rows": 1, "engine_run_rows": 1, "company_result_rows": 1}
    conn.close()  # baglantiyi KAPAT

    satirlar = yeni_baglantiyla_oku(
        "SELECT ticker, final_score, decision FROM analytics.company_total_rasyo_result "
        "WHERE run_id=%s", (r["run_id"],))
    assert len(satirlar) == 1, "commit edilmemis: satir kayboldu"
    assert satirlar[0][0] == "GARAN"
    assert float(satirlar[0][1]) == pytest.approx(0.62)
    assert satirlar[0][2] == "IZLE"


def test_kaynak_kodunda_with_conn_var():
    import re
    from pathlib import Path
    kaynak = Path("src/analytics/total_rasyo_persistence.py").read_text(encoding="utf-8")
    govde = kaynak[kaynak.index("def persist_total_rasyo_report"):]
    assert re.search(r"^\s{4}with conn:\s*$", govde, re.M)


# ============================================ 2) ROLLBACK
def test_ortada_sql_hatasi_yarim_kayit_birakmaz(conn):
    """
    Ikinci INSERT'te kontrollu hata: TUM transaction geri alinmali.
    Run satiri, motor satiri ve sirket satiri -- ucu de kalmamali.
    """
    r = rapor([basarili(), basarili("AKBNK")], [motor()])
    # Kontrollu bozma: ikinci sirket satirinda gecersiz durum degeri.
    r["results"][1]["total_rasyo_status"] = "BOYLE_BIR_DURUM_YOK"

    with pytest.raises(psycopg2.Error):
        persist_total_rasyo_report(conn, r)
    conn.close()

    for tablo in ("total_rasyo_run", "daily_engine_run", "company_total_rasyo_result"):
        kalan = yeni_baglantiyla_oku(
            f"SELECT count(*) FROM analytics.{tablo} WHERE run_id=%s", (r["run_id"],))
        assert kalan[0][0] == 0, f"{tablo} yarim kayit birakti"


def test_motor_satiri_yazildiktan_sonra_hata_da_geri_alinir(conn):
    """Motor satiri sirket satirlarindan ONCE yazilir; o da rollback olmali."""
    r = rapor([basarili()], [motor(), motor("CRYPTO")])  # gecersiz motor adi
    with pytest.raises(psycopg2.Error):
        persist_total_rasyo_report(conn, r)
    conn.close()
    kalan = yeni_baglantiyla_oku(
        "SELECT count(*) FROM analytics.daily_engine_run WHERE run_id=%s", (r["run_id"],))
    assert kalan[0][0] == 0


# ============================================ 3) OTORITATIF YENIDEN CALISMA
def test_basari_sonra_RET_eski_skor_silinir(conn):
    """Onceki basarili sirket yeni kosuda reddedilirse eski skor KALMAMALI."""
    ilk = rapor([basarili("GARAN")], [motor()])
    persist_total_rasyo_report(conn, ilk)

    ikinci = rapor([yetersiz("GARAN", "BANK", "EKSIK_BILESEN: M1")], [motor()])
    persist_total_rasyo_report(conn, ikinci)
    conn.close()

    satirlar = yeni_baglantiyla_oku(
        "SELECT total_rasyo_status, final_score, decision "
        "FROM analytics.company_total_rasyo_result "
        "WHERE analysis_at=%s AND ticker='GARAN'", (ANALIZ,))
    assert len(satirlar) == 1, "eski ve yeni sonuc birlikte duruyor"
    assert satirlar[0][0] == "YETERSIZ_VERI"
    assert satirlar[0][1] is None, "eski basarili skor hala gorunuyor"
    assert satirlar[0][2] is None


def test_ret_sonra_BASARI_eski_ret_temizlenir(conn):
    ilk = rapor([yetersiz("GARAN", "BANK")], [motor()])
    persist_total_rasyo_report(conn, ilk)

    ikinci = rapor([basarili("GARAN")], [motor()])
    persist_total_rasyo_report(conn, ikinci)
    conn.close()

    satirlar = yeni_baglantiyla_oku(
        "SELECT total_rasyo_status, final_score, rejection_reason "
        "FROM analytics.company_total_rasyo_result "
        "WHERE analysis_at=%s AND ticker='GARAN'", (ANALIZ,))
    assert len(satirlar) == 1
    assert satirlar[0][0] == "OK"
    assert float(satirlar[0][1]) == pytest.approx(0.62)
    assert satirlar[0][2] is None, "eski ret nedeni temizlenmedi"


# ============================================ 4) SILME KAPSAMI
def test_denenmeyen_sirket_silinmez(conn):
    """
    Kesim genelinde silmek, o kosuda hic denenmemis sirketleri de yok ederdi.
    Silme YALNIZ denenen ticker kumesini hedeflemeli.
    """
    ilk = rapor([basarili("GARAN"), basarili("AKBNK")], [motor(results=2, routed=2)])
    persist_total_rasyo_report(conn, ilk)

    # Ikinci kosu YALNIZ GARAN'i deniyor.
    ikinci = rapor([basarili("GARAN", score=0.71)], [motor()])
    ikinci["results"][0]["decision"] = "AL"
    persist_total_rasyo_report(conn, ikinci)
    conn.close()

    satirlar = dict(yeni_baglantiyla_oku(
        "SELECT ticker, final_score FROM analytics.company_total_rasyo_result "
        "WHERE analysis_at=%s", (ANALIZ,)))
    assert set(satirlar) == {"GARAN", "AKBNK"}, "denenmeyen sirket silindi"
    assert float(satirlar["GARAN"]) == pytest.approx(0.71)
    assert float(satirlar["AKBNK"]) == pytest.approx(0.62)


def test_baska_kesim_etkilenmez(conn):
    onceki = ANALIZ - timedelta(days=1)
    persist_total_rasyo_report(conn, rapor([basarili()], [motor()], analysis_at=onceki))
    persist_total_rasyo_report(conn, rapor([basarili()], [motor()]))
    conn.close()
    satirlar = yeni_baglantiyla_oku(
        "SELECT analysis_at FROM analytics.company_total_rasyo_result ORDER BY 1")
    assert len(satirlar) == 2, "onceki kesim silindi"


def test_denenmeyen_motor_silinmez(conn):
    persist_total_rasyo_report(conn, rapor(
        [basarili("GARAN", "BANK"), basarili("AGYO", "GYO")],
        [motor("BANK"), motor("GYO")]))
    persist_total_rasyo_report(conn, rapor([basarili("GARAN", "BANK")], [motor("BANK")]))
    conn.close()
    motorlar = {m for (m,) in yeni_baglantiyla_oku(
        "SELECT engine FROM analytics.daily_engine_run WHERE analysis_at=%s", (ANALIZ,))}
    assert motorlar == {"BANK", "GYO"}, "denenmeyen motor kaydi silindi"


# ============================================ 5) RUN KIMLIGI
def test_ayni_run_id_farkli_icerik_reddedilir(conn):
    rid = "sabit-run-1"
    persist_total_rasyo_report(conn, rapor([basarili()], [motor()], run_id=rid))
    farkli = rapor([basarili("THYAO")], [motor()], run_id=rid)
    with pytest.raises(PersistenceError, match="yeniden kullanildi"):
        persist_total_rasyo_report(conn, farkli)


def test_ayni_run_id_ayni_icerik_kabul(conn):
    """Idempotent yeniden yazim engellenmemeli."""
    rid = "sabit-run-2"
    r = rapor([basarili()], [motor()], run_id=rid)
    persist_total_rasyo_report(conn, r)
    persist_total_rasyo_report(conn, r)
    conn.close()
    assert yeni_baglantiyla_oku(
        "SELECT count(*) FROM analytics.company_total_rasyo_result WHERE run_id=%s",
        (rid,))[0][0] == 1


def test_parmak_izi_zaman_damgasindan_bagimsiz():
    a = rapor([basarili()], [motor()], run_id="x")
    b = dict(a)
    b["finished_at"] = a["finished_at"] + timedelta(minutes=7)
    assert run_payload_fingerprint(a) == run_payload_fingerprint(b)


def test_parmak_izi_skor_degisince_degisir():
    a = rapor([basarili(score=0.62)], [motor()], run_id="x")
    b = rapor([basarili(score=0.80)], [motor()], run_id="x")
    assert run_payload_fingerprint(a) != run_payload_fingerprint(b)


def test_parmak_izi_sira_degismez():
    a = rapor([basarili("GARAN"), basarili("AKBNK")], [motor()], run_id="x")
    b = rapor([basarili("AKBNK"), basarili("GARAN")], [motor()], run_id="x")
    assert run_payload_fingerprint(a) == run_payload_fingerprint(b)


# ============================================ 6) ESZAMANLILIK
def test_advisory_lock_ayni_kesimi_serilestirir(conn):
    """
    Ikinci baglanti ayni kesimin kilidini BEKLEMELI. Kilit transaction
    kapsamlidir; uygulama cokse bile sizmaz.
    """
    ikinci = _baglan()
    try:
        from src.analytics.total_rasyo_persistence import (
            ADVISORY_LOCK_SQL, _lock_keys)
        ns, key = _lock_keys(ANALIZ)
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(ADVISORY_LOCK_SQL, (ns, key))  # kilidi tut
            with ikinci.cursor() as cur2:
                cur2.execute("SET LOCAL lock_timeout = '400ms'")
                with pytest.raises(psycopg2.errors.LockNotAvailable):
                    cur2.execute(ADVISORY_LOCK_SQL, (ns, key))
        conn.rollback()

        # Kilit birakildiktan sonra alinabilmeli.
        ikinci.rollback()
        with ikinci.cursor() as cur2:
            cur2.execute(ADVISORY_LOCK_SQL, (ns, key))
        ikinci.rollback()
    finally:
        ikinci.close()


def test_farkli_kesimler_kilit_paylasmaz(conn):
    from src.analytics.total_rasyo_persistence import _lock_keys
    assert _lock_keys(ANALIZ) != _lock_keys(ANALIZ + timedelta(hours=1))


# ============================================ 7) SAYAC DURUSTLUGU
def test_rapor_sayaclari_veritabanindan_YENIDEN_SAYILANLA_esit(conn):
    sonuclar = [basarili("GARAN"), basarili("AKBNK"), yetersiz("THYAO")]
    r = rapor(sonuclar, [motor(results=2, routed=3)])
    bildirilen = persist_total_rasyo_report(conn, r)

    okunan = count_persisted(conn, run_id=r["run_id"])
    assert bildirilen == okunan, "bildirilen sayac gercek satir sayisiyla uyusmuyor"
    conn.close()

    db = yeni_baglantiyla_oku(
        "SELECT company_count, successful_company_count, insufficient_data_count "
        "FROM analytics.total_rasyo_run WHERE run_id=%s", (r["run_id"],))[0]
    gercek = yeni_baglantiyla_oku(
        "SELECT count(*), count(*) FILTER (WHERE total_rasyo_status='OK'), "
        "count(*) FILTER (WHERE total_rasyo_status='YETERSIZ_VERI') "
        "FROM analytics.company_total_rasyo_result WHERE run_id=%s", (r["run_id"],))[0]
    assert tuple(db) == tuple(gercek) == (3, 2, 1)


def test_tutarsiz_sayac_veritabanina_giremez(conn):
    """Alt sayaclarin toplami sirket sayisini asamaz (CHECK kisiti)."""
    r = rapor([basarili()], [motor()])
    r["counters"]["successful_company_count"] = 5
    with pytest.raises(psycopg2.Error):
        persist_total_rasyo_report(conn, r)


# ============================================ 8) SUTUN SOZLESMESI
def test_tuple_ve_insert_sutun_sayilari_kilitli():
    from src.analytics.total_rasyo_persistence import (
        COMPANY_RESULT_INSERT, ENGINE_RUN_INSERT, RUN_INSERT, _company_row,
        _engine_row, _run_row)
    r = rapor([basarili()], [motor()], run_id="x")
    assert len(_engine_row(ANALIZ, "x", r["engine_runs"][0])) == len(ENGINE_RUN_COLUMNS)
    assert len(_company_row(ANALIZ, "x", r["results"][0])) == len(COMPANY_RESULT_COLUMNS)
    assert len(_run_row(r, "0" * 64)) == len(RUN_COLUMNS)
    for sql, sutunlar in [(ENGINE_RUN_INSERT, ENGINE_RUN_COLUMNS),
                          (COMPANY_RESULT_INSERT, COMPANY_RESULT_COLUMNS),
                          (RUN_INSERT, RUN_COLUMNS)]:
        icerik = sql[sql.index("(") + 1:sql.index(")")]
        assert [p.strip() for p in icerik.split(",")] == list(sutunlar)


def test_sutunlar_migration_semasiyla_ESLESIR(conn):
    """
    Python sutun listesi ile GERCEK tablo semasi birebir eslesmeli.
    Migration'a sutun eklenip Python guncellenmezse bu test kirilir.
    """
    beklenen = {
        "analytics.daily_engine_run": set(ENGINE_RUN_COLUMNS) | {"inserted_at"},
        "analytics.company_total_rasyo_result": set(COMPANY_RESULT_COLUMNS) | {"inserted_at"},
        "analytics.total_rasyo_run": set(RUN_COLUMNS) | {"inserted_at"},
    }
    with conn.cursor() as cur:
        for tam_ad, sutunlar in beklenen.items():
            sema, tablo = tam_ad.split(".")
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s", (sema, tablo))
            gercek = {satir[0] for satir in cur.fetchall()}
            assert gercek == sutunlar, f"{tam_ad} sema/Python sutun farki: {gercek ^ sutunlar}"


# ============================================ 9) HATA MESAJI SINIRI
def test_uzun_hata_mesaji_veritabaninda_sinirlanir(conn):
    e = motor("GYO", "FAILED", results=0, hata="ValueError")
    e["error_message"] = "x" * 5000
    r = rapor([], [e])
    persist_total_rasyo_report(conn, r)
    conn.close()
    mesaj = yeni_baglantiyla_oku(
        "SELECT error_message FROM analytics.daily_engine_run WHERE run_id=%s",
        (r["run_id"],))[0][0]
    assert len(mesaj) <= 500


def test_hassas_deger_veritabanina_yazilmaz(conn):
    e = motor("GYO", "FAILED", results=0, hata="OperationalError")
    e["error_message"] = "baglanti hatasi password: hunter2 host=10.0.0.1"
    r = rapor([], [e])
    persist_total_rasyo_report(conn, r)
    conn.close()
    mesaj = yeni_baglantiyla_oku(
        "SELECT error_message FROM analytics.daily_engine_run WHERE run_id=%s",
        (r["run_id"],))[0][0]
    assert "hunter2" not in mesaj
    assert "***" in mesaj


def test_uzun_ret_nedeni_sinirlanir(conn):
    s = yetersiz("GARAN", "BANK", "E" * 5000)
    r = rapor([s], [motor()])
    persist_total_rasyo_report(conn, r)
    conn.close()
    neden = yeni_baglantiyla_oku(
        "SELECT rejection_reason FROM analytics.company_total_rasyo_result "
        "WHERE run_id=%s", (r["run_id"],))[0][0]
    assert len(neden) <= 500


# ============================================ 10) GIRDI DOGRULAMA
@pytest.mark.parametrize("bozuk", [
    {"run_id": ""}, {"run_id": "x" * 200}, {"run_id": None},
    {"analysis_at": datetime(2026, 8, 5, 20, 0)},  # naive
])
def test_bozuk_rapor_veritabanina_DOKUNMADAN_reddedilir(conn, bozuk):
    r = rapor([basarili()], [motor()])
    r.update(bozuk)
    with pytest.raises(PersistenceError):
        persist_total_rasyo_report(conn, r)
    conn.close()
    assert yeni_baglantiyla_oku(
        "SELECT count(*) FROM analytics.company_total_rasyo_result")[0][0] == 0


def test_finished_before_started_reddedilir(conn):
    r = rapor([basarili()], [motor()])
    r["finished_at"] = r["started_at"] - timedelta(seconds=1)
    with pytest.raises(PersistenceError):
        persist_total_rasyo_report(conn, r)


def test_bos_kosu_da_yazilir(conn):
    """Hicbir sirket denenmemis kosu da kayit birakmali; sessiz bosluk olmaz."""
    r = rapor([], [])
    sayim = persist_total_rasyo_report(conn, r)
    assert sayim == {"run_rows": 1, "engine_run_rows": 0, "company_result_rows": 0}
    conn.close()
    assert yeni_baglantiyla_oku(
        "SELECT count(*) FROM analytics.total_rasyo_run WHERE run_id=%s",
        (r["run_id"],))[0][0] == 1
