"""
V23-B — SAF HESAPLAYICI: PIT <-> RESTATE reconciliation.

REPORT-ONLY. Otomatik duzeltici kosu BASLATMAZ.

KRITIK SOZLESME: Hicbir ticker GERCEKTEN karsilastirilamadiysa ust durum
PASS OLAMAZ. mismatch_count == 0 TEK BASINA PASS uretmez -- compared_count
>= 1 sarttir.

BULGULAR (birbirinden BAGIMSIZ, KARISMAZ):
  PIT_MISSING        : kanonik PIT satiri YOK (veya PIT OK degil).
  RESTATE_INCOMPLETE : RESTATE sonucu total_rasyo_status != OK.
  VALUE_CHANGED      : ikisi de mevcut VE OK, final_score farkli.
  DECISION_CHANGED   : ikisi de mevcut VE OK, decision farkli.

UST DURUM:
  PASS       : en az bir ticker GERCEKTEN karsilastirilmis VE hicbirinde
               mismatch yok.
  MISMATCH   : en az bir karsilastirilmis ticker'da VALUE/DECISION_CHANGED.
  INCOMPLETE : compared_count == 0.
  ERROR      : bu modul URETMEZ; cagiran katman istisna yakaladiginda atar.

fully_verified=true ANCAK hedef kumedeki HER ticker gercekten
karsilastirilmis VE mismatch yoksa.

V1 SINIRI (bilerek cozulmez, yalniz gorunur kilinir): PIT'in kendisi
sonradan MESRU bicimde degisebilir. Karsilastirilan PIT satirinin run_id'si
diagnostics'e TASINIR ama nedensellik SINIFLANDIRILMAZ.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

STATUS_PASS = "PASS"
STATUS_MISMATCH = "MISMATCH"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_ERROR = "ERROR"

FINDING_PIT_MISSING = "PIT_MISSING"
FINDING_RESTATE_INCOMPLETE = "RESTATE_INCOMPLETE"
FINDING_VALUE_CHANGED = "VALUE_CHANGED"
FINDING_DECISION_CHANGED = "DECISION_CHANGED"

RECONCILER_VERSION = 1
RECONCILIATION_TYPE = "PIT_VS_RESTATE"

_TOLERANCE = 1e-9


class RestatePitReconciliationError(ValueError):
    pass


@dataclass(frozen=True)
class PitSnapshot:
    exists: bool
    total_rasyo_status: Optional[str] = None
    final_score: Optional[float] = None
    decision: Optional[str] = None
    run_id: Optional[str] = None


@dataclass(frozen=True)
class RestateSnapshot:
    exists: bool
    total_rasyo_status: Optional[str] = None
    final_score: Optional[float] = None
    decision: Optional[str] = None


@dataclass(frozen=True)
class TickerComparison:
    ticker: str
    compared: bool
    findings: tuple[str, ...]
    pit_run_id: Optional[str] = None
    pit_final_score: Optional[float] = None
    restate_final_score: Optional[float] = None
    pit_decision: Optional[str] = None
    restate_decision: Optional[str] = None
    restate_status: Optional[str] = None

    def has_mismatch(self) -> bool:
        return (FINDING_VALUE_CHANGED in self.findings
                or FINDING_DECISION_CHANGED in self.findings)


@dataclass(frozen=True)
class RestatePitReconciliation:
    reconciliation_run_id: str
    reconciliation_type: str
    reconciler_version: int
    restate_run_id: str
    tickers: tuple[str, ...]
    comparisons: Mapping[str, TickerComparison]
    status: str
    fully_verified: bool
    compared_count: int
    mismatch_count: int
    pit_missing_count: int
    restate_incomplete_count: int
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def mismatched_tickers(self) -> tuple[str, ...]:
        return tuple(t for t, c in self.comparisons.items() if c.has_mismatch())

    def pit_missing_tickers(self) -> tuple[str, ...]:
        return tuple(t for t, c in self.comparisons.items()
                    if FINDING_PIT_MISSING in c.findings)

    def restate_incomplete_tickers(self) -> tuple[str, ...]:
        return tuple(t for t, c in self.comparisons.items()
                    if FINDING_RESTATE_INCOMPLETE in c.findings)


def _norm_ticker(t: str) -> str:
    if not isinstance(t, str) or not t.strip():
        raise RestatePitReconciliationError("ticker dolu metin olmali")
    return t.strip().upper()


def _reconciliation_run_id(restate_run_id: str, tickers: tuple[str, ...]) -> str:
    ham = json.dumps({
        "reconciliation_type": RECONCILIATION_TYPE,
        "reconciler_version": RECONCILER_VERSION,
        "restate_run_id": restate_run_id,
        "tickers": list(tickers),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def reconciliation_sha256(result: RestatePitReconciliation) -> str:
    govde = {
        "reconciliation_run_id": result.reconciliation_run_id,
        "status": result.status,
        "fully_verified": result.fully_verified,
        "comparisons": sorted(
            (t, c.compared, sorted(c.findings), c.pit_final_score,
             c.restate_final_score, c.pit_decision, c.restate_decision)
            for t, c in result.comparisons.items()),
    }
    ham = json.dumps(govde, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def _compare_ticker(ticker: str, pit: PitSnapshot,
                    restate: RestateSnapshot) -> TickerComparison:
    if not restate.exists:
        raise RestatePitReconciliationError(
            f"{ticker}: restate snapshot exists=False -- cagiran katman bu "
            "ticker'i hic gondermemeliydi")

    if not pit.exists:
        return TickerComparison(ticker=ticker, compared=False,
                                findings=(FINDING_PIT_MISSING,),
                                restate_status=restate.total_rasyo_status)

    if restate.total_rasyo_status != "OK":
        return TickerComparison(
            ticker=ticker, compared=False, findings=(FINDING_RESTATE_INCOMPLETE,),
            pit_run_id=pit.run_id, pit_final_score=pit.final_score,
            pit_decision=pit.decision, restate_status=restate.total_rasyo_status)

    if pit.total_rasyo_status != "OK":
        return TickerComparison(
            ticker=ticker, compared=False, findings=(FINDING_PIT_MISSING,),
            pit_run_id=pit.run_id, restate_status=restate.total_rasyo_status)

    bulgular = []
    deger_degisti = (
        pit.final_score is None or restate.final_score is None
        or abs(float(pit.final_score) - float(restate.final_score)) > _TOLERANCE)
    karar_degisti = pit.decision != restate.decision
    if deger_degisti:
        bulgular.append(FINDING_VALUE_CHANGED)
    if karar_degisti:
        bulgular.append(FINDING_DECISION_CHANGED)

    return TickerComparison(
        ticker=ticker, compared=True, findings=tuple(bulgular),
        pit_run_id=pit.run_id, pit_final_score=pit.final_score,
        restate_final_score=restate.final_score, pit_decision=pit.decision,
        restate_decision=restate.decision, restate_status=restate.total_rasyo_status)


def reconcile_pit_vs_restate(
    *,
    restate_run_id: str,
    tickers,
    pit_snapshots: Mapping[str, PitSnapshot],
    restate_snapshots: Mapping[str, RestateSnapshot],
) -> RestatePitReconciliation:
    if not isinstance(restate_run_id, str) or not restate_run_id.strip():
        raise RestatePitReconciliationError("restate_run_id dolu metin olmali")

    norm_tickers = tuple(sorted({_norm_ticker(t) for t in tickers}))
    if not norm_tickers:
        raise RestatePitReconciliationError("en az bir ticker gerekli")

    eksik_pit = set(norm_tickers) - set(pit_snapshots)
    eksik_restate = set(norm_tickers) - set(restate_snapshots)
    if eksik_pit or eksik_restate:
        raise RestatePitReconciliationError(
            f"snapshot eksik: pit={sorted(eksik_pit)} restate={sorted(eksik_restate)}")

    karsilastirmalar: dict[str, TickerComparison] = {}
    for t in norm_tickers:
        karsilastirmalar[t] = _compare_ticker(t, pit_snapshots[t], restate_snapshots[t])

    compared_count = sum(1 for c in karsilastirmalar.values() if c.compared)
    mismatch_count = sum(1 for c in karsilastirmalar.values() if c.has_mismatch())
    pit_missing_count = sum(1 for c in karsilastirmalar.values()
                            if FINDING_PIT_MISSING in c.findings)
    restate_incomplete_count = sum(1 for c in karsilastirmalar.values()
                                   if FINDING_RESTATE_INCOMPLETE in c.findings)

    if compared_count == 0:
        durum = STATUS_INCOMPLETE
    elif mismatch_count > 0:
        durum = STATUS_MISMATCH
    else:
        durum = STATUS_PASS

    tam_dogrulandi = (durum == STATUS_PASS and compared_count == len(norm_tickers))

    rid = _reconciliation_run_id(restate_run_id, norm_tickers)
    return RestatePitReconciliation(
        reconciliation_run_id=rid, reconciliation_type=RECONCILIATION_TYPE,
        reconciler_version=RECONCILER_VERSION, restate_run_id=restate_run_id,
        tickers=norm_tickers, comparisons=karsilastirmalar, status=durum,
        fully_verified=tam_dogrulandi, compared_count=compared_count,
        mismatch_count=mismatch_count, pit_missing_count=pit_missing_count,
        restate_incomplete_count=restate_incomplete_count,
        diagnostics={"expected_ticker_count": len(norm_tickers)})
