"""
Change-impact → READINESS BARIYERI → V19 orkestratörü.

TEMEL KURAL
-----------
Impact plani bir modulun etkilendigini soyluyorsa, o modulun GERCEKTEN
tazelendigi KANITLANMADAN Total Rasyo calistirilmaz. "Baska bir hat herhalde
guncellemistir" varsayimi, change-impact sisteminin varlik sebebini yok eder.

ESKI M1 + YENI M2 gibi sessiz karisim KESINLIKLE YASAKTIR.

"ESKI OLMAK" TEK BASINA SORUN DEGILDIR
--------------------------------------
Plan M3/Ek4/Ek9'u etkilenmis GOSTERMIYORSA, onlarin eski degerlerini
kullanmak DOGRUDUR -- registry bu modullerin finansal tablo degisikliginden
etkilenmedigini kanitliyor. Fail-closed kapisi yalniz PLANIN "etkilendi"
dedigi girdinin bayat kalmasini engeller.

TICKER BAZINDA ATOMIKLIK
------------------------
Bir ticker icin planin gerektirdigi BUTUN downstream girdiler hazirsa o
ticker orkestratöre gider. Hazir olmayan BASKA bir ticker yuzunden hazir
ticker'lar engellenmez. Ama TEK ticker icinde girdilerin bir kismi eski bir
kismi yeniyse o ticker CALISTIRILMAZ.

V19 DOKUNULMAZ
--------------
compute_total_rasyo() ve orkestratör mantigi degistirilmez. Readiness bu
V20 koprusunde bir HAZIRLIK BARIYERIDIR; V19 genel workflow yoneticisine
donusturulmez.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

from src.analytics.change_impact_detector import ImpactPlan
from src.analytics.change_impact_registry import atomic_groups

# Ticker bazinda hazirlik durumu
READY = "READY"
NOT_READY = "INPUTS_NOT_READY"

# Uygulama kosusu durumu
APP_READY = "READY"
APP_PARTIAL = "PARTIAL_NOT_READY"
APP_APPLIED = "APPLIED"
APP_FAILED = "FAILED"

# Hazir olmama nedenleri
REASON_NO_LINEAGE = "LINEAGE_KAYDI_YOK"
REASON_STALE_SOURCE = "ESKI_KAYNAK_SURUMU"
REASON_STALE_TIME = "URETIM_DEGISIKLIKTEN_ONCE"
REASON_WRONG_CUT = "YANLIS_ANALYSIS_AT"
REASON_WRONG_ENGINE = "YANLIS_MOTOR"
REASON_GROUP_PARTIAL = "ATOMIK_GRUP_EKSIK"

LINEAGE_SQL = """
SELECT ticker, module, engine_family, source_version_id, produced_at,
       calculation_profile, calculation_version, impact_plan_id
FROM analytics.module_production_lineage
WHERE analysis_at = %(analysis_at)s
  AND ticker = ANY(%(tickers)s::text[])
"""


class BridgeError(ValueError):
    pass


@dataclass(frozen=True)
class ModuleLineage:
    ticker: str
    module: str
    engine_family: Optional[str]
    source_version_id: Optional[str]
    produced_at: Optional[datetime]
    calculation_profile: Optional[str] = None
    calculation_version: Optional[int] = None
    impact_plan_id: Optional[str] = None


@dataclass(frozen=True)
class TickerReadiness:
    ticker: str
    status: str
    required_modules: tuple[str, ...]
    satisfied_modules: tuple[str, ...]
    missing_modules: tuple[str, ...]
    reasons: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadinessReport:
    impact_plan_id: str
    per_ticker: Mapping[str, TickerReadiness]
    planned_targets: int
    refreshed_targets: int
    not_ready_targets: int

    def ready_tickers(self) -> tuple[str, ...]:
        return tuple(sorted(t for t, r in self.per_ticker.items()
                            if r.status == READY))

    def not_ready_tickers(self) -> tuple[str, ...]:
        return tuple(sorted(t for t, r in self.per_ticker.items()
                            if r.status != READY))

    def overall_status(self) -> str:
        if not self.per_ticker:
            return APP_READY
        if self.not_ready_targets == 0:
            return APP_READY
        return APP_PARTIAL


def _required(plan: ImpactPlan) -> dict[str, set[str]]:
    """
    ticker -> planin YENI URETIM istedigi downstream modul kumesi.

    Plana GIRMEYEN modul (orn. finansal tablo degisikliginden etkilenmeyen
    M3/Ek4/Ek9) burada YER ALMAZ; eski degeri gecerlidir.
    """
    gerekli: dict[str, set[str]] = {}
    for entry in plan.entries:
        gerekli.setdefault(entry.impacted_ticker, set()).add(entry.module)
    return gerekli


def _plan_engine(plan: ImpactPlan) -> dict[tuple[str, str], str]:
    """(ticker, module) -> beklenen engine_family."""
    return {(e.impacted_ticker, e.module): e.engine_family for e in plan.entries}


def _atomic_expansion(gerekli: set[str]) -> tuple[set[str], bool]:
    """
    ATOMIK GRUP: gruptan biri etkilendiyse UCU DE hazir olmalidir.
    M1 tazelenip Ek1/GOOD_COUNT eski kalirsa skor tutarsiz olur.
    """
    genisletilmis = set(gerekli)
    genisledi = False
    for kenarlar in atomic_groups().values():
        hedefler = {e.downstream_target for e in kenarlar}
        if gerekli & hedefler and not hedefler <= gerekli:
            genisletilmis |= hedefler
            genisledi = True
    return genisletilmis, genisledi


def evaluate_readiness(
    plan: ImpactPlan,
    lineage: Iterable[ModuleLineage],
    *,
    expected_source_version_id: str,
    change_published_at: Optional[datetime] = None,
) -> ReadinessReport:
    """
    Saf readiness degerlendirmesi. Veritabanina dokunmaz.

    Bir modul HAZIR sayilir ancak ve ancak:
      - lineage kaydi VAR,
      - kaynak surumu plandaki revizyonla ESLESIYOR,
      - uretim zamani degisikligin yayin zamanindan SONRA,
      - motor ailesi plandakiyle uyumlu.
    """
    if not isinstance(plan, ImpactPlan):
        raise BridgeError("plan ImpactPlan olmali")
    if not isinstance(expected_source_version_id, str) or not expected_source_version_id.strip():
        raise BridgeError("expected_source_version_id dolu metin olmali")

    kayitlar: dict[tuple[str, str], ModuleLineage] = {}
    for satir in lineage:
        kayitlar[(satir.ticker.strip().upper(), satir.module)] = satir

    beklenen_motor = _plan_engine(plan)
    sonuc: dict[str, TickerReadiness] = {}

    for ticker, moduller in _required(plan).items():
        genisletilmis, grup_genisledi = _atomic_expansion(moduller)
        karsilanan: list[str] = []
        eksik: list[str] = []
        nedenler: dict[str, str] = {}

        for modul in sorted(genisletilmis):
            kayit = kayitlar.get((ticker, modul))
            if kayit is None:
                eksik.append(modul)
                nedenler[modul] = (REASON_GROUP_PARTIAL
                                   if grup_genisledi and modul not in moduller
                                   else REASON_NO_LINEAGE)
                continue
            if kayit.source_version_id != expected_source_version_id:
                eksik.append(modul)
                nedenler[modul] = REASON_STALE_SOURCE
                continue
            if (change_published_at is not None and kayit.produced_at is not None
                    and kayit.produced_at < change_published_at):
                # Kaynak surumu dogru gorunse bile uretim degisiklikten
                # ONCEYSE, o uretim bu revizyonu goremezdi.
                eksik.append(modul)
                nedenler[modul] = REASON_STALE_TIME
                continue
            beklenen = beklenen_motor.get((ticker, modul))
            if (beklenen is not None and kayit.engine_family is not None
                    and kayit.engine_family != beklenen):
                eksik.append(modul)
                nedenler[modul] = REASON_WRONG_ENGINE
                continue
            karsilanan.append(modul)

        sonuc[ticker] = TickerReadiness(
            ticker=ticker,
            status=READY if not eksik else NOT_READY,
            required_modules=tuple(sorted(genisletilmis)),
            satisfied_modules=tuple(sorted(karsilanan)),
            missing_modules=tuple(sorted(eksik)),
            reasons=nedenler)

    hazir = sum(1 for r in sonuc.values() if r.status == READY)
    return ReadinessReport(
        impact_plan_id=plan.impact_plan_id, per_ticker=sonuc,
        planned_targets=len(sonuc), refreshed_targets=hazir,
        not_ready_targets=len(sonuc) - hazir)


def fetch_module_lineage(conn: Any, *, tickers: Sequence[str],
                         analysis_at: datetime) -> list[ModuleLineage]:
    kodlar = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    if not kodlar:
        return []
    with conn.cursor() as cur:
        cur.execute(LINEAGE_SQL, {"analysis_at": analysis_at, "tickers": kodlar})
        return [ModuleLineage(*satir) for satir in cur.fetchall()]


def bridge_targeted_tickers(report: ReadinessReport) -> tuple[str, ...]:
    """
    V19'a verilecek hedef kume: YALNIZ bariyeri gecenler.

    Hazir olmayan ticker'i gecirmek, kismi eski/yeni karisim uretmek
    demektir -- yasak olan tam da budur.
    """
    return report.ready_tickers()


def application_counters(report: ReadinessReport,
                         orchestrated: Sequence[str] = (),
                         failed: Sequence[str] = ()) -> dict[str, int]:
    return {
        "planned_targets": report.planned_targets,
        "refreshed_targets": report.refreshed_targets,
        "verified_unchanged_targets": 0,
        "not_ready_targets": report.not_ready_targets,
        "failed_targets": len(failed),
        "orchestrated_tickers": len(orchestrated),
    }


def assert_no_plan_ticker_lost(plan: ImpactPlan, report: ReadinessReport,
                               orchestrated: Sequence[str]) -> None:
    """
    ZINCIR KANITI: impact_plan targeted -> readiness passed -> orchestrator
    targeted. Plandaki bir ticker'in sessizce kaybolmasi da hatadir.

    Orkestratöre giden kume, bariyeri gecen kumeye BIREBIR esit olmalidir;
    ne eksik ne fazla.
    """
    plan_kumesi = set(plan.targeted_tickers())
    rapor_kumesi = set(report.per_ticker)
    if plan_kumesi != rapor_kumesi:
        kayip = plan_kumesi - rapor_kumesi
        raise BridgeError(f"plan ticker'i readiness raporunda yok: {sorted(kayip)}")
    hazir = set(report.ready_tickers())
    gonderilen = set(orchestrated)
    if gonderilen - hazir:
        raise BridgeError(
            f"bariyeri gecmeyen ticker orkestratöre gonderildi: "
            f"{sorted(gonderilen - hazir)}")
    if hazir - gonderilen:
        raise BridgeError(
            f"bariyeri gecen ticker orkestratöre gonderilmedi: "
            f"{sorted(hazir - gonderilen)}")
