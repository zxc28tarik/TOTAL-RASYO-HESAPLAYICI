"""
V21 Reconciliation-1 — SAF HESAPLAMA: Impact Plan <-> Orkestratör gercek kumesi.

REPORT-ONLY. Bu modul hicbir yeniden hesaplama BASLATMAZ, veritabanina
YAZMAZ. Girdi iki kume (beklenen, gercek) ve bir run kimlik eslemesi;
cikti bir ReconciliationResult'tir.

KARSILASTIRMA KIMLIK KUMESI UZERINDEN yapilir, sayi uzerinden DEGIL. Iki
kume ayni BUYUKLUKTE olup FARKLI ticker'lar icerebilir; sayi karsilastirmasi
bunu KACIRIR.

UC AYRI BULGU TURU -- V20'de ogrenilen ders: yalniz "happy path" esitligi
yetmez.
  MISSING    beklenen ticker icin HIC satir yok
  UNEXPECTED beklenmeyen ticker icin satir VAR
  STALE      ticker beklenen VE gercek kumede, fakat satirin GUNCEL run_id'si
             bu attempt'e ait DEGIL -- bu attempt'in etkisi dogrulanamiyor
             (baska bir kosu tarafindan sonradan ezilmis olabilir)
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

RECONCILER_VERSION = 1
RECONCILIATION_TYPE = "IMPACT_PLAN_VS_ORCHESTRATOR_ACTUAL"

STATUS_PASS = "PASS"
STATUS_MISMATCH = "MISMATCH"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_ERROR = "ERROR"

FINDING_MISSING = "MISSING"
FINDING_UNEXPECTED = "UNEXPECTED"
FINDING_STALE = "STALE"


class ReconciliationError(ValueError):
    pass


@dataclass(frozen=True)
class ActualRow:
    """Kalicilastirilmis Total Rasyo sonucundan okunan tek satir."""
    ticker: str
    current_run_id: Optional[str]


@dataclass(frozen=True)
class Finding:
    ticker: str
    finding_type: str
    detail: str


@dataclass(frozen=True)
class ReconciliationResult:
    reconciliation_run_id: str
    reconciliation_type: str
    reconciler_version: int
    application_run_id: str
    impact_plan_id: str
    analysis_at: datetime
    started_at: datetime
    finished_at: datetime
    status: str
    expected: tuple[str, ...]
    actual: tuple[str, ...]
    findings: tuple[Finding, ...]
    # GORUNUR SOZLESME: orchestrator_run_id verilmezse STALE kontrolu
    # ATLANIR. Bu alan olmadan bir PASS'in "uc kontrol de temiz" mi yoksa
    # "STALE kontrolu hic CALISMADI" mi oldugu ayirt edilemez -- sessiz bir
    # kanit boslugu olurdu. PASS uretilirken hangi kontrollerin GERCEKTEN
    # uygulandigi bu alandan okunabilir olmali.
    stale_check_performed: bool = True
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def missing(self) -> tuple[str, ...]:
        return tuple(f.ticker for f in self.findings if f.finding_type == FINDING_MISSING)

    def unexpected(self) -> tuple[str, ...]:
        return tuple(f.ticker for f in self.findings if f.finding_type == FINDING_UNEXPECTED)

    def stale(self) -> tuple[str, ...]:
        return tuple(f.ticker for f in self.findings if f.finding_type == FINDING_STALE)


def _normalize(tickers) -> frozenset[str]:
    out = set()
    for t in tickers:
        if not isinstance(t, str) or not t.strip():
            raise ReconciliationError("ticker dolu metin olmali")
        out.add(t.strip().upper())
    return frozenset(out)


def _run_id(application_run_id: str, impact_plan_id: str, analysis_at: datetime) -> str:
    """
    Deterministik kimlik: ayni (application_run, plan, analysis_at) -> ayni
    reconciliation_run_id. Idempotent yeniden calistirmayi mumkun kilar.
    """
    ham = json.dumps({
        "reconciliation_type": RECONCILIATION_TYPE,
        "reconciler_version": RECONCILER_VERSION,
        "application_run_id": application_run_id,
        "impact_plan_id": impact_plan_id,
        "analysis_at": analysis_at.isoformat(),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def reconciliation_sha256(result: ReconciliationResult) -> str:
    """Sonucun KANONIK icerik ozeti. Kimlik ile icerik AYRI tutulur."""
    govde = {
        "reconciliation_run_id": result.reconciliation_run_id,
        "status": result.status,
        "expected": sorted(result.expected),
        "actual": sorted(result.actual),
        "stale_check_performed": result.stale_check_performed,
        "findings": sorted(
            (f.ticker, f.finding_type, f.detail) for f in result.findings),
    }
    ham = json.dumps(govde, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def reconcile_impact_vs_actual(
    *,
    application_run_id: str,
    impact_plan_id: str,
    analysis_at: datetime,
    started_at: datetime,
    finished_at: datetime,
    application_status: str,
    expected_tickers,
    actual_rows,
    orchestrator_run_id: Optional[str] = None,
) -> ReconciliationResult:
    """
    Saf karsilastirma.

    `application_run_id`: impact_application_run kimligi -- reconciliation
    KIMLIGINI ve idempotency'yi belirler.
    `orchestrator_run_id`: V19 orkestratörüne verilen `run_id`. Kalicilastirilmis
    company_total_rasyo_result satirlarindaki `run_id` sutunu BUDUR,
    application_run_id DEGIL -- ikisi FARKLI kimlik uzaylaridir (biri V20
    impact katmaninin, digeri V19 orkestratörünün). STALE kontrolu bu yuzden
    `orchestrator_run_id`'ye karsi yapilir; verilmezse `application_run_id`
    ile ayni oldugu varsayilmaz, STALE kontrolu ATLANIR (asla yanlislikla
    STALE=[] doner gibi gorunup gizli hata birakmaz; ayri isaretlenir).

    `application_status`: impact_application_run.status. 'PENDING' ise
    kosu henuz bitmemis demektir ve MISSING/UNEXPECTED hesaplamak ERKEN
    yargidir -- INCOMPLETE donulur, MISMATCH DENMEZ.

    `actual_rows`: ActualRow dizisi. current_run_id, o ticker/analysis_at
    icin veritabanindaki GUNCEL kanonik satirin run_id'sidir (orkestratör
    run_id'si). None ise hic satir yok demektir ama MISSING zaten
    expected-actual farkindan cikar; bu alan yalniz STALE ayrimi icin
    kullanilir.
    """
    if not isinstance(application_run_id, str) or not application_run_id.strip():
        raise ReconciliationError("application_run_id dolu metin olmali")
    if not isinstance(impact_plan_id, str) or not impact_plan_id.strip():
        raise ReconciliationError("impact_plan_id dolu metin olmali")
    if not isinstance(analysis_at, datetime) or analysis_at.tzinfo is None:
        raise ReconciliationError("analysis_at timezone bilgili olmali")
    for alan, deger in (("started_at", started_at), ("finished_at", finished_at)):
        if not isinstance(deger, datetime) or deger.tzinfo is None:
            raise ReconciliationError(f"{alan} timezone bilgili olmali")
    if finished_at < started_at:
        raise ReconciliationError("finished_at started_at'ten once olamaz")

    beklenen = _normalize(expected_tickers)
    actual_map: dict[str, Optional[str]] = {}
    for row in actual_rows:
        kod = row.ticker.strip().upper()
        if kod in actual_map:
            raise ReconciliationError(f"actual_rows'ta yinelenen ticker: {kod}")
        actual_map[kod] = row.current_run_id
    gercek = frozenset(actual_map)

    rid = _run_id(application_run_id, impact_plan_id, analysis_at)

    if application_status == "PENDING":
        return ReconciliationResult(
            reconciliation_run_id=rid, reconciliation_type=RECONCILIATION_TYPE,
            reconciler_version=RECONCILER_VERSION,
            application_run_id=application_run_id, impact_plan_id=impact_plan_id,
            analysis_at=analysis_at, started_at=started_at, finished_at=finished_at,
            status=STATUS_INCOMPLETE, expected=tuple(sorted(beklenen)),
            actual=tuple(sorted(gercek)), findings=(),
            stale_check_performed=False,
            diagnostics={"reason": "application_run_pending"})

    bulgular: list[Finding] = []
    for t in sorted(beklenen - gercek):
        bulgular.append(Finding(t, FINDING_MISSING,
                                "beklenen ticker icin satir yok"))
    for t in sorted(gercek - beklenen):
        bulgular.append(Finding(t, FINDING_UNEXPECTED,
                                "beklenmeyen ticker icin satir uretildi"))
    if orchestrator_run_id is not None:
        for t in sorted(beklenen & gercek):
            guncel = actual_map[t]
            if guncel != orchestrator_run_id:
                bulgular.append(Finding(
                    t, FINDING_STALE,
                    f"guncel satir run_id={guncel!r}, beklenen orkestratör "
                    f"run_id={orchestrator_run_id!r} -- bu attempt'in etkisi "
                    "baska bir kosu tarafindan ezilmis olabilir"))

    durum = STATUS_PASS if not bulgular else STATUS_MISMATCH
    return ReconciliationResult(
        reconciliation_run_id=rid, reconciliation_type=RECONCILIATION_TYPE,
        reconciler_version=RECONCILER_VERSION,
        application_run_id=application_run_id, impact_plan_id=impact_plan_id,
        analysis_at=analysis_at, started_at=started_at, finished_at=finished_at,
        status=durum, expected=tuple(sorted(beklenen)), actual=tuple(sorted(gercek)),
        findings=tuple(bulgular),
        stale_check_performed=(orchestrator_run_id is not None),
        diagnostics={"expected_count": len(beklenen), "actual_count": len(gercek)})
