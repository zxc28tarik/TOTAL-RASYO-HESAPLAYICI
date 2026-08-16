"""
Change-impact — SAF TESPIT KATMANI.

Bu modul HICBIR degerleme veya Total Rasyo calistirmaz, veritabanina
YAZMAZ. Girdi bir finansal fact degisikligi, cikti bir ETKI PLANIDIR.
Uygulama ayri katmanin isidir.

LEAVE-ONE-OUT HEDEF BAZINDADIR
------------------------------
Motorlarda hedef sirket kendi emsal ornegine KATILMAZ. Bu yuzden "havuz
degisti mi" diye tek bir global soru sorulamaz; her HEDEF ticker icin ayri
ornek kurulur:

    ornek_once(T)  = uygunluk(havuz \\ {T}, ESKI deger)
    ornek_sonra(T) = uygunluk(havuz \\ {T}, YENI deger)

Degisen sirketin KENDISI DIRECT etki alir; kendisine PEER_PROPAGATED
yazilmaz. A'nin kendi PD/DD degisimi A'nin kendi emsal medyanini
etkilemez -- cunku A kendini emsal olarak kullanamaz. Aksi halde ayni
etki iki kez sayilir.

ESKI ∪ YENI EVREN
-----------------
Uygunluk yalnizca YENI degere gore hesaplanirsa, havuzdan CIKAN bir emsalin
diger sirketlerin ESKI degerlemelerinde hala medyana dahil oldugu gozden
kacar ve o skorlar sessizce bayat kalir. Bu yuzden aday evren
`eligible_before ∪ eligible_after` uzerinden yurutulur.

POTANSIYEL vs GERCEKLESEN
-------------------------
Registry'deki `failure_mode` bu kenar degisirse NE TUR sonuc DOGABILECEGINI
soyler. Kosuda GERCEKTEN olan sey plana `actual_effects[]` olarak yazilir.
Ikisi karistirilmaz.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from src.analytics.change_impact_periods import (
    PeriodError,
    affected_anchor_period_ends,
    period_ordinal,
)
from src.analytics.change_impact_registry import (
    DEPENDENCY_EDGES,
    PERIOD_8Q_GROUP,
    STATEMENT_TYPES,
    DependencyEdge,
    RegistryError,
    atomic_groups,
    edges_for_fact,
    registry_sha256,
)
from src.utils.missing_values import is_bool_like, is_missing_like

DETECTOR_VERSION = 1

IMPACT_DIRECT = "DIRECT"
IMPACT_PEER = "PEER_PROPAGATED"
IMPACT_MODULE = "MODULE_DEPENDENCY"

# BILGI TABANI. Ayni fact degisikliginden ciksalar bile PIT ve RESTATE
# planlari AYNI kimligi PAYLASAMAZ -- bilgi tabanlari farklidir ve bu
# farkli bir etki kumesi uretir.
KNOWLEDGE_PIT = "PIT_HISTORY"
KNOWLEDGE_RESTATE = "CURRENT_KNOWLEDGE_RESTATE"
KNOWLEDGE_BASES = (KNOWLEDGE_PIT, KNOWLEDGE_RESTATE)

# BOS PLAN NEDENLERI. Bos plan "hata" degildir, ama NEDENI onemlidir ve
# birbirine karistirilmamalidir:
#
#   NO_SCORING_DEPENDENCY  statement_type V20 KAPSAMINDA, fakat registry'de
#                          bu fact icin skorlayan kenar YOK. Su an
#                          CASH_FLOW'un tamami bu durumda: nakit akim
#                          tablosu kabul edilen bir kaynaktir ama hicbir
#                          motor nakit akim kalemi tuketmez.
#   DIAGNOSTIC_ONLY_CHANGE kenar VAR ama hepsi tanisal. Total Rasyo yeniden
#                          hesaplamasi gerekmez -- fakat bu "bu veri hicbir
#                          yerde tazelenmeyecek" DEMEK DEGILDIR;
#                          reconciliation/veri-kalitesi hatti tanisal
#                          alanlari ayrica tazeleyebilir.
#   LATEST_ONLY_EXPIRED    kenar var ama degisen donem artik son donem degil.
#
# KAPSAM DISI KAYNAK (MARKET_PRICE / NAV_REPORT) bu listede YOKTUR: o
# durumda plan bile uretilmez, ChangeImpactError atilir. Iki durum ayni
# sebep degildir.
EMPTY_NO_SCORING_DEPENDENCY = "NO_SCORING_DEPENDENCY"
EMPTY_DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY_CHANGE"
EMPTY_LATEST_ONLY_EXPIRED = "LATEST_ONLY_EXPIRED"

# GERCEKLESEN etkiler. Registry'nin potansiyel failure_mode'undan AYRIDIR.
EFFECT_ENTER = "ELIGIBILITY_ENTER"
EFFECT_EXIT = "ELIGIBILITY_EXIT"
EFFECT_VALUE = "STATISTIC_VALUE_CHANGE"
EFFECT_TTM_LOSS = "TTM_LOSS"
EFFECT_MIN_PEER = "MIN_PEER_COUNT_CROSSING"
EFFECT_VETO = "VETO_FLIP"


class ChangeImpactError(ValueError):
    pass


class EngineContractError(ChangeImpactError):
    """
    Motor cagrisinin veri/sozlesme invarianti bozuk (orn. NONFIN'de emsalin
    sector_code veya anchor_period_end uyusmazligi).

    Bu bir VERI BAGIMLILIGI DEGILDIR. Registry'ye kenar olarak konmaz ve
    "butun sektoru yeniden hesapla" plani URETILMEZ; plan FAIL-CLOSED durur.
    """


@dataclass(frozen=True)
class FactChange:
    """V20 girdisi. Haber/duyuru degil, FINANSAL FACT degisikligi."""
    ticker: str
    statement_type: str
    fact_key: str
    period_end: date
    old_value: Optional[float]
    new_value: Optional[float]
    published_at: datetime
    source_fact_id: str
    source_statement_id: str
    source_version_id: str
    # TEK MOTOR SAHIPLIGI (V19 ilkesi): bir sirket TEK sektor motoruna aittir.
    # Bu alan olmadan ayni fact adi butun motorlarin kenarlarini tetikler ve
    # GARFA icin INSURANCE/NONFIN/BANK planlari uretilir -- sessizce yanlis.
    routed_engine: str = ""
    accounting_profile: str = "BIST_IFRS"
    accounting_version: int = 1


@dataclass(frozen=True)
class PeerCandidate:
    """
    Emsal havuzu adayi. `eligible_before/after` motorun kendi uygunluk
    sozlesmesinden gelir; bu modul onu YENIDEN UYGULAMAZ, cagirandan alir.
    """
    ticker: str
    eligible_before: bool
    eligible_after: bool
    statistic_before: Optional[float] = None
    statistic_after: Optional[float] = None


@dataclass(frozen=True)
class ImpactEntry:
    impact_run_id: str
    source_fact_id: str
    source_statement_id: str
    source_version_id: str
    changed_period_end: date
    direct_ticker: str
    impacted_ticker: str
    impact_type: str
    engine_family: str
    module: str
    dependency_edge_id: str
    dependency_group_key: Optional[str]
    reason_code: str
    actual_effects: tuple[str, ...]
    effective_from: datetime
    affected_anchor_period_ends: tuple[date, ...]
    eligibility_scope: Optional[str]
    analysis_at: Optional[datetime]
    registry_sha256: str
    detector_version: int


@dataclass(frozen=True)
class ImpactPlan:
    impact_plan_id: str
    impact_run_id: str
    entries: tuple[ImpactEntry, ...]
    registry_sha256: str
    detector_version: int
    knowledge_basis: str = KNOWLEDGE_PIT
    run_scope: str = "TARGETED"
    knowledge_cutoff_at: Optional[datetime] = None
    analysis_at: Optional[datetime] = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def plan_sha256(self) -> str:
        """
        Planin KANONIK ICERIK ozeti. impact_plan_id kimlik, bu ise ICERIK.
        Ikisi ayri tutulur: ayni kimlikle FARKLI icerik gelirse catisma
        boylece tespit edilir.
        """
        govde = {
            "impact_plan_id": self.impact_plan_id,
            "registry_sha256": self.registry_sha256,
            "detector_version": self.detector_version,
            "knowledge_basis": self.knowledge_basis,
            "run_scope": self.run_scope,
            "entries": sorted(
                (e.impacted_ticker, e.impact_type, e.engine_family, e.module,
                 e.dependency_edge_id, e.reason_code,
                 ",".join(e.actual_effects),
                 ",".join(d.isoformat() for d in e.affected_anchor_period_ends))
                for e in self.entries),
        }
        ham = json.dumps(govde, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
        return hashlib.sha256(ham.encode("utf-8")).hexdigest()

    def targeted_tickers(self) -> tuple[str, ...]:
        return tuple(sorted({e.impacted_ticker for e in self.entries}))

    def engines(self) -> tuple[str, ...]:
        return tuple(sorted({e.engine_family for e in self.entries
                             if e.engine_family != "MODULE_PIPELINE"}))

    def modules(self) -> tuple[str, ...]:
        return tuple(sorted({e.module for e in self.entries}))


def _num(name: str, value: Any) -> Optional[float]:
    if is_missing_like(value):
        return None
    if is_bool_like(value):
        raise ChangeImpactError(f"{name} bool olamaz")
    try:
        sonuc = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ChangeImpactError(f"{name} sayiya cevrilemedi") from exc
    if sonuc != sonuc or sonuc in (float("inf"), float("-inf")):
        raise ChangeImpactError(f"{name} sonlu olmali")
    return sonuc


def _validate_change(change: FactChange) -> FactChange:
    if not isinstance(change, FactChange):
        raise ChangeImpactError("change FactChange olmali")
    if not isinstance(change.ticker, str) or not change.ticker.strip():
        raise ChangeImpactError("ticker dolu metin olmali")
    if change.statement_type not in STATEMENT_TYPES:
        raise ChangeImpactError(
            f"V20 yalniz {STATEMENT_TYPES} kabul eder: {change.statement_type}")
    if not isinstance(change.fact_key, str) or not change.fact_key.strip():
        raise ChangeImpactError("fact_key dolu metin olmali")
    if not isinstance(change.period_end, date):
        raise ChangeImpactError("period_end date olmali")
    period_ordinal(change.period_end)  # gercek ceyrek sonu mu
    if not isinstance(change.published_at, datetime) or change.published_at.tzinfo is None:
        raise ChangeImpactError("published_at timezone bilgili olmali")
    for alan in ("source_fact_id", "source_statement_id", "source_version_id"):
        deger = getattr(change, alan)
        if not isinstance(deger, str) or not deger.strip():
            raise ChangeImpactError(f"{alan} dolu metin olmali")
    if not isinstance(change.routed_engine, str) or not change.routed_engine.strip():
        raise ChangeImpactError(
            "routed_engine zorunlu: sirket TEK sektor motoruna aittir")
    if change.routed_engine.strip().upper() not in (
            "BANK", "NONFIN", "HOLDING", "GYO", "INSURANCE", "FINANCIAL"):
        raise ChangeImpactError(f"gecersiz routed_engine: {change.routed_engine}")
    eski = _num("old_value", change.old_value)
    yeni = _num("new_value", change.new_value)
    if eski is None and yeni is None:
        raise ChangeImpactError("old_value ve new_value birlikte bos olamaz")
    if eski == yeni:
        raise ChangeImpactError("degisiklik yok: old_value == new_value")
    return change


def _plan_id(change: FactChange, sha: str, *, knowledge_basis: str,
             run_scope: str, analysis_at: Optional[datetime],
             knowledge_cutoff_at: Optional[datetime]) -> str:
    """
    Kimlik YALNIZ kaynak revizyona dayanmaz. Kanonik girdiler:
      - degisen fact kumesi ve kaynak revizyon
      - analysis_at / etki baglami
      - registry_sha256
      - detector_version
      - run_scope ve knowledge_basis

    Ayni fact degisikligi PIT ve RESTATE icin FARKLI plan kimligi uretir.
    """
    ham = json.dumps({
        "knowledge_basis": knowledge_basis,
        "run_scope": run_scope,
        "analysis_at": None if analysis_at is None else analysis_at.isoformat(),
        "knowledge_cutoff_at": (None if knowledge_cutoff_at is None
                                else knowledge_cutoff_at.isoformat()),
        "source_fact_id": change.source_fact_id,
        "source_statement_id": change.source_statement_id,
        "source_version_id": change.source_version_id,
        "ticker": change.ticker.strip().upper(),
        "fact_key": change.fact_key.strip(),
        "statement_type": change.statement_type,
        "period_end": change.period_end.isoformat(),
        "old_value": repr(change.old_value),
        "new_value": repr(change.new_value),
        "registry_sha256": sha,
        "detector_version": DETECTOR_VERSION,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def _anchors(edge: DependencyEdge, change: FactChange,
             latest_period_end: Optional[date]) -> tuple[date, ...]:
    """
    Etkilenen anchor donemleri.

    LATEST_ONLY kenari, degisen ceyrek gercekten kullanilan EN SON donem
    degilse HIC yayilmaz -- iki ceyrek eski bir bilanco duzeltmesi son
    bilanco metrigini etkilemez.
    """
    if edge.latest_only:
        if latest_period_end is not None and change.period_end != latest_period_end:
            return ()
        return (change.period_end,)
    return tuple(affected_anchor_period_ends(
        change.period_end,
        affected_anchor_count=edge.affected_anchor_count,
        max_forward_period_offset=edge.max_forward_period_offset,
    ))


def _peer_effects(cand: PeerCandidate) -> tuple[str, ...]:
    etkiler: list[str] = []
    if cand.eligible_after and not cand.eligible_before:
        etkiler.append(EFFECT_ENTER)
    if cand.eligible_before and not cand.eligible_after:
        etkiler.append(EFFECT_EXIT)
    onceki = _num("statistic_before", cand.statistic_before)
    sonraki = _num("statistic_after", cand.statistic_after)
    if onceki != sonraki and (cand.eligible_before or cand.eligible_after):
        etkiler.append(EFFECT_VALUE)
    return tuple(etkiler)


def _sample(candidates: Sequence[PeerCandidate], target: str,
            *, after: bool) -> tuple[frozenset[str], tuple[float, ...]]:
    """
    HEDEF BAZLI leave-one-out ornegi. Hedef kendi ornegine GIRMEZ.
    """
    uyeler: list[str] = []
    degerler: list[float] = []
    for cand in candidates:
        if cand.ticker == target:
            continue
        uygun = cand.eligible_after if after else cand.eligible_before
        if not uygun:
            continue
        uyeler.append(cand.ticker)
        deger = cand.statistic_after if after else cand.statistic_before
        sayi = _num("statistic", deger)
        if sayi is not None:
            degerler.append(sayi)
    return frozenset(uyeler), tuple(sorted(degerler))


def detect_change_impact(
    change: FactChange,
    *,
    impact_run_id: str,
    knowledge_basis: str = KNOWLEDGE_PIT,
    run_scope: str = "TARGETED",
    knowledge_cutoff_at: Optional[datetime] = None,
    peer_candidates: Mapping[str, Sequence[PeerCandidate]] | None = None,
    minimum_peer_count: Mapping[str, int] | None = None,
    latest_period_end: Optional[date] = None,
    analysis_at: Optional[datetime] = None,
    quantile_fn: Optional[Callable[[Sequence[float]], tuple[float, ...]]] = None,
) -> ImpactPlan:
    """
    Saf etki tespiti. Deger uretmez, veritabanina dokunmaz.

    `peer_candidates`: engine_family -> aday listesi. Uygunluk motorun kendi
    sozlesmesinden gelir; bu modul onu yeniden uygulamaz.
    """
    change = _validate_change(change)
    if not isinstance(impact_run_id, str) or not impact_run_id.strip():
        raise ChangeImpactError("impact_run_id dolu metin olmali")
    if knowledge_basis not in KNOWLEDGE_BASES:
        raise ChangeImpactError(f"gecersiz knowledge_basis: {knowledge_basis}")
    if knowledge_basis == KNOWLEDGE_RESTATE and knowledge_cutoff_at is None:
        raise ChangeImpactError("RESTATE plani knowledge_cutoff_at gerektirir")
    if knowledge_cutoff_at is not None:
        if not isinstance(knowledge_cutoff_at, datetime) or knowledge_cutoff_at.tzinfo is None:
            raise ChangeImpactError("knowledge_cutoff_at tz bilgili olmali")
        if analysis_at is not None and knowledge_cutoff_at < analysis_at:
            raise ChangeImpactError("knowledge_cutoff_at analysis_at'ten once olamaz")
    sha = registry_sha256()
    kod = change.ticker.strip().upper()
    adaylar = peer_candidates or {}
    min_peer = minimum_peer_count or {}

    aile = change.routed_engine.strip().upper()
    # Sirketin KENDI motoru + motordan bagimsiz modul hatti. Diger sektor
    # motorlarinin kenarlari bu sirkete UYGULANMAZ.
    kenarlar = tuple(
        e for e in edges_for_fact(statement_type=change.statement_type,
                                  fact_key=change.fact_key.strip(),
                                  only_v20_triggers=True)
        if e.engine_family in (aile, "MODULE_PIPELINE")
    )

    skorlayan_kenar = [e for e in kenarlar
                       if e.dependency_role != "DIAGNOSTIC_ONLY"]
    entries: list[ImpactEntry] = []
    tanilar: dict[str, Any] = {
        "edge_count": len(kenarlar),
        "scoring_edge_count": len(skorlayan_kenar),
        "skipped_latest_only": 0,
        "peer_pools_examined": 0,
    }

    for edge in kenarlar:
        if edge.dependency_role == "DIAGNOSTIC_ONLY":
            # Tanisal alan skoru veya bandi degistirmez; plana girmez.
            continue
        anchors = _anchors(edge, change, latest_period_end)
        if not anchors:
            tanilar["skipped_latest_only"] += 1
            continue

        tur = IMPACT_MODULE if edge.engine_family == "MODULE_PIPELINE" else IMPACT_DIRECT
        etkiler: list[str] = [EFFECT_VALUE]
        if edge.failure_mode == "TTM_NULLIFIED" and (
                change.old_value is None or change.new_value is None):
            etkiler.append(EFFECT_TTM_LOSS)
        if edge.dependency_role == "VETO_INPUT":
            etkiler.append(EFFECT_VETO)

        # --- DIRECT: degisen sirketin KENDISI --------------------------
        entries.append(ImpactEntry(
            impact_run_id=impact_run_id, source_fact_id=change.source_fact_id,
            source_statement_id=change.source_statement_id,
            source_version_id=change.source_version_id,
            changed_period_end=change.period_end, direct_ticker=kod,
            impacted_ticker=kod, impact_type=tur,
            engine_family=edge.engine_family, module=edge.downstream_target,
            dependency_edge_id=edge.dependency_edge_id,
            dependency_group_key=edge.dependency_group_key,
            reason_code=f"{edge.transform_key}:{edge.dependency_role}",
            actual_effects=tuple(etkiler),
            effective_from=change.published_at,
            affected_anchor_period_ends=anchors,
            eligibility_scope=edge.eligibility_scope,
            analysis_at=analysis_at, registry_sha256=sha,
            detector_version=DETECTOR_VERSION))

        # --- PEER_PROPAGATED: HEDEF BAZLI leave-one-out -----------------
        if not edge.peer_propagates:
            continue
        havuz = list(adaylar.get(edge.engine_family, ()))
        if not havuz:
            continue
        tanilar["peer_pools_examined"] += 1
        esik = min_peer.get(edge.engine_family, 0)

        # ESKI ∪ YENI aday evreni: havuzdan CIKAN emsal de degerlendirilir.
        hedefler = sorted({
            c.ticker for c in havuz
            if (c.eligible_before or c.eligible_after) and c.ticker != kod
        })
        for hedef in hedefler:
            uye_once, deger_once = _sample(havuz, hedef, after=False)
            uye_sonra, deger_sonra = _sample(havuz, hedef, after=True)

            hedef_etkiler: list[str] = []
            if uye_once != uye_sonra:
                if kod in uye_sonra - uye_once:
                    hedef_etkiler.append(EFFECT_ENTER)
                if kod in uye_once - uye_sonra:
                    hedef_etkiler.append(EFFECT_EXIT)
            if deger_once != deger_sonra:
                hedef_etkiler.append(EFFECT_VALUE)
            # minimum_peer_count kapisi: deger kaymasi degil SONUC KAYBI.
            if esik and (len(uye_once) >= esik) != (len(uye_sonra) >= esik):
                hedef_etkiler.append(EFFECT_MIN_PEER)
            if quantile_fn is not None and not hedef_etkiler:
                if quantile_fn(deger_once) != quantile_fn(deger_sonra):
                    hedef_etkiler.append(EFFECT_VALUE)
            if not hedef_etkiler:
                continue

            entries.append(ImpactEntry(
                impact_run_id=impact_run_id, source_fact_id=change.source_fact_id,
                source_statement_id=change.source_statement_id,
                source_version_id=change.source_version_id,
                changed_period_end=change.period_end, direct_ticker=kod,
                impacted_ticker=hedef, impact_type=IMPACT_PEER,
                engine_family=edge.engine_family, module=edge.downstream_target,
                dependency_edge_id=edge.dependency_edge_id,
                dependency_group_key=edge.dependency_group_key,
                reason_code=f"{edge.transform_key}:{edge.dependency_role}",
                actual_effects=tuple(dict.fromkeys(hedef_etkiler)),
                effective_from=change.published_at,
                affected_anchor_period_ends=anchors,
                eligibility_scope=edge.eligibility_scope,
                analysis_at=analysis_at, registry_sha256=sha,
                detector_version=DETECTOR_VERSION))

    if not entries:
        if not kenarlar:
            tanilar["empty_reason"] = EMPTY_NO_SCORING_DEPENDENCY
        elif not skorlayan_kenar:
            tanilar["empty_reason"] = EMPTY_DIAGNOSTIC_ONLY
        elif tanilar["skipped_latest_only"]:
            tanilar["empty_reason"] = EMPTY_LATEST_ONLY_EXPIRED
        else:
            tanilar["empty_reason"] = EMPTY_NO_SCORING_DEPENDENCY

    plan = ImpactPlan(
        impact_plan_id=_plan_id(change, sha, knowledge_basis=knowledge_basis,
                                run_scope=run_scope, analysis_at=analysis_at,
                                knowledge_cutoff_at=knowledge_cutoff_at),
        impact_run_id=impact_run_id, entries=tuple(entries),
        registry_sha256=sha, detector_version=DETECTOR_VERSION,
        knowledge_basis=knowledge_basis, run_scope=run_scope,
        knowledge_cutoff_at=knowledge_cutoff_at, analysis_at=analysis_at,
        diagnostics=tanilar)
    _assert_atomic_groups(plan)
    return plan


def _assert_atomic_groups(plan: ImpactPlan) -> None:
    """
    ATOMIK GRUP BUTUNLUGU. M1 + Ek1 + GOOD_COUNT ayni upstream'den gelir;
    birinin tazelenip digerinin bayat kalmasi YASAKTIR. Plan gruptan yalniz
    bir kismini iceriyorsa fail-closed.
    """
    for grup, kenarlar in atomic_groups().items():
        beklenen = {e.downstream_target for e in kenarlar}
        gorulen = {e.module for e in plan.entries
                   if e.dependency_group_key == grup}
        if gorulen and gorulen != beklenen:
            raise EngineContractError(
                f"atomik grup eksik: {grup} bekleniyor {sorted(beklenen)}, "
                f"gorulen {sorted(gorulen)}")
