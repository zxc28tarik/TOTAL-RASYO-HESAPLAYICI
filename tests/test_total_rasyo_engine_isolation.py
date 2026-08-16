"""
Motor yalitimi ve tek motor sahipligi testleri.

Kilitlenen davranislar:
  - coken motor digerlerini dusurmez
  - coken motora yonlenen sirket rapordan KAYBOLMAZ
  - CALISTIRILMADI ile MOTOR_COKTU ayri durumlardir
  - ayni sirket iki motordan basarili sonuc ALAMAZ (fail-closed)
  - hata mesaji kanonik, sinirli ve HASSAS DEGER ICERMEZ
  - KeyboardInterrupt yutulmaz
"""
from __future__ import annotations

import pytest

from src.analytics.total_rasyo_engine_isolation import (
    ENGINE_FAMILIES,
    MAX_ERROR_MESSAGE,
    RUN_FAILED,
    RUN_OK,
    RUN_SKIPPED,
    EngineIsolationError,
    EngineRun,
    resolve_engine_ownership,
    run_engine_safely,
    sanitize_error_message,
    skipped_engine_run,
)


def m2(score=0.8, source="TEST_V1"):
    return {"m2": score, "m2_source": source, "valuation_usable": True,
            "valuation_status": "OK", "valuation_reason": None,
            "valuation_confidence": 0.7}


def calisan(engine, results, rejections=None):
    return run_engine_safely(engine, lambda: {"results": results,
                                              "rejections": rejections or {}})


def coken(engine, exc=None):
    def patla():
        raise (exc or ValueError("motor patladi"))
    return run_engine_safely(engine, patla)


# ===================================================== yalitim
def test_coken_motor_digerlerini_dusurmez():
    kosular = [calisan("BANK", {"GARAN": m2()}), coken("GYO"),
               calisan("NONFIN", {"THYAO": m2()})]
    assert [k.status for k in kosular] == [RUN_OK, RUN_FAILED, RUN_OK]
    assert kosular[0].m2_by_ticker["GARAN"]["m2"] == 0.8
    assert kosular[2].m2_by_ticker["THYAO"]["m2"] == 0.8


def test_coken_motor_hata_tipi_tasir():
    kosu = coken("GYO", KeyError("eksik anahtar"))
    assert kosu.status == RUN_FAILED
    assert kosu.error_type == "KeyError"
    assert kosu.error_message
    assert kosu.result_count == 0


def test_bank_cokse_de_digerleri_korunur():
    """BANK motoru digerlerinden farkli arayuz kullanir; yalitim ayni olmali."""
    kosular = {k.engine: k for k in [
        coken("BANK"), calisan("GYO", {"AGYO": m2()}),
        calisan("INSURANCE", {"ANSGR": m2()}),
    ]}
    assert kosular["BANK"].status == RUN_FAILED
    assert kosular["GYO"].m2_by_ticker["AGYO"]["m2"] == 0.8
    assert kosular["INSURANCE"].m2_by_ticker["ANSGR"]["m2"] == 0.8


def test_birden_cok_motor_cokebilir():
    kosular = [coken("GYO"), coken("HOLDING"), calisan("BANK", {"GARAN": m2()})]
    assert sum(1 for k in kosular if k.status == RUN_FAILED) == 2
    assert kosular[2].status == RUN_OK


def test_keyboard_interrupt_yutulmaz():
    """Operatorun durdurma iradesi motor hatasi degildir."""
    def patla():
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run_engine_safely("BANK", patla)


def test_system_exit_yutulmaz():
    def patla():
        raise SystemExit(1)
    with pytest.raises(SystemExit):
        run_engine_safely("BANK", patla)


def test_calistirilmadi_coktu_degildir():
    atlanan = skipped_engine_run("GYO", "config yok")
    assert atlanan.status == RUN_SKIPPED
    assert atlanan.status != RUN_FAILED
    assert atlanan.error_type is None


# ===================================================== hata mesaji
def test_hata_mesaji_uzunluk_sinirli():
    kosu = coken("BANK", ValueError("x" * 5000))
    assert len(kosu.error_message) <= MAX_ERROR_MESSAGE
    # Kesildigi GORUNUR olmali; sessizce kirpilmis mesaj yanlis teshise yol acar.
    assert "..." in kosu.error_message
    # Konum eki redaksiyona veya kesmeye KURBAN GITMEMELI.
    assert kosu.error_message.endswith(f"@{kosu.diagnostics['failed_at']}")


@pytest.mark.parametrize("gizli", [
    "password: hunter2", "api_key=SK-12345", "DB_SECRET: abcdef",
    "token=eyJhbGciOi", "Authorization: Bearer xyz123",
])
def test_hata_mesajinda_hassas_deger_sizmaz(gizli):
    kosu = coken("BANK", ValueError(f"baglanti basarisiz {gizli}"))
    assert "hunter2" not in kosu.error_message
    assert "SK-12345" not in kosu.error_message
    assert "abcdef" not in kosu.error_message
    assert "eyJhbGciOi" not in kosu.error_message
    assert "xyz123" not in kosu.error_message
    assert "***" in kosu.error_message


def test_baglanti_uri_parolasi_maskelenir():
    mesaj = sanitize_error_message(
        "could not connect postgresql://admin:gizliparola@10.0.0.1:5432/db")
    assert "gizliparola" not in mesaj
    assert "admin" not in mesaj
    assert "***:***@" in mesaj


def test_mesaj_kanonik_bosluk():
    assert sanitize_error_message("a\n\n  b\tc") == "a b c"
    assert sanitize_error_message(None) == ""


# ===================================================== tek motor sahipligi
def test_tek_sahiplik_normal_durum():
    kosular = {"BANK": calisan("BANK", {"GARAN": m2()}),
               "GYO": calisan("GYO", {"AGYO": m2()})}
    c = resolve_engine_ownership({"GARAN": "BANK", "AGYO": "GYO"}, kosular)
    assert c.owner_by_ticker == {"GARAN": "BANK", "AGYO": "GYO"}
    assert c.conflicts == {}


def test_ayni_sirket_iki_motorda_cakisma():
    """Sessiz secim YOK: ilk gelen kazanmaz, oncelik sirasi uygulanmaz."""
    kosular = {"BANK": calisan("BANK", {"GARAN": m2()}),
               "NONFIN": calisan("NONFIN", {"GARAN": m2(0.3)})}
    c = resolve_engine_ownership({"GARAN": "BANK"}, kosular)
    assert "GARAN" not in c.owner_by_ticker
    assert c.conflicts["GARAN"] == ("BANK", "NONFIN")


def test_yonlendirmeden_farkli_motor_uretirse_cakisma():
    kosular = {"NONFIN": calisan("NONFIN", {"GARAN": m2()})}
    c = resolve_engine_ownership({"GARAN": "BANK"}, kosular)
    assert "GARAN" not in c.owner_by_ticker
    assert c.conflicts["GARAN"] == ("BANK", "NONFIN")


def test_yonlendirmede_olmayan_sirket_uretilirse_cakisma():
    kosular = {"GYO": calisan("GYO", {"SURPRIZ": m2()})}
    c = resolve_engine_ownership({}, kosular)
    assert c.conflicts["SURPRIZ"] == ("GYO",)


def test_coken_motorun_sonucu_sahiplik_saymaz():
    """Coken motor sonuc uretmedigi icin cakisma da uretemez."""
    kosular = {"BANK": calisan("BANK", {"GARAN": m2()}), "GYO": coken("GYO")}
    c = resolve_engine_ownership({"GARAN": "BANK", "AGYO": "GYO"}, kosular)
    assert c.owner_by_ticker == {"GARAN": "BANK", "AGYO": "GYO"}
    assert c.conflicts == {}


def test_sonuc_uretmemis_sirket_sahibi_kalir():
    """Motor calisti ama bu sirket icin sonuc yok: yine de sahibi bellidir."""
    kosular = {"BANK": calisan("BANK", {})}
    c = resolve_engine_ownership({"GARAN": "BANK"}, kosular)
    assert c.owner_by_ticker == {"GARAN": "BANK"}


def test_sahiplik_sira_degismezligi():
    a = {"BANK": calisan("BANK", {"GARAN": m2()}),
         "NONFIN": calisan("NONFIN", {"GARAN": m2()})}
    b = {"NONFIN": a["NONFIN"], "BANK": a["BANK"]}
    assert (resolve_engine_ownership({"GARAN": "BANK"}, a).conflicts
            == resolve_engine_ownership({"GARAN": "BANK"}, b).conflicts)


# ===================================================== girdi dogrulama
def test_desteklenmeyen_motor_reddedilir():
    with pytest.raises(EngineIsolationError):
        run_engine_safely("CRYPTO", lambda: {"results": {}})


def test_alti_motor_ailesi():
    assert set(ENGINE_FAMILIES) == {"BANK", "NONFIN", "HOLDING", "GYO",
                                    "INSURANCE", "FINANCIAL"}


def test_ayni_ticker_hem_sonuc_hem_ret_reddedilir():
    with pytest.raises(EngineIsolationError, match="hem sonuc hem ret"):
        run_engine_safely("BANK", lambda: {"results": {"GARAN": m2()},
                                           "rejections": {"GARAN": "x"}})


def test_ticker_normalize_edilir():
    kosu = calisan("BANK", {" garan ": m2()})
    assert "GARAN" in kosu.m2_by_ticker


def test_yinelenen_ticker_reddedilir():
    with pytest.raises(EngineIsolationError, match="yinelenen ticker"):
        run_engine_safely("BANK", lambda: {"results": {"garan": m2(), "GARAN": m2()}})


def test_mapping_olmayan_sonuc_reddedilir():
    with pytest.raises(EngineIsolationError):
        run_engine_safely("BANK", lambda: ["liste"])


def test_ret_nedeni_bos_kalmaz():
    kosu = calisan("BANK", {}, {"GARAN": ""})
    assert kosu.rejections["GARAN"] == "NEDEN_BELIRTILMEDI"


def test_sayaclar():
    kosu = calisan("BANK", {"A": m2(), "B": m2()}, {"C": "yetersiz"})
    assert kosu.result_count == 2 and kosu.rejection_count == 1
