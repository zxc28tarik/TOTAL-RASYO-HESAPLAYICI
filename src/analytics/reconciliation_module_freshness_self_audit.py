#!/usr/bin/env python3
"""
V22-B Reconciliation-2 OZ DENETIMI — 15.000 senaryo.

KAPSAM: SAF hesaplayici (reconcile_module_freshness). V20/V21 emsalinde:
GERCEK PostgreSQL BAGLANTISI GEREKTIRMEZ. Toplayici/kalicilik katmanlarinin
GERCEK davranisi tests/test_reconciliation_module_freshness_live.py'de
(canli pytest) ve reconciliation_module_freshness_e2e_audit.py'de
(gercek zincir) kanitlanir.

"Exception olmadi" BASARI SAYILMAZ; her senaryoda invariant'lar dogrulanir.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Optional

from src.analytics.reconciliation_module_freshness import (
    FINDING_LINEAGE_STALE,
    FINDING_MISSING,
    FINDING_TOTAL_STALE,
    REASON_IDENTITY_UNAVAILABLE,
    REASON_MODULE_MISSING,
    REASON_NO_BASELINE_CONTEXT,
    STATUS_INCOMPLETE,
    STATUS_MISMATCH,
    STATUS_PASS,
    ConsumedModule,
    ProducerSuccessor,
    reconcile_module_freshness,
    reconciliation_sha256,
)
from src.analytics.total_rasyo_score import MODULE_KEYS

TZ = timezone(timedelta(hours=3))
ANALIZ = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
BASLA = ANALIZ
BITIR = ANALIZ + timedelta(seconds=5)
KAYNAK = ANALIZ - timedelta(hours=1)

DISTRIBUTION: tuple[tuple[str, int], ...] = (
    ("tam_temiz_pass", 1500),
    ("missing_module", 1500),
    ("total_stale_only", 1500),
    ("lineage_stale_only", 1500),
    ("total_ve_lineage_birlikte", 1500),
    ("identity_known_false_cesitli", 1000),
    ("m2_ozel_durum", 1000),
    ("incomplete_kanit_yok", 1000),
    ("fully_verified_sinir", 1500),
    ("idempotency_sha_sozlesmesi", 1000),
    ("look_ahead_siniri", 1000),
    ("karma_coklu_modul", 1000),
)
TOTAL = sum(n for _, n in DISTRIBUTION)
assert TOTAL == 15000, f"dagilim toplami 15000 olmali, {TOTAL} bulundu"


class InvariantIhlali(AssertionError):
    pass


def temiz_modul(rng, module, *, missing=False, identity_known=True,
                source_run_key=None):
    if missing:
        return ConsumedModule(module, True, None, None, None, False)
    return ConsumedModule(
        module=module, missing=False, source_at=KAYNAK, analysis_at=KAYNAK,
        source_run_key=source_run_key or f"RUN-{rng.randint(1, 999999)}",
        identity_known=identity_known)


def temiz_successor(module, *, newer=False, same_label_key=None,
                    freshness_available=True, lineage_available=True):
    return ProducerSuccessor(module, newer, same_label_key, freshness_available,
                             lineage_available)


def tam_girdiler(rng):
    consumed = {m: temiz_modul(rng, m) for m in MODULE_KEYS}
    successors = {
        m: temiz_successor(m, same_label_key=consumed[m].source_run_key)
        for m in MODULE_KEYS if m != "M2"
    }
    successors["M2"] = temiz_successor("M2", lineage_available=False)
    return consumed, successors


def kos(consumed, successors, *, evidence=True, run_id="RUN-A", ticker="AAA"):
    return reconcile_module_freshness(
        total_rasyo_run_id=run_id, ticker=ticker, analysis_at=ANALIZ,
        started_at=BASLA, finished_at=BITIR, consumed_modules=consumed,
        successors=successors, evidence_available=evidence)


def ortak_invariantlar(r):
    for modul, c in r.checks.items():
        if c.missing:
            if c.freshness_performed or c.lineage_performed:
                raise InvariantIhlali(f"{modul}: eksikken kontrol yapilmis")
            if c.total_stale is not None or c.lineage_stale is not None:
                raise InvariantIhlali(f"{modul}: eksikken sonuc alani dolu")
        if not c.freshness_performed and c.total_stale is not None:
            raise InvariantIhlali(f"{modul}: freshness yapilmadan sonuc var")
        if not c.lineage_performed and c.lineage_stale is not None:
            raise InvariantIhlali(f"{modul}: lineage yapilmadan sonuc var")
        if modul == "M2" and c.lineage_performed:
            raise InvariantIhlali("M2 icin lineage YAPILMIS gorunuyor")
    if r.status == STATUS_PASS and any(c.findings() for c in r.checks.values()):
        raise InvariantIhlali("PASS ama bulgu var")
    if r.status == STATUS_MISMATCH and not any(c.findings() for c in r.checks.values()):
        raise InvariantIhlali("MISMATCH ama bulgu yok")


# ================================================================== senaryolar
def sc_tam_temiz_pass(rng, sid):
    consumed, successors = tam_girdiler(rng)
    r = kos(consumed, successors)
    ortak_invariantlar(r)
    if r.status != STATUS_PASS or r.fully_verified is not True:
        raise InvariantIhlali("tam temiz girdi PASS+fully_verified vermedi")


def sc_missing_module(rng, sid):
    consumed, successors = tam_girdiler(rng)
    kayip = rng.sample(list(MODULE_KEYS), rng.randint(1, 3))
    for m in kayip:
        consumed[m] = temiz_modul(rng, m, missing=True)
    r = kos(consumed, successors)
    ortak_invariantlar(r)
    if r.status != STATUS_MISMATCH:
        raise InvariantIhlali("eksik modul MISMATCH vermedi")
    if set(r.missing_modules()) != set(kayip):
        raise InvariantIhlali("missing_modules kumesi yanlis")
    for m in kayip:
        if r.checks[m].freshness_reason != REASON_MODULE_MISSING:
            raise InvariantIhlali(f"{m}: yanlis freshness_reason")


def sc_total_stale_only(rng, sid):
    consumed, successors = tam_girdiler(rng)
    hedef = rng.choice([m for m in MODULE_KEYS if m != "M2"])
    successors[hedef] = temiz_successor(hedef, newer=True,
                                        same_label_key=consumed[hedef].source_run_key)
    r = kos(consumed, successors)
    ortak_invariantlar(r)
    if hedef not in r.total_stale_modules():
        raise InvariantIhlali("TOTAL_STALE yakalanmadi")
    if hedef in r.lineage_stale_modules():
        raise InvariantIhlali("yalniz total_stale beklenirken lineage_stale cikti")


def sc_lineage_stale_only(rng, sid):
    consumed, successors = tam_girdiler(rng)
    hedef = rng.choice([m for m in MODULE_KEYS if m != "M2"])
    successors[hedef] = temiz_successor(hedef, newer=False,
                                        same_label_key="FARKLI-KIMLIK")
    r = kos(consumed, successors)
    ortak_invariantlar(r)
    if hedef not in r.lineage_stale_modules():
        raise InvariantIhlali("MODULE_LINEAGE_STALE yakalanmadi")
    if hedef in r.total_stale_modules():
        raise InvariantIhlali("yalniz lineage_stale beklenirken total_stale cikti")


def sc_total_ve_lineage_birlikte(rng, sid):
    """Ayni modulde ikisi de true olabilir; birbirinden BAGIMSIZDIR."""
    consumed, successors = tam_girdiler(rng)
    hedef = rng.choice([m for m in MODULE_KEYS if m != "M2"])
    successors[hedef] = temiz_successor(hedef, newer=True,
                                        same_label_key="FARKLI-KIMLIK")
    r = kos(consumed, successors)
    ortak_invariantlar(r)
    if not (r.checks[hedef].total_stale and r.checks[hedef].lineage_stale):
        raise InvariantIhlali("ikisi de true olmasi beklenirken biri eksik")


def sc_identity_known_false(rng, sid):
    consumed, successors = tam_girdiler(rng)
    hedef = rng.choice([m for m in MODULE_KEYS if m != "M2"])
    consumed[hedef] = temiz_modul(rng, hedef, identity_known=False)
    r = kos(consumed, successors)
    ortak_invariantlar(r)
    c = r.checks[hedef]
    if c.lineage_performed:
        raise InvariantIhlali("identity_known=false iken lineage YAPILMIS")
    if c.lineage_stale is not None:
        raise InvariantIhlali("identity_known=false iken lineage_stale HUKUM VERDI")
    if c.lineage_reason != REASON_IDENTITY_UNAVAILABLE:
        raise InvariantIhlali("yanlis lineage_reason")


def sc_m2_ozel_durum(rng, sid):
    consumed, successors = tam_girdiler(rng)
    r = kos(consumed, successors)
    ortak_invariantlar(r)
    m2 = r.checks["M2"]
    if m2.lineage_performed:
        raise InvariantIhlali("M2 lineage YAPILMIS")
    if m2.freshness_performed is not True:
        raise InvariantIhlali("M2 freshness yapilamadi (beklenen: yapilabilir)")
    # M2'nin lineage'i mimari olarak yapilamadigi icin bu TEK BASINA
    # fully_verified'i False'a CEKMEMELI.
    if r.status == STATUS_PASS and r.fully_verified is not True:
        raise InvariantIhlali("M2 lineage eksikligi fully_verified'i bozdu")

    # MIMARI ZORUNLULUK -- CIFT BARIYERI AYNI ANDA GECERSIZ KIL: M2'ye
    # yanlislikla identity_known=True VE lineage_lookup_available=True
    # verilse BILE (toplayicida iki bagimsiz hata birlikte olsa), saf
    # hesaplayici KENDISI M2 lineage'ini engellemek ZORUNDADIR. Bu, yalniz
    # tam_girdiler()'in varsayilan (lineage_lookup_available=False)
    # korumasina guvenmeyen, IZOLE bir sinama.
    bozuk_consumed = dict(consumed)
    bozuk_consumed["M2"] = temiz_modul(rng, "M2", identity_known=True,
                                       source_run_key="YANLISLIKLA")
    bozuk_successors = dict(successors)
    bozuk_successors["M2"] = temiz_successor(
        "M2", same_label_key="YANLISLIKLA", lineage_available=True)
    r2 = kos(bozuk_consumed, bozuk_successors)
    if r2.checks["M2"].lineage_performed:
        raise InvariantIhlali(
            "cift bariyer gecersiz kilindiginda M2 lineage YAPILDI -- "
            "hesaplayicinin kendi zorlamasi calismiyor")
    if r2.checks["M2"].lineage_stale is not None:
        raise InvariantIhlali("M2 lineage_stale HUKUM VERDI")


def sc_incomplete(rng, sid):
    r = kos({}, {}, evidence=False)
    if r.status != STATUS_INCOMPLETE:
        raise InvariantIhlali("kanit yokken INCOMPLETE donmedi")
    if r.fully_verified is not None:
        raise InvariantIhlali("INCOMPLETE'de fully_verified None olmali")
    if r.status == STATUS_MISMATCH or r.status == STATUS_PASS:
        raise InvariantIhlali("INCOMPLETE, PASS/MISMATCH ile karisti")


def sc_fully_verified_sinir(rng, sid):
    consumed, successors = tam_girdiler(rng)
    if rng.random() < 0.5:
        hedef = rng.choice([m for m in MODULE_KEYS if m != "M2"])
        consumed[hedef] = temiz_modul(rng, hedef, identity_known=False)
        r = kos(consumed, successors)
        ortak_invariantlar(r)
        if r.status == STATUS_PASS and r.fully_verified is not False:
            raise InvariantIhlali("eksik kapsamda fully_verified True kaldi")
    else:
        r = kos(consumed, successors)
        ortak_invariantlar(r)
        if r.status == STATUS_PASS and r.fully_verified is not True:
            raise InvariantIhlali("tam kapsamda fully_verified False oldu")


def sc_idempotency_sha(rng, sid):
    consumed, successors = tam_girdiler(rng)
    a = kos(consumed, successors, run_id=f"RUN-{sid}")
    b = kos(consumed, successors, run_id=f"RUN-{sid}")
    if a.reconciliation_run_id != b.reconciliation_run_id:
        raise InvariantIhlali("ayni girdi farkli kimlik uretti")
    if reconciliation_sha256(a) != reconciliation_sha256(b):
        raise InvariantIhlali("ayni girdi farkli SHA uretti")
    farkli = kos(consumed, successors, run_id=f"RUN-{sid}", ticker="BBB")
    if farkli.reconciliation_run_id == a.reconciliation_run_id:
        raise InvariantIhlali("farkli ticker ayni kimlik uretti")


def sc_look_ahead_siniri(rng, sid):
    """
    HALEF KURALI: successor'in newer_eligible_exists=True DEMESI, ozunde
    'analysis_at <= total_rasyo.analysis_at' sinirinin ZATEN toplayicida
    uygulandigini varsayar. Bu senaryo, sinirin ihlal EDILMEDIGI durumu
    (newer_eligible_exists yalniz gercekten uygun oldugunda True) dogrular.
    """
    consumed, successors = tam_girdiler(rng)
    hedef = rng.choice([m for m in MODULE_KEYS if m != "M2"])
    # Toplayici katmani zaten sinira uyuyor VARSAYIMIYLA newer=True verilir;
    # calistirici bunu KORMEDEN kabul etmemeli -- yalniz TOTAL_STALE uretmeli.
    successors[hedef] = temiz_successor(hedef, newer=True,
                                        same_label_key=consumed[hedef].source_run_key)
    r = kos(consumed, successors)
    ortak_invariantlar(r)
    if hedef not in r.total_stale_modules():
        raise InvariantIhlali("sinir icindeki successor STALE olarak isaretlenmedi")
    # newer=False durumunda ASLA stale uretilmemeli (look-ahead disi/yok).
    successors[hedef] = temiz_successor(hedef, newer=False,
                                        same_label_key=consumed[hedef].source_run_key)
    r2 = kos(consumed, successors)
    if hedef in r2.total_stale_modules():
        raise InvariantIhlali("newer=False iken TOTAL_STALE uretildi")


def sc_karma(rng, sid):
    consumed, successors = tam_girdiler(rng)
    modul_havuzu = [m for m in MODULE_KEYS if m != "M2"]
    kayip = rng.sample(modul_havuzu, 1)
    kalan = [m for m in modul_havuzu if m not in kayip]
    stale_hedef = rng.choice(kalan)
    lineage_hedef = rng.choice([m for m in kalan if m != stale_hedef] or kalan)

    for m in kayip:
        consumed[m] = temiz_modul(rng, m, missing=True)
    successors[stale_hedef] = temiz_successor(
        stale_hedef, newer=True, same_label_key=consumed[stale_hedef].source_run_key)
    successors[lineage_hedef] = temiz_successor(
        lineage_hedef, newer=False, same_label_key="FARKLI")

    r = kos(consumed, successors)
    ortak_invariantlar(r)
    if r.status != STATUS_MISMATCH:
        raise InvariantIhlali("karma senaryo MISMATCH vermedi")
    if set(r.missing_modules()) != set(kayip):
        raise InvariantIhlali("karma: missing kumesi yanlis")


SCENARIOS: Mapping[str, Callable] = {
    "tam_temiz_pass": sc_tam_temiz_pass,
    "missing_module": sc_missing_module,
    "total_stale_only": sc_total_stale_only,
    "lineage_stale_only": sc_lineage_stale_only,
    "total_ve_lineage_birlikte": sc_total_ve_lineage_birlikte,
    "identity_known_false_cesitli": sc_identity_known_false,
    "m2_ozel_durum": sc_m2_ozel_durum,
    "incomplete_kanit_yok": sc_incomplete,
    "fully_verified_sinir": sc_fully_verified_sinir,
    "idempotency_sha_sozlesmesi": sc_idempotency_sha,
    "look_ahead_siniri": sc_look_ahead_siniri,
    "karma_coklu_modul": sc_karma,
}


def _tam_plan() -> list[tuple[str, str, int]]:
    isler: list[tuple[str, str, int]] = []
    sira = 0
    for tur, adet in DISTRIBUTION:
        for _ in range(adet):
            isler.append((f"S{sira:05d}", tur, 4_000_037 + sira * 5077))
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
    except Exception as exc:  # noqa: BLE001
        izi = traceback.format_exc(limit=3).strip().splitlines()[-1]
        return f"BEKLENMEYEN {type(exc).__name__}: {exc} | {izi}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V22-B Reconciliation-2 oz denetimi")
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
    print("V22-B RECONCILIATION-2 OZ DENETIMI")
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
        print(f"  python3 -m src.analytics.reconciliation_module_freshness_self_audit"
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
