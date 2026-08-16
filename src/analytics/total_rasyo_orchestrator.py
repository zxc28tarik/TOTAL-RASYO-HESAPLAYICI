"""
Total Rasyo ANA ORKESTRATORU.

EVREN SEKTOR YONLENDIRMESINDEN GELIR
------------------------------------
Sirket evreni BASARILI MOTOR CIKTILARINDAN kurulmaz. Kurulsaydi, motoru
coken veya verisi yetersiz olan sirketler rapordan sessizce DUSERDI ve
"sonuc yok" ile "hic bakilmadi" ayirt edilemezdi. Evren, yonlendirme
haritasindaki HER sirkettir; her biri mutlaka bir sonuc satiri alir.

MODUL SATIRI BUTUNLUGU
----------------------
M1, M3, Ek4, Ek1, Ek9 ve `good_count_ge8` sirket basina secilen TEK bir
`analytics.module_scores` satirindan gelir. Modulleri farkli tarihli
satirlardan toplamak, hicbir gunde birlikte var olmamis bir modul kumesini
tek skora cevirmek olurdu. Secim modul okuyucusunda row_number() ile satir
duzeyinde yapilir; bu dosya satiri PARCALAMAZ.

M2 SOZLESMESI
-------------
Sektor motorundan gelen M2, `module_scores` satirindaki eski m2 alaninin
YERINE GECER. Okuyucu zaten m2 sutununu hic SELECT etmez; ikisi birlikte
puanlanamaz.

DENENMIS KUMESI
---------------
Otoritatif silme "bu kosuda denenen" ticker/motor kumesini hedefler.
Bu kume EVRENIN TAMAMIDIR -- motoru coken sirketler de DENENMIS sayilir.
Aksi halde coken motora yonelen sirketlerin onceki basarili skorlari
tabloda kalir ve guncel sanilir.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from src.analytics.total_rasyo_combine import (
    STATUS_ENGINE_CRASHED,
    STATUS_INSUFFICIENT,
    STATUS_NOT_RUN,
    STATUS_OK,
    STATUS_ROUTING_CONFLICT,
    WEIGHTS_PROFILE,
    CompanyResult,
    combine_company_result,
    resolve_weights,
)
from src.analytics.total_rasyo_engine_isolation import (
    ENGINE_FAMILIES,
    RUN_FAILED,
    RUN_OK,
    RUN_SKIPPED,
    EngineIsolationError,
    EngineRun,
    resolve_engine_ownership,
    run_engine_safely,
    skipped_engine_run,
)
from src.analytics.total_rasyo_module_reader import (
    CompanyModuleContext,
    fetch_module_context,
)
from src.analytics.total_rasyo_persistence import (
    PersistenceError,
    persist_total_rasyo_report,
)

OVERALL_COMPLETE = "COMPLETE"
OVERALL_COMPLETE_NO_RESULTS = "COMPLETE_NO_RESULTS"
OVERALL_PARTIAL = "PARTIAL"
OVERALL_FAILED = "FAILED"

SCOPE_FULL = "FULL_UNIVERSE"
SCOPE_TARGETED = "TARGETED"

# Bilincli calistirilmayan motorun ESKI sonucuna ne olacagi.
#   OVERWRITE -> MOTOR_CALISTIRILMADI yazilir, eski skor SILINIR (varsayilan)
#   PRESERVE  -> sirket bu kosuda hic dokunulmaz, eski sonuc KALIR
# Varsayilan OVERWRITE'tir: sessizce duran bayat skor, gorunur bir
# "calistirilmadi" kaydindan daha tehlikelidir. PRESERVE yalniz cagiran
# ACIKCA istediginde uygulanir.
NOT_RUN_OVERWRITE = "OVERWRITE"
NOT_RUN_PRESERVE = "PRESERVE"

PERSIST_OK = "OK"
PERSIST_FAILED = "KALICILIK_HATASI"

# Sirket durumlari AYRIK ve TAM KAPSAYICI. Bir sirket tam olarak birine girer.
COMPANY_STATUS_COUNTERS: Mapping[str, str] = {
    STATUS_OK: "successful_company_count",
    STATUS_INSUFFICIENT: "insufficient_data_count",
    STATUS_ENGINE_CRASHED: "engine_failed_company_count",
    STATUS_NOT_RUN: "not_run_company_count",
    STATUS_ROUTING_CONFLICT: "routing_conflict_count",
}


class OrchestratorError(ValueError):
    pass


def _aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OrchestratorError(f"{name} timezone bilgili datetime olmali")
    return value


def _normalize_routing(routing: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(routing, Mapping) or not routing:
        raise OrchestratorError("routing bos olmayan mapping olmali")
    out: dict[str, str] = {}
    for ticker, family in routing.items():
        if not isinstance(ticker, str) or not ticker.strip():
            raise OrchestratorError("routing bos ticker iceriyor")
        if not isinstance(family, str) or family.strip().upper() not in ENGINE_FAMILIES:
            raise OrchestratorError(f"routing desteklenmeyen aile: {family}")
        kod = ticker.strip().upper()
        if kod in out:
            raise OrchestratorError(f"routing yinelenen ticker: {kod}")
        out[kod] = family.strip().upper()
    return out


def _result_to_mapping(r: CompanyResult) -> dict[str, Any]:
    return {
        "ticker": r.ticker, "routed_engine": r.routed_engine,
        "engine_status": r.engine_status, "engine_reason": r.engine_reason,
        "m2_score": r.m2_score, "m2_source": r.m2_source,
        "m2_source_at": r.m2_source_at, "m2_source_type": r.m2_source_type,
        "m2_missing": r.m2_missing, "valuation_confidence": r.valuation_confidence,
        "modules": r.modules, "good_count_ge8": r.good_count_ge8,
        "good_count_missing": r.good_count_missing,
        "base_score": r.base_score, "final_score": r.final_score,
        "total_rasyo_100": r.total_rasyo_100, "veto_flag": r.veto_flag,
        "decision": r.decision, "weights_profile": r.weights_profile,
        "total_rasyo_status": r.total_rasyo_status,
        "rejection_reason": r.rejection_reason,
        "insufficiency_reason": r.insufficiency_reason,
        "missing_modules": r.missing_modules,
        "data_confidence": r.data_confidence, "diagnostics": dict(r.diagnostics),
    }


def _engine_entry(run: EngineRun, routed_count: int) -> dict[str, Any]:
    return {
        "engine": run.engine, "status": run.status,
        "result_count": run.result_count, "rejection_count": run.rejection_count,
        "routed_company_count": routed_count,
        "error_type": run.error_type, "error_message": run.error_message,
        "duration_ms": run.duration_ms, "config_sha256": run.config_sha256,
        "diagnostics": dict(run.diagnostics),
    }


def _overall_status(engine_runs: Sequence[EngineRun],
                    results: Sequence[CompanyResult]) -> str:
    """
    DETERMINISTIK sozlesme. Ayirici soru: ORKESTRASYON mu bozuldu, yoksa
    orkestrasyon calisti da VERI mi yetersizdi?

      FAILED              -> orkestrasyon KULLANILAMAZ: motor var ama
                             hicbiri saglikli calismadi. (Kalicilik hatasi
                             da cagiran tarafindan FAILED'a cekilir.)
      PARTIAL             -> bazi motorlar coktu/atlandi veya bazi sirketler
                             motor cokmesi/cakisma yuzunden sonuc alamadi,
                             ama saglikli motorlar da var.
      COMPLETE_NO_RESULTS -> butun motorlar SAGLIKLI calisti fakat hicbir
                             sirket skor alamadi (hepsi YETERSIZ_VERI).
                             Calisma teknik olarak TAMAMLANDI; sorun veridedir.
      COMPLETE            -> motorlar saglikli ve en az bir sirket skor aldi.

    "Butun motorlar calisti ama veri yetersiz" durumunu FAILED saymak,
    "sistem bozuk" ile "veri yok"u ayni kefeye koyar ve operatoru yanlis
    yere baktirir. Bu yuzden ayri statu vardir.
    """
    calisan = [r for r in engine_runs if r.status == RUN_OK]
    if engine_runs and not calisan:
        return OVERALL_FAILED

    bozuk_motor = any(r.status in (RUN_FAILED, RUN_SKIPPED) for r in engine_runs)
    bozuk_sirket = any(
        r.total_rasyo_status in (STATUS_ENGINE_CRASHED, STATUS_NOT_RUN,
                                 STATUS_ROUTING_CONFLICT)
        for r in results
    )
    if bozuk_motor or bozuk_sirket:
        return OVERALL_PARTIAL

    # Buradan sonrasi: motorlarin hepsi saglikli, sirket duzeyinde
    # orkestrasyon kaynakli kayip YOK. Geriye yalniz veri yeterliligi kalir.
    if results and not any(r.total_rasyo_status == STATUS_OK for r in results):
        return OVERALL_COMPLETE_NO_RESULTS
    return OVERALL_COMPLETE


def _counters(results: Sequence[CompanyResult],
              engine_runs: Sequence[EngineRun]) -> dict[str, int]:
    sayaclar = {ad: 0 for ad in COMPANY_STATUS_COUNTERS.values()}
    eksik = {"missing_m1_count": 0, "missing_m2_count": 0, "missing_m3_count": 0,
             "missing_ek4_count": 0, "missing_ek1_count": 0,
             "missing_ek9_count": 0, "missing_good_count": 0}
    for r in results:
        ad = COMPANY_STATUS_COUNTERS.get(r.total_rasyo_status)
        if ad is None:
            raise OrchestratorError(f"sayaci olmayan durum: {r.total_rasyo_status}")
        sayaclar[ad] += 1
        if r.m2_missing:
            eksik["missing_m2_count"] += 1
        for anahtar, alan in [("M1", "missing_m1_count"), ("M3", "missing_m3_count"),
                              ("Ek4", "missing_ek4_count"), ("Ek1", "missing_ek1_count"),
                              ("Ek9", "missing_ek9_count")]:
            if (r.modules.get(anahtar) or {}).get("missing"):
                eksik[alan] += 1
        if r.good_count_missing:
            eksik["missing_good_count"] += 1

    toplam = sum(sayaclar.values())
    if toplam != len(results):
        # Ayriklik ihlali: bir sirket iki sayacta veya hic sayilmamis.
        raise OrchestratorError(
            f"sayac ayrikligi bozuldu: {toplam} != {len(results)}")

    return {
        "engine_error_count": sum(1 for r in engine_runs if r.status == RUN_FAILED),
        "company_count": len(results),
        **sayaclar, **eksik,
    }


def run_total_rasyo_orchestrator(
    conn: Any,
    *,
    analysis_at: datetime,
    routing: Mapping[str, str],
    engine_runners: Mapping[str, Callable[[], Any]],
    run_id: Optional[str] = None,
    weights: Optional[Mapping[str, Any]] = None,
    targeted_tickers: Optional[Iterable[str]] = None,
    not_run_policy: str = NOT_RUN_OVERWRITE,
    persist: bool = True,
    module_context_fetcher: Callable[..., Mapping[str, CompanyModuleContext]] = fetch_module_context,
    horizon_days: Optional[int] = None,
    max_context_age_days: Optional[int] = None,
) -> dict[str, Any]:
    """
    Butun sektor motorlarini yalitilmis calistirir, alti modulu birlestirir
    ve sonucu atomik olarak kalicilastirir.

    EVREN vs HEDEF: `routing` EVRENI tanimlar. `targeted_tickers` verilirse
    bu kosu YALNIZ o sirketleri hesaplar ve YALNIZ onlarin eski sonuclarini
    otoritatif olarak degistirir. Change-impact kosusu boyle calisir; ikisini
    yapistirmak, birkac sirketlik bir kosunun butun kesimi yeniden yazmasina
    yol acardi.

    Kalicilik basarisiz olursa sonuc BASARILI SAYILMAZ: overall_status
    FAILED'a cekilir ve `persistence_status=KALICILIK_HATASI` raporlanir.
    """
    kesim = _aware("analysis_at", analysis_at)
    yonlendirme = _normalize_routing(routing)
    # Agirliklar URETIM SINIRINDA yeniden dogrulanir. Config'in from_dict()
    # uzerinden gectigi VARSAYILAMAZ; cagiran dogrudan sozluk verebilir.
    dogrulanmis_agirlik = resolve_weights(weights)

    if not isinstance(engine_runners, Mapping):
        raise OrchestratorError("engine_runners mapping olmali")
    if not_run_policy not in (NOT_RUN_OVERWRITE, NOT_RUN_PRESERVE):
        raise OrchestratorError(f"gecersiz not_run_policy: {not_run_policy}")

    # HEDEF KUME: verilmezse evrenin tamami.
    if targeted_tickers is None:
        hedef = set(yonlendirme)
        kapsam = SCOPE_FULL
    else:
        hedef = set()
        for t in targeted_tickers:
            if not isinstance(t, str) or not t.strip():
                raise OrchestratorError("targeted_tickers bos ticker iceriyor")
            kod = t.strip().upper()
            if kod not in yonlendirme:
                # Evrende olmayan sirketi hedeflemek sessizce yok sayilmaz:
                # yonlendirmesi bilinmeyen sirkete hangi motorun bakacagi
                # belli degildir.
                raise OrchestratorError(f"hedeflenen ticker evrende yok: {kod}")
            hedef.add(kod)
        if not hedef:
            raise OrchestratorError("targeted_tickers bos olamaz")
        kapsam = SCOPE_TARGETED

    started_at = datetime.now(timezone.utc)
    kimlik = (run_id or f"tr-{uuid.uuid4().hex[:16]}").strip()

    # --- 1) Motorlari YALITILMIS calistir --------------------------------
    # Sira DETERMINISTIK; sonuc sirayla degismemeli ama kosu tekrarlanabilir
    # olmali diye yine de sabitlenir.
    yonlenen_sayi: dict[str, int] = {}
    for ticker, aile in yonlendirme.items():
        if ticker in hedef:
            yonlenen_sayi[aile] = yonlenen_sayi.get(aile, 0) + 1

    engine_runs: dict[str, EngineRun] = {}
    for aile in ENGINE_FAMILIES:
        if aile not in yonlenen_sayi and aile not in engine_runners:
            continue
        kosucu = engine_runners.get(aile)
        if kosucu is None:
            engine_runs[aile] = skipped_engine_run(aile, "MOTOR_CALISTIRILMADI")
            continue
        engine_runs[aile] = run_engine_safely(aile, kosucu)

    # --- 2) TEK MOTOR SAHIPLIGI ------------------------------------------
    sahiplik = resolve_engine_ownership(yonlendirme, engine_runs)

    # --- 3) Modul baglami: sirket basina TEK satir ------------------------
    kwargs: dict[str, Any] = {}
    if horizon_days is not None:
        kwargs["horizon_days"] = horizon_days
    if max_context_age_days is not None:
        kwargs["max_context_age_days"] = max_context_age_days
    modul_baglami = module_context_fetcher(
        conn, tickers=sorted(hedef), analysis_at=kesim, **kwargs)
    if not isinstance(modul_baglami, Mapping):
        raise OrchestratorError("module_context_fetcher mapping dondurmeli")

    # --- 4) Birlestirme: EVRENIN TAMAMI uzerinde -------------------------
    results: list[CompanyResult] = []
    korunan_ticker: list[str] = []
    for ticker in sorted(hedef):
        aile = yonlendirme[ticker]
        kosu = engine_runs.get(aile)
        # PRESERVE: bilincli calistirilmayan motorun sirketlerine HIC
        # dokunulmaz -- ne yazilir ne silinir. Eski sonuc oldugu gibi kalir.
        if (not_run_policy == NOT_RUN_PRESERVE
                and (kosu is None or kosu.status == RUN_SKIPPED)):
            korunan_ticker.append(ticker)
            continue
        results.append(combine_company_result(
            ticker=ticker,
            routed_engine=aile,
            engine_run=kosu,
            # Satiri OLMAYAN sirket icin None gecilir; combine bunu
            # MODUL_SATIRI_YOK olarak siniflar. Bos baglam UYDURULMAZ.
            module_context=modul_baglami.get(ticker),
            weights=weights,
            routing_conflict=sahiplik.conflicts.get(ticker),
        ))

    finished_at = datetime.now(timezone.utc)
    engine_entries = [
        _engine_entry(engine_runs[aile], yonlenen_sayi.get(aile, 0))
        for aile in sorted(engine_runs)
    ]

    rapor: dict[str, Any] = {
        "run_id": kimlik,
        "analysis_at": kesim,
        "started_at": started_at,
        "finished_at": finished_at,
        "overall_status": _overall_status(list(engine_runs.values()), results),
        "weights_profile": WEIGHTS_PROFILE,
        "engine_runs": engine_entries,
        "results": [_result_to_mapping(r) for r in results],
        "counters": _counters(results, list(engine_runs.values())),
        "engine_coverage": _engine_coverage(results, yonlenen_sayi),
        "diagnostics": {"weights": dict(dogrulanmis_agirlik)},
        "persistence_status": None,
        "persisted": False,
        "run_scope": kapsam,
        "universe_company_count": len(yonlendirme),
        "not_run_policy": not_run_policy,
        "preserved_tickers": tuple(korunan_ticker),
    }

    if not persist:
        return rapor

    # --- 5) Kalicilik: basarisizsa kosu BASARILI SAYILMAZ ----------------
    # Durum yazimdan ONCE OK olarak konur: transaction commit olduysa
    # kalicilik zaten basarilidir, olmadiysa satirin kendisi YOKTUR.
    # Sonradan set etmek, tabloda kalici olarak NULL birakirdi.
    rapor["persistence_status"] = PERSIST_OK
    try:
        yazilan = persist_total_rasyo_report(conn, rapor)
    except (PersistenceError, Exception) as exc:  # noqa: BLE001
        rapor["overall_status"] = OVERALL_FAILED
        rapor["persistence_status"] = PERSIST_FAILED
        rapor["persisted"] = False
        rapor["persistence_error"] = type(exc).__name__
        raise OrchestratorError(
            f"KALICILIK_HATASI: {type(exc).__name__}"
        ) from exc

    rapor["persistence_status"] = PERSIST_OK
    rapor["persisted"] = True
    rapor["persisted_counts"] = yazilan
    return rapor


def _engine_coverage(results: Sequence[CompanyResult],
                     yonlenen_sayi: Mapping[str, int]) -> list[dict[str, Any]]:
    """Sektor kapsam ozeti. coverage_ratio paydasi sifir olabilir."""
    ozet: dict[str, dict[str, int]] = {}
    for r in results:
        girdi = ozet.setdefault(r.routed_engine, {
            "routed_company_count": 0, "successful_count": 0,
            "insufficient_data_count": 0, "engine_failed_count": 0,
            "rejected_count": 0,
        })
        girdi["routed_company_count"] += 1
        if r.total_rasyo_status == STATUS_OK:
            girdi["successful_count"] += 1
        elif r.total_rasyo_status == STATUS_INSUFFICIENT:
            girdi["insufficient_data_count"] += 1
        elif r.total_rasyo_status == STATUS_ENGINE_CRASHED:
            girdi["engine_failed_count"] += 1
        else:
            girdi["rejected_count"] += 1

    cikti = []
    for engine in sorted(ozet):
        girdi = ozet[engine]
        toplam = girdi["routed_company_count"]
        cikti.append({
            "engine_name": engine, **girdi,
            "coverage_ratio": (girdi["successful_count"] / toplam) if toplam else None,
        })
    return cikti
