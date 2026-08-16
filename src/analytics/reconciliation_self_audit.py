#!/usr/bin/env python3
"""
V21 Reconciliation-1 OZ DENETIMI — 15.000 senaryo.

KAPSAM: SAF hesaplama katmani (reconcile_impact_vs_actual). V20'nin
change_impact_self_audit.py emsalinde: gercek PostgreSQL BAGLANTISI
GEREKTIRMEZ, hizli ve genis kombinasyon taramasi yapar.

SINIR (acikca belirtilir): idempotency ve immutability'nin GERCEK veritabani
davranisi burada DEGIL, tests/test_reconciliation_live.py (canli pytest) ve
reconciliation_e2e_audit.py'de (gercek zincir) kanitlanir. Bu dosyada
"idempotent_replay" ve "reconciliation_immutability" aileleri SOZLESME
DUZEYINDE (ayni girdi -> ayni kimlik/SHA; farkli girdi -> farkli SHA)
sinanir -- bu, DB'ye yazmadan da dogrulanabilir bir INVARIANT'tir ve gercek
DB davranisinin (idempotent INSERT, immutable UPDATE reddi) ON KOSULUDUR.

"Exception olmadi" BASARI SAYILMAZ; her senaryoda invariant'lar dogrulanir.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import traceback
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Optional

from src.analytics.reconciliation_impact_orchestrator import (
    FINDING_MISSING,
    FINDING_STALE,
    FINDING_UNEXPECTED,
    STATUS_INCOMPLETE,
    STATUS_MISMATCH,
    STATUS_PASS,
    ActualRow,
    ReconciliationError,
    reconcile_impact_vs_actual,
    reconciliation_sha256,
)

TZ = timezone(timedelta(hours=3))
KESIM = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
BASLA = KESIM
BITIR = KESIM + timedelta(seconds=5)

TICKER_HAVUZU = tuple(f"T{n:03d}" for n in range(200))

DISTRIBUTION: tuple[tuple[str, int], ...] = (
    ("kume_buyukluk_cesitliligi", 2000),
    ("yalniz_missing", 1500),
    ("yalniz_unexpected", 1500),
    ("yalniz_stale", 1500),
    ("karma_missing_unexpected_stale", 2000),
    ("pending_incomplete", 1000),
    ("bos_expected_veya_actual", 1000),
    ("yanlis_orkestratör_kimligi", 1000),
    ("stale_check_performed_sozlesmesi", 1000),
    ("idempotent_replay_sozlesmesi", 1500),
    ("immutability_sozlesmesi", 1000),
)
TOTAL = sum(n for _, n in DISTRIBUTION)
assert TOTAL == 15000, f"dagilim toplami 15000 olmali, {TOTAL} bulundu"


class InvariantIhlali(AssertionError):
    pass


def kimlik(n: int, rng: random.Random) -> str:
    return f"P{n:03d}"


def _kos(rng, expected, actual_rows, *, app_status="APPLIED",
        app_id="APP-1", plan_id=None, orch_id="ORCH-1"):
    return reconcile_impact_vs_actual(
        application_run_id=app_id, impact_plan_id=plan_id or ("P" * 64),
        analysis_at=KESIM, started_at=BASLA, finished_at=BITIR,
        application_status=app_status, expected_tickers=expected,
        actual_rows=actual_rows, orchestrator_run_id=orch_id)


def _tickers(rng, n):
    return set(rng.sample(TICKER_HAVUZU, min(n, len(TICKER_HAVUZU))))


# ================================================================== invariants
def inv_ayrik_ve_tam(r):
    """MISSING/UNEXPECTED/STALE birbirini DISLAR (bir ticker ikisinde birden olamaz)."""
    tumu = [f.ticker for f in r.findings]
    if len(tumu) != len(set(tumu)):
        raise InvariantIhlali("bir ticker birden fazla bulgu turunde")
    for t in r.missing():
        if t in r.actual:
            raise InvariantIhlali(f"{t} MISSING ama actual'da mevcut")
    for t in r.unexpected():
        if t in r.expected:
            raise InvariantIhlali(f"{t} UNEXPECTED ama expected'te mevcut")
    for t in r.stale():
        if t not in r.expected or t not in r.actual:
            raise InvariantIhlali(f"{t} STALE ama kesisimde degil")


def inv_pass_bos_bulgu(r):
    if r.status == STATUS_PASS and r.findings:
        raise InvariantIhlali("PASS ama bulgu var")
    if r.status == STATUS_MISMATCH and not r.findings:
        raise InvariantIhlali("MISMATCH ama bulgu yok")


def inv_kimlik_kumesi_sayi_degil(r):
    """
    Ayni BUYUKLUKTE farkli kumeler PASS uretmemeli. Sayi karsilastirmasi
    bunu kacirirdi.
    """
    if len(r.expected) == len(r.actual) and set(r.expected) != set(r.actual):
        if r.status == STATUS_PASS:
            raise InvariantIhlali("ayni buyuklukte farkli kume PASS verdi")


def ortak(r):
    inv_ayrik_ve_tam(r)
    inv_pass_bos_bulgu(r)
    inv_kimlik_kumesi_sayi_degil(r)


# ================================================================== senaryolar
def sc_kume_buyukluk_cesitliligi(rng, sid):
    n = rng.choice((0, 1, 2, 5, 20, 100))
    kume = _tickers(rng, n)
    actual = [ActualRow(t, "ORCH-1") for t in kume]
    r = _kos(rng, kume, actual, plan_id=sid.zfill(64)[-64:] if False else None)
    ortak(r)
    if kume and r.status != STATUS_PASS:
        raise InvariantIhlali("tam eslesme PASS vermedi")


def sc_yalniz_missing(rng, sid):
    tam = _tickers(rng, rng.randint(2, 30))
    eksik_sayi = rng.randint(1, len(tam))
    kalan = set(rng.sample(sorted(tam), len(tam) - eksik_sayi))
    actual = [ActualRow(t, "ORCH-1") for t in kalan]
    r = _kos(rng, tam, actual)
    ortak(r)
    if set(r.missing()) != (tam - kalan):
        raise InvariantIhlali("missing kumesi yanlis")
    if r.unexpected() or r.stale():
        raise InvariantIhlali("yalniz missing beklenirken baska bulgu cikti")


def sc_yalniz_unexpected(rng, sid):
    beklenen = _tickers(rng, rng.randint(1, 20))
    fazla = _tickers(rng, rng.randint(1, 10)) - beklenen
    actual = [ActualRow(t, "ORCH-1") for t in beklenen | fazla]
    r = _kos(rng, beklenen, actual)
    ortak(r)
    if set(r.unexpected()) != fazla:
        raise InvariantIhlali("unexpected kumesi yanlis")
    if r.missing() or r.stale():
        raise InvariantIhlali("yalniz unexpected beklenirken baska bulgu cikti")


def sc_yalniz_stale(rng, sid):
    kume = _tickers(rng, rng.randint(1, 15))
    ezilen = _tickers(rng, rng.randint(1, len(kume))) & kume
    actual = [ActualRow(t, "BASKA-RUN" if t in ezilen else "ORCH-1") for t in kume]
    r = _kos(rng, kume, actual)
    ortak(r)
    if set(r.stale()) != ezilen:
        raise InvariantIhlali("stale kumesi yanlis")
    if r.missing() or r.unexpected():
        raise InvariantIhlali("yalniz stale beklenirken baska bulgu cikti")


def sc_karma(rng, sid):
    ortak_kume = _tickers(rng, rng.randint(3, 15))
    eksik = _tickers(rng, rng.randint(0, 5))
    fazla = _tickers(rng, rng.randint(0, 5)) - ortak_kume - eksik
    ezilen = set(rng.sample(sorted(ortak_kume), rng.randint(0, len(ortak_kume))))
    beklenen = ortak_kume | eksik
    gercek_kume = (ortak_kume - eksik) | fazla
    actual = [ActualRow(t, "BASKA-RUN" if t in ezilen and t in gercek_kume else "ORCH-1")
              for t in gercek_kume]
    r = _kos(rng, beklenen, actual)
    ortak(r)
    if set(r.missing()) != (beklenen - gercek_kume):
        raise InvariantIhlali("karma: missing yanlis")
    if set(r.unexpected()) != (gercek_kume - beklenen):
        raise InvariantIhlali("karma: unexpected yanlis")


def sc_pending_incomplete(rng, sid):
    kume = _tickers(rng, rng.randint(0, 10))
    actual = [ActualRow(t, "ORCH-1") for t in rng.sample(
        sorted(kume) or ["X"], rng.randint(0, len(kume)))]
    r = _kos(rng, kume, actual, app_status="PENDING")
    if r.status != STATUS_INCOMPLETE:
        raise InvariantIhlali("PENDING kosu INCOMPLETE vermedi")
    if r.findings:
        raise InvariantIhlali("INCOMPLETE'de bulgu uretilmemeli (yargi ertelenir)")
    if r.status == STATUS_MISMATCH:
        raise InvariantIhlali("PENDING iken MISMATCH densin")
    if r.stale_check_performed:
        raise InvariantIhlali("PENDING'de stale_check_performed True olamaz")


def sc_bos_expected_veya_actual(rng, sid):
    if rng.random() < 0.5:
        r = _kos(rng, set(), [])
        if r.status != STATUS_PASS:
            raise InvariantIhlali("ikisi de bos iken PASS beklenirdi")
    else:
        kume = _tickers(rng, rng.randint(1, 10))
        if rng.random() < 0.5:
            r = _kos(rng, kume, [])
            if set(r.missing()) != kume:
                raise InvariantIhlali("bos actual: tum kume missing olmali")
        else:
            r = _kos(rng, set(), [ActualRow(t, "ORCH-1") for t in kume])
            if set(r.unexpected()) != kume:
                raise InvariantIhlali("bos expected: tum kume unexpected olmali")
    ortak(r)


def sc_yanlis_orkestratör_kimligi(rng, sid):
    kume = _tickers(rng, rng.randint(1, 10))
    dogru_id = f"ORCH-{sid}"
    yanlis_id = f"ORCH-YANLIS-{sid}"
    actual = [ActualRow(t, yanlis_id) for t in kume]
    r = _kos(rng, kume, actual, orch_id=dogru_id)
    ortak(r)
    if set(r.stale()) != kume:
        raise InvariantIhlali("yanlis orkestratör kimligi STALE olarak isaretlenmedi")
    if r.stale_check_performed is not True:
        raise InvariantIhlali("orch_id verilmisken stale_check_performed False")


def sc_stale_check_performed(rng, sid):
    kume = _tickers(rng, rng.randint(1, 8))
    actual = [ActualRow(t, "RASTGELE-RUN") for t in kume]
    with_check = _kos(rng, kume, actual, orch_id="ORCH-X")
    without_check = _kos(rng, kume, actual, orch_id=None)
    if with_check.stale_check_performed is not True:
        raise InvariantIhlali("orch_id verilince stale_check_performed True olmali")
    if without_check.stale_check_performed is not False:
        raise InvariantIhlali("orch_id verilmeyince stale_check_performed False olmali")
    if without_check.stale():
        raise InvariantIhlali("kontrol atlanmisken STALE bulgusu uretildi")
    if without_check.status == STATUS_PASS and with_check.status != STATUS_PASS:
        # Ayni veri, kontrol acikken STALE cikarabilir; bu farkin GORUNUR
        # olmasi gerekir -- iki sonucun sha'si FARKLI olmali.
        if reconciliation_sha256(with_check) == reconciliation_sha256(without_check):
            raise InvariantIhlali("stale_check_performed farki SHA'ya yansimadi")


def sc_idempotent_replay(rng, sid):
    """
    SOZLESME DUZEYI: ayni girdi -> ayni kimlik VE ayni SHA (DB'siz kanit).
    Gercek DB idempotency'si tests/test_reconciliation_live.py'de kanitlanir.
    """
    kume = _tickers(rng, rng.randint(0, 15))
    actual = [ActualRow(t, "ORCH-1") for t in kume]
    app_id = f"APP-{sid}"
    a = _kos(rng, kume, actual, app_id=app_id)
    b = _kos(rng, kume, actual, app_id=app_id)
    if a.reconciliation_run_id != b.reconciliation_run_id:
        raise InvariantIhlali("ayni girdi farkli kimlik uretti")
    if reconciliation_sha256(a) != reconciliation_sha256(b):
        raise InvariantIhlali("ayni girdi farkli SHA uretti")
    # Farkli application_run_id -> farkli kimlik (karisma yok).
    c = _kos(rng, kume, actual, app_id=app_id + "-FARKLI")
    if c.reconciliation_run_id == a.reconciliation_run_id:
        raise InvariantIhlali("farkli application_run_id ayni kimlik uretti")


def sc_immutability_sozlesmesi(rng, sid):
    """
    SOZLESME DUZEYI: ayni kimlik + FARKLI icerik -> FARKLI SHA (bu, DB
    katmaninin ImpactPlanConflict/ReconciliationConflict ile REDDETMESI
    icin on kosuldur -- SHA ayni olsaydi catisma hic tespit edilemezdi).
    """
    kume = _tickers(rng, rng.randint(2, 15))
    app_id = f"APP-{sid}"
    a = _kos(rng, kume, [ActualRow(t, "ORCH-1") for t in kume], app_id=app_id)
    # EKLENEN ticker'in GERCEKTEN yeni oldugundan emin ol; havuzdan rastgele
    # secim zaten kumede olan bir ticker'i secebilir ve icerik degismemis
    # gorunurdu (ilk surumde S14006/S14012 bunu yakaladi).
    disarida = [t for t in TICKER_HAVUZU if t not in kume]
    if not disarida:
        return
    degisik_kume = kume | {rng.choice(disarida)}
    assert degisik_kume != kume, "kume gercekten degismeli"
    b = _kos(rng, degisik_kume,
            [ActualRow(t, "ORCH-1") for t in degisik_kume], app_id=app_id)
    if a.reconciliation_run_id != b.reconciliation_run_id:
        raise InvariantIhlali("ayni attempt farkli kimlik uretti (test kurulumu bozuk)")
    if reconciliation_sha256(a) == reconciliation_sha256(b):
        raise InvariantIhlali(
            "farkli icerik ayni SHA uretti -- DB catisma tespiti calismazdi")


SCENARIOS: Mapping[str, Callable] = {
    "kume_buyukluk_cesitliligi": sc_kume_buyukluk_cesitliligi,
    "yalniz_missing": sc_yalniz_missing,
    "yalniz_unexpected": sc_yalniz_unexpected,
    "yalniz_stale": sc_yalniz_stale,
    "karma_missing_unexpected_stale": sc_karma,
    "pending_incomplete": sc_pending_incomplete,
    "bos_expected_veya_actual": sc_bos_expected_veya_actual,
    "yanlis_orkestratör_kimligi": sc_yanlis_orkestratör_kimligi,
    "stale_check_performed_sozlesmesi": sc_stale_check_performed,
    "idempotent_replay_sozlesmesi": sc_idempotent_replay,
    "immutability_sozlesmesi": sc_immutability_sozlesmesi,
}


def _tam_plan() -> list[tuple[str, str, int]]:
    isler: list[tuple[str, str, int]] = []
    sira = 0
    for tur, adet in DISTRIBUTION:
        for _ in range(adet):
            isler.append((f"S{sira:05d}", tur, 3_000_017 + sira * 4111))
            sira += 1
    return isler


def plan(count: int) -> list[tuple[str, str, int]]:
    tam = _tam_plan()
    if count >= TOTAL:
        return tam
    oran = count / TOTAL
    gruplar: dict[str, list] = {}
    for is_ in tam:
        gruplar.setdefault(is_[1], []).append(is_)
    secilen: list[tuple[str, str, int]] = []
    for tur, adet in DISTRIBUTION:
        secilen.extend(gruplar[tur][:max(1, round(adet * oran))])
    return sorted(secilen)


def calistir(sid: str, tur: str, tohum: int) -> Optional[str]:
    rng = random.Random(tohum)
    try:
        SCENARIOS[tur](rng, sid)
        return None
    except InvariantIhlali as exc:
        return f"INVARIANT: {exc}"
    except ReconciliationError as exc:
        return f"KONTROLSUZ ReconciliationError: {exc}"
    except Exception as exc:  # noqa: BLE001
        izi = traceback.format_exc(limit=3).strip().splitlines()[-1]
        return f"BEKLENMEYEN {type(exc).__name__}: {exc} | {izi}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V21 Reconciliation-1 oz denetimi")
    ap.add_argument("--count", type=int, default=TOTAL)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--replay", type=str, default=None)
    args = ap.parse_args(argv)

    isler = plan(args.count)
    if args.replay:
        isler = [i for i in isler if i[0] == args.replay]
        if not isler:
            print(f"senaryo bulunamadi: {args.replay}")
            return 2

    tur_sayaci: dict[str, dict[str, int]] = {}
    hatalar: list[dict] = []
    for sid, tur, tohum in isler:
        girdi = tur_sayaci.setdefault(tur, {"gecti": 0, "kaldi": 0})
        hata = calistir(sid, tur, tohum)
        if hata is None:
            girdi["gecti"] += 1
        else:
            girdi["kaldi"] += 1
            hatalar.append({"senaryo": sid, "tur": tur, "tohum": tohum, "hata": hata})

    gecti = sum(g["gecti"] for g in tur_sayaci.values())
    kaldi = sum(g["kaldi"] for g in tur_sayaci.values())
    print("\n" + "=" * 62)
    print("V21 RECONCILIATION-1 OZ DENETIMI")
    print("=" * 62)
    for tur, _ in DISTRIBUTION:
        g = tur_sayaci.get(tur, {"gecti": 0, "kaldi": 0})
        print(f"  {tur:36s} {g['gecti']:6d} / {g['gecti'] + g['kaldi']:6d}")
    print("-" * 62)
    print(f"  {'TOPLAM':36s} {gecti:6d} / {gecti + kaldi:6d}")
    if hatalar:
        print(f"\nBASARISIZ {len(hatalar)} senaryo (ilk 15):")
        for h in hatalar[:15]:
            print(f"  {h['senaryo']} [{h['tur']}] {h['hata']}")
        print("\nYeniden uretmek icin:")
        print(f"  python3 -m src.analytics.reconciliation_self_audit "
              f"--replay {hatalar[0]['senaryo']}")

    if args.json:
        calisan_dagilim = {tur: g["gecti"] + g["kaldi"] for tur, g in tur_sayaci.items()}
        toplam = gecti + kaldi
        assert sum(calisan_dagilim.values()) == toplam
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"total": toplam, "passed": gecti, "failed": kaldi,
                       "distribution": calisan_dagilim,
                       "planned_distribution": {k: v for k, v in DISTRIBUTION},
                       "is_full_run": toplam == TOTAL,
                       "by_type": tur_sayaci, "failures": hatalar[:200]},
                      fh, ensure_ascii=False, indent=2)
        print(f"\nJSON: {args.json}")

    return 0 if kaldi == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
