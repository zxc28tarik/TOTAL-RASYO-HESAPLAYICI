#!/usr/bin/env python3
"""
V23-B OZ DENETIMI — 15.000 senaryo.

KAPSAM: SAF hesaplayici (reconcile_pit_vs_restate). GERCEK PostgreSQL
BAGLANTISI GEREKTIRMEZ. Toplayici/kalicilik katmanlarinin gercek davranisi
tests/test_restate_pit_reconciliation_live.py'de (canli pytest) ve
E2E'de kanitlanir.

EN KRITIK KORUMA: compared_count == 0 iken status ASLA PASS/MISMATCH
olamaz -- mismatch_count == 0 TEK BASINA PASS uretmez.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import traceback
from typing import Callable, Mapping, Optional

from src.analytics.restate_pit_reconciliation import (
    FINDING_DECISION_CHANGED,
    FINDING_PIT_MISSING,
    FINDING_RESTATE_INCOMPLETE,
    FINDING_VALUE_CHANGED,
    PitSnapshot,
    RestatePitReconciliationError,
    RestateSnapshot,
    STATUS_INCOMPLETE,
    STATUS_MISMATCH,
    STATUS_PASS,
    reconcile_pit_vs_restate,
    reconciliation_sha256,
)

TICKER_HAVUZU = tuple(f"T{n:03d}" for n in range(200))

DISTRIBUTION: tuple[tuple[str, int], ...] = (
    ("tam_pit_missing", 1500),
    ("tam_restate_incomplete", 2000),
    ("karma_incomplete_pass", 1500),
    ("value_changed_yalniz", 1500),
    ("decision_changed_yalniz", 1500),
    ("ikisi_birlikte", 1000),
    ("bulgu_yok_pass", 1500),
    ("kucuk_ondalik_farki", 1000),
    ("karma_coklu_ticker", 2000),
    ("idempotency_sha_sozlesmesi", 1500),
)
TOTAL = sum(n for _, n in DISTRIBUTION)
assert TOTAL == 15000, f"dagilim toplami 15000 olmali, {TOTAL} bulundu"


class InvariantIhlali(AssertionError):
    pass


def pit_ok(rng, score=None, decision=None, run_id=None):
    return PitSnapshot(exists=True, total_rasyo_status="OK",
                       final_score=score if score is not None else rng.uniform(0, 1),
                       decision=decision or rng.choice(("AL", "IZLE", "UZAK")),
                       run_id=run_id or f"RUN-{rng.randint(1, 999999)}")


def restate_ok(rng, score=None, decision=None):
    return RestateSnapshot(exists=True, total_rasyo_status="OK",
                           final_score=score if score is not None else rng.uniform(0, 1),
                           decision=decision or rng.choice(("AL", "IZLE", "UZAK")))


def restate_incomplete():
    return RestateSnapshot(exists=True, total_rasyo_status="YETERSIZ_VERI")


def pit_yok():
    return PitSnapshot(exists=False)


def kos(pit, restate, sid, tickers=None):
    return reconcile_pit_vs_restate(
        restate_run_id=(sid * 8).ljust(64, "0")[:64] if isinstance(sid, str)
        else f"{sid:064x}",
        tickers=tickers or tuple(pit), pit_snapshots=pit, restate_snapshots=restate)


def ortak_invariantlar(r):
    if r.compared_count == 0 and r.status not in (STATUS_INCOMPLETE,):
        raise InvariantIhlali(
            f"compared_count=0 ama status={r.status} (EN KRITIK IHLAL)")
    if r.status == STATUS_PASS and r.compared_count == 0:
        raise InvariantIhlali("PASS ama compared_count=0 (kanit yok ama PASS)")
    if r.status == STATUS_PASS and r.mismatch_count != 0:
        raise InvariantIhlali("PASS ama mismatch_count != 0")
    if r.status == STATUS_MISMATCH and r.mismatch_count == 0:
        raise InvariantIhlali("MISMATCH ama mismatch_count = 0")
    if r.fully_verified and r.status != STATUS_PASS:
        raise InvariantIhlali("fully_verified=true ama status != PASS")
    if r.fully_verified and r.compared_count != len(r.tickers):
        raise InvariantIhlali("fully_verified=true ama tum ticker'lar karsilastirilmadi")
    for t, c in r.comparisons.items():
        if FINDING_PIT_MISSING in c.findings and c.compared:
            raise InvariantIhlali(f"{t}: PIT_MISSING ama compared=True")
        if FINDING_RESTATE_INCOMPLETE in c.findings and c.compared:
            raise InvariantIhlali(f"{t}: RESTATE_INCOMPLETE ama compared=True")
        if (FINDING_VALUE_CHANGED in c.findings
                or FINDING_DECISION_CHANGED in c.findings) and not c.compared:
            raise InvariantIhlali(f"{t}: VALUE/DECISION_CHANGED ama compared=False")


def sc_tam_pit_missing(rng, sid):
    n = rng.randint(1, 10)
    tks = rng.sample(TICKER_HAVUZU, n)
    pit = {t: pit_yok() for t in tks}
    restate = {t: restate_ok(rng) for t in tks}
    r = kos(pit, restate, sid, tks)
    ortak_invariantlar(r)
    if r.status != STATUS_INCOMPLETE:
        raise InvariantIhlali("hepsi PIT_MISSING iken INCOMPLETE beklenirdi")
    if r.pit_missing_count != n:
        raise InvariantIhlali("pit_missing_count yanlis")


def sc_tam_restate_incomplete(rng, sid):
    """EN KRITIK SENARYO: bugunku V23-A gercekligi."""
    n = rng.randint(1, 15)
    tks = rng.sample(TICKER_HAVUZU, n)
    pit = {t: pit_ok(rng) for t in tks}
    restate = {t: restate_incomplete() for t in tks}
    r = kos(pit, restate, sid, tks)
    ortak_invariantlar(r)
    if r.status != STATUS_INCOMPLETE:
        raise InvariantIhlali("hepsi RESTATE_INCOMPLETE iken INCOMPLETE beklenirdi")
    if r.status == STATUS_PASS:
        raise InvariantIhlali("KANIT YOK AMA PASS -- kritik sozlesme ihlali")
    if r.mismatch_count != 0:
        raise InvariantIhlali("sahte mismatch uretildi")


def sc_karma_incomplete_pass(rng, sid):
    n = rng.randint(3, 20)
    tks = rng.sample(TICKER_HAVUZU, n)
    incomplete_sayisi = rng.randint(1, n - 1)
    incomplete_tks = set(tks[:incomplete_sayisi])
    pit = {t: pit_ok(rng, score=0.5, decision="IZLE") for t in tks}
    restate = {t: (restate_incomplete() if t in incomplete_tks
                  else restate_ok(rng, score=0.5, decision="IZLE")) for t in tks}
    r = kos(pit, restate, sid, tks)
    ortak_invariantlar(r)
    beklenen_compared = n - incomplete_sayisi
    if r.compared_count != beklenen_compared:
        raise InvariantIhlali("compared_count yanlis")
    if r.status != STATUS_PASS:
        raise InvariantIhlali("temiz kismi karsilastirmada PASS beklenirdi")
    if r.fully_verified:
        raise InvariantIhlali("kismi kapsamda fully_verified=true olamaz")


def sc_value_changed_yalniz(rng, sid):
    t = rng.choice(TICKER_HAVUZU)
    pit = {t: pit_ok(rng, score=0.3, decision="IZLE")}
    restate = {t: restate_ok(rng, score=0.9, decision="IZLE")}
    r = kos(pit, restate, sid, [t])
    ortak_invariantlar(r)
    c = r.comparisons[t]
    if FINDING_VALUE_CHANGED not in c.findings or FINDING_DECISION_CHANGED in c.findings:
        raise InvariantIhlali("yalniz VALUE_CHANGED beklenirdi")
    if r.status != STATUS_MISMATCH:
        raise InvariantIhlali("MISMATCH beklenirdi")


def sc_decision_changed_yalniz(rng, sid):
    t = rng.choice(TICKER_HAVUZU)
    pit = {t: pit_ok(rng, score=0.5, decision="IZLE")}
    restate = {t: restate_ok(rng, score=0.5, decision="AL")}
    r = kos(pit, restate, sid, [t])
    ortak_invariantlar(r)
    c = r.comparisons[t]
    if FINDING_DECISION_CHANGED not in c.findings or FINDING_VALUE_CHANGED in c.findings:
        raise InvariantIhlali("yalniz DECISION_CHANGED beklenirdi")


def sc_ikisi_birlikte(rng, sid):
    t = rng.choice(TICKER_HAVUZU)
    pit = {t: pit_ok(rng, score=0.2, decision="UZAK")}
    restate = {t: restate_ok(rng, score=0.9, decision="AL")}
    r = kos(pit, restate, sid, [t])
    ortak_invariantlar(r)
    c = r.comparisons[t]
    if not (FINDING_VALUE_CHANGED in c.findings and FINDING_DECISION_CHANGED in c.findings):
        raise InvariantIhlali("ikisi de beklenirdi")


def sc_bulgu_yok_pass(rng, sid):
    n = rng.randint(1, 10)
    tks = rng.sample(TICKER_HAVUZU, n)
    pit = {}
    restate = {}
    for t in tks:
        skor = rng.uniform(0, 1)
        karar = rng.choice(("AL", "IZLE", "UZAK"))
        pit[t] = pit_ok(rng, score=skor, decision=karar)
        restate[t] = restate_ok(rng, score=skor, decision=karar)
    r = kos(pit, restate, sid, tks)
    ortak_invariantlar(r)
    if r.status != STATUS_PASS or r.fully_verified is not True:
        raise InvariantIhlali("tam temiz PASS+fully_verified beklenirdi")


def sc_kucuk_ondalik_farki(rng, sid):
    t = rng.choice(TICKER_HAVUZU)
    taban = rng.uniform(0.1, 0.9)
    pit = {t: pit_ok(rng, score=taban, decision="IZLE")}
    restate = {t: restate_ok(rng, score=taban + 1e-12, decision="IZLE")}
    r = kos(pit, restate, sid, [t])
    ortak_invariantlar(r)
    if FINDING_VALUE_CHANGED in r.comparisons[t].findings:
        raise InvariantIhlali("tolerans altindaki fark VALUE_CHANGED sayildi")


def sc_karma_coklu_ticker(rng, sid):
    n = rng.randint(5, 30)
    tks = rng.sample(TICKER_HAVUZU, n)
    pit, restate = {}, {}
    beklenen_mismatch = 0
    for t in tks:
        durum = rng.choice(("temiz", "pit_missing", "restate_incomplete",
                            "value", "decision"))
        if durum == "pit_missing":
            pit[t] = pit_yok()
            restate[t] = restate_ok(rng)
        elif durum == "restate_incomplete":
            pit[t] = pit_ok(rng)
            restate[t] = restate_incomplete()
        elif durum == "value":
            pit[t] = pit_ok(rng, score=0.1, decision="IZLE")
            restate[t] = restate_ok(rng, score=0.9, decision="IZLE")
            beklenen_mismatch += 1
        elif durum == "decision":
            pit[t] = pit_ok(rng, score=0.5, decision="IZLE")
            restate[t] = restate_ok(rng, score=0.5, decision="AL")
            beklenen_mismatch += 1
        else:
            skor, karar = rng.uniform(0, 1), rng.choice(("AL", "IZLE", "UZAK"))
            pit[t] = pit_ok(rng, score=skor, decision=karar)
            restate[t] = restate_ok(rng, score=skor, decision=karar)
    r = kos(pit, restate, sid, tks)
    ortak_invariantlar(r)
    if r.mismatch_count != beklenen_mismatch:
        raise InvariantIhlali(f"mismatch_count yanlis: {r.mismatch_count} != {beklenen_mismatch}")
    if beklenen_mismatch > 0 and r.status != STATUS_MISMATCH:
        raise InvariantIhlali("mismatch varken status MISMATCH degil")


def sc_idempotency_sha(rng, sid):
    t = rng.choice(TICKER_HAVUZU)
    pit = {t: pit_ok(rng, score=0.5, decision="IZLE")}
    restate = {t: restate_ok(rng, score=0.5, decision="IZLE")}
    rid = f"{sid:064x}"
    a = reconcile_pit_vs_restate(restate_run_id=rid, tickers=[t],
                                 pit_snapshots=pit, restate_snapshots=restate)
    b = reconcile_pit_vs_restate(restate_run_id=rid, tickers=[t],
                                 pit_snapshots=pit, restate_snapshots=restate)
    if a.reconciliation_run_id != b.reconciliation_run_id:
        raise InvariantIhlali("ayni girdi farkli kimlik uretti")
    if reconciliation_sha256(a) != reconciliation_sha256(b):
        raise InvariantIhlali("ayni girdi farkli SHA uretti")
    farkli_restate = {t: restate_ok(rng, score=0.99, decision="AL")}
    c = reconcile_pit_vs_restate(restate_run_id=rid, tickers=[t],
                                 pit_snapshots=pit, restate_snapshots=farkli_restate)
    if c.reconciliation_run_id != a.reconciliation_run_id:
        raise InvariantIhlali("ayni restate_run_id farkli kimlik uretti")
    if reconciliation_sha256(c) == reconciliation_sha256(a):
        raise InvariantIhlali("farkli icerik ayni SHA uretti")


SCENARIOS: Mapping[str, Callable] = {
    "tam_pit_missing": sc_tam_pit_missing,
    "tam_restate_incomplete": sc_tam_restate_incomplete,
    "karma_incomplete_pass": sc_karma_incomplete_pass,
    "value_changed_yalniz": sc_value_changed_yalniz,
    "decision_changed_yalniz": sc_decision_changed_yalniz,
    "ikisi_birlikte": sc_ikisi_birlikte,
    "bulgu_yok_pass": sc_bulgu_yok_pass,
    "kucuk_ondalik_farki": sc_kucuk_ondalik_farki,
    "karma_coklu_ticker": sc_karma_coklu_ticker,
    "idempotency_sha_sozlesmesi": sc_idempotency_sha,
}


def _tam_plan() -> list[tuple[str, str, int]]:
    isler: list[tuple[str, str, int]] = []
    sira = 0
    for tur, adet in DISTRIBUTION:
        for _ in range(adet):
            isler.append((f"S{sira:05d}", tur, 5_000_011 + sira * 6301))
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
        SCENARIOS[tur](rng, tohum)
        return None
    except InvariantIhlali as exc:
        return f"INVARIANT: {exc}"
    except RestatePitReconciliationError as exc:
        return f"KONTROLSUZ RestatePitReconciliationError: {exc}"
    except Exception as exc:  # noqa: BLE001
        izi = traceback.format_exc(limit=3).strip().splitlines()[-1]
        return f"BEKLENMEYEN {type(exc).__name__}: {exc} | {izi}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V23-B oz denetimi")
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
    print("V23-B PIT<->RESTATE RECONCILIATION OZ DENETIMI")
    print("=" * 62)
    for tur, _ in DISTRIBUTION:
        g = tur_sayaci.get(tur, {"gecti": 0, "kaldi": 0})
        print(f"  {tur:32s} {g['gecti']:6d} / {g['gecti'] + g['kaldi']:6d}")
    print("-" * 62)
    print(f"  {'TOPLAM':32s} {gecti:6d} / {gecti + kaldi:6d}")
    if hatalar:
        print(f"\nBASARISIZ {len(hatalar)} senaryo (ilk 15):")
        for h in hatalar[:15]:
            print(f"  {h['senaryo']} [{h['tur']}] {h['hata']}")
        print("\nYeniden uretmek icin:")
        print(f"  python3 -m src.analytics.restate_pit_reconciliation_self_audit"
              f" --replay {hatalar[0]['senaryo']}")

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
