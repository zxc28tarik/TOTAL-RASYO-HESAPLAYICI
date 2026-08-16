#!/usr/bin/env python3
"""
Total Rasyo ORKESTRATOR OZ DENETIMI — 15.000 senaryo, hata enjeksiyonlu.

"Exception olmadi" BASARI SAYILMAZ. Her senaryoda invariant'lar tek tek
dogrulanir; bir invariant ihlali senaryoyu DUSURUR.

DETERMINIZM: her senaryonun kendi tohumu vardir ve senaryo kimligiyle
yeniden uretilebilir:

    python3 -m src.analytics.total_rasyo_self_audit --replay S00042

Kullanim:
    python3 -m src.analytics.total_rasyo_self_audit            # 15.000
    python3 -m src.analytics.total_rasyo_self_audit --count 500
    python3 -m src.analytics.total_rasyo_self_audit --json rapor.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional

import psycopg2

from src.analytics.total_rasyo_combine import (
    FORBIDDEN_DRAFT_WEIGHTS,
    INSUFF_NO_MODULE_ROW,
    STATUS_ENGINE_CRASHED,
    STATUS_INSUFFICIENT,
    STATUS_NOT_RUN,
    STATUS_OK,
    STATUS_ROUTING_CONFLICT,
)
from src.analytics.total_rasyo_module_reader import READ_MODULE_KEYS
from src.analytics.total_rasyo_orchestrator import (
    NOT_RUN_PRESERVE,
    OVERALL_COMPLETE,
    OVERALL_COMPLETE_NO_RESULTS,
    OVERALL_FAILED,
    OVERALL_PARTIAL,
    OrchestratorError,
    run_total_rasyo_orchestrator,
)
from src.analytics.total_rasyo_score import (
    DEFAULT_WEIGHTS,
    MODULE_KEYS,
    compute_total_rasyo,
)

TZ = timezone(timedelta(hours=3))
ANALIZ = datetime(2026, 8, 5, 20, 0, tzinfo=TZ)

# NOBETCI DEGERLER: gelecek/eski satirlari isaretlemek icin. Rastgele
# skorlar round(uniform(0,1), 4) ile uretilir -- yani 4 ONDALIK. Nobetciler
# 5 ondalikli secildi ki rastgele bir deger onlara ASLA esit olamasin.
# Ilk surumde 0.999/0.111 kullanilmisti ve mesru bir skor tesaduefen 0.999
# cikinca oz denetim SAHTE sizinti bildirdi (15.000'de 6 kez; beklenen ~3.75).
SENTINEL_FUTURE = 0.98765
SENTINEL_STALE = 0.12345
SCORE_DECIMALS = 4

FAMILIES = ("BANK", "NONFIN", "HOLDING", "GYO", "INSURANCE", "FINANCIAL")
TICKERS = ("GARAN", "AKBNK", "THYAO", "KCHOL", "AGYO", "ANSGR", "GARFA",
           "SISE", "EREGL", "TUPRS")

# Senaryo dagilimi -- toplam TAM 15.000.
#
# NOT: istenen liste (3000+2000+1000+2000+1500+1000+1000+1000+750+750)
# 14.000 eder. Kalan 1.000, tek basina test edilmis kirilma bicimlerinin
# AYNI KOSUDA birlestigi karma yuke ayrildi: gercek arizalar tek tek degil
# ust uste gelir ve etkilesimleri yalniz boyle gorunur.
DISTRIBUTION: tuple[tuple[str, int], ...] = (
    ("tam_basarili", 3000),
    ("tek_motor_cokmesi", 2000),
    ("coklu_motor_cokmesi", 1000),
    ("eksik_modul_kombinasyonu", 2000),
    ("zaman_kesimi_ve_satir_butunlugu", 1500),
    ("yonlendirme_catismasi", 1000),
    ("yeniden_calisma", 1000),
    ("sira_degismezligi", 1000),
    ("kalicilik_hata_enjeksiyonu", 750),
    ("sinir_deger_ve_config_bypass", 750),
    ("karma_yuk", 1000),
)
TOTAL = sum(n for _, n in DISTRIBUTION)
assert TOTAL == 15000, f"dagilim toplami 15000 olmali, {TOTAL} bulundu"


class InvariantIhlali(AssertionError):
    pass


def _dsn() -> Optional[str]:
    return os.environ.get("TOTAL_RASYO_TEST_DSN") or os.environ.get("PGDATABASE")


def baglan():
    dsn = _dsn()
    if not dsn:
        return None
    try:
        if "=" in dsn or dsn.startswith("postgres"):
            return psycopg2.connect(dsn)
        return psycopg2.connect(dbname=dsn)
    except psycopg2.Error:
        return None


# ==================================================================== fikstur
def temizle(conn) -> None:
    with conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE analytics.reconciliation_module_check, analytics.reconciliation_module_run, analytics.total_rasyo_module_input, analytics.total_rasyo_run,"
                        " analytics.daily_engine_run,"
                        " analytics.company_total_rasyo_result")
            cur.execute("DELETE FROM analytics.module_scores")


def modul_satiri(conn, ticker: str, *, asof: date, analysis_at, degerler: Mapping[str, Any],
                 good: Any) -> None:
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO analytics.module_scores (ticker, asof_date,"
                " horizon_days, m1, m2, m3, ek1, ek4, ek9, good_count_ge8,"
                " analysis_at) VALUES (%s,%s,20,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ticker, asof, degerler.get("M1"), 0.99, degerler.get("M3"),
                 degerler.get("Ek1"), degerler.get("Ek4"), degerler.get("Ek9"),
                 good, analysis_at))


def m2cikti(score=0.80, source="AUDIT_V1", usable=True, conf=0.7):
    return {"m2": score, "m2_source": source, "valuation_usable": usable,
            "valuation_confidence": conf, "valuation_reason": None}


def motor(results: Mapping[str, Any]) -> Callable[[], Any]:
    return lambda: {"results": dict(results), "rejections": {}}


def coken(mesaj="motor cokti") -> Callable[[], Any]:
    def f():
        raise RuntimeError(mesaj)
    return f


# ================================================================= invariants
def inv_sayaclar_ayrik(rapor) -> None:
    s = rapor["counters"]
    toplam = (s["successful_company_count"] + s["insufficient_data_count"]
              + s["engine_failed_company_count"] + s["not_run_company_count"]
              + s["routing_conflict_count"])
    if toplam != s["company_count"]:
        raise InvariantIhlali(
            f"sayac ayrikligi: {toplam} != {s['company_count']}")


def inv_sirket_kaybolmaz(rapor, beklenen_tickerlar) -> None:
    gorulen = {r["ticker"] for r in rapor["results"]}
    korunan = set(rapor.get("preserved_tickers") or ())
    if gorulen | korunan != set(beklenen_tickerlar):
        eksik = set(beklenen_tickerlar) - gorulen - korunan
        raise InvariantIhlali(f"sirket rapordan kayboldu: {sorted(eksik)}")


def inv_ok_yalniz_tam_veriyle(rapor) -> None:
    for r in rapor["results"]:
        if r["total_rasyo_status"] != STATUS_OK:
            continue
        if r["m2_missing"] or r["good_count_missing"]:
            raise InvariantIhlali(f"{r['ticker']}: OK ama M2/good_count eksik")
        for key in MODULE_KEYS:
            if (r["modules"].get(key) or {}).get("missing"):
                raise InvariantIhlali(f"{r['ticker']}: OK ama {key} eksik")
        if r["final_score"] is None or r["decision"] is None:
            raise InvariantIhlali(f"{r['ticker']}: OK ama skor/karar yok")


def inv_skor_tek_kaynaktan(rapor) -> None:
    """compute_total_rasyo() ile birebir esitlik; ikinci formul olamaz."""
    for r in rapor["results"]:
        if r["total_rasyo_status"] != STATUS_OK:
            continue
        beklenen = compute_total_rasyo(
            {k: r["modules"][k]["score"] for k in MODULE_KEYS},
            good_count_ge8=r["good_count_ge8"])
        if abs(beklenen["final_score"] - r["final_score"]) > 1e-9:
            raise InvariantIhlali(
                f"{r['ticker']}: skor referanstan sapti "
                f"{r['final_score']} != {beklenen['final_score']}")
        if beklenen["decision"] != r["decision"]:
            raise InvariantIhlali(f"{r['ticker']}: karar sapti")


def inv_agirliklar_degismedi(rapor) -> None:
    agirlik = rapor["diagnostics"]["weights"]
    if agirlik != dict(DEFAULT_WEIGHTS):
        raise InvariantIhlali(f"agirliklar degisti: {agirlik}")
    if abs(sum(agirlik.values()) - 1.0) > 1e-9:
        raise InvariantIhlali("agirlik toplami 1.0 degil")


def inv_ikinci_m2_puanlanmaz(rapor) -> None:
    """
    module_scores.m2 fikstürde 0.99. Sektor M2'si 0.80. Skorda 0.99
    gorunuyorsa eski m2 sizmis demektir.
    """
    for r in rapor["results"]:
        if r["total_rasyo_status"] != STATUS_OK:
            continue
        if r["modules"]["M2"]["score"] is not None and \
                abs(r["modules"]["M2"]["score"] - 0.99) < 1e-9:
            raise InvariantIhlali(f"{r['ticker']}: eski module_scores.m2 puanlandi")
        if r["m2_source_type"] != "SECTOR_ENGINE":
            raise InvariantIhlali(f"{r['ticker']}: M2 kaynagi sektor motoru degil")


def inv_tek_sahiplik(rapor, routing) -> None:
    for r in rapor["results"]:
        if r["total_rasyo_status"] == STATUS_ROUTING_CONFLICT:
            if r["final_score"] is not None:
                raise InvariantIhlali(f"{r['ticker']}: cakismada skor uretilmis")
            continue
        if r["routed_engine"] != routing[r["ticker"]]:
            raise InvariantIhlali(f"{r['ticker']}: yanlis motor sahipligi")


def inv_gelecek_kayit_secilmez(rapor, kesim) -> None:
    for r in rapor["results"]:
        for key in READ_MODULE_KEYS:
            kaynak = (r["modules"].get(key) or {}).get("source_at")
            if kaynak is not None and kaynak > kesim:
                raise InvariantIhlali(f"{r['ticker']}.{key}: gelecek kayit secildi")


def inv_kalicilik_durustlugu(rapor) -> None:
    if rapor.get("persisted") and rapor.get("persistence_status") != "OK":
        raise InvariantIhlali("persisted=True ama persistence_status != OK")
    if rapor["overall_status"] == OVERALL_FAILED and rapor.get("persisted"):
        if rapor.get("persistence_status") != "OK":
            raise InvariantIhlali("FAILED kosu basarili kalicilik bildirdi")


def inv_overall_status_tutarli(rapor) -> None:
    durum = rapor["overall_status"]
    s = rapor["counters"]
    gecerli = {OVERALL_COMPLETE, OVERALL_COMPLETE_NO_RESULTS,
               OVERALL_PARTIAL, OVERALL_FAILED}
    if durum not in gecerli:
        raise InvariantIhlali(f"bilinmeyen overall_status: {durum}")
    if durum == OVERALL_COMPLETE and s["successful_company_count"] == 0:
        raise InvariantIhlali("COMPLETE ama hicbir sirket basarili degil")
    if durum == OVERALL_COMPLETE_NO_RESULTS:
        if s["successful_company_count"]:
            raise InvariantIhlali("COMPLETE_NO_RESULTS ama basarili sirket var")
        if s["engine_error_count"]:
            raise InvariantIhlali("COMPLETE_NO_RESULTS ama motor hatasi var")
    if durum in (OVERALL_COMPLETE, OVERALL_COMPLETE_NO_RESULTS):
        if s["engine_failed_company_count"] or s["routing_conflict_count"]:
            raise InvariantIhlali(f"{durum} ama motor/cakisma kaybi var")


def inv_db_ile_ayni(conn, rapor) -> None:
    """DB'ye yazilan ile yeniden OKUNAN ayni olmali."""
    if not rapor.get("persisted"):
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, total_rasyo_status, final_score, decision"
            " FROM analytics.company_total_rasyo_result WHERE run_id=%s",
            (rapor["run_id"],))
        db = {t: (d, None if f is None else float(f), k)
              for t, d, f, k in cur.fetchall()}
    if len(db) != len(rapor["results"]):
        raise InvariantIhlali(
            f"DB satir sayisi farkli: {len(db)} != {len(rapor['results'])}")
    for r in rapor["results"]:
        gercek = db.get(r["ticker"])
        if gercek is None:
            raise InvariantIhlali(f"{r['ticker']} DB'de yok")
        beklenen_skor = None if r["final_score"] is None else round(float(r["final_score"]), 6)
        if gercek[0] != r["total_rasyo_status"]:
            raise InvariantIhlali(f"{r['ticker']}: DB durumu farkli")
        if (gercek[1] is None) != (beklenen_skor is None):
            raise InvariantIhlali(f"{r['ticker']}: DB skor varligi farkli")
        if beklenen_skor is not None and abs(gercek[1] - beklenen_skor) > 1e-6:
            raise InvariantIhlali(f"{r['ticker']}: DB skoru farkli")
        if gercek[2] != r["decision"]:
            raise InvariantIhlali(f"{r['ticker']}: DB karari farkli")


def ortak_invariantlar(conn, rapor, routing, kesim=ANALIZ) -> None:
    inv_sayaclar_ayrik(rapor)
    inv_ok_yalniz_tam_veriyle(rapor)
    inv_skor_tek_kaynaktan(rapor)
    inv_agirliklar_degismedi(rapor)
    inv_ikinci_m2_puanlanmaz(rapor)
    inv_tek_sahiplik(rapor, routing)
    inv_gelecek_kayit_secilmez(rapor, kesim)
    inv_kalicilik_durustlugu(rapor)
    inv_overall_status_tutarli(rapor)
    inv_db_ile_ayni(conn, rapor)


# ================================================================== senaryolar
def _routing(rng, n: int) -> dict[str, str]:
    secilen = rng.sample(TICKERS, min(n, len(TICKERS)))
    return {t: rng.choice(FAMILIES) for t in secilen}


def _tam_degerler(rng) -> dict[str, float]:
    return {k: round(rng.uniform(0.0, 1.0), SCORE_DECIMALS)
            for k in READ_MODULE_KEYS}


def _kur_moduller(conn, routing, rng, *, degerler_map=None, good_map=None,
                  asof=date(2026, 8, 5), analysis_at=None):
    if analysis_at is None:
        analysis_at = ANALIZ - timedelta(hours=1)
    for t in routing:
        degerler = (degerler_map or {}).get(t) or _tam_degerler(rng)
        good = (good_map or {}).get(t, rng.randint(0, 12))
        modul_satiri(conn, t, asof=asof, analysis_at=analysis_at,
                     degerler=degerler, good=good)


def _runners(routing, *, coken_aileler=(), atlanan_aileler=()):
    aileler = set(routing.values())
    out: dict[str, Callable[[], Any]] = {}
    for aile in aileler:
        if aile in atlanan_aileler:
            continue
        if aile in coken_aileler:
            out[aile] = coken()
            continue
        out[aile] = motor({t: m2cikti() for t, a in routing.items() if a == aile})
    return out


def sc_tam_basarili(conn, rng, sid):
    routing = _routing(rng, rng.randint(1, 6))
    _kur_moduller(conn, routing, rng)
    rapor = run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing,
        engine_runners=_runners(routing), run_id=sid)
    ortak_invariantlar(conn, rapor, routing)
    inv_sirket_kaybolmaz(rapor, routing)
    if rapor["overall_status"] not in (OVERALL_COMPLETE, OVERALL_COMPLETE_NO_RESULTS):
        raise InvariantIhlali(f"tam kosu {rapor['overall_status']} dondu")
    if rapor["counters"]["successful_company_count"] != len(routing):
        raise InvariantIhlali("tam veride butun sirketler basarili olmali")


def sc_tek_motor_cokmesi(conn, rng, sid):
    routing = _routing(rng, rng.randint(2, 6))
    _kur_moduller(conn, routing, rng)
    coken_aile = rng.choice(sorted(set(routing.values())))
    rapor = run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing,
        engine_runners=_runners(routing, coken_aileler=(coken_aile,)), run_id=sid)
    ortak_invariantlar(conn, rapor, routing)
    inv_sirket_kaybolmaz(rapor, routing)
    coken_sirketler = {t for t, a in routing.items() if a == coken_aile}
    for r in rapor["results"]:
        if r["ticker"] in coken_sirketler:
            if r["total_rasyo_status"] != STATUS_ENGINE_CRASHED:
                raise InvariantIhlali(f"{r['ticker']}: coken motor sirketi "
                                      f"{r['total_rasyo_status']} oldu")
        elif r["total_rasyo_status"] == STATUS_ENGINE_CRASHED:
            raise InvariantIhlali(f"{r['ticker']}: cokmeyen motorun sirketi dustu")


def sc_coklu_motor_cokmesi(conn, rng, sid):
    routing = _routing(rng, rng.randint(3, 8))
    _kur_moduller(conn, routing, rng)
    aileler = sorted(set(routing.values()))
    n = rng.randint(2, max(2, len(aileler)))
    cokenler = tuple(rng.sample(aileler, min(n, len(aileler))))
    rapor = run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing,
        engine_runners=_runners(routing, coken_aileler=cokenler), run_id=sid)
    ortak_invariantlar(conn, rapor, routing)
    inv_sirket_kaybolmaz(rapor, routing)
    if rapor["counters"]["engine_error_count"] != len(cokenler):
        raise InvariantIhlali("coken motor sayisi uyusmuyor")
    if len(cokenler) == len(aileler) and rapor["overall_status"] != OVERALL_FAILED:
        raise InvariantIhlali("butun motorlar coktu ama FAILED degil")


def sc_eksik_modul_kombinasyonu(conn, rng, sid):
    routing = _routing(rng, rng.randint(1, 5))
    degerler_map, good_map, eksikler = {}, {}, {}
    for t in routing:
        d = _tam_degerler(rng)
        eksik = set()
        for k in READ_MODULE_KEYS:
            if rng.random() < 0.35:
                d[k] = None
                eksik.add(k)
        good = rng.randint(0, 12)
        if rng.random() < 0.25:
            good = None
            eksik.add("good_count_ge8")
        degerler_map[t], good_map[t], eksikler[t] = d, good, eksik
    _kur_moduller(conn, routing, rng, degerler_map=degerler_map, good_map=good_map)
    rapor = run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing,
        engine_runners=_runners(routing), run_id=sid)
    ortak_invariantlar(conn, rapor, routing)
    inv_sirket_kaybolmaz(rapor, routing)
    for r in rapor["results"]:
        eksik = eksikler[r["ticker"]]
        if eksik and r["total_rasyo_status"] != STATUS_INSUFFICIENT:
            raise InvariantIhlali(
                f"{r['ticker']}: eksik {sorted(eksik)} ama durum "
                f"{r['total_rasyo_status']}")
        if eksik and r["final_score"] is not None:
            raise InvariantIhlali(f"{r['ticker']}: eksik bilesenle skor uretildi")
        # Eksiklik ONCEDEN yakalanmali. HESAP_HATASI'na dusmek, eksik degerin
        # compute_total_rasyo()'ya GECIRILDIGI ve ancak orada reddedildigi
        # anlamina gelir; kapi gorevini yapmamis demektir.
        if eksik and r["insufficiency_reason"] == "HESAP_HATASI":
            raise InvariantIhlali(
                f"{r['ticker']}: eksik deger compute_total_rasyo'ya gecirilmis")
        if eksik and r["insufficiency_reason"] not in (
                "EKSIK_BILESEN", "MODUL_SATIRI_YOK", "M2_YOK"):
            raise InvariantIhlali(
                f"{r['ticker']}: beklenmeyen yetersizlik sinifi "
                f"{r['insufficiency_reason']}")
        if not eksik and r["total_rasyo_status"] != STATUS_OK:
            raise InvariantIhlali(f"{r['ticker']}: tam veri ama OK degil")


def sc_zaman_kesimi_ve_satir_butunlugu(conn, rng, sid):
    """
    Ayni sirket icin ESKI ve GELECEK satirlar. Gelecek satir SECILMEMELI,
    moduller TEK satirdan gelmeli, eksik modul eski satirdan TAMAMLANMAMALI.
    Eski satir bilerek ayirt edici degerler tasir.
    """
    routing = _routing(rng, rng.randint(1, 4))
    dogru = {}
    for t in routing:
        eski = {k: SENTINEL_STALE for k in READ_MODULE_KEYS}
        modul_satiri(conn, t, asof=date(2026, 7, 20),
                     analysis_at=ANALIZ - timedelta(days=16),
                     degerler=eski, good=99)
        gelecek = {k: SENTINEL_FUTURE for k in READ_MODULE_KEYS}
        modul_satiri(conn, t, asof=date(2026, 8, 7),
                     analysis_at=ANALIZ + timedelta(days=2),
                     degerler=gelecek, good=77)
        guncel = _tam_degerler(rng)
        if rng.random() < 0.3:
            guncel[rng.choice(list(READ_MODULE_KEYS))] = None
        good = rng.randint(0, 12)
        modul_satiri(conn, t, asof=date(2026, 8, 5),
                     analysis_at=ANALIZ - timedelta(hours=1),
                     degerler=guncel, good=good)
        dogru[t] = (guncel, good)

    rapor = run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing,
        engine_runners=_runners(routing), run_id=sid)
    ortak_invariantlar(conn, rapor, routing)
    inv_sirket_kaybolmaz(rapor, routing)
    for r in rapor["results"]:
        guncel, good = dogru[r["ticker"]]
        for k in READ_MODULE_KEYS:
            secilen = r["modules"][k]["score"]
            if secilen is None:
                if guncel[k] is not None:
                    raise InvariantIhlali(f"{r['ticker']}.{k}: dolu deger kayboldu")
                continue
            if abs(secilen - SENTINEL_FUTURE) < 1e-9:
                raise InvariantIhlali(f"{r['ticker']}.{k}: GELECEK satir secildi")
            if abs(secilen - SENTINEL_STALE) < 1e-9:
                raise InvariantIhlali(f"{r['ticker']}.{k}: ESKI satirdan tamamlandi")
            if abs(secilen - guncel[k]) > 1e-9:
                raise InvariantIhlali(f"{r['ticker']}.{k}: yanlis satirdan geldi")
        if r["good_count_ge8"] not in (None, good):
            raise InvariantIhlali(f"{r['ticker']}: good_count baska satirdan")


def sc_yonlendirme_catismasi(conn, rng, sid):
    routing = _routing(rng, rng.randint(2, 5))
    _kur_moduller(conn, routing, rng)
    kurban = rng.choice(sorted(routing))
    runners = _runners(routing)
    digerleri = [a for a in FAMILIES if a != routing[kurban]]
    ikinci = rng.choice(digerleri)
    onceki = runners.get(ikinci)
    mevcut = {} if onceki is None else onceki()["results"]
    runners[ikinci] = motor({**mevcut, kurban: m2cikti(0.2)})

    rapor = run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing,
        engine_runners=runners, run_id=sid)
    ortak_invariantlar(conn, rapor, routing)
    inv_sirket_kaybolmaz(rapor, routing)
    hedef = [r for r in rapor["results"] if r["ticker"] == kurban][0]
    if hedef["total_rasyo_status"] != STATUS_ROUTING_CONFLICT:
        raise InvariantIhlali(f"{kurban}: cakisma sessizce cozuldu "
                              f"({hedef['total_rasyo_status']})")
    if hedef["final_score"] is not None or hedef["m2_score"] is not None:
        raise InvariantIhlali(f"{kurban}: cakismada skor uretildi")


def sc_yeniden_calisma(conn, rng, sid):
    routing = _routing(rng, rng.randint(1, 4))
    _kur_moduller(conn, routing, rng)
    run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing,
        engine_runners=_runners(routing), run_id=sid + "-a")

    mod = rng.choice(("ret", "cokme", "basari"))
    if mod == "ret":
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE analytics.module_scores SET m1=NULL")
        runners = _runners(routing)
        beklenen = STATUS_INSUFFICIENT
    elif mod == "cokme":
        runners = _runners(routing, coken_aileler=tuple(set(routing.values())))
        beklenen = STATUS_ENGINE_CRASHED
    else:
        runners = _runners(routing)
        beklenen = STATUS_OK

    rapor = run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing,
        engine_runners=runners, run_id=sid + "-b")
    ortak_invariantlar(conn, rapor, routing)

    with conn.cursor() as cur:
        cur.execute("SELECT ticker, total_rasyo_status, final_score"
                    " FROM analytics.company_total_rasyo_result"
                    " WHERE analysis_at=%s", (ANALIZ,))
        satirlar = cur.fetchall()
    if len(satirlar) != len(routing):
        raise InvariantIhlali(
            f"yeniden calisma sonrasi satir sayisi {len(satirlar)} != {len(routing)}")
    for _, durum, skor in satirlar:
        if durum != beklenen:
            raise InvariantIhlali(f"otoritatiflik bozuk: {durum} != {beklenen}")
        if beklenen != STATUS_OK and skor is not None:
            raise InvariantIhlali("eski basarili skor temizlenmedi")


def sc_sira_degismezligi(conn, rng, sid):
    routing = _routing(rng, rng.randint(2, 6))
    degerler_map = {t: _tam_degerler(rng) for t in routing}
    good_map = {t: rng.randint(0, 12) for t in routing}
    _kur_moduller(conn, routing, rng, degerler_map=degerler_map, good_map=good_map)

    a = run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing,
        engine_runners=_runners(routing), run_id=sid + "-a")

    ters_routing = dict(reversed(list(routing.items())))
    ters_runners = dict(reversed(list(_runners(routing).items())))
    b = run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=ters_routing,
        engine_runners=ters_runners, run_id=sid + "-b")

    def ozet(r):
        return sorted((x["ticker"], x["total_rasyo_status"],
                       None if x["final_score"] is None else round(x["final_score"], 9),
                       x["decision"]) for x in r["results"])
    if ozet(a) != ozet(b):
        raise InvariantIhlali("sira degisince sonuc degisti")
    if a["counters"] != b["counters"]:
        raise InvariantIhlali("sira degisince sayaclar degisti")
    if a["overall_status"] != b["overall_status"]:
        raise InvariantIhlali("sira degisince overall_status degisti")


def sc_kalicilik_hata_enjeksiyonu(conn, rng, sid):
    routing = _routing(rng, rng.randint(1, 4))
    _kur_moduller(conn, routing, rng)
    mod = rng.choice(("baglanti_dustu", "run_id_catismasi", "kirik_cursor"))

    if mod == "run_id_catismasi":
        run_total_rasyo_orchestrator(
            conn, analysis_at=ANALIZ, routing=routing,
            engine_runners=_runners(routing), run_id=sid)
        farkli = dict(routing)
        farkli.pop(sorted(farkli)[0], None)
        if not farkli:
            return
        try:
            run_total_rasyo_orchestrator(
                conn, analysis_at=ANALIZ, routing=farkli,
                engine_runners=_runners(farkli), run_id=sid)
        except OrchestratorError:
            return
        raise InvariantIhlali("ayni run_id farkli icerikle kabul edildi")

    class BozukConn:
        def __init__(self, gercek, kirik_cursor=False):
            self._g = gercek
            self._kirik = kirik_cursor
            self.closed = 0

        def cursor(self):
            if self._kirik:
                raise psycopg2.OperationalError("cursor acilamadi")
            return self._g.cursor()

        def __enter__(self):
            raise psycopg2.OperationalError("baglanti dustu")

        def __exit__(self, *a):
            return False

    bozuk = BozukConn(conn, kirik_cursor=(mod == "kirik_cursor"))
    try:
        rapor = run_total_rasyo_orchestrator(
            bozuk,
            analysis_at=ANALIZ, routing=routing,
            engine_runners=_runners(routing), run_id=sid)
    except (OrchestratorError, psycopg2.Error):
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM analytics.company_total_rasyo_result"
                        " WHERE run_id=%s", (sid,))
            if cur.fetchone()[0]:
                raise InvariantIhlali("kalicilik hatasindan sonra yarim kayit kaldi")
        return
    if rapor.get("persisted"):
        raise InvariantIhlali("kalicilik hatasi basari olarak raporlandi")


def sc_sinir_deger_ve_config_bypass(conn, rng, sid):
    routing = _routing(rng, rng.randint(1, 3))
    mod = rng.choice(("bozuk_agirlik", "taslak_agirlik", "gecersiz_m2",
                      "bos_metin_m2", "inf_m2", "naive_kesim",
                      "bozuk_routing", "hedef_disi"))

    if mod in ("bozuk_agirlik", "taslak_agirlik"):
        _kur_moduller(conn, routing, rng)
        agirlik = (dict(FORBIDDEN_DRAFT_WEIGHTS) if mod == "taslak_agirlik"
                   else {"M2": 0.5, "M1": 0.5})
        try:
            run_total_rasyo_orchestrator(
                conn, analysis_at=ANALIZ, routing=routing,
                engine_runners=_runners(routing), run_id=sid, weights=agirlik)
        except Exception:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM analytics"
                            ".company_total_rasyo_result WHERE run_id=%s", (sid,))
                if cur.fetchone()[0]:
                    raise InvariantIhlali("bozuk config veritabanina yazdi")
            return
        raise InvariantIhlali(f"{mod} kabul edildi")

    if mod in ("gecersiz_m2", "bos_metin_m2", "inf_m2"):
        _kur_moduller(conn, routing, rng)
        bozuk = {"gecersiz_m2": True, "bos_metin_m2": "",
                 "inf_m2": float("inf")}[mod]
        aile = sorted(set(routing.values()))[0]
        runners = _runners(routing)
        hedef = [t for t, a in routing.items() if a == aile][0]
        runners[aile] = motor({hedef: m2cikti(bozuk)})
        try:
            run_total_rasyo_orchestrator(
                conn, analysis_at=ANALIZ, routing=routing,
                engine_runners=runners, run_id=sid)
        except Exception:
            return
        raise InvariantIhlali(f"{mod} kabul edildi")

    if mod == "naive_kesim":
        try:
            run_total_rasyo_orchestrator(
                conn, analysis_at=datetime(2026, 8, 5, 20, 0), routing=routing,
                engine_runners=_runners(routing), run_id=sid)
        except OrchestratorError:
            return
        raise InvariantIhlali("naive analysis_at kabul edildi")

    if mod == "bozuk_routing":
        try:
            run_total_rasyo_orchestrator(
                conn, analysis_at=ANALIZ, routing={"GARAN": "CRYPTO"},
                engine_runners={}, run_id=sid)
        except OrchestratorError:
            return
        raise InvariantIhlali("gecersiz sektor ailesi kabul edildi")

    # hedef_disi
    _kur_moduller(conn, routing, rng)
    try:
        run_total_rasyo_orchestrator(
            conn, analysis_at=ANALIZ, routing=routing,
            engine_runners=_runners(routing), run_id=sid,
            targeted_tickers=["YOKBUTICKER"])
    except OrchestratorError:
        return
    raise InvariantIhlali("evrende olmayan hedef kabul edildi")


def sc_karma_yuk(conn, rng, sid):
    """
    KARMA YUK: motor cokmesi + eksik modul + gelecek satir + cakisma +
    hedefli kosu ayni kosuda. Gercek arizalar tek tek degil UST USTE gelir;
    etkilesimleri yalniz boyle gorunur.
    """
    routing = _routing(rng, rng.randint(3, 8))
    tickerlar = sorted(routing)

    eksikler = {}
    for t in tickerlar:
        d = _tam_degerler(rng)
        eksik = set()
        if rng.random() < 0.4:
            k = rng.choice(list(READ_MODULE_KEYS))
            d[k] = None
            eksik.add(k)
        good = rng.randint(0, 12)
        if rng.random() < 0.2:
            good = None
            eksik.add("good_count_ge8")
        # Gelecek satir da ekle: SECILMEMELI.
        modul_satiri(conn, t, asof=date(2026, 8, 8),
                     analysis_at=ANALIZ + timedelta(days=3),
                     degerler={k: SENTINEL_FUTURE for k in READ_MODULE_KEYS}, good=77)
        modul_satiri(conn, t, asof=date(2026, 8, 5),
                     analysis_at=ANALIZ - timedelta(hours=1),
                     degerler=d, good=good)
        eksikler[t] = eksik

    aileler = sorted(set(routing.values()))
    cokenler = tuple(rng.sample(aileler, rng.randint(0, max(0, len(aileler) - 1)))) \
        if len(aileler) > 1 else ()
    runners = _runners(routing, coken_aileler=cokenler)

    # Cakisma enjekte et.
    cakisan = None
    if rng.random() < 0.5:
        adaylar = [t for t in tickerlar if routing[t] not in cokenler]
        if adaylar:
            cakisan = rng.choice(adaylar)
            ikinci = rng.choice([a for a in FAMILIES if a != routing[cakisan]])
            onceki = runners.get(ikinci)
            mevcut = {} if onceki is None else (
                {} if ikinci in cokenler else onceki()["results"])
            if ikinci not in cokenler:
                runners[ikinci] = motor({**mevcut, cakisan: m2cikti(0.2)})
            else:
                cakisan = None

    hedef = None
    if rng.random() < 0.3:
        hedef = rng.sample(tickerlar, rng.randint(1, len(tickerlar)))

    rapor = run_total_rasyo_orchestrator(
        conn, analysis_at=ANALIZ, routing=routing, engine_runners=runners,
        run_id=sid, targeted_tickers=hedef)

    ortak_invariantlar(conn, rapor, routing)
    beklenen = set(hedef) if hedef else set(routing)
    inv_sirket_kaybolmaz(rapor, beklenen)

    for r in rapor["results"]:
        for k in READ_MODULE_KEYS:
            skor = r["modules"][k]["score"]
            if skor is not None and abs(skor - SENTINEL_FUTURE) < 1e-9:
                raise InvariantIhlali(f"{r['ticker']}.{k}: gelecek satir secildi")
        if r["ticker"] == cakisan:
            if r["total_rasyo_status"] != STATUS_ROUTING_CONFLICT:
                raise InvariantIhlali(f"{cakisan}: cakisma sessizce cozuldu")
            continue
        if routing[r["ticker"]] in cokenler:
            if r["total_rasyo_status"] != STATUS_ENGINE_CRASHED:
                raise InvariantIhlali(f"{r['ticker']}: coken motor sirketi degil")
            continue
        if eksikler[r["ticker"]] and r["total_rasyo_status"] == STATUS_OK:
            raise InvariantIhlali(f"{r['ticker']}: eksik bilesenle OK")


SCENARIOS: Mapping[str, Callable] = {
    "tam_basarili": sc_tam_basarili,
    "tek_motor_cokmesi": sc_tek_motor_cokmesi,
    "coklu_motor_cokmesi": sc_coklu_motor_cokmesi,
    "eksik_modul_kombinasyonu": sc_eksik_modul_kombinasyonu,
    "zaman_kesimi_ve_satir_butunlugu": sc_zaman_kesimi_ve_satir_butunlugu,
    "yonlendirme_catismasi": sc_yonlendirme_catismasi,
    "yeniden_calisma": sc_yeniden_calisma,
    "sira_degismezligi": sc_sira_degismezligi,
    "kalicilik_hata_enjeksiyonu": sc_kalicilik_hata_enjeksiyonu,
    "sinir_deger_ve_config_bypass": sc_sinir_deger_ve_config_bypass,
    "karma_yuk": sc_karma_yuk,
}


def _tam_plan() -> list[tuple[str, str, int]]:
    """
    Kanonik 15.000'lik plan. Senaryo kimligi -> (tur, tohum) eslemesi
    BURADA ve YALNIZ burada belirlenir; hicbir CLI secenegi onu degistirmez.
    Aksi halde `--replay S00277` baska bir senaryoyu calistirir ve
    "tekrar uretilebilir kimlik" vaadi bos cikar.
    """
    isler: list[tuple[str, str, int]] = []
    sira = 0
    for tur, adet in DISTRIBUTION:
        for _ in range(adet):
            isler.append((f"S{sira:05d}", tur, 1_000_003 + sira * 7919))
            sira += 1
    return isler


def plan(count: int) -> list[tuple[str, str, int]]:
    """
    Tam plandan alt kume. Her turden orantili pay alinir; kimlikler ve
    tohumlar tam plandakiyle AYNI kalir.
    """
    tam = _tam_plan()
    if count >= TOTAL:
        return tam
    oran = count / TOTAL
    gruplar: dict[str, list] = {}
    for is_ in tam:
        gruplar.setdefault(is_[1], []).append(is_)
    secilen: list[tuple[str, str, int]] = []
    for tur, adet in DISTRIBUTION:
        n = max(1, round(adet * oran))
        secilen.extend(gruplar[tur][:n])
    return sorted(secilen)


def calistir(conn, sid: str, tur: str, tohum: int) -> Optional[str]:
    rng = random.Random(tohum)
    temizle(conn)
    try:
        SCENARIOS[tur](conn, rng, sid)
        return None
    except InvariantIhlali as exc:
        return f"INVARIANT: {exc}"
    except Exception as exc:  # noqa: BLE001
        izi = traceback.format_exc(limit=3).strip().splitlines()[-1]
        return f"BEKLENMEYEN {type(exc).__name__}: {exc} | {izi}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Total Rasyo orkestratör oz denetimi")
    ap.add_argument("--count", type=int, default=TOTAL)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--replay", type=str, default=None,
                    help="tek senaryoyu kimligiyle yeniden calistir")
    ap.add_argument("--fail-fast", action="store_true")
    args = ap.parse_args(argv)

    conn = baglan()
    if conn is None:
        print("ATLANDI: PostgreSQL erisilemedi "
              "(TOTAL_RASYO_TEST_DSN / PGDATABASE tanimli degil).")
        return 2

    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('analytics.total_rasyo_run')")
        if cur.fetchone()[0] is None:
            print("ATLANDI: sql/027-030 uygulanmamis.")
            return 2

    isler = plan(args.count)
    if args.replay:
        isler = [i for i in isler if i[0] == args.replay]
        if not isler:
            print(f"senaryo bulunamadi: {args.replay}")
            return 2

    tur_sayaci: dict[str, dict[str, int]] = {}
    hatalar: list[dict[str, str]] = []
    for sid, tur, tohum in isler:
        girdi = tur_sayaci.setdefault(tur, {"gecti": 0, "kaldi": 0})
        hata = calistir(conn, sid, tur, tohum)
        if hata is None:
            girdi["gecti"] += 1
        else:
            girdi["kaldi"] += 1
            hatalar.append({"senaryo": sid, "tur": tur, "tohum": tohum,
                            "hata": hata})
            if args.fail_fast:
                break
        toplam = sum(g["gecti"] + g["kaldi"] for g in tur_sayaci.values())
        if toplam % 500 == 0:
            print(f"  ... {toplam}/{len(isler)}", flush=True)

    conn.close()

    gecti = sum(g["gecti"] for g in tur_sayaci.values())
    kaldi = sum(g["kaldi"] for g in tur_sayaci.values())
    print("\n" + "=" * 62)
    print("TOTAL RASYO ORKESTRATOR OZ DENETIMI")
    print("=" * 62)
    for tur, _ in DISTRIBUTION:
        g = tur_sayaci.get(tur, {"gecti": 0, "kaldi": 0})
        print(f"  {tur:34s} {g['gecti']:6d} / {g['gecti'] + g['kaldi']:6d}")
    print("-" * 62)
    print(f"  {'TOPLAM':34s} {gecti:6d} / {gecti + kaldi:6d}")
    if hatalar:
        print(f"\nBASARISIZ {len(hatalar)} senaryo (ilk 15):")
        for h in hatalar[:15]:
            print(f"  {h['senaryo']} [{h['tur']}] {h['hata']}")
        print("\nYeniden uretmek icin:")
        print(f"  python3 -m src.analytics.total_rasyo_self_audit "
              f"--replay {hatalar[0]['senaryo']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"total": gecti + kaldi, "passed": gecti, "failed": kaldi,
                       "by_type": tur_sayaci, "failures": hatalar[:200]},
                      fh, ensure_ascii=False, indent=2)
        print(f"\nJSON: {args.json}")

    return 0 if kaldi == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
