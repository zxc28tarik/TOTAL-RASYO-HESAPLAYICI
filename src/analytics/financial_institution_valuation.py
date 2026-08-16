from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.utils.missing_values import is_missing_like as _is_missing_like
from zoneinfo import ZoneInfo

STATUS_OK = "OK"
STATUS_INSUFFICIENT = "YETERSIZ_VERI"
STATUS_TOO_WIDE = "BAND_TOO_WIDE"
MAX_ABS_NUMBER = 1e100
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
# Banka disi finansal kuruluslar. Bunlari BANK veya NONFIN motoruna sokmak yanlistir:
#   - mevduat toplamazlar, bilanco yapisi ve sermaye rejimi bankadan farklidir
#   - bilancolari finansman alacagi agirliklidir, NONFIN'in FD/FAVOK mantigi calismaz
# Alt gruplar AYRI emsal havuzlaridir; faktoring ile leasing ayni gruba konmaz.
SUPPORTED_BUSINESS_TYPES = frozenset({"FACTORING", "LEASING", "CONSUMER_FINANCE"})


class FinancialInstitutionValuationError(ValueError):
    pass


def _is_bool_like(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    try:
        import numpy as np  # type: ignore
    except ImportError:
        np = None
    if np is not None and isinstance(value, np.bool_):
        return True
    return getattr(getattr(value, "dtype", None), "kind", None) == "b"


def _finite_number(
    name: str,
    value: Any,
    *,
    minimum: float | None = None,
    strict_minimum: bool = False,
    maximum: float | None = None,
) -> float:
    if _is_bool_like(value) or isinstance(value, (list, tuple, dict, set)):
        raise FinancialInstitutionValuationError(f"{name} sonlu sayi olmali")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise FinancialInstitutionValuationError(f"{name} sonlu sayi olmali") from exc
    if not math.isfinite(result) or abs(result) > MAX_ABS_NUMBER:
        raise FinancialInstitutionValuationError(f"{name} sonlu sayi sinirinda olmali")
    if minimum is not None:
        if strict_minimum and result <= minimum:
            raise FinancialInstitutionValuationError(f"{name} {minimum} degerinden buyuk olmali")
        if not strict_minimum and result < minimum:
            raise FinancialInstitutionValuationError(f"{name} {minimum} degerinden kucuk olamaz")
    if maximum is not None and result > maximum:
        raise FinancialInstitutionValuationError(f"{name} {maximum} degerini asamaz")
    return result



def _optional_finite(
    name: str,
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if _is_missing_like(value):
        return None
    return _finite_number(name, value, minimum=minimum, maximum=maximum)


def _strict_text(name: str, value: Any, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FinancialInstitutionValuationError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _strict_date(name: str, value: Any) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise FinancialInstitutionValuationError(f"{name} date olmali")
    return value


def _aware_datetime(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FinancialInstitutionValuationError(f"{name} timezone iceren datetime olmali")
    return value


def _strict_int(name: str, value: Any, *, minimum: int = 0) -> int:
    if _is_bool_like(value) or not isinstance(value, int):
        raise FinancialInstitutionValuationError(f"{name} Python int olmali")
    if value < minimum:
        raise FinancialInstitutionValuationError(f"{name} {minimum} degerinden kucuk olamaz")
    return value


def _strict_sha256(name: str, value: Any) -> str:
    text = _strict_text(name, value).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise FinancialInstitutionValuationError(f"{name} 64 karakter hex SHA256 olmali")
    return text


def _stable_json_sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _quantile_linear(values: Sequence[float], q: float) -> float:
    if not values:
        raise FinancialInstitutionValuationError("quantile bos veriyle hesaplanamaz")
    quantile = _finite_number("quantile", q, minimum=0.0, maximum=1.0)
    ordered = sorted(_finite_number("multiple", value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * quantile
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    fraction = pos - lo
    return ordered[lo] * (1.0 - fraction) + ordered[hi] * fraction


def _weighted_geometric(values: Sequence[float], weights: Sequence[float]) -> float:
    if len(values) != len(weights) or not values:
        raise FinancialInstitutionValuationError("geometrik birlestirme girdileri gecersiz")
    total = sum(weights)
    if total <= 0:
        raise FinancialInstitutionValuationError("geometrik birlestirme agirligi pozitif olmali")
    return math.exp(sum((w / total) * math.log(v) for v, w in zip(values, weights, strict=True)))


def _valuation_score(price: float, low: float, mid: float, high: float) -> tuple[float, float]:
    if not (0 < low <= mid <= high):
        raise FinancialInstitutionValuationError("valuation band geometrisi gecersiz")
    log_scale = max(math.log(high / low), 1e-12)
    z = math.log(mid / price) / (log_scale / 2.0)
    score = 1.0 / (1.0 + math.exp(-1.6 * max(min(z, 20.0), -20.0)))
    return float(score), float(z)


def _quarter_end(value: date) -> bool:
    return (value.month, value.day) in {(3, 31), (6, 30), (9, 30), (12, 31)}




@dataclass(frozen=True)
class FinancialInstitutionValuationConfig:
    """
    Banka disi finansal kurulus (faktoring / leasing / tuketici finansmani)
    goreli degerleme sozlesmesi.

    PD/DD ANA yontemdir: bu sirketlerin degeri ozkaynak uretkenligine baglidir ve
    ozkaynak defter degeri anlamli bir capadir. F/K yalnizca kar POZITIF ve
    surdurulebilir oldugunda ikincil yontem olarak devreye girer.

    Aktif kalitesi gostergeleri (takip orani, karsilik kapsami, net finansman
    marji, ozkaynak tamponu) bandi KEYFI bicimde sismez; yalniz guven katsayisina
    ve tani alanlarina girer. Bu, sigorta motorundaki teknik gosterge ilkesiyle
    ayni sozlesmedir.
    """
    valuation_profile: str
    valuation_version: int
    source_metrics_profile: str
    source_metrics_version: int
    accounting_profile: str
    accounting_version: int
    share_basis: str
    currency: str = "TRY"
    lower_quantile: float = 0.25
    upper_quantile: float = 0.75
    minimum_peer_count: int = 2
    full_confidence_peer_count: int = 6
    minimum_method_count: int = 1
    minimum_pb: float = 0.05
    maximum_pb: float = 6.0
    minimum_pe: float = 1.0
    maximum_pe: float = 60.0
    pb_weight: float = 0.70
    pe_weight: float = 0.30
    max_statement_age_days: int = 220
    full_freshness_days: int = 100
    max_price_age_days: int = 7
    minimum_source_confidence: float = 0.40
    max_halfwidth: float = 1.25
    band_width_shadow_mode: bool = True
    valuation_axis_weight: float = 0.65
    follow_axis_weight: float = 0.35
    # F/K yalnizca kar surdurulebilir gorundugunde acilir.
    minimum_pe_roe: float = 0.02
    # Aktif kalitesi guven katsayilari (yer tutucu; gercek dagilimla kalibre edilecek)
    npl_full_confidence: float = 0.03
    npl_zero_confidence: float = 0.25
    coverage_full_confidence: float = 0.80
    minimum_equity_buffer: float = 0.05

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FinancialInstitutionValuationConfig":
        if not isinstance(data, Mapping):
            raise FinancialInstitutionValuationError("financial institution config nesne olmali")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(data) - allowed
        if unknown:
            raise FinancialInstitutionValuationError(
                "financial institution bilinmeyen alanlar: "
                + ", ".join(sorted(repr(x) for x in unknown))
            )
        kwargs: dict[str, Any] = {
            "valuation_profile": _strict_text("valuation_profile", data.get("valuation_profile")),
            "valuation_version": _strict_int("valuation_version", data.get("valuation_version"), minimum=1),
            "source_metrics_profile": _strict_text("source_metrics_profile", data.get("source_metrics_profile")),
            "source_metrics_version": _strict_int("source_metrics_version", data.get("source_metrics_version"), minimum=1),
            "accounting_profile": _strict_text("accounting_profile", data.get("accounting_profile"), uppercase=True),
            "accounting_version": _strict_int("accounting_version", data.get("accounting_version"), minimum=1),
            "share_basis": _strict_text("share_basis", data.get("share_basis"), uppercase=True),
            "currency": _strict_text("currency", data.get("currency", "TRY"), uppercase=True),
        }
        sayisal = {
            "lower_quantile": (0.0, 1.0), "upper_quantile": (0.0, 1.0),
            "minimum_pb": (0.0, None), "maximum_pb": (0.0, None),
            "minimum_pe": (0.0, None), "maximum_pe": (0.0, None),
            "pb_weight": (0.0, 1.0), "pe_weight": (0.0, 1.0),
            "minimum_source_confidence": (0.0, 1.0), "max_halfwidth": (0.0, None),
            "valuation_axis_weight": (0.0, 1.0), "follow_axis_weight": (0.0, 1.0),
            "minimum_pe_roe": (None, None),
            "npl_full_confidence": (0.0, 1.0), "npl_zero_confidence": (0.0, 1.0),
            "coverage_full_confidence": (0.0, None), "minimum_equity_buffer": (0.0, 1.0),
        }
        for isim, (lo, hi) in sayisal.items():
            varsayilan = cls.__dataclass_fields__[isim].default
            kwargs[isim] = _finite_number(isim, data.get(isim, varsayilan), minimum=lo, maximum=hi)
        tamsayi = {
            "minimum_peer_count": 1, "full_confidence_peer_count": 1,
            "minimum_method_count": 1, "max_statement_age_days": 1,
            "full_freshness_days": 1, "max_price_age_days": 0,
        }
        for isim, alt in tamsayi.items():
            varsayilan = cls.__dataclass_fields__[isim].default
            kwargs[isim] = _strict_int(isim, data.get(isim, varsayilan), minimum=alt)
        golge = data.get("band_width_shadow_mode", cls.__dataclass_fields__["band_width_shadow_mode"].default)
        if type(golge) is not bool:
            raise FinancialInstitutionValuationError("band_width_shadow_mode Python bool olmali")
        kwargs["band_width_shadow_mode"] = golge
        return _validated_config(cls(**kwargs))

    @classmethod
    def load(cls, path: Any) -> "FinancialInstitutionValuationConfig":
        text = Path(path).read_text(encoding="utf-8")
        return cls.from_dict(json.loads(text))

    @property
    def config_sha256(self) -> str:
        return _stable_json_sha({
            key: getattr(self, key) for key in sorted(self.__dataclass_fields__)
        })


def _validated_config(config: FinancialInstitutionValuationConfig) -> FinancialInstitutionValuationConfig:
    """
    ILKE 16: dataclass DOGRUDAN kurulumu dogrulamalari atlayamaz.
    from_dict icindeki _strict_int/_finite_number kapilari bypass edilebildigi
    icin ayni sinirlar burada da denetlenir.
    """
    if not isinstance(config, FinancialInstitutionValuationConfig):
        raise FinancialInstitutionValuationError("config FinancialInstitutionValuationConfig olmali")
    for alan, alt in (
        ("minimum_peer_count", 1), ("full_confidence_peer_count", 1),
        ("minimum_method_count", 1), ("max_statement_age_days", 1),
        ("full_freshness_days", 1), ("max_price_age_days", 0),
        ("valuation_version", 1), ("source_metrics_version", 1), ("accounting_version", 1),
    ):
        deger = getattr(config, alan)
        if isinstance(deger, bool) or not isinstance(deger, int) or deger < alt:
            raise FinancialInstitutionValuationError(f"{alan} en az {alt} olan int olmali")
    for alan in (
        "lower_quantile", "upper_quantile", "minimum_pb", "maximum_pb",
        "minimum_pe", "maximum_pe", "pb_weight", "pe_weight",
        "minimum_source_confidence", "max_halfwidth", "valuation_axis_weight",
        "follow_axis_weight", "minimum_pe_roe", "npl_full_confidence",
        "npl_zero_confidence", "coverage_full_confidence", "minimum_equity_buffer",
    ):
        deger = getattr(config, alan)
        if isinstance(deger, bool) or not isinstance(deger, (int, float)) or not math.isfinite(float(deger)):
            raise FinancialInstitutionValuationError(f"{alan} sonlu sayi olmali")
    if config.max_halfwidth <= 0:
        raise FinancialInstitutionValuationError("max_halfwidth pozitif olmali")
    if type(config.band_width_shadow_mode) is not bool:
        raise FinancialInstitutionValuationError("band_width_shadow_mode Python bool olmali")
    for alan in ("valuation_profile", "source_metrics_profile", "accounting_profile", "share_basis", "currency"):
        deger = getattr(config, alan)
        if not isinstance(deger, str) or not deger.strip():
            raise FinancialInstitutionValuationError(f"{alan} dolu metin olmali")
    if not 0.0 <= config.lower_quantile < 0.5:
        raise FinancialInstitutionValuationError("lower_quantile [0, 0.5) araliginda olmali")
    if not 0.5 < config.upper_quantile <= 1.0:
        raise FinancialInstitutionValuationError("upper_quantile (0.5, 1] araliginda olmali")
    if config.minimum_pb >= config.maximum_pb:
        raise FinancialInstitutionValuationError("minimum_pb maximum_pb'den kucuk olmali")
    if config.minimum_pe >= config.maximum_pe:
        raise FinancialInstitutionValuationError("minimum_pe maximum_pe'den kucuk olmali")
    if config.full_freshness_days > config.max_statement_age_days:
        raise FinancialInstitutionValuationError("full_freshness_days max_statement_age_days'i asamaz")
    if config.minimum_peer_count > config.full_confidence_peer_count:
        raise FinancialInstitutionValuationError("minimum_peer_count full_confidence_peer_count'u asamaz")
    if config.minimum_method_count > 2:
        raise FinancialInstitutionValuationError("minimum_method_count en fazla 2 olabilir")
    if abs(config.pb_weight + config.pe_weight - 1.0) > 1e-9:
        raise FinancialInstitutionValuationError("pb_weight + pe_weight 1.0 olmali")
    if abs(config.valuation_axis_weight + config.follow_axis_weight - 1.0) > 1e-9:
        raise FinancialInstitutionValuationError("valuation_axis_weight + follow_axis_weight 1.0 olmali")
    if config.npl_full_confidence >= config.npl_zero_confidence:
        raise FinancialInstitutionValuationError("npl_full_confidence npl_zero_confidence'tan kucuk olmali")
    return config


def validate_financial_institution_config(
    config: FinancialInstitutionValuationConfig,
) -> FinancialInstitutionValuationConfig:
    return _validated_config(config)


@dataclass(frozen=True)
class FinancialInstitutionSnapshot:
    """
    Banka disi finansal kurulus anlik goruntusu.

    ZORUNLU alanlar sirketin kendi finansal tablosundan gelir. Turetilmis
    gostergeler (takip orani, karsilik kapsami, net finansman marji, ozkaynak
    tamponu) OPSIYONELDIR: veri yoksa TAHMIN EDILMEZ, ilgili guven katsayisi
    notr birakilir ve tani alaninda None olarak gorunur.
    """
    ticker: str
    analysis_at: datetime
    business_type: str
    currency: str
    share_basis: str
    current_price: float
    price_trade_date: date
    period_end: date
    published_at: datetime
    total_equity: float
    average_equity: float
    net_income_ttm: float
    total_assets: float
    finance_receivables: float
    shares_out: float
    # Opsiyonel aktif kalitesi / karlilik gostergeleri
    npl_gross: float | None
    provisions: float | None
    net_finance_income_ttm: float | None
    funding_cost_ttm: float | None
    operating_expenses_ttm: float | None
    capital_adequacy_ratio: float | None
    source_confidence: float
    source_document_id: str
    source_sha256: str
    metrics_profile: str
    metrics_version: int
    accounting_profile: str
    accounting_version: int

    @property
    def market_cap(self) -> float:
        return self.current_price * self.shares_out

    @property
    def book_value_per_share(self) -> float:
        return self.total_equity / self.shares_out

    @property
    def earnings_per_share(self) -> float:
        return self.net_income_ttm / self.shares_out

    @property
    def current_pb(self) -> float:
        return self.market_cap / self.total_equity

    @property
    def current_pe(self) -> float | None:
        if self.net_income_ttm <= 0:
            return None
        return self.market_cap / self.net_income_ttm

    @property
    def roe_ttm(self) -> float:
        """ORTALAMA ozkaynak uzerinden: donem ici sermaye artisi ROE'yi sismez."""
        return self.net_income_ttm / self.average_equity

    @property
    def npl_ratio(self) -> float | None:
        """Takipteki alacaklar / finansman alacaklari."""
        if self.npl_gross is None or self.finance_receivables <= 0:
            return None
        return self.npl_gross / self.finance_receivables

    @property
    def provision_coverage(self) -> float | None:
        """Karsiliklar / takipteki alacaklar."""
        if self.provisions is None or self.npl_gross is None or self.npl_gross <= 0:
            return None
        return self.provisions / self.npl_gross

    @property
    def net_finance_margin(self) -> float | None:
        """Net finansman geliri / finansman alacaklari (bankadaki NIM karsiligi)."""
        if self.net_finance_income_ttm is None or self.finance_receivables <= 0:
            return None
        return self.net_finance_income_ttm / self.finance_receivables

    @property
    def funding_cost_ratio(self) -> float | None:
        """Fonlama maliyeti / (aktif - ozkaynak) = borclanmanin maliyeti."""
        if self.funding_cost_ttm is None:
            return None
        borclanma = self.total_assets - self.total_equity
        if borclanma <= 0:
            return None
        return self.funding_cost_ttm / borclanma

    @property
    def cost_to_income(self) -> float | None:
        if self.operating_expenses_ttm is None or self.net_finance_income_ttm is None:
            return None
        if self.net_finance_income_ttm <= 0:
            return None
        return self.operating_expenses_ttm / self.net_finance_income_ttm

    @property
    def equity_buffer(self) -> float:
        """Ozkaynak / aktif. Sermaye yeterliligi yoksa asgari tampon olcusu."""
        return self.total_equity / self.total_assets

    @property
    def receivables_concentration(self) -> float:
        """Finansman alacaklari / aktif. Is modelinin ne kadar cekirdek oldugunu gosterir."""
        return self.finance_receivables / self.total_assets


def build_financial_institution_snapshot(
    *,
    ticker: Any,
    analysis_at: Any,
    business_type: Any,
    currency: Any,
    share_basis: Any,
    current_price: Any,
    price_trade_date: Any,
    period_end: Any,
    published_at: Any,
    total_equity: Any,
    average_equity: Any,
    net_income_ttm: Any,
    total_assets: Any,
    finance_receivables: Any,
    shares_out: Any,
    source_confidence: Any,
    source_document_id: Any,
    source_sha256: Any,
    metrics_profile: Any,
    metrics_version: Any,
    accounting_profile: Any,
    accounting_version: Any,
    npl_gross: Any = None,
    provisions: Any = None,
    net_finance_income_ttm: Any = None,
    funding_cost_ttm: Any = None,
    operating_expenses_ttm: Any = None,
    capital_adequacy_ratio: Any = None,
) -> FinancialInstitutionSnapshot:
    tip = _strict_text("business_type", business_type, uppercase=True)
    if tip not in SUPPORTED_BUSINESS_TYPES:
        raise FinancialInstitutionValuationError(
            f"business_type desteklenmiyor: {tip}"
        )
    donem = _strict_date("period_end", period_end)
    if not _quarter_end(donem):
        raise FinancialInstitutionValuationError("period_end takvim ceyrek sonu olmali")
    ozkaynak = _finite_number("total_equity", total_equity, minimum=0.0, strict_minimum=True)
    ortalama = _finite_number("average_equity", average_equity, minimum=0.0, strict_minimum=True)
    aktif = _finite_number("total_assets", total_assets, minimum=0.0, strict_minimum=True)
    alacak = _finite_number("finance_receivables", finance_receivables, minimum=0.0)
    if ozkaynak > aktif:
        raise FinancialInstitutionValuationError("total_equity total_assets'i asamaz")
    if alacak > aktif:
        raise FinancialInstitutionValuationError("finance_receivables total_assets'i asamaz")
    takip = _optional_finite("npl_gross", npl_gross, minimum=0.0)
    if takip is not None and takip > alacak:
        raise FinancialInstitutionValuationError("npl_gross finance_receivables'i asamaz")
    karsilik = _optional_finite("provisions", provisions, minimum=0.0)
    return FinancialInstitutionSnapshot(
        ticker=_strict_text("ticker", ticker, uppercase=True),
        analysis_at=_aware_datetime("analysis_at", analysis_at),
        business_type=tip,
        currency=_strict_text("currency", currency, uppercase=True),
        share_basis=_strict_text("share_basis", share_basis, uppercase=True),
        current_price=_finite_number("current_price", current_price, minimum=0.0, strict_minimum=True),
        price_trade_date=_strict_date("price_trade_date", price_trade_date),
        period_end=donem,
        published_at=_aware_datetime("published_at", published_at),
        total_equity=ozkaynak,
        average_equity=ortalama,
        net_income_ttm=_finite_number("net_income_ttm", net_income_ttm),
        total_assets=aktif,
        finance_receivables=alacak,
        shares_out=_finite_number("shares_out", shares_out, minimum=0.0, strict_minimum=True),
        npl_gross=takip,
        provisions=karsilik,
        net_finance_income_ttm=_optional_finite("net_finance_income_ttm", net_finance_income_ttm),
        funding_cost_ttm=_optional_finite("funding_cost_ttm", funding_cost_ttm, minimum=0.0),
        operating_expenses_ttm=_optional_finite("operating_expenses_ttm", operating_expenses_ttm, minimum=0.0),
        capital_adequacy_ratio=_optional_finite("capital_adequacy_ratio", capital_adequacy_ratio, minimum=0.0),
        source_confidence=_finite_number("source_confidence", source_confidence, minimum=0.0, maximum=1.0),
        source_document_id=_strict_text("source_document_id", source_document_id),
        source_sha256=_strict_sha256("source_sha256", source_sha256),
        metrics_profile=_strict_text("metrics_profile", metrics_profile),
        metrics_version=_strict_int("metrics_version", metrics_version, minimum=1),
        accounting_profile=_strict_text("accounting_profile", accounting_profile, uppercase=True),
        accounting_version=_strict_int("accounting_version", accounting_version, minimum=1),
    )


def _validated_snapshot(snapshot: FinancialInstitutionSnapshot) -> FinancialInstitutionSnapshot:
    if not isinstance(snapshot, FinancialInstitutionSnapshot):
        raise FinancialInstitutionValuationError("snapshot FinancialInstitutionSnapshot olmali")
    return build_financial_institution_snapshot(
        **{key: getattr(snapshot, key) for key in snapshot.__dataclass_fields__}
    )


def _freshness_confidence(age_days: int, config: FinancialInstitutionValuationConfig) -> float:
    if age_days <= config.full_freshness_days:
        return 1.0
    span = config.max_statement_age_days - config.full_freshness_days
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, (config.max_statement_age_days - age_days) / span))


def _asset_quality(
    snapshot: FinancialInstitutionSnapshot,
    config: FinancialInstitutionValuationConfig,
) -> float:
    """
    Aktif kalitesi guven katsayisi.

    ONEMLI SOZLESME: bu deger FIYAT BANDINI genisletmez veya daraltmaz; yalniz
    v_conf carpanina ve tani alanlarina girer. Bandi aktif kalitesine gore
    oynatmak, gozlenen emsal carpanlarinin uzerine ikinci bir ozgun gorus
    eklemek olurdu ve cift sayim yaratirdi.

    Veri YOKSA ilgili faktor eklenmez (notr); tahmin uretilmez.
    """
    factors: list[float] = []
    npl = snapshot.npl_ratio
    if npl is not None:
        span = config.npl_zero_confidence - config.npl_full_confidence
        if span <= 0:
            factors.append(1.0)
        else:
            factors.append(max(0.25, min(1.0, (config.npl_zero_confidence - npl) / span)))
    coverage = snapshot.provision_coverage
    if coverage is not None:
        factors.append(max(0.35, min(1.0, coverage / config.coverage_full_confidence)))
    margin = snapshot.net_finance_margin
    if margin is not None:
        factors.append(max(0.35, min(1.0, 0.55 + 4.0 * margin)))
    cost_income = snapshot.cost_to_income
    if cost_income is not None:
        factors.append(max(0.35, min(1.0, 1.35 - cost_income)))
    buffer_ratio = snapshot.equity_buffer
    factors.append(max(0.30, min(1.0, buffer_ratio / max(config.minimum_equity_buffer * 3.0, 1e-9))))
    if snapshot.capital_adequacy_ratio is not None:
        factors.append(max(0.35, min(1.0, snapshot.capital_adequacy_ratio / 0.15)))
    if not factors:
        return 1.0
    return _weighted_geometric(factors, [1.0] * len(factors))


def _base_result(
    target: FinancialInstitutionSnapshot,
    config: FinancialInstitutionValuationConfig,
) -> dict[str, Any]:
    return {
        "ticker": target.ticker,
        "analysis_at": target.analysis_at.isoformat(),
        "period_end": target.period_end.isoformat(),
        "business_type": target.business_type,
        "currency": target.currency,
        "share_basis": target.share_basis,
        "price_trade_date": target.price_trade_date.isoformat(),
        "current_price": target.current_price,
        "published_at": target.published_at.isoformat(),
        "total_equity": target.total_equity,
        "average_equity": target.average_equity,
        "net_income_ttm": target.net_income_ttm,
        "total_assets": target.total_assets,
        "finance_receivables": target.finance_receivables,
        "shares_out": target.shares_out,
        "npl_gross": target.npl_gross,
        "provisions": target.provisions,
        "net_finance_income_ttm": target.net_finance_income_ttm,
        "funding_cost_ttm": target.funding_cost_ttm,
        "operating_expenses_ttm": target.operating_expenses_ttm,
        "capital_adequacy_ratio": target.capital_adequacy_ratio,
        "source_confidence": target.source_confidence,
        "source_document_id": target.source_document_id,
        "source_sha256": target.source_sha256,
        "metrics_profile": target.metrics_profile,
        "metrics_version": target.metrics_version,
        "accounting_profile": target.accounting_profile,
        "accounting_version": target.accounting_version,
        "valuation_profile": config.valuation_profile,
        "valuation_version": config.valuation_version,
        "source_metrics_profile": config.source_metrics_profile,
        "source_metrics_version": config.source_metrics_version,
        "config_sha256": config.config_sha256,
        "status": STATUS_INSUFFICIENT,
        "reason": None,
        "V_low": None,
        "V_mid": None,
        "V_high": None,
        "valuation_score": 0.5,
        "z_val": None,
        "v_conf": 0.0,
        "lower_halfwidth": None,
        "upper_halfwidth": None,
        "target_pb": None,
        "target_pe": None,
        "method_count": 0,
        "roe_ttm": target.roe_ttm,
        "npl_ratio": target.npl_ratio,
        "provision_coverage": target.provision_coverage,
        "net_finance_margin": target.net_finance_margin,
        "equity_buffer": target.equity_buffer,
        "diagnostics": {},
    }


def _insufficient(base: Mapping[str, Any], reason: str, diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    return {**base, "status": STATUS_INSUFFICIENT, "reason": reason, "diagnostics": dict(diagnostics)}


def value_financial_institution_snapshot(
    target: FinancialInstitutionSnapshot,
    peers: Iterable[FinancialInstitutionSnapshot],
    config: FinancialInstitutionValuationConfig,
) -> dict[str, Any]:
    """
    Banka disi finansal kurulus goreli degerlemesi.

    Emsaller YALNIZ ayni: alt grup (FACTORING/LEASING/CONSUMER_FINANCE), finansal
    donem, muhasebe profili, metrik profili, para birimi ve pay/fiyat bazinda
    karsilastirilir. Leave-one-out zorunludur.
    """
    config = _validated_config(config)
    target = _validated_snapshot(target)
    base = _base_result(target, config)
    analysis_date = target.analysis_at.astimezone(ISTANBUL_TZ).date()
    price_age = (analysis_date - target.price_trade_date).days
    statement_age = (analysis_date - target.period_end).days
    common = {
        "target_price_age_days": price_age,
        "target_statement_age_days": statement_age,
        "target_current_pb": target.current_pb,
        "target_current_pe": target.current_pe,
        "target_source_confidence": target.source_confidence,
        "target_asset_quality": _asset_quality(target, config),
    }
    if (target.metrics_profile != config.source_metrics_profile
            or target.metrics_version != config.source_metrics_version):
        raise FinancialInstitutionValuationError("hedef metrics profil/surum config ile uyusmuyor")
    if (target.accounting_profile != config.accounting_profile
            or target.accounting_version != config.accounting_version):
        return _insufficient(base, "HEDEF_MUHASEBE_PROFILI_UYUSMUYOR", common)
    if target.share_basis != config.share_basis:
        return _insufficient(base, "HEDEF_PAY_BAZI_UYUSMUYOR", common)
    if target.currency != config.currency:
        return _insufficient(base, "HEDEF_PARA_BIRIMI_UYUSMUYOR", common)
    if price_age < 0 or price_age > config.max_price_age_days:
        return _insufficient(base, "HEDEF_FIYAT_BAYAT", common)
    if statement_age < 0 or statement_age > config.max_statement_age_days:
        return _insufficient(base, "HEDEF_FINANSAL_BILGI_BAYAT", common)
    if target.source_confidence < config.minimum_source_confidence:
        return _insufficient(base, "HEDEF_KAYNAK_GUVENI_DUSUK", common)
    if not config.minimum_pb <= target.current_pb <= config.maximum_pb:
        return _insufficient(base, "HEDEF_PD_DD_MODEL_ARALIGI_DISINDA", common)
    if target.equity_buffer < config.minimum_equity_buffer:
        return _insufficient(base, "HEDEF_OZKAYNAK_TAMPONU_YETERSIZ", common)

    valid_peers: list[FinancialInstitutionSnapshot] = []
    seen: set[str] = set()
    excluded: dict[str, str] = {}
    for raw in peers:
        peer = _validated_snapshot(raw)
        if peer.ticker == target.ticker:
            raise FinancialInstitutionValuationError("leave-one-out ihlali: hedef emsal listesinde")
        if peer.ticker in seen:
            raise FinancialInstitutionValuationError(f"yinelenen peer ticker: {peer.ticker}")
        seen.add(peer.ticker)
        if peer.business_type != target.business_type:
            raise FinancialInstitutionValuationError(
                f"business_type uyusmuyor: {peer.ticker} ({peer.business_type} != {target.business_type})"
            )
        if peer.period_end != target.period_end:
            excluded[peer.ticker] = "PERIOD_MISMATCH"
            continue
        if (peer.metrics_profile != config.source_metrics_profile
                or peer.metrics_version != config.source_metrics_version):
            excluded[peer.ticker] = "METRICS_PROFILE_MISMATCH"
            continue
        if (peer.accounting_profile != config.accounting_profile
                or peer.accounting_version != config.accounting_version):
            excluded[peer.ticker] = "ACCOUNTING_PROFILE_MISMATCH"
            continue
        if peer.currency != config.currency:
            excluded[peer.ticker] = "CURRENCY_MISMATCH"
            continue
        if peer.share_basis != config.share_basis:
            excluded[peer.ticker] = "SHARE_BASIS_MISMATCH"
            continue
        peer_price_age = (analysis_date - peer.price_trade_date).days
        peer_statement_age = (analysis_date - peer.period_end).days
        if peer_price_age < 0 or peer_price_age > config.max_price_age_days:
            excluded[peer.ticker] = "FIYAT_BAYAT"
            continue
        if peer_statement_age < 0 or peer_statement_age > config.max_statement_age_days:
            excluded[peer.ticker] = "FINANSAL_BILGI_BAYAT"
            continue
        if peer.source_confidence < config.minimum_source_confidence:
            excluded[peer.ticker] = "KAYNAK_GUVENI_DUSUK"
            continue
        if not config.minimum_pb <= peer.current_pb <= config.maximum_pb:
            excluded[peer.ticker] = "PD_DD_MODEL_ARALIGI_DISINDA"
            continue
        if peer.equity_buffer < config.minimum_equity_buffer:
            excluded[peer.ticker] = "OZKAYNAK_TAMPONU_YETERSIZ"
            continue
        valid_peers.append(peer)

    valid_peers.sort(key=lambda item: item.ticker)
    if len(valid_peers) < config.minimum_peer_count:
        return _insufficient(base, "YETERSIZ_FINANSAL_KURULUS_EMSALI", {
            **common,
            "peer_tickers": [peer.ticker for peer in valid_peers],
            "peer_count": len(valid_peers),
            "excluded_peers": excluded,
        })

    method_bands: dict[str, tuple[float, float, float]] = {}
    method_peer_counts: dict[str, int] = {}
    pb_values = [peer.current_pb for peer in valid_peers]
    method_bands["PB"] = (
        target.book_value_per_share * _quantile_linear(pb_values, config.lower_quantile),
        target.book_value_per_share * _quantile_linear(pb_values, 0.5),
        target.book_value_per_share * _quantile_linear(pb_values, config.upper_quantile),
    )
    method_peer_counts["PB"] = len(pb_values)

    # F/K yalnizca kar POZITIF ve ROE surdurulebilir esigin uzerindeyse acilir.
    if target.net_income_ttm > 0 and target.roe_ttm >= config.minimum_pe_roe:
        pe_values = [
            value
            for peer in valid_peers
            if peer.roe_ttm >= config.minimum_pe_roe
            and (value := peer.current_pe) is not None
            and config.minimum_pe <= value <= config.maximum_pe
        ]
        if len(pe_values) >= config.minimum_peer_count:
            method_bands["PE"] = (
                target.earnings_per_share * _quantile_linear(pe_values, config.lower_quantile),
                target.earnings_per_share * _quantile_linear(pe_values, 0.5),
                target.earnings_per_share * _quantile_linear(pe_values, config.upper_quantile),
            )
            method_peer_counts["PE"] = len(pe_values)

    if len(method_bands) < config.minimum_method_count:
        return _insufficient(base, "YETERSIZ_DEGERLEME_YONTEMI", {
            **common,
            "peer_tickers": [peer.ticker for peer in valid_peers],
            "peer_count": len(valid_peers),
            "method_peer_counts": method_peer_counts,
            "excluded_peers": excluded,
        })

    selected = sorted(method_bands)
    weights_by_method = {"PB": config.pb_weight, "PE": config.pe_weight}
    low = _weighted_geometric([method_bands[n][0] for n in selected], [weights_by_method[n] for n in selected])
    mid = _weighted_geometric([method_bands[n][1] for n in selected], [weights_by_method[n] for n in selected])
    high = _weighted_geometric([method_bands[n][2] for n in selected], [weights_by_method[n] for n in selected])
    if not (0 < low <= mid <= high):
        raise FinancialInstitutionValuationError("financial institution band geometrisi gecersiz")

    valuation_score, z_val = _valuation_score(target.current_price, low, mid, high)
    lower_halfwidth = math.log(mid / low)
    upper_halfwidth = math.log(high / mid)
    observed_halfwidth = max(lower_halfwidth, upper_halfwidth)
    shadow_too_wide = observed_halfwidth > config.max_halfwidth
    peer_factor = min(1.0, len(valid_peers) / config.full_confidence_peer_count)
    freshness = _freshness_confidence(statement_age, config)
    coverage = len(method_bands) / 2.0
    target_quality = _asset_quality(target, config)
    peer_quality = sum(
        _asset_quality(peer, config) * peer.source_confidence for peer in valid_peers
    ) / len(valid_peers)
    width_factor = min(1.0, config.max_halfwidth / max(observed_halfwidth, 1e-12))
    v_conf = max(0.0, min(1.0,
        peer_factor * freshness * coverage * target.source_confidence
        * target_quality * peer_quality * width_factor
    ))
    target_pb = method_bands["PB"][1] / target.book_value_per_share
    target_pe = method_bands["PE"][1] / target.earnings_per_share if "PE" in method_bands else None

    diagnostics = {
        "peer_tickers": [peer.ticker for peer in valid_peers],
        "peer_count": len(valid_peers),
        "excluded_peers": excluded,
        "method_bands": {n: {"low": b[0], "mid": b[1], "high": b[2]} for n, b in method_bands.items()},
        "method_peer_counts": method_peer_counts,
        "method_weights": {n: weights_by_method[n] for n in selected},
        "target_price_age_days": price_age,
        "target_statement_age_days": statement_age,
        "target_asset_quality": target_quality,
        "peer_asset_quality": peer_quality,
        "target_metrics": {
            "current_pb": target.current_pb,
            "current_pe": target.current_pe,
            "roe_ttm": target.roe_ttm,
            "npl_ratio": target.npl_ratio,
            "provision_coverage": target.provision_coverage,
            "net_finance_margin": target.net_finance_margin,
            "funding_cost_ratio": target.funding_cost_ratio,
            "cost_to_income": target.cost_to_income,
            "equity_buffer": target.equity_buffer,
            "receivables_concentration": target.receivables_concentration,
            "capital_adequacy_ratio": target.capital_adequacy_ratio,
        },
        "confidence_factors": {
            "peer_factor": peer_factor,
            "freshness_factor": freshness,
            "method_coverage": coverage,
            "source_confidence": target.source_confidence,
            "target_asset_quality": target_quality,
            "peer_asset_quality": peer_quality,
            "width_factor": width_factor,
        },
        "aggregation": {
            "max_halfwidth": config.max_halfwidth,
            "observed_halfwidth": observed_halfwidth,
            "shadow_too_wide": shadow_too_wide,
        },
    }
    result = {
        **base,
        "status": STATUS_OK,
        "reason": None,
        "V_low": low,
        "V_mid": mid,
        "V_high": high,
        "valuation_score": valuation_score,
        "z_val": z_val,
        "v_conf": v_conf,
        "lower_halfwidth": lower_halfwidth,
        "upper_halfwidth": upper_halfwidth,
        "target_pb": target_pb,
        "target_pe": target_pe,
        "method_count": len(method_bands),
        "diagnostics": diagnostics,
    }
    if shadow_too_wide and not config.band_width_shadow_mode:
        return {
            **result,
            "status": STATUS_TOO_WIDE,
            "reason": "FINANCIAL_INSTITUTION_VALUATION_BAND_TOO_WIDE",
            "valuation_score": 0.5,
            "z_val": None,
            "v_conf": 0.0,
        }
    return result


def combine_financial_institution_m2(
    valuation: Mapping[str, Any],
    *,
    follow_score: Any,
    follow_active: Any,
    config: FinancialInstitutionValuationConfig,
) -> dict[str, Any]:
    """
    Iki eksenli M2: degerleme ekseni + kalibre gecikme ekseni.

    Degerleme skoru guven ile NOTRE daraltilir (0.5 + (skor - 0.5) * v_conf),
    carpilmaz: bir SKORUN notru 0.5'tir, 0 degil.
    """
    config = _validated_config(config)
    if not isinstance(valuation, Mapping):
        raise FinancialInstitutionValuationError("valuation mapping olmali")
    ticker = _strict_text("valuation.ticker", valuation.get("ticker"), uppercase=True)
    if type(follow_active) is not bool:
        raise FinancialInstitutionValuationError("follow_active Python bool olmali")
    follow = _finite_number("follow_score", follow_score, minimum=0.0, maximum=1.0) if follow_active else 0.5
    raw_valuation = _finite_number(
        "valuation_score", valuation.get("valuation_score", 0.5), minimum=0.0, maximum=1.0
    )
    conf = _finite_number("v_conf", valuation.get("v_conf", 0.0), minimum=0.0, maximum=1.0)
    usable = valuation.get("status") == STATUS_OK and conf > 0.0
    effective = 0.5 + (raw_valuation - 0.5) * conf if usable else 0.5
    m2 = config.valuation_axis_weight * effective + config.follow_axis_weight * follow
    return {
        "ticker": ticker,
        "analysis_at": valuation.get("analysis_at"),
        "period_end": valuation.get("period_end"),
        "m2": float(max(0.0, min(1.0, m2))),
        "m2_source": "FINANCIAL_INSTITUTION_PB_PE_TWO_AXIS_V1",
        "valuation_usable": bool(usable),
        "score_inputs": {
            "valuation_score_raw": raw_valuation,
            "valuation_score_effective": effective,
            "valuation_confidence": conf,
            "valuation_weight": config.valuation_axis_weight,
            "follow_score": follow,
            "follow_active": follow_active,
            "follow_weight": config.follow_axis_weight,
            "valuation_status": valuation.get("status"),
            "valuation_reason": valuation.get("reason"),
            "business_type": valuation.get("business_type"),
            "roe_ttm": valuation.get("roe_ttm"),
            "npl_ratio": valuation.get("npl_ratio"),
            "provision_coverage": valuation.get("provision_coverage"),
            "net_finance_margin": valuation.get("net_finance_margin"),
            "equity_buffer": valuation.get("equity_buffer"),
        },
    }


def evaluate_financial_institution_batch(
    snapshots: Iterable[FinancialInstitutionSnapshot],
    *,
    config: FinancialInstitutionValuationConfig,
    follow_contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """
    Emsal havuzu ALT GRUBA gore ayrilir: faktoring sirketi yalniz faktoring
    emsalleriyle, leasing yalniz leasing emsalleriyle karsilastirilir.
    """
    config = _validated_config(config)
    if not isinstance(follow_contexts, Mapping):
        raise FinancialInstitutionValuationError("follow_contexts mapping olmali")
    items = [_validated_snapshot(item) for item in snapshots]
    by_ticker: dict[str, FinancialInstitutionSnapshot] = {}
    for item in items:
        if item.ticker in by_ticker:
            raise FinancialInstitutionValuationError(f"yinelenen snapshot ticker: {item.ticker}")
        by_ticker[item.ticker] = item
    extra = set(follow_contexts) - set(by_ticker)
    if extra:
        raise FinancialInstitutionValuationError(
            "beklenmeyen follow_context ticker: " + ", ".join(sorted(repr(x) for x in extra))
        )
    results: list[dict[str, Any]] = []
    for ticker in sorted(by_ticker):
        target = by_ticker[ticker]
        peers = [
            item for other, item in sorted(by_ticker.items())
            if other != ticker
            and item.business_type == target.business_type
            and item.period_end == target.period_end
        ]
        valuation = value_financial_institution_snapshot(target, peers, config)
        context = follow_contexts.get(ticker, {"follow_score": 0.5, "follow_active": False})
        if not isinstance(context, Mapping):
            raise FinancialInstitutionValuationError(f"{ticker} follow context mapping olmali")
        m2 = combine_financial_institution_m2(
            valuation,
            follow_score=context.get("follow_score", 0.5),
            follow_active=context.get("follow_active", False),
            config=config,
        )
        results.append({"ticker": ticker, "valuation": valuation, "m2": m2})
    results.sort(key=lambda row: (-float(row["m2"]["m2"]), row["ticker"]))
    return {
        "valuation_profile": config.valuation_profile,
        "valuation_version": config.valuation_version,
        "source_metrics_profile": config.source_metrics_profile,
        "source_metrics_version": config.source_metrics_version,
        "accounting_profile": config.accounting_profile,
        "accounting_version": config.accounting_version,
        "config_sha256": config.config_sha256,
        "result_count": len(results),
        "results": results,
    }
