"""
V23-A — SAF RESTATE HESAPLAYICI.

M2 ZORUNLU MODUL OLMAYA DEVAM EDER. M2 icin cutoff'a duyarli bir uretim
kaynagi HENUZ YOK (V22-A bulgusu). Bu yuzden:
  - M2 bu hesaplayicida HER ZAMAN eksik sayilir.
  - PIT'teki mevcut M2 degeri FALLBACK OLARAK ASLA KULLANILMAZ.
  - compute_total_rasyo() DEGISTIRILMEZ; alti-modul sozlesmesi KORUNUR.
  - M2 eksikken hicbir ticker COMPLETE(OK) olamaz -- bilincli sonuc.

KAPANIS ADI (bilerek): "RESTATE production foundation complete; full
restatement blocked by M2 source availability" -- "RESTATE tamamlandi"
DENMEZ.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from src.analytics.total_rasyo_restate_reader import RestateCompanyContext
from src.analytics.total_rasyo_score import (
    MODULE_KEYS,
    TotalRasyoScoreError,
    compute_total_rasyo,
)

RESTATE_CONTRACT_VERSION = 1
READER_VERSION = 1

STATUS_OK = "OK"
STATUS_INSUFFICIENT = "YETERSIZ_VERI"

REASON_NO_RESTATE_SOURCE_M2 = "NO_RESTATE_SOURCE_FOR_M2"
REASON_MODULE_UNAVAILABLE_AT_CUTOFF = "MODULE_UNAVAILABLE_AT_CUTOFF"
REASON_NO_MODULE_RECORD = "MODULE_KAYDI_YOK"


class RestateCalculationError(ValueError):
    pass


@dataclass(frozen=True)
class RestateModuleValue:
    module: str
    score: Optional[float]
    missing: bool
    source_at: Optional[datetime]
    source_run_key: Optional[str]
    identity_known: bool


@dataclass(frozen=True)
class RestateCompanyResult:
    ticker: str
    modules: Mapping[str, RestateModuleValue]
    total_rasyo_status: str
    insufficiency_reason: Optional[str]
    good_count_ge8: Optional[int]
    good_count_missing: bool
    base_score: Optional[float]
    final_score: Optional[float]
    veto_flag: Optional[bool]
    decision: Optional[str]

    def is_complete(self) -> bool:
        return self.total_rasyo_status == STATUS_OK


@dataclass(frozen=True)
class RestateComputation:
    restate_run_id: str
    inputs_sha256: str
    results_sha256: str
    target_analysis_at: datetime
    knowledge_cutoff_at: datetime
    tickers: tuple[str, ...]
    company_results: Mapping[str, RestateCompanyResult]
    restate_contract_version: int
    reader_version: int
    calculation_profile: str
    calculation_version: int
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def complete_tickers(self) -> tuple[str, ...]:
        return tuple(t for t, r in self.company_results.items() if r.is_complete())

    def incomplete_tickers(self) -> tuple[str, ...]:
        return tuple(t for t, r in self.company_results.items() if not r.is_complete())


def _norm_ticker(t: str) -> str:
    if not isinstance(t, str) or not t.strip():
        raise RestateCalculationError("ticker dolu metin olmali")
    return t.strip().upper()


def _norm_ts(dt: datetime) -> str:
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise RestateCalculationError("timestamp timezone bilgili olmali")
    return dt.astimezone(timezone.utc).isoformat()


def _restate_run_id(*, target_analysis_at: datetime, knowledge_cutoff_at: datetime,
                    tickers: tuple[str, ...], calculation_profile: str,
                    calculation_version: int) -> str:
    ham = json.dumps({
        "restate_contract_version": RESTATE_CONTRACT_VERSION,
        "reader_version": READER_VERSION,
        "target_analysis_at": _norm_ts(target_analysis_at),
        "knowledge_cutoff_at": _norm_ts(knowledge_cutoff_at),
        "tickers": list(tickers),
        "calculation_profile": calculation_profile,
        "calculation_version": calculation_version,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def _build_company_result(ticker: str,
                          ctx: Optional[RestateCompanyContext]) -> RestateCompanyResult:
    m2 = RestateModuleValue("M2", None, True, None, None, False)

    if ctx is None:
        moduller = {k: RestateModuleValue(k, None, True, None, None, False)
                   for k in ("M1", "M3", "Ek4", "Ek1", "Ek9")}
        moduller["M2"] = m2
        return RestateCompanyResult(
            ticker=ticker, modules=moduller, total_rasyo_status=STATUS_INSUFFICIENT,
            insufficiency_reason=REASON_NO_MODULE_RECORD, good_count_ge8=None,
            good_count_missing=True, base_score=None, final_score=None,
            veto_flag=None, decision=None)

    moduller = {}
    for key, comp in ctx.components.items():
        moduller[key] = RestateModuleValue(
            module=key, score=comp.score, missing=comp.missing,
            source_at=comp.source_at, source_run_key=comp.source_run_key,
            identity_known=comp.identity_known)
    moduller["M2"] = m2

    eksikler = sorted(k for k, v in moduller.items() if v.missing)
    if ctx.good_count_missing:
        eksikler.append("GOOD_COUNT")

    if eksikler:
        neden = REASON_NO_RESTATE_SOURCE_M2 if eksikler == ["M2"] \
            else REASON_MODULE_UNAVAILABLE_AT_CUTOFF
        return RestateCompanyResult(
            ticker=ticker, modules=moduller, total_rasyo_status=STATUS_INSUFFICIENT,
            insufficiency_reason=neden, good_count_ge8=ctx.good_count_ge8,
            good_count_missing=ctx.good_count_missing, base_score=None,
            final_score=None, veto_flag=None, decision=None)

    try:
        hesap = compute_total_rasyo(
            {k: moduller[k].score for k in MODULE_KEYS},
            good_count_ge8=ctx.good_count_ge8)
    except TotalRasyoScoreError as exc:
        raise RestateCalculationError(f"{ticker}: skor hesaplama hatasi: {exc}") from exc

    return RestateCompanyResult(
        ticker=ticker, modules=moduller, total_rasyo_status=STATUS_OK,
        insufficiency_reason=None, good_count_ge8=ctx.good_count_ge8,
        good_count_missing=False, base_score=hesap["base_score"],
        final_score=hesap["final_score"], veto_flag=hesap["veto_flag"],
        decision=hesap["decision"])


def _inputs_sha256(tickers: tuple[str, ...],
                   results: Mapping[str, RestateCompanyResult]) -> str:
    satirlar = []
    for t in tickers:
        for modul, v in sorted(results[t].modules.items()):
            satirlar.append((
                t, modul, v.score, v.missing,
                None if v.source_at is None else v.source_at.isoformat(),
                v.source_run_key, v.identity_known,
            ))
    ham = json.dumps(satirlar, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def _results_sha256(tickers: tuple[str, ...],
                    results: Mapping[str, RestateCompanyResult]) -> str:
    satirlar = []
    for t in tickers:
        r = results[t]
        satirlar.append((
            t, r.total_rasyo_status, r.insufficiency_reason, r.good_count_ge8,
            r.good_count_missing, r.base_score, r.final_score, r.veto_flag,
            r.decision,
        ))
    ham = json.dumps(satirlar, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def compute_restate(
    *,
    target_analysis_at: datetime,
    knowledge_cutoff_at: datetime,
    tickers,
    module_contexts: Mapping[str, RestateCompanyContext],
    calculation_profile: str = "TOTAL_RASYO_RESTATE_V1",
    calculation_version: int = 1,
) -> RestateComputation:
    hedef = target_analysis_at
    cutoff = knowledge_cutoff_at
    if not isinstance(hedef, datetime) or hedef.tzinfo is None:
        raise RestateCalculationError("target_analysis_at timezone bilgili olmali")
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None:
        raise RestateCalculationError("knowledge_cutoff_at timezone bilgili olmali")
    if cutoff < hedef:
        raise RestateCalculationError("knowledge_cutoff_at target_analysis_at'ten once olamaz")
    if not isinstance(calculation_profile, str) or not calculation_profile.strip():
        raise RestateCalculationError("calculation_profile dolu metin olmali")
    if isinstance(calculation_version, bool) or not isinstance(calculation_version, int) \
            or calculation_version < 1:
        raise RestateCalculationError("calculation_version pozitif tam sayi olmali")

    norm_tickers = tuple(sorted({_norm_ticker(t) for t in tickers}))
    if not norm_tickers:
        raise RestateCalculationError("en az bir ticker gerekli")

    sonuclar: dict[str, RestateCompanyResult] = {}
    for t in norm_tickers:
        sonuclar[t] = _build_company_result(t, module_contexts.get(t))

    rid = _restate_run_id(
        target_analysis_at=hedef, knowledge_cutoff_at=cutoff, tickers=norm_tickers,
        calculation_profile=calculation_profile, calculation_version=calculation_version)
    inputs_sha = _inputs_sha256(norm_tickers, sonuclar)
    results_sha = _results_sha256(norm_tickers, sonuclar)

    return RestateComputation(
        restate_run_id=rid, inputs_sha256=inputs_sha, results_sha256=results_sha,
        target_analysis_at=hedef, knowledge_cutoff_at=cutoff, tickers=norm_tickers,
        company_results=sonuclar, restate_contract_version=RESTATE_CONTRACT_VERSION,
        reader_version=READER_VERSION, calculation_profile=calculation_profile,
        calculation_version=calculation_version,
        diagnostics={"complete_count": sum(1 for r in sonuclar.values() if r.is_complete()),
                    "incomplete_count": sum(1 for r in sonuclar.values() if not r.is_complete())})
