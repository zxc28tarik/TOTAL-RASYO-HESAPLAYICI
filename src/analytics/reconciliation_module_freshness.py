"""
V22-B — SAF HESAPLAYICI: Total Rasyo tuketim snapshotu <-> modul hattinin
tazeligi/lineage'i.

REPORT-ONLY. Bu modul hicbir yeniden hesaplama BASLATMAZ, veritabanina
YAZMAZ. Girdi tuketim-ani kanit (V22-A) + guncel uretici durumu (bagimsiz
sorgularla toplanir), cikti bir ModuleReconciliationResult'tir.

HALEF KURALI (V22-A'dan sonra kod yazilmadan ONCE kilitlendi):

  TOTAL_STALE: guncel uretici satiri
      ticker/module ESLESIR
      AND R.analysis_at <= total_rasyo.analysis_at   -- ASLA look-ahead DEGIL
      AND R.analysis_at >  tuketilen.module_analysis_at
    varsa. M2 icin ZAYIF PROXY kullanilir (company_total_rasyo_result'in
    GUNCEL kanonik m2_source_at'i); bu yalniz "daha sonraki resmi bir Total
    Rasyo kosusu M2'yi tazeledi mi" sorusunu cevaplar, HAM sektor motoru
    verisinin tazeligini DEGIL.

  MODULE_LINEAGE_STALE: yalniz identity_known=true oldugunda, AYNI
    (ticker, module, analysis_at) ETIKETI icin guncel source_version_id
    tuketilenden FARKLIYSA. M2 icin identity_known HER ZAMAN false'tur
    (V22-A); lineage kontrolu M2'de HICBIR ZAMAN yapilmaz.

  MISSING_MODULE: tuketim snapshotunda module_missing=true ise. Bu modul
  icin freshness/lineage kontrolleri YAPILMAZ (temel deger yok).

"lineage satiri var" != "source identity biliniyor": identity_known=false
olan modul icin MODULE_LINEAGE_STALE HUKUM VERMEZ; lineage_performed=false
ile kontrolun uygulanamadigi acikca gorunur.

PASS/fully_verified AYRIMI: `status=PASS` yalniz YURUTULEN kontrollerde
bulgu olmadigi anlamina gelir. `fully_verified=true` ANCAK beklenen butun
uygulanabilir kontroller GERCEKTEN yapilmissa verilir (orn. M2 kimligi
bilinmedigi icin fully_verified=false olabilir, status yine PASS kalir).
Bu, INCOMPLETE ile KARISTIRILMAZ: INCOMPLETE kosunun/verinin HENUZ yargi
vermeye hazir olmadigi durumdur; kanit kapsami eksikligi ayri bir kavramdir.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

from src.analytics.total_rasyo_score import MODULE_KEYS

RECONCILER_VERSION = 1
RECONCILIATION_TYPE = "TOTAL_RASYO_MODULE_FRESHNESS"

STATUS_PASS = "PASS"
STATUS_MISMATCH = "MISMATCH"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_ERROR = "ERROR"

FINDING_MISSING = "MISSING_MODULE"
FINDING_TOTAL_STALE = "TOTAL_STALE"
FINDING_LINEAGE_STALE = "MODULE_LINEAGE_STALE"

REASON_MODULE_MISSING = "MODULE_WAS_MISSING_AT_CONSUMPTION"
REASON_IDENTITY_UNAVAILABLE = "SOURCE_IDENTITY_UNAVAILABLE"
REASON_NO_BASELINE_CONTEXT = "NO_BASELINE_CONTEXT"


class ModuleReconciliationError(ValueError):
    pass


@dataclass(frozen=True)
class ConsumedModule:
    """V22-A'nin tuketim-ani snapshot'indan tek bir modulun kaydi."""
    module: str
    missing: bool
    source_at: Optional[datetime]
    analysis_at: Optional[datetime]
    source_run_key: Optional[str]
    identity_known: bool


@dataclass(frozen=True)
class ProducerSuccessor:
    """
    Guncel uretici durumu -- bagimsiz sorguyla toplanir (M1/M3/Ek1/Ek4/Ek9
    icin module_production_lineage, M2 icin zayif proxy).

    `newer_eligible_exists`: TOTAL_STALE icin -- ticker/analysis_at
    baglaminda tuketilenden yeni fakat total_rasyo.analysis_at sinirinda
    kalan bir uretici satiri var mi.
    `same_label_source_run_key`: MODULE_LINEAGE_STALE icin -- tuketilenle
    AYNI analysis_at etiketindeki GUNCEL source_version_id (yoksa None).
    """
    module: str
    newer_eligible_exists: bool
    same_label_source_run_key: Optional[str]
    freshness_available: bool
    lineage_lookup_available: bool


@dataclass(frozen=True)
class ModuleCheck:
    module: str
    missing: bool
    freshness_performed: bool
    freshness_reason: Optional[str]
    total_stale: Optional[bool]
    lineage_performed: bool
    lineage_reason: Optional[str]
    lineage_stale: Optional[bool]

    def findings(self) -> tuple[str, ...]:
        out = []
        if self.missing:
            out.append(FINDING_MISSING)
        if self.total_stale:
            out.append(FINDING_TOTAL_STALE)
        if self.lineage_stale:
            out.append(FINDING_LINEAGE_STALE)
        return tuple(out)


@dataclass(frozen=True)
class ModuleReconciliationResult:
    reconciliation_run_id: str
    reconciliation_type: str
    reconciler_version: int
    total_rasyo_run_id: str
    ticker: str
    analysis_at: datetime
    started_at: datetime
    finished_at: datetime
    status: str
    fully_verified: Optional[bool]
    checks: Mapping[str, ModuleCheck]
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def missing_modules(self) -> tuple[str, ...]:
        return tuple(m for m, c in self.checks.items() if c.missing)

    def total_stale_modules(self) -> tuple[str, ...]:
        return tuple(m for m, c in self.checks.items() if c.total_stale)

    def lineage_stale_modules(self) -> tuple[str, ...]:
        return tuple(m for m, c in self.checks.items() if c.lineage_stale)


def _run_id(total_rasyo_run_id: str, ticker: str, analysis_at: datetime) -> str:
    ham = json.dumps({
        "reconciliation_type": RECONCILIATION_TYPE,
        "reconciler_version": RECONCILER_VERSION,
        "total_rasyo_run_id": total_rasyo_run_id,
        "ticker": ticker,
        "analysis_at": analysis_at.isoformat(),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def reconciliation_sha256(result: ModuleReconciliationResult) -> str:
    govde = {
        "reconciliation_run_id": result.reconciliation_run_id,
        "status": result.status,
        "fully_verified": result.fully_verified,
        "checks": sorted(
            (m, c.missing, c.freshness_performed, c.freshness_reason,
             c.total_stale, c.lineage_performed, c.lineage_reason, c.lineage_stale)
            for m, c in result.checks.items()),
    }
    ham = json.dumps(govde, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def _build_check(consumed: ConsumedModule,
                 successor: Optional[ProducerSuccessor]) -> ModuleCheck:
    if consumed.missing:
        return ModuleCheck(
            module=consumed.module, missing=True,
            freshness_performed=False, freshness_reason=REASON_MODULE_MISSING,
            total_stale=None,
            lineage_performed=False, lineage_reason=REASON_MODULE_MISSING,
            lineage_stale=None)

    if successor is None or not successor.freshness_available:
        freshness_performed = False
        freshness_reason = REASON_NO_BASELINE_CONTEXT
        total_stale = None
    else:
        freshness_performed = True
        freshness_reason = None
        total_stale = successor.newer_eligible_exists

    if consumed.module == "M2":
        # MIMARI ZORUNLULUK: M2 icin sektor motorlerinin kendi uretim
        # kimligini tutan bir tablo YOK (V22-A). Bu, cagiranin verdigi
        # `identity_known` bayragina GUVENMEK yerine BURADA ZORLANIR --
        # aksi halde toplayicidaki bir hata (M2'ye yanlislikla
        # identity_known=True vermek) yalniz DB CHECK kisiti tarafindan
        # yakalanirdi, saf katman tarafindan DEGIL.
        lineage_performed = False
        lineage_reason = REASON_IDENTITY_UNAVAILABLE
        lineage_stale = None
    elif not consumed.identity_known:
        lineage_performed = False
        lineage_reason = REASON_IDENTITY_UNAVAILABLE
        lineage_stale = None
    elif successor is None or not successor.lineage_lookup_available:
        lineage_performed = False
        lineage_reason = REASON_NO_BASELINE_CONTEXT
        lineage_stale = None
    else:
        lineage_performed = True
        lineage_reason = None
        guncel_kimlik = successor.same_label_source_run_key
        lineage_stale = (guncel_kimlik is not None
                         and guncel_kimlik != consumed.source_run_key)

    return ModuleCheck(
        module=consumed.module, missing=False,
        freshness_performed=freshness_performed, freshness_reason=freshness_reason,
        total_stale=total_stale,
        lineage_performed=lineage_performed, lineage_reason=lineage_reason,
        lineage_stale=lineage_stale)


def reconcile_module_freshness(
    *,
    total_rasyo_run_id: str,
    ticker: str,
    analysis_at: datetime,
    started_at: datetime,
    finished_at: datetime,
    consumed_modules: Mapping[str, ConsumedModule],
    successors: Mapping[str, ProducerSuccessor],
    evidence_available: bool = True,
) -> ModuleReconciliationResult:
    """
    Saf karsilastirma. `evidence_available=False` ise (V22-A snapshot'i bu
    run icin hic yazilmamis) INCOMPLETE doner -- MISMATCH DENMEZ, sessizce
    PASS de DENMEZ.
    """
    if not isinstance(total_rasyo_run_id, str) or not total_rasyo_run_id.strip():
        raise ModuleReconciliationError("total_rasyo_run_id dolu metin olmali")
    if not isinstance(ticker, str) or not ticker.strip():
        raise ModuleReconciliationError("ticker dolu metin olmali")
    kod = ticker.strip().upper()
    for alan, deger in (("analysis_at", analysis_at), ("started_at", started_at),
                        ("finished_at", finished_at)):
        if not isinstance(deger, datetime) or deger.tzinfo is None:
            raise ModuleReconciliationError(f"{alan} timezone bilgili olmali")
    if finished_at < started_at:
        raise ModuleReconciliationError("finished_at started_at'ten once olamaz")

    rid = _run_id(total_rasyo_run_id, kod, analysis_at)

    if not evidence_available:
        return ModuleReconciliationResult(
            reconciliation_run_id=rid, reconciliation_type=RECONCILIATION_TYPE,
            reconciler_version=RECONCILER_VERSION,
            total_rasyo_run_id=total_rasyo_run_id, ticker=kod,
            analysis_at=analysis_at, started_at=started_at, finished_at=finished_at,
            status=STATUS_INCOMPLETE, fully_verified=None, checks={},
            diagnostics={"reason": "consumption_snapshot_missing"})

    eksik_modul = set(MODULE_KEYS) - set(consumed_modules)
    if eksik_modul:
        raise ModuleReconciliationError(
            f"consumed_modules eksik modul iceriyor: {sorted(eksik_modul)}")

    checks: dict[str, ModuleCheck] = {}
    for anahtar in MODULE_KEYS:
        checks[anahtar] = _build_check(consumed_modules[anahtar],
                                       successors.get(anahtar))

    bulgu_var = any(c.findings() for c in checks.values())
    durum = STATUS_MISMATCH if bulgu_var else STATUS_PASS
    tam_dogrulandi = all(
        (c.missing) or (c.freshness_performed and (c.module == "M2" or c.lineage_performed))
        for c in checks.values()
    )
    # M2'nin lineage'i mimari olarak HICBIR ZAMAN yapilamaz (V22-A); bunu
    # "eksik kanit" olarak fully_verified'i surekli False'a cekmesini
    # ONLEMEK icin M2'de yalniz freshness aranir. Diger bes modulde HEM
    # freshness HEM lineage sart kosulur.

    return ModuleReconciliationResult(
        reconciliation_run_id=rid, reconciliation_type=RECONCILIATION_TYPE,
        reconciler_version=RECONCILER_VERSION,
        total_rasyo_run_id=total_rasyo_run_id, ticker=kod,
        analysis_at=analysis_at, started_at=started_at, finished_at=finished_at,
        status=durum, fully_verified=tam_dogrulandi, checks=checks,
        diagnostics={"expected_module_count": len(MODULE_KEYS)})
