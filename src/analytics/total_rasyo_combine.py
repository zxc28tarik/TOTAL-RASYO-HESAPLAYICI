"""
Total Rasyo orkestratoru — ALTI MODULLU birlestirme cekirdegi.

FORMUL BURADA DEGILDIR. Tek ve kanitlanmis kaynak:

    src/analytics/total_rasyo_score.py
        MODULE_KEYS    = (M2, M1, M3, Ek4, Ek1, Ek9)
        DEFAULT_WEIGHTS= M2 .40 | M1 .18 | M3 .12 | Ek4 .16 | Ek1 .08 | Ek9 .06
        + good_count_ge8 vetosu (esik 5, carpan 0.60)
        + karar bantlari: >=0.70 AL, >=0.55 IZLE, altisi UZAK

Bu modul veto, agirlik ve karar mantigini YENIDEN YAZMAZ; `compute_total_rasyo()`
fonksiyonunu CAGIRIR. Ikinci bir formul, zamanla ilkinden sapan ve hangisinin
dogru oldugu belirsiz iki gercek uretir.

KAYIP TASLAKTA TESPIT EDILEN YANLIS SOZLESME
--------------------------------------------
Onceki oturumda yazilip paketlenmeden kaybolan orkestratör taslagi sunu
kullaniyordu:

    DEFAULT_WEIGHTS = {"m1": 0.30, "m2": 0.45, "m3": 0.25}

Bu YALNIZCA yer tutucu agirlik degildi: Ek4 (.16), Ek1 (.08) ve Ek9 (.06)
modullerini -- toplam agirligin %30'unu -- tamamen DUSURUP kalan uc modulu
kendi icinde yeniden normalize ediyordu. good_count vetosunu da atliyordu.
Sonuc, ayni ada sahip fakat farkli bir buyuklук olurdu. Bu agirliklar
hicbir yerde KULLANILMAZ.

BILESEN SOZLESMESI
------------------
M2  : sirketin yonlendirildigi SEKTOR MOTORUNDAN gelir (otoritatif kaynak).
      analytics.module_scores.m2 KULLANILMAZ -- cift sayim olurdu.
M1, M3, Ek4, Ek1, Ek9 ve good_count_ge8: point-in-time modul okuyucusundan.

EKSIK BILESEN
-------------
Alti modulden HERHANGI BIRI veya veto girdisi eksikse:
  - notr deger verilmez,
  - agirlik kalan modullere DAGITILMAZ,
  - eski veya gelecekteki kayitla tamamlanmaz,
  - fillna kullanilmaz.
Sonuc `YETERSIZ_VERI` olur ve eksik moduller ACIKCA yazilir.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional

from src.analytics.total_rasyo_engine_isolation import (
    ENGINE_STATUS_CRASHED,
    ENGINE_STATUS_NOT_RUN,
    ENGINE_STATUS_OK,
    ENGINE_STATUS_REJECTED,
    RUN_FAILED,
    RUN_OK,
    RUN_SKIPPED,
    EngineRun,
    sanitize_error_message,
)
from src.analytics.total_rasyo_module_reader import (
    MODULE_SOURCE_TYPE,
    READ_MODULE_KEYS,
    CompanyModuleContext,
)
from src.analytics.total_rasyo_score import (
    MODULE_KEYS,
    TotalRasyoScoreError,
    compute_total_rasyo,
    normalize_weights,
)
from src.utils.missing_values import is_bool_like, is_missing_like

STATUS_OK = "OK"
STATUS_INSUFFICIENT = "YETERSIZ_VERI"
STATUS_ENGINE_CRASHED = "MOTOR_COKTU"
STATUS_NOT_RUN = "CALISTIRILMADI"
STATUS_ROUTING_CONFLICT = "YONLENDIRME_CAKISMASI"

# Yetersiz verinin AYRINTI sinifi. `YETERSIZ_VERI` tek basina uc farkli
# olayi orterdi ve "motor reddetti" ile "modul satiri hic yok" ayirt
# edilemezdi.
INSUFF_M2_MISSING = "M2_YOK"
INSUFF_NO_MODULE_ROW = "MODUL_SATIRI_YOK"
INSUFF_PARTIAL = "EKSIK_BILESEN"
INSUFF_ENGINE_REJECTED = "MOTOR_REDDETTI"
INSUFF_UNUSABLE = "DEGERLEME_KULLANILAMAZ"
INSUFF_COMPUTE_ERROR = "HESAP_HATASI"

M2_SOURCE_TYPE = "SECTOR_ENGINE"
WEIGHTS_PROFILE = "TOTAL_RASYO_SCORE_V1"

# Kesin olarak YASAK agirlik kumesi. Kayip taslakta kullaniliyordu; yanlislikla
# geri gelirse test bunu yakalar.
FORBIDDEN_DRAFT_WEIGHTS = {"m1": 0.30, "m2": 0.45, "m3": 0.25}


class CombineError(ValueError):
    pass


@dataclass(frozen=True)
class CompanyResult:
    """Tek sirketin birlestirilmis sonucu. Sozluge cevrilerek kalicilastirilir."""
    ticker: str
    routed_engine: str
    engine_status: str
    engine_reason: Optional[str]
    m2_score: Optional[float]
    m2_source: Optional[str]
    m2_source_at: Optional[datetime]
    m2_source_type: Optional[str]
    m2_missing: bool
    valuation_confidence: Optional[float]
    modules: Mapping[str, Any]
    good_count_ge8: Optional[int]
    good_count_missing: bool
    base_score: Optional[float]
    final_score: Optional[float]
    total_rasyo_100: Optional[float]
    veto_flag: Optional[bool]
    decision: Optional[str]
    weights_profile: Optional[str]
    total_rasyo_status: str
    rejection_reason: Optional[str]
    insufficiency_reason: Optional[str]
    missing_modules: tuple[str, ...]
    data_confidence: Optional[float]
    diagnostics: Mapping[str, Any]


def _m2_value(payload: Mapping[str, Any]) -> Any:
    """
    Bes sektor motoru `m2`, BANK hatti `m2_score` anahtarini kullanir.
    Ikisi de kabul edilir; IKISI BIRDEN ve FARKLI ise hata verilir --
    sessizce birini secmek, hangi degerin puanlandigini belirsiz birakirdi.
    """
    var_m2 = "m2" in payload
    var_score = "m2_score" in payload
    if var_m2 and var_score:
        a, b = payload["m2"], payload["m2_score"]
        if not (is_missing_like(a) and is_missing_like(b)) and a != b:
            raise CombineError("m2 ve m2_score farkli deger tasiyor")
        return a
    if var_m2:
        return payload["m2"]
    if var_score:
        return payload["m2_score"]
    return None


def _unit_score(name: str, value: Any) -> tuple[Optional[float], bool]:
    """(skor, eksik_mi). EKSIK ile GECERSIZ ayrimi korunur."""
    if is_missing_like(value):
        return None, True
    if is_bool_like(value):
        raise CombineError(f"{name} bool olamaz")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CombineError(f"{name} sayiya cevrilemedi") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise CombineError(f"{name} sonlu olmali")
    if result < 0.0 or result > 1.0:
        raise CombineError(f"{name} [0, 1] araliginda olmali")
    return result, False


def _module_entry(key: str, score, source_at, source_type, missing, reason) -> dict[str, Any]:
    return {
        "score": score, "source_at": source_at, "source_type": source_type,
        "missing": missing, "reason": reason,
    }


def _empty_modules(reason: str) -> dict[str, dict[str, Any]]:
    return {
        key: _module_entry(key, None, None, None, True, reason)
        for key in MODULE_KEYS
    }


def _data_confidence(
    valuation_confidence: Optional[float],
    module_context: Optional[CompanyModuleContext],
) -> Optional[float]:
    """
    Veri guveni = degerleme guveni x modul tamlik orani.

    Bu bir SKOR DEGILDIR ve Total Rasyo'ya girmez; yalniz sonucun ne kadar
    saglam veriye dayandigini raporlar.
    """
    if valuation_confidence is None or module_context is None:
        return None
    toplam = len(READ_MODULE_KEYS) + 1  # +1 veto girdisi
    dolu = sum(1 for k in READ_MODULE_KEYS if not module_context.components[k].missing)
    if not module_context.good_count_missing:
        dolu += 1
    return float(valuation_confidence) * (dolu / toplam)


def combine_company_result(
    *,
    ticker: str,
    routed_engine: str,
    engine_run: Optional[EngineRun],
    module_context: Optional[CompanyModuleContext],
    weights: Optional[Mapping[str, Any]] = None,
    routing_conflict: Optional[tuple[str, ...]] = None,
) -> CompanyResult:
    """
    Bir sirket icin alti modulu birlestirir.

    Sirket HER DURUMDA bir sonuc satiri alir; hicbir kosulda rapordan
    dusmez. Durum alani ne oldugunu ayirt eder.
    """
    if not isinstance(ticker, str) or not ticker.strip():
        raise CombineError("ticker dolu metin olmali")
    code = ticker.strip().upper()
    engine = routed_engine.strip().upper() if isinstance(routed_engine, str) else ""
    if not engine:
        raise CombineError(f"{code} icin routed_engine bos")

    # --- 1) Yonlendirme cakismasi: fail-closed, skor URETILMEZ -------------
    if routing_conflict:
        aileler = ", ".join(routing_conflict)
        return CompanyResult(
            ticker=code, routed_engine=engine, engine_status=ENGINE_STATUS_REJECTED,
            engine_reason=f"CIFT_MOTOR_SAHIPLIGI: {aileler}",
            m2_score=None, m2_source=None, m2_source_at=None, m2_source_type=None,
            m2_missing=True, valuation_confidence=None,
            modules=_empty_modules("YONLENDIRME_CAKISMASI"),
            good_count_ge8=None, good_count_missing=True,
            base_score=None, final_score=None, total_rasyo_100=None,
            veto_flag=None, decision=None, weights_profile=None,
            total_rasyo_status=STATUS_ROUTING_CONFLICT,
            rejection_reason=f"CIFT_MOTOR_SAHIPLIGI: {aileler}",
            insufficiency_reason=None,
            missing_modules=tuple(MODULE_KEYS) + ("good_count_ge8",),
            data_confidence=None,
            diagnostics={"conflicting_engines": list(routing_conflict)},
        )

    # --- 2) Motor hic calistirilmadi ---------------------------------------
    if engine_run is None or engine_run.status == RUN_SKIPPED:
        neden = (engine_run.error_message if engine_run else None) or "MOTOR_CALISTIRILMADI"
        return CompanyResult(
            ticker=code, routed_engine=engine, engine_status=ENGINE_STATUS_NOT_RUN,
            engine_reason=sanitize_error_message(neden),
            m2_score=None, m2_source=None, m2_source_at=None, m2_source_type=None,
            m2_missing=True, valuation_confidence=None,
            modules=_empty_modules("MOTOR_CALISTIRILMADI"),
            good_count_ge8=None, good_count_missing=True,
            base_score=None, final_score=None, total_rasyo_100=None,
            veto_flag=None, decision=None, weights_profile=None,
            total_rasyo_status=STATUS_NOT_RUN,
            rejection_reason=sanitize_error_message(neden),
            insufficiency_reason=None,
            missing_modules=tuple(MODULE_KEYS) + ("good_count_ge8",),
            data_confidence=None, diagnostics={},
        )

    # --- 3) Motor coktu: sirket KAYBOLMAZ ----------------------------------
    if engine_run.status == RUN_FAILED:
        neden = f"{engine_run.error_type}: {engine_run.error_message}"
        return CompanyResult(
            ticker=code, routed_engine=engine, engine_status=ENGINE_STATUS_CRASHED,
            engine_reason=sanitize_error_message(neden),
            m2_score=None, m2_source=None, m2_source_at=None, m2_source_type=None,
            m2_missing=True, valuation_confidence=None,
            modules=_empty_modules("MOTOR_COKTU"),
            good_count_ge8=None, good_count_missing=True,
            base_score=None, final_score=None, total_rasyo_100=None,
            veto_flag=None, decision=None, weights_profile=None,
            total_rasyo_status=STATUS_ENGINE_CRASHED,
            rejection_reason=sanitize_error_message(neden),
            insufficiency_reason=None,
            missing_modules=tuple(MODULE_KEYS) + ("good_count_ge8",),
            data_confidence=None,
            diagnostics={"engine_error_type": engine_run.error_type},
        )

    if engine_run.status != RUN_OK:
        raise CombineError(f"beklenmeyen motor durumu: {engine_run.status}")

    # --- 4) Motor calisti; sirketi kontrollu reddetmis olabilir -------------
    engine_status = ENGINE_STATUS_OK
    engine_reason: Optional[str] = None
    payload = engine_run.m2_by_ticker.get(code)
    if payload is None:
        neden = engine_run.rejections.get(code) or "MOTOR_SONUC_URETMEDI"
        engine_status = ENGINE_STATUS_REJECTED
        engine_reason = sanitize_error_message(neden)

    # --- 5) M2: sektor motorundan (otoritatif) ------------------------------
    m2_score: Optional[float] = None
    m2_source: Optional[str] = None
    m2_source_at: Optional[datetime] = None
    m2_missing = True
    valuation_confidence: Optional[float] = None
    if payload is not None:
        m2_score, m2_missing = _unit_score("M2", _m2_value(payload))
        m2_source = payload.get("m2_source")
        if m2_source is not None and (not isinstance(m2_source, str) or not m2_source.strip()):
            raise CombineError("m2_source dolu metin olmali")
        m2_source_at = payload.get("m2_source_at")
        ham_conf = payload.get("valuation_confidence")
        if not is_missing_like(ham_conf):
            valuation_confidence, _ = _unit_score("valuation_confidence", ham_conf)
        # Motor sonucu "kullanilamaz" diyorsa M2 EKSIK sayilir; kullanilamaz
        # bir degerlemeyi puanlamak sessizce yanlis skor uretir.
        if payload.get("valuation_usable") is False:
            m2_missing = True
            m2_score = None
            engine_status = ENGINE_STATUS_REJECTED
            engine_reason = sanitize_error_message(
                payload.get("valuation_reason") or "DEGERLEME_KULLANILAMAZ")

    modules: dict[str, dict[str, Any]] = {}
    modules["M2"] = _module_entry(
        "M2", m2_score, m2_source_at, None if m2_missing else M2_SOURCE_TYPE,
        m2_missing, "M2_KAYNAGI_YOK" if m2_missing else None)

    # --- 6) M1/M3/Ek4/Ek1/Ek9 + veto girdisi --------------------------------
    modul_satiri_yok = module_context is None
    if module_context is None:
        for key in READ_MODULE_KEYS:
            modules[key] = _module_entry(key, None, None, None, True, f"{key}_KAYNAGI_YOK")
        good_count, good_missing = None, True
    else:
        for key in READ_MODULE_KEYS:
            bilesen = module_context.components[key]
            modules[key] = _module_entry(
                key, bilesen.score, bilesen.source_at, bilesen.source_type,
                bilesen.missing, bilesen.reason)
        good_count = module_context.good_count_ge8
        good_missing = module_context.good_count_missing

    eksikler = tuple(k for k in MODULE_KEYS if modules[k]["missing"])
    if good_missing:
        eksikler = eksikler + ("good_count_ge8",)

    # --- 7) Eksik varsa SKOR URETILMEZ -------------------------------------
    if eksikler:
        neden = "EKSIK_BILESEN: " + ", ".join(eksikler)
        # AYRINTI SINIFI: en spesifik olan kazanir. "modul satiri hic yok"
        # ile "satir var ama bir modul NULL" ayni sey degildir.
        if modul_satiri_yok:
            ayrinti = INSUFF_NO_MODULE_ROW
        elif engine_status == ENGINE_STATUS_REJECTED and m2_missing:
            ayrinti = (INSUFF_UNUSABLE if payload is not None
                       else INSUFF_ENGINE_REJECTED)
        elif m2_missing and len(eksikler) == 1:
            ayrinti = INSUFF_M2_MISSING
        else:
            ayrinti = INSUFF_PARTIAL
        return CompanyResult(
            ticker=code, routed_engine=engine,
            engine_status=engine_status, engine_reason=engine_reason,
            m2_score=m2_score, m2_source=m2_source, m2_source_at=m2_source_at,
            m2_source_type=None if m2_missing else M2_SOURCE_TYPE,
            m2_missing=m2_missing, valuation_confidence=valuation_confidence,
            modules=modules, good_count_ge8=good_count, good_count_missing=good_missing,
            base_score=None, final_score=None, total_rasyo_100=None,
            veto_flag=None, decision=None, weights_profile=None,
            total_rasyo_status=STATUS_INSUFFICIENT,
            rejection_reason=neden, insufficiency_reason=ayrinti,
            missing_modules=eksikler,
            data_confidence=_data_confidence(valuation_confidence, module_context),
            diagnostics={"missing_modules": list(eksikler)},
        )

    # --- 8) Alti modul de tam: KANITLANMIS formule devret -------------------
    try:
        hesap = compute_total_rasyo(
            {key: modules[key]["score"] for key in MODULE_KEYS},
            good_count_ge8=good_count,
            weights=weights,
        )
    except TotalRasyoScoreError as exc:
        neden = sanitize_error_message(f"TOTAL_RASYO_HESAP_HATASI: {exc}")
        return CompanyResult(
            ticker=code, routed_engine=engine,
            engine_status=engine_status, engine_reason=engine_reason,
            m2_score=m2_score, m2_source=m2_source, m2_source_at=m2_source_at,
            m2_source_type=M2_SOURCE_TYPE, m2_missing=False,
            valuation_confidence=valuation_confidence, modules=modules,
            good_count_ge8=good_count, good_count_missing=False,
            base_score=None, final_score=None, total_rasyo_100=None,
            veto_flag=None, decision=None, weights_profile=None,
            total_rasyo_status=STATUS_INSUFFICIENT,
            rejection_reason=neden, insufficiency_reason=INSUFF_COMPUTE_ERROR,
            missing_modules=(),
            data_confidence=_data_confidence(valuation_confidence, module_context),
            diagnostics={},
        )

    return CompanyResult(
        ticker=code, routed_engine=engine,
        engine_status=engine_status, engine_reason=engine_reason,
        m2_score=m2_score, m2_source=m2_source, m2_source_at=m2_source_at,
        m2_source_type=M2_SOURCE_TYPE, m2_missing=False,
        valuation_confidence=valuation_confidence, modules=modules,
        good_count_ge8=good_count, good_count_missing=False,
        base_score=hesap["base_score"], final_score=hesap["final_score"],
        total_rasyo_100=hesap["total_rasyo_100"], veto_flag=hesap["veto_flag"],
        decision=hesap["decision"], weights_profile=WEIGHTS_PROFILE,
        total_rasyo_status=STATUS_OK, rejection_reason=None,
        insufficiency_reason=None, missing_modules=(),
        data_confidence=_data_confidence(valuation_confidence, module_context),
        diagnostics={
            "contributions": hesap["contributions"],
            "weights": hesap["weights"],
            "veto_threshold": hesap["veto_threshold"],
            "veto_factor": hesap["veto_factor"],
        },
    )


def resolve_weights(weights: Optional[Mapping[str, Any]] = None) -> dict[str, float]:
    """
    Agirliklari KANITLANMIS sozlesme uzerinden dogrular.

    Uc modullu bir agirlik kumesi buradan GECEMEZ: normalize_weights() alti
    anahtarin tamamini zorunlu tutar ve eksik/fazla anahtari reddeder.
    """
    return normalize_weights(weights)
