"""
Change-impact — PROVENANCE-AWARE BAGIMLILIK KAYIT DEFTERI.

KANONIK TANIM BURADADIR. PostgreSQL yalniz KAYDEDER; otorite degildir.
Runtime'da elle UPDATE edilen bir registry, gecmis impact planlarini
aciklanamaz hale getirir.

BAGIMLILIK != TETIKLEYICI
-------------------------
MARKET_PRICE ve NAV_REPORT kenarlari registry'de DURUR (gunluk fiyat DAG'i
ve ileride NAV/corporate-action tetikleyicisi ayni registry'yi kullanacak),
fakat V20 kapisindan gecmez. V20 yalniz:

    source_domain = FINANCIAL_STATEMENT
    statement_type in (BALANCE_SHEET, INCOME_STATEMENT, CASH_FLOW)
    trigger_enabled = True

AYNI METRIK, FARKLI PROVENANCE
------------------------------
`shares_out` NONFIN'de finansal ceyrek satirinin bir sutunu; HOLDING/GYO'da
NAV raporunun bir alani. Ayni mantiksal metrik, IKI AYRI KENAR. Bu yuzden
`metric_key` tek basina kimlik olamaz.

HARD_ERROR BURADA YOKTUR
------------------------
NONFIN'de emsalin sector_code/anchor_period_end uyusmazligi motoru dusurur.
Bu bir VERI BAGIMLILIGI degil, motor cagrisinin sozlesme ihlalidir; ayri
kanaldan (ENGINE_CONTRACT_ERROR) fail-closed ele alinir. Registry'nin gorevi
gercek veri bagimliliklarini aciklamaktir, program hatalarini degil.
`impact_blast_radius` yalniz COMPANY veya PEER_POOL olabilir.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Optional

REGISTRY_VERSION = 1

# ------------------------------------------------------------------ enum'lar
SOURCE_DOMAINS = ("FINANCIAL_STATEMENT", "NAV_REPORT", "MARKET_PRICE",
                  "CORPORATE_ACTION", "MARKET_REFERENCE")
STATEMENT_TYPES = ("BALANCE_SHEET", "INCOME_STATEMENT", "CASH_FLOW")
ENGINE_FAMILIES = ("BANK", "NONFIN", "HOLDING", "GYO", "INSURANCE",
                   "FINANCIAL", "MODULE_PIPELINE")
DEPENDENCY_ROLES = ("SCORE_INPUT", "PEER_STATISTIC_INPUT",
                    "PEER_ELIGIBILITY_INPUT", "VETO_INPUT", "DIAGNOSTIC_ONLY")
DOWNSTREAM_TARGETS = ("M2", "M1", "M3", "Ek1", "Ek4", "Ek9", "GOOD_COUNT")
WINDOW_KINDS = ("LATEST_ONLY", "TTM_4Q", "SERIES_8Q", "TRADING_DAYS")
# Registry'nin POTANSIYEL davranisi. Gerceklesen davranis plana
# actual_effects[] olarak yazilir; ikisi KARISTIRILMAZ.
FAILURE_MODES = ("VALUE_SHIFT", "POOL_DROP", "TTM_NULLIFIED", "VETO_FLIP")
BLAST_RADII = ("COMPANY", "PEER_POOL")

PEER_ROLES = ("PEER_STATISTIC_INPUT", "PEER_ELIGIBILITY_INPUT")

V20_TRIGGER_DOMAIN = "FINANCIAL_STATEMENT"


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class DependencyEdge:
    engine_family: str
    source_domain: str
    statement_type: Optional[str]
    source_fact_key: str
    fact_profile: str
    fact_profile_version: int
    transform_key: str
    metric_key: str
    derivation_profile: str
    derivation_version: int
    dependency_role: str
    downstream_target: str
    eligibility_scope: Optional[str]
    window_kind: str
    lookback_periods: int
    affected_anchor_count: int
    max_forward_period_offset: int
    latest_only: bool
    requires_contiguous_periods: bool
    failure_mode: str
    impact_blast_radius: str
    trigger_enabled: bool
    dependency_group_key: Optional[str] = None
    group_atomic: bool = False

    @property
    def peer_propagates(self) -> bool:
        """
        TURETILMIS alan -- SAKLANMAZ. Ayri bir mutable boolean tutmak ikinci
        bir truth kaynagi yaratir ve rolle celisebilir.
        """
        return self.dependency_role in PEER_ROLES

    @property
    def v20_triggers(self) -> bool:
        return (self.source_domain == V20_TRIGGER_DOMAIN
                and self.statement_type in STATEMENT_TYPES
                and self.trigger_enabled)

    def canonical(self) -> dict[str, Any]:
        return {k: v for k, v in sorted(asdict(self).items())}

    @property
    def dependency_edge_id(self) -> str:
        ham = json.dumps(self.canonical(), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def _validate(edge: DependencyEdge) -> DependencyEdge:
    if edge.engine_family not in ENGINE_FAMILIES:
        raise RegistryError(f"gecersiz engine_family: {edge.engine_family}")
    if edge.source_domain not in SOURCE_DOMAINS:
        raise RegistryError(f"gecersiz source_domain: {edge.source_domain}")
    # statement_type YALNIZ finansal tabloda dolu olabilir; aksi halde NULL.
    if edge.source_domain == V20_TRIGGER_DOMAIN:
        if edge.statement_type not in STATEMENT_TYPES:
            raise RegistryError("FINANCIAL_STATEMENT statement_type gerektirir")
    elif edge.statement_type is not None:
        raise RegistryError(
            f"{edge.source_domain} statement_type TASIYAMAZ")
    if edge.dependency_role not in DEPENDENCY_ROLES:
        raise RegistryError(f"gecersiz dependency_role: {edge.dependency_role}")
    if edge.downstream_target not in DOWNSTREAM_TARGETS:
        raise RegistryError(f"gecersiz downstream_target: {edge.downstream_target}")
    if edge.window_kind not in WINDOW_KINDS:
        raise RegistryError(f"gecersiz window_kind: {edge.window_kind}")
    if edge.failure_mode not in FAILURE_MODES:
        raise RegistryError(f"gecersiz failure_mode: {edge.failure_mode}")
    if edge.impact_blast_radius not in BLAST_RADII:
        raise RegistryError(
            f"gecersiz impact_blast_radius: {edge.impact_blast_radius}. "
            "ENGINE_FAMILY normal fact propagation sonucu OLAMAZ.")
    # Off-by-one korumasi: n anchor, 0..n-1 offset demektir.
    if edge.max_forward_period_offset != edge.affected_anchor_count - 1:
        raise RegistryError(
            f"{edge.metric_key}: max_forward_period_offset "
            f"affected_anchor_count-1 olmali")
    if edge.latest_only and edge.affected_anchor_count != 1:
        raise RegistryError("latest_only edge tek anchor etkiler")
    if edge.window_kind == "LATEST_ONLY" and not edge.latest_only:
        raise RegistryError("LATEST_ONLY window latest_only=True gerektirir")
    # Peer rolu ile blast radius tutarli olmali.
    if edge.peer_propagates and edge.impact_blast_radius != "PEER_POOL":
        raise RegistryError(f"{edge.metric_key}: peer rolu PEER_POOL gerektirir")
    if not edge.peer_propagates and edge.impact_blast_radius != "COMPANY":
        raise RegistryError(f"{edge.metric_key}: peer olmayan rol COMPANY olmali")
    if edge.eligibility_scope is not None:
        if not (edge.eligibility_scope == "GLOBAL_POOL"
                or edge.eligibility_scope.startswith("PER_MULTIPLE:")):
            raise RegistryError(f"gecersiz eligibility_scope: {edge.eligibility_scope}")
        if not edge.peer_propagates:
            raise RegistryError("eligibility_scope yalniz peer rollerinde anlamli")
    if edge.group_atomic and not edge.dependency_group_key:
        raise RegistryError("group_atomic dependency_group_key gerektirir")
    return edge


# ------------------------------------------------------------------ yardimci
def _fin(engine: str, statement: str, fact: str, transform: str, metric: str,
         role: str, target: str, *, scope: Optional[str] = None,
         window: str = "LATEST_ONLY", lookback: int = 1, anchors: int = 1,
         failure: str = "VALUE_SHIFT", contiguous: bool = False,
         group: Optional[str] = None, atomic: bool = False,
         derivation: str = "V1", derivation_version: int = 1) -> DependencyEdge:
    peer = role in PEER_ROLES
    return DependencyEdge(
        engine_family=engine, source_domain="FINANCIAL_STATEMENT",
        statement_type=statement, source_fact_key=fact,
        fact_profile="BIST_IFRS", fact_profile_version=1,
        transform_key=transform, metric_key=metric,
        derivation_profile=derivation, derivation_version=derivation_version,
        dependency_role=role, downstream_target=target,
        eligibility_scope=scope, window_kind=window,
        lookback_periods=lookback, affected_anchor_count=anchors,
        max_forward_period_offset=anchors - 1,
        latest_only=(window == "LATEST_ONLY"),
        requires_contiguous_periods=contiguous, failure_mode=failure,
        impact_blast_radius="PEER_POOL" if peer else "COMPANY",
        trigger_enabled=True, dependency_group_key=group, group_atomic=atomic)


def _nontrigger(engine: str, domain: str, fact: str, transform: str,
                metric: str, role: str, target: str, *,
                scope: Optional[str] = None,
                failure: str = "VALUE_SHIFT") -> DependencyEdge:
    """Registry'de GORUNUR ama V20'yi TETIKLEMEZ."""
    peer = role in PEER_ROLES
    return DependencyEdge(
        engine_family=engine, source_domain=domain, statement_type=None,
        source_fact_key=fact, fact_profile=domain, fact_profile_version=1,
        transform_key=transform, metric_key=metric,
        derivation_profile="V1", derivation_version=1,
        dependency_role=role, downstream_target=target,
        eligibility_scope=scope, window_kind="LATEST_ONLY",
        lookback_periods=1, affected_anchor_count=1,
        max_forward_period_offset=0, latest_only=True,
        requires_contiguous_periods=False, failure_mode=failure,
        impact_blast_radius="PEER_POOL" if peer else "COMPANY",
        trigger_enabled=False)


TTM = dict(window="TTM_4Q", lookback=4, anchors=4, contiguous=True)
S8Q = dict(window="SERIES_8Q", lookback=8, anchors=8, contiguous=True)

PERIOD_8Q_GROUP = "PERIOD_8Q_QUALITY_GROUP_V1"


def _build_edges() -> tuple[DependencyEdge, ...]:
    edges: list[DependencyEdge] = []

    # ---------------------------------------------------------- FINANCIAL
    # total_equity IKI AYRI YOL izler: biri istatistigi, digeri havuz
    # uyeligini degistirir. Tek edge'e sikistirilamaz.
    edges += [
        _fin("FINANCIAL", "BALANCE_SHEET", "total_equity",
             "MARKET_CAP_TO_BOOK", "current_pb", "PEER_STATISTIC_INPUT", "M2",
             scope="GLOBAL_POOL"),
        _fin("FINANCIAL", "BALANCE_SHEET", "total_equity",
             "EQUITY_BUFFER", "equity_buffer", "PEER_ELIGIBILITY_INPUT", "M2",
             scope="GLOBAL_POOL", failure="POOL_DROP"),
        _fin("FINANCIAL", "BALANCE_SHEET", "total_equity",
             "PB_MODEL_RANGE", "current_pb", "PEER_ELIGIBILITY_INPUT", "M2",
             scope="GLOBAL_POOL", failure="POOL_DROP"),
        _fin("FINANCIAL", "BALANCE_SHEET", "total_assets",
             "EQUITY_BUFFER", "equity_buffer", "PEER_ELIGIBILITY_INPUT", "M2",
             scope="GLOBAL_POOL", failure="POOL_DROP"),
        _fin("FINANCIAL", "INCOME_STATEMENT", "net_income",
             "TTM_SUM_4Q", "net_income_ttm", "PEER_STATISTIC_INPUT", "M2",
             scope="GLOBAL_POOL", **TTM),
        _fin("FINANCIAL", "INCOME_STATEMENT", "net_income",
             "ROE_TTM", "roe_ttm", "PEER_ELIGIBILITY_INPUT", "M2",
             scope="GLOBAL_POOL", failure="POOL_DROP", **TTM),
        _fin("FINANCIAL", "BALANCE_SHEET", "average_equity",
             "ROE_TTM", "roe_ttm", "PEER_ELIGIBILITY_INPUT", "M2",
             scope="GLOBAL_POOL", failure="POOL_DROP", **TTM),
        _fin("FINANCIAL", "BALANCE_SHEET", "shares_out",
             "MARKET_CAP", "current_pb", "PEER_STATISTIC_INPUT", "M2",
             scope="GLOBAL_POOL"),
        _fin("FINANCIAL", "BALANCE_SHEET", "finance_receivables",
             "NPL_RATIO", "npl_ratio", "DIAGNOSTIC_ONLY", "M2"),
        _fin("FINANCIAL", "BALANCE_SHEET", "npl_gross",
             "NPL_RATIO", "npl_ratio", "DIAGNOSTIC_ONLY", "M2"),
        _fin("FINANCIAL", "BALANCE_SHEET", "provisions",
             "PROVISION_COVERAGE", "provision_coverage", "DIAGNOSTIC_ONLY", "M2"),
    ]

    # ---------------------------------------------------------- INSURANCE
    edges += [
        _fin("INSURANCE", "BALANCE_SHEET", "total_equity",
             "MARKET_CAP_TO_BOOK", "current_pb", "PEER_STATISTIC_INPUT", "M2",
             scope="GLOBAL_POOL"),
        _fin("INSURANCE", "BALANCE_SHEET", "total_equity",
             "PB_MODEL_RANGE", "current_pb", "PEER_ELIGIBILITY_INPUT", "M2",
             scope="GLOBAL_POOL", failure="POOL_DROP"),
        _fin("INSURANCE", "INCOME_STATEMENT", "net_income",
             "TTM_SUM_4Q", "net_income_ttm", "PEER_STATISTIC_INPUT", "M2",
             scope="GLOBAL_POOL", **TTM),
        _fin("INSURANCE", "INCOME_STATEMENT", "net_income",
             "ROE_TTM", "roe_ttm", "PEER_ELIGIBILITY_INPUT", "M2",
             scope="GLOBAL_POOL", failure="POOL_DROP", **TTM),
        _fin("INSURANCE", "BALANCE_SHEET", "shares_out",
             "MARKET_CAP", "current_pb", "PEER_STATISTIC_INPUT", "M2",
             scope="GLOBAL_POOL"),
        _fin("INSURANCE", "INCOME_STATEMENT", "written_premiums",
             "TECHNICAL_MARGIN", "technical_margin", "DIAGNOSTIC_ONLY", "M2", **TTM),
        _fin("INSURANCE", "INCOME_STATEMENT", "technical_result",
             "TECHNICAL_MARGIN", "technical_margin", "DIAGNOSTIC_ONLY", "M2", **TTM),
    ]

    # ---------------------------------------------------------- NONFIN
    # Carpan-bazli havuz: emsal PE orneginden duser ama PB/PS'te KALIR.
    edges += [
        _fin("NONFIN", "INCOME_STATEMENT", "net_income",
             "TTM_SUM_4Q", "net_income_ttm", "PEER_STATISTIC_INPUT", "M2",
             scope="PER_MULTIPLE:PE", **TTM),
        _fin("NONFIN", "INCOME_STATEMENT", "net_income",
             "TTM_POSITIVITY_GATE", "net_income_ttm", "PEER_ELIGIBILITY_INPUT",
             "M2", scope="PER_MULTIPLE:PE", failure="POOL_DROP", **TTM),
        _fin("NONFIN", "INCOME_STATEMENT", "net_income",
             "TTM_COMPLETENESS", "net_income_ttm", "SCORE_INPUT", "M2",
             failure="TTM_NULLIFIED", **TTM),
        _fin("NONFIN", "INCOME_STATEMENT", "revenue",
             "TTM_SUM_4Q", "revenue_ttm", "PEER_STATISTIC_INPUT", "M2",
             scope="PER_MULTIPLE:PS", **TTM),
        _fin("NONFIN", "INCOME_STATEMENT", "revenue",
             "TTM_COMPLETENESS", "revenue_ttm", "SCORE_INPUT", "M2",
             failure="TTM_NULLIFIED", **TTM),
        _fin("NONFIN", "INCOME_STATEMENT", "ebit",
             "TTM_SUM_4Q", "ebit_ttm", "PEER_STATISTIC_INPUT", "M2",
             scope="PER_MULTIPLE:EV_EBIT", **TTM),
        _fin("NONFIN", "INCOME_STATEMENT", "ebit",
             "TTM_COMPLETENESS", "ebit_ttm", "SCORE_INPUT", "M2",
             failure="TTM_NULLIFIED", **TTM),
        # Bilanco kalemleri YALNIZ SON ceyrekten gelir -> latest_only.
        _fin("NONFIN", "BALANCE_SHEET", "total_equity",
             "MARKET_CAP_TO_BOOK", "current_pb", "PEER_STATISTIC_INPUT", "M2",
             scope="PER_MULTIPLE:PB"),
        _fin("NONFIN", "BALANCE_SHEET", "debt_st",
             "NET_DEBT", "net_debt", "PEER_STATISTIC_INPUT", "M2",
             scope="PER_MULTIPLE:EV_EBIT"),
        _fin("NONFIN", "BALANCE_SHEET", "debt_lt",
             "NET_DEBT", "net_debt", "PEER_STATISTIC_INPUT", "M2",
             scope="PER_MULTIPLE:EV_EBIT"),
        _fin("NONFIN", "BALANCE_SHEET", "cash_and_eq",
             "NET_DEBT", "net_debt", "PEER_STATISTIC_INPUT", "M2",
             scope="PER_MULTIPLE:EV_EBIT"),
        _fin("NONFIN", "BALANCE_SHEET", "st_investments",
             "NET_DEBT", "net_debt", "PEER_STATISTIC_INPUT", "M2",
             scope="PER_MULTIPLE:EV_EBIT"),
        # NONFIN'de shares_out finansal ceyrek satirinin bir SUTUNU.
        _fin("NONFIN", "BALANCE_SHEET", "shares_out",
             "MARKET_CAP", "market_cap", "PEER_STATISTIC_INPUT", "M2",
             scope="GLOBAL_POOL"),
    ]

    # ---------------------------------------------------------- BANK
    # Kesitsel emsal YOK; kendi 8 donemlik zaman dizisi.
    edges += [
        _fin("BANK", "INCOME_STATEMENT", "net_income",
             "ROE_SERIES_8Q", "roe_sus", "SCORE_INPUT", "M2", **S8Q),
        _fin("BANK", "BALANCE_SHEET", "total_equity",
             "ROE_SERIES_8Q", "roe_sus", "SCORE_INPUT", "M2", **S8Q),
        _nontrigger("BANK", "NAV_REPORT", "nav_per_share",
                    "NAV_DISCOUNT", "nav_discount", "PEER_STATISTIC_INPUT", "M2",
                    scope="GLOBAL_POOL"),
    ]

    # ---------------------------------------------------------- HOLDING / GYO
    # M2 tamamen NAV + fiyat kaynakli. V20 finansal fact tetikleyicisi YOK.
    for engine, pd_metric in (("HOLDING", "current_discount"),
                              ("GYO", "current_pd_nav")):
        edges += [
            _nontrigger(engine, "NAV_REPORT", "nav_total", "NAV_PER_SHARE",
                        pd_metric, "PEER_STATISTIC_INPUT", "M2",
                        scope="GLOBAL_POOL"),
            _nontrigger(engine, "NAV_REPORT", "shares_out", "NAV_PER_SHARE",
                        pd_metric, "PEER_STATISTIC_INPUT", "M2",
                        scope="GLOBAL_POOL"),
            _nontrigger(engine, "NAV_REPORT", "nav_asof_date", "NAV_FRESHNESS",
                        "nav_age_days", "PEER_ELIGIBILITY_INPUT", "M2",
                        scope="GLOBAL_POOL", failure="POOL_DROP"),
            _nontrigger(engine, "MARKET_PRICE", "current_price", "MARKET_CAP",
                        pd_metric, "PEER_STATISTIC_INPUT", "M2",
                        scope="GLOBAL_POOL"),
        ]

    # ------------------------------------------------- MODULE_PIPELINE
    # ATOMIK GRUP: ayni upstream, UC AYRI downstream. Birinin tazelenip
    # digerinin bayat kalmasi YASAK.
    # ORTAK UPSTREAM: analytics.rsc_summary_quarterly -> period_8q_comparison.
    # quality_trend_score ve good_count_latest AYNI ceyreklik kaynaktan
    # turetilir. V20 tetikleyicisi turetilmis analytics sutunu DEGIL, o
    # sutunlari besleyen FINANSAL TABLO fact'idir. Aksi halde grup yalniz
    # kismen tetiklenir ve M1 bayat kalirken Ek1/veto tazelenir.
    for fact, statement in (("net_income", "INCOME_STATEMENT"),
                            ("revenue", "INCOME_STATEMENT"),
                            ("total_equity", "BALANCE_SHEET")):
        for transform, metric, role, target in (
            ("PERIOD_8Q_TREND", "m1", "SCORE_INPUT", "M1"),
            ("PERIOD_8Q_GOODCOUNT", "ek1", "SCORE_INPUT", "Ek1"),
            ("PERIOD_8Q_GOODCOUNT", "good_count_ge8", "VETO_INPUT", "GOOD_COUNT"),
        ):
            edges.append(_fin(
                "MODULE_PIPELINE", statement, fact,
                transform, metric, role, target,
                failure="VETO_FLIP" if role == "VETO_INPUT" else "VALUE_SHIFT",
                group=PERIOD_8Q_GROUP, atomic=True, **S8Q))

    # Fiyat kaynakli moduller: registry'de VAR, V20 TETIKLEMEZ.
    for fact, metric, target in (("adj_close", "alpha_score", "M3"),
                                 ("adj_close", "momentum_20d", "Ek4"),
                                 ("adj_close", "volatility_63d", "Ek9")):
        edges.append(_nontrigger(
            "MODULE_PIPELINE", "MARKET_PRICE", fact, "PRICE_SERIES",
            metric, "SCORE_INPUT", target))

    return tuple(_validate(edge) for edge in edges)


DEPENDENCY_EDGES: tuple[DependencyEdge, ...] = _build_edges()


def registry_sha256(edges: Iterable[DependencyEdge] = DEPENDENCY_EDGES) -> str:
    """Butun kenar tanimlarinin kanonik ozeti. impact_run bunu tasir."""
    kimlikler = sorted(edge.dependency_edge_id for edge in edges)
    ham = json.dumps({"version": REGISTRY_VERSION, "edges": kimlikler},
                     separators=(",", ":"))
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def edges_for_fact(
    *,
    statement_type: str,
    fact_key: str,
    engine_family: Optional[str] = None,
    only_v20_triggers: bool = True,
) -> tuple[DependencyEdge, ...]:
    """Bir finansal fact degisikliginin dokundugu kenarlar."""
    if statement_type not in STATEMENT_TYPES:
        raise RegistryError(f"gecersiz statement_type: {statement_type}")
    if not isinstance(fact_key, str) or not fact_key.strip():
        raise RegistryError("fact_key dolu metin olmali")
    anahtar = fact_key.strip()
    return tuple(
        edge for edge in DEPENDENCY_EDGES
        if edge.statement_type == statement_type
        and edge.source_fact_key == anahtar
        and (engine_family is None or edge.engine_family == engine_family)
        and (not only_v20_triggers or edge.v20_triggers)
    )


def atomic_group_members(group_key: str) -> tuple[DependencyEdge, ...]:
    return tuple(e for e in DEPENDENCY_EDGES
                 if e.dependency_group_key == group_key)


def atomic_groups() -> Mapping[str, tuple[DependencyEdge, ...]]:
    gruplar: dict[str, list[DependencyEdge]] = {}
    for edge in DEPENDENCY_EDGES:
        if edge.group_atomic and edge.dependency_group_key:
            gruplar.setdefault(edge.dependency_group_key, []).append(edge)
    return {k: tuple(v) for k, v in gruplar.items()}
