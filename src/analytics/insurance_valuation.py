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
SUPPORTED_BUSINESS_TYPES = frozenset({"NON_LIFE", "LIFE_PENSION"})


class InsuranceValuationError(ValueError):
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
        raise InsuranceValuationError(f"{name} sonlu sayi olmali")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InsuranceValuationError(f"{name} sonlu sayi olmali") from exc
    if not math.isfinite(result) or abs(result) > MAX_ABS_NUMBER:
        raise InsuranceValuationError(f"{name} sonlu sayi sinirinda olmali")
    if minimum is not None:
        if strict_minimum and result <= minimum:
            raise InsuranceValuationError(f"{name} {minimum} degerinden buyuk olmali")
        if not strict_minimum and result < minimum:
            raise InsuranceValuationError(f"{name} {minimum} degerinden kucuk olamaz")
    if maximum is not None and result > maximum:
        raise InsuranceValuationError(f"{name} {maximum} degerini asamaz")
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
        raise InsuranceValuationError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _strict_date(name: str, value: Any) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise InsuranceValuationError(f"{name} date olmali")
    return value


def _aware_datetime(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InsuranceValuationError(f"{name} timezone iceren datetime olmali")
    return value


def _strict_int(name: str, value: Any, *, minimum: int = 0) -> int:
    if _is_bool_like(value) or not isinstance(value, int):
        raise InsuranceValuationError(f"{name} Python int olmali")
    if value < minimum:
        raise InsuranceValuationError(f"{name} {minimum} degerinden kucuk olamaz")
    return value


def _strict_sha256(name: str, value: Any) -> str:
    text = _strict_text(name, value).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise InsuranceValuationError(f"{name} 64 karakter hex SHA256 olmali")
    return text


def _stable_json_sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _quantile_linear(values: Sequence[float], q: float) -> float:
    if not values:
        raise InsuranceValuationError("quantile bos veriyle hesaplanamaz")
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
        raise InsuranceValuationError("geometrik birlestirme girdileri gecersiz")
    total = sum(weights)
    if total <= 0:
        raise InsuranceValuationError("geometrik birlestirme agirligi pozitif olmali")
    return math.exp(sum((w / total) * math.log(v) for v, w in zip(values, weights, strict=True)))


def _valuation_score(price: float, low: float, mid: float, high: float) -> tuple[float, float]:
    if not (0 < low <= mid <= high):
        raise InsuranceValuationError("valuation band geometrisi gecersiz")
    log_scale = max(math.log(high / low), 1e-12)
    z = math.log(mid / price) / (log_scale / 2.0)
    score = 1.0 / (1.0 + math.exp(-1.6 * max(min(z, 20.0), -20.0)))
    return float(score), float(z)


def _quarter_end(value: date) -> bool:
    return (value.month, value.day) in {(3, 31), (6, 30), (9, 30), (12, 31)}


@dataclass(frozen=True)
class InsuranceValuationConfig:
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
    maximum_pb: float = 8.0
    minimum_pe: float = 1.0
    maximum_pe: float = 80.0
    pb_weight: float = 0.65
    pe_weight: float = 0.35
    max_statement_age_days: int = 220
    full_freshness_days: int = 100
    max_price_age_days: int = 7
    minimum_source_confidence: float = 0.40
    max_halfwidth: float = 1.25
    band_width_shadow_mode: bool = True
    valuation_axis_weight: float = 0.65
    follow_axis_weight: float = 0.35

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InsuranceValuationConfig":
        if not isinstance(data, Mapping):
            raise InsuranceValuationError("insurance valuation config nesne olmali")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(data) - allowed
        if unknown:
            raise InsuranceValuationError(
                "insurance valuation bilinmeyen alanlar: " + ", ".join(sorted(repr(x) for x in unknown))
            )
        profile = _strict_text("valuation_profile", data.get("valuation_profile"))
        version = _strict_int("valuation_version", data.get("valuation_version"), minimum=1)
        source_profile = _strict_text("source_metrics_profile", data.get("source_metrics_profile"))
        source_version = _strict_int("source_metrics_version", data.get("source_metrics_version"), minimum=1)
        accounting_profile = _strict_text("accounting_profile", data.get("accounting_profile"), uppercase=True)
        accounting_version = _strict_int("accounting_version", data.get("accounting_version"), minimum=1)
        share_basis = _strict_text("share_basis", data.get("share_basis"), uppercase=True)
        currency = _strict_text("currency", data.get("currency", "TRY"), uppercase=True)
        lower = _finite_number("lower_quantile", data.get("lower_quantile", 0.25), minimum=0.0, maximum=1.0)
        upper = _finite_number("upper_quantile", data.get("upper_quantile", 0.75), minimum=0.0, maximum=1.0)
        if not 0.0 <= lower < 0.5 < upper <= 1.0:
            raise InsuranceValuationError("quantile sirasi lower < 0.5 < upper olmali")
        min_peers = _strict_int("minimum_peer_count", data.get("minimum_peer_count", 2), minimum=1)
        full_peers = _strict_int(
            "full_confidence_peer_count", data.get("full_confidence_peer_count", 6), minimum=min_peers
        )
        min_methods = _strict_int("minimum_method_count", data.get("minimum_method_count", 1), minimum=1)
        if min_methods > 2:
            raise InsuranceValuationError("minimum_method_count 2 degerini asamaz")
        minimum_pb = _finite_number("minimum_pb", data.get("minimum_pb", 0.05), minimum=0.0, strict_minimum=True)
        maximum_pb = _finite_number("maximum_pb", data.get("maximum_pb", 8.0), minimum=minimum_pb, strict_minimum=True)
        if minimum_pb >= maximum_pb:
            raise InsuranceValuationError("minimum_pb maximum_pb degerinden kucuk olmali")
        minimum_pe = _finite_number("minimum_pe", data.get("minimum_pe", 1.0), minimum=0.0, strict_minimum=True)
        maximum_pe = _finite_number("maximum_pe", data.get("maximum_pe", 80.0), minimum=minimum_pe, strict_minimum=True)
        if minimum_pe >= maximum_pe:
            raise InsuranceValuationError("minimum_pe maximum_pe degerinden kucuk olmali")
        pb_weight = _finite_number("pb_weight", data.get("pb_weight", 0.65), minimum=0.0, maximum=1.0)
        pe_weight = _finite_number("pe_weight", data.get("pe_weight", 0.35), minimum=0.0, maximum=1.0)
        if pb_weight <= 0 or pe_weight <= 0:
            raise InsuranceValuationError("PB ve PE agirliklari pozitif olmali")
        if not math.isclose(pb_weight + pe_weight, 1.0, abs_tol=1e-12, rel_tol=0.0):
            raise InsuranceValuationError("PB ve PE agirliklari toplami 1 olmali")
        max_age = _strict_int("max_statement_age_days", data.get("max_statement_age_days", 220), minimum=1)
        full_fresh = _strict_int("full_freshness_days", data.get("full_freshness_days", 100), minimum=0)
        if full_fresh > max_age:
            raise InsuranceValuationError("full_freshness_days max_statement_age_days degerini asamaz")
        max_price_age = _strict_int("max_price_age_days", data.get("max_price_age_days", 7), minimum=0)
        if max_price_age > 31:
            raise InsuranceValuationError("max_price_age_days 31 gunu asamaz")
        min_source = _finite_number(
            "minimum_source_confidence", data.get("minimum_source_confidence", 0.40), minimum=0.0, maximum=1.0
        )
        max_halfwidth = _finite_number(
            "max_halfwidth", data.get("max_halfwidth", 1.25), minimum=0.0, strict_minimum=True
        )
        shadow = data.get("band_width_shadow_mode", True)
        if type(shadow) is not bool:
            raise InsuranceValuationError("band_width_shadow_mode Python bool olmali")
        valuation_weight = _finite_number(
            "valuation_axis_weight", data.get("valuation_axis_weight", 0.65), minimum=0.0, maximum=1.0
        )
        follow_weight = _finite_number(
            "follow_axis_weight", data.get("follow_axis_weight", 0.35), minimum=0.0, maximum=1.0
        )
        if not math.isclose(valuation_weight + follow_weight, 1.0, abs_tol=1e-12, rel_tol=0.0):
            raise InsuranceValuationError("M2 eksen agirliklari toplami 1 olmali")
        return cls(
            valuation_profile=profile,
            valuation_version=version,
            source_metrics_profile=source_profile,
            source_metrics_version=source_version,
            accounting_profile=accounting_profile,
            accounting_version=accounting_version,
            share_basis=share_basis,
            currency=currency,
            lower_quantile=lower,
            upper_quantile=upper,
            minimum_peer_count=min_peers,
            full_confidence_peer_count=full_peers,
            minimum_method_count=min_methods,
            minimum_pb=minimum_pb,
            maximum_pb=maximum_pb,
            minimum_pe=minimum_pe,
            maximum_pe=maximum_pe,
            pb_weight=pb_weight,
            pe_weight=pe_weight,
            max_statement_age_days=max_age,
            full_freshness_days=full_fresh,
            max_price_age_days=max_price_age,
            minimum_source_confidence=min_source,
            max_halfwidth=max_halfwidth,
            band_width_shadow_mode=shadow,
            valuation_axis_weight=valuation_weight,
            follow_axis_weight=follow_weight,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "InsuranceValuationConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    @property
    def config_sha256(self) -> str:
        return _stable_json_sha({key: getattr(self, key) for key in self.__dataclass_fields__})


def _validated_config(config: InsuranceValuationConfig) -> InsuranceValuationConfig:
    if not isinstance(config, InsuranceValuationConfig):
        raise InsuranceValuationError("config InsuranceValuationConfig olmali")
    return InsuranceValuationConfig.from_dict({key: getattr(config, key) for key in config.__dataclass_fields__})


def validate_insurance_config(config: InsuranceValuationConfig) -> InsuranceValuationConfig:
    return _validated_config(config)


@dataclass(frozen=True)
class InsuranceSnapshot:
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
    net_income_ttm: float
    written_premiums_ttm: float
    technical_result_ttm: float
    investment_income_ttm: float
    shares_out: float
    earned_premiums_ttm: float | None
    net_claims_ttm: float | None
    operating_expenses_ttm: float | None
    solvency_ratio: float | None
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
        return self.net_income_ttm / self.total_equity

    @property
    def technical_margin(self) -> float:
        return self.technical_result_ttm / self.written_premiums_ttm

    @property
    def combined_ratio(self) -> float | None:
        if self.earned_premiums_ttm is None or self.earned_premiums_ttm <= 0:
            return None
        if self.net_claims_ttm is None or self.operating_expenses_ttm is None:
            return None
        return (self.net_claims_ttm + self.operating_expenses_ttm) / self.earned_premiums_ttm

    @property
    def investment_dependency(self) -> float:
        denominator = max(abs(self.net_income_ttm), 1e-12)
        return max(0.0, self.investment_income_ttm / denominator)


def build_insurance_snapshot(
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
    net_income_ttm: Any,
    written_premiums_ttm: Any,
    technical_result_ttm: Any,
    investment_income_ttm: Any,
    shares_out: Any,
    earned_premiums_ttm: Any = None,
    net_claims_ttm: Any = None,
    operating_expenses_ttm: Any = None,
    solvency_ratio: Any = None,
    source_confidence: Any = 1.0,
    source_document_id: Any = None,
    source_sha256: Any = None,
    metrics_profile: Any = None,
    metrics_version: Any = None,
    accounting_profile: Any = None,
    accounting_version: Any = None,
) -> InsuranceSnapshot:
    analysis = _aware_datetime("analysis_at", analysis_at)
    published = _aware_datetime("published_at", published_at)
    if published > analysis:
        raise InsuranceValuationError("published_at analysis_at sonrasinda olamaz")
    period = _strict_date("period_end", period_end)
    if not _quarter_end(period):
        raise InsuranceValuationError("period_end gercek takvim ceyrek sonu olmali")
    if period > published.astimezone(ISTANBUL_TZ).date():
        raise InsuranceValuationError("period_end published_at tarihinden sonra olamaz")
    price_date = _strict_date("price_trade_date", price_trade_date)
    if price_date > analysis.astimezone(ISTANBUL_TZ).date():
        raise InsuranceValuationError("price_trade_date analysis_at sonrasinda olamaz")
    business = _strict_text("business_type", business_type, uppercase=True)
    if business not in SUPPORTED_BUSINESS_TYPES:
        raise InsuranceValuationError("business_type NON_LIFE veya LIFE_PENSION olmali")
    earned = _optional_finite("earned_premiums_ttm", earned_premiums_ttm, minimum=0.0)
    claims = _optional_finite("net_claims_ttm", net_claims_ttm, minimum=0.0)
    expenses = _optional_finite("operating_expenses_ttm", operating_expenses_ttm, minimum=0.0)
    combined_fields = (earned, claims, expenses)
    if any(value is None for value in combined_fields) and not all(value is None for value in combined_fields):
        raise InsuranceValuationError(
            "earned_premiums_ttm, net_claims_ttm ve operating_expenses_ttm birlikte verilmeli"
        )
    if business == "LIFE_PENSION" and any(value is not None for value in combined_fields):
        raise InsuranceValuationError("LIFE_PENSION icin combined ratio alanlari bu surumde kullanilmaz")
    snapshot = InsuranceSnapshot(
        ticker=_strict_text("ticker", ticker, uppercase=True),
        analysis_at=analysis,
        business_type=business,
        currency=_strict_text("currency", currency, uppercase=True),
        share_basis=_strict_text("share_basis", share_basis, uppercase=True),
        current_price=_finite_number("current_price", current_price, minimum=0.0, strict_minimum=True),
        price_trade_date=price_date,
        period_end=period,
        published_at=published,
        total_equity=_finite_number("total_equity", total_equity, minimum=0.0, strict_minimum=True),
        net_income_ttm=_finite_number("net_income_ttm", net_income_ttm),
        written_premiums_ttm=_finite_number(
            "written_premiums_ttm", written_premiums_ttm, minimum=0.0, strict_minimum=True
        ),
        technical_result_ttm=_finite_number("technical_result_ttm", technical_result_ttm),
        investment_income_ttm=_finite_number("investment_income_ttm", investment_income_ttm),
        shares_out=_finite_number("shares_out", shares_out, minimum=0.0, strict_minimum=True),
        earned_premiums_ttm=earned,
        net_claims_ttm=claims,
        operating_expenses_ttm=expenses,
        solvency_ratio=_optional_finite("solvency_ratio", solvency_ratio, minimum=0.0),
        source_confidence=_finite_number("source_confidence", source_confidence, minimum=0.0, maximum=1.0),
        source_document_id=_strict_text("source_document_id", source_document_id),
        source_sha256=_strict_sha256("source_sha256", source_sha256),
        metrics_profile=_strict_text("metrics_profile", metrics_profile),
        metrics_version=_strict_int("metrics_version", metrics_version, minimum=1),
        accounting_profile=_strict_text("accounting_profile", accounting_profile, uppercase=True),
        accounting_version=_strict_int("accounting_version", accounting_version, minimum=1),
    )
    if snapshot.combined_ratio is not None and snapshot.combined_ratio > 10.0:
        raise InsuranceValuationError("combined_ratio model sinirini asiyor")
    if abs(snapshot.technical_margin) > 10.0:
        raise InsuranceValuationError("technical_margin model sinirini asiyor")
    return snapshot


def _validated_snapshot(snapshot: InsuranceSnapshot) -> InsuranceSnapshot:
    if not isinstance(snapshot, InsuranceSnapshot):
        raise InsuranceValuationError("snapshot InsuranceSnapshot olmali")
    return build_insurance_snapshot(**{key: getattr(snapshot, key) for key in snapshot.__dataclass_fields__})


def _freshness_confidence(age_days: int, config: InsuranceValuationConfig) -> float:
    if age_days <= config.full_freshness_days:
        return 1.0
    span = config.max_statement_age_days - config.full_freshness_days
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, (config.max_statement_age_days - age_days) / span))


def _technical_quality(snapshot: InsuranceSnapshot) -> float:
    margin_factor = max(0.25, min(1.0, 0.55 + 3.0 * snapshot.technical_margin))
    investment_factor = max(0.35, min(1.0, 1.15 - 0.15 * snapshot.investment_dependency))
    factors = [margin_factor, investment_factor]
    if snapshot.business_type == "NON_LIFE" and snapshot.combined_ratio is not None:
        combined_factor = max(0.25, min(1.0, 1.8 - snapshot.combined_ratio))
        factors.append(combined_factor)
    if snapshot.solvency_ratio is not None:
        solvency_factor = max(0.35, min(1.0, snapshot.solvency_ratio / 1.5))
        factors.append(solvency_factor)
    return _weighted_geometric(factors, [1.0] * len(factors))


def _base_result(target: InsuranceSnapshot, config: InsuranceValuationConfig) -> dict[str, Any]:
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
        "net_income_ttm": target.net_income_ttm,
        "written_premiums_ttm": target.written_premiums_ttm,
        "technical_result_ttm": target.technical_result_ttm,
        "investment_income_ttm": target.investment_income_ttm,
        "shares_out": target.shares_out,
        "earned_premiums_ttm": target.earned_premiums_ttm,
        "net_claims_ttm": target.net_claims_ttm,
        "operating_expenses_ttm": target.operating_expenses_ttm,
        "solvency_ratio": target.solvency_ratio,
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
        "technical_margin": target.technical_margin,
        "combined_ratio": target.combined_ratio,
        "roe_ttm": target.roe_ttm,
        "investment_dependency": target.investment_dependency,
        "diagnostics": {},
    }


def _insufficient(base: Mapping[str, Any], reason: str, diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    return {**base, "status": STATUS_INSUFFICIENT, "reason": reason, "diagnostics": dict(diagnostics)}


def value_insurance_snapshot(
    target: InsuranceSnapshot,
    peers: Iterable[InsuranceSnapshot],
    config: InsuranceValuationConfig,
) -> dict[str, Any]:
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
        "target_technical_quality": _technical_quality(target),
    }
    if target.metrics_profile != config.source_metrics_profile or target.metrics_version != config.source_metrics_version:
        raise InsuranceValuationError("hedef metrics profil/surum config ile uyusmuyor")
    if target.accounting_profile != config.accounting_profile or target.accounting_version != config.accounting_version:
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

    valid_peers: list[InsuranceSnapshot] = []
    seen: set[str] = set()
    excluded: dict[str, str] = {}
    for raw in peers:
        peer = _validated_snapshot(raw)
        if peer.ticker == target.ticker:
            raise InsuranceValuationError("leave-one-out ihlali: hedef emsal listesinde")
        if peer.ticker in seen:
            raise InsuranceValuationError(f"yinelenen peer ticker: {peer.ticker}")
        seen.add(peer.ticker)
        if peer.business_type != target.business_type:
            raise InsuranceValuationError(f"business_type uyusmuyor: {peer.ticker}")
        if peer.period_end != target.period_end:
            excluded[peer.ticker] = "PERIOD_MISMATCH"
            continue
        if peer.metrics_profile != config.source_metrics_profile or peer.metrics_version != config.source_metrics_version:
            excluded[peer.ticker] = "METRICS_PROFILE_MISMATCH"
            continue
        if peer.accounting_profile != config.accounting_profile or peer.accounting_version != config.accounting_version:
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
        valid_peers.append(peer)

    valid_peers.sort(key=lambda item: item.ticker)
    if len(valid_peers) < config.minimum_peer_count:
        return _insufficient(base, "YETERSIZ_SIGORTA_EMSALI", {
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

    if target.net_income_ttm > 0:
        pe_values = [
            value
            for peer in valid_peers
            if (value := peer.current_pe) is not None and config.minimum_pe <= value <= config.maximum_pe
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
    lows = [method_bands[name][0] for name in selected]
    mids = [method_bands[name][1] for name in selected]
    highs = [method_bands[name][2] for name in selected]
    weights = [weights_by_method[name] for name in selected]
    low = _weighted_geometric(lows, weights)
    mid = _weighted_geometric(mids, weights)
    high = _weighted_geometric(highs, weights)
    if not (0 < low <= mid <= high):
        raise InsuranceValuationError("insurance valuation band geometrisi gecersiz")

    valuation_score, z_val = _valuation_score(target.current_price, low, mid, high)
    lower_halfwidth = math.log(mid / low)
    upper_halfwidth = math.log(high / mid)
    observed_halfwidth = max(lower_halfwidth, upper_halfwidth)
    shadow_too_wide = observed_halfwidth > config.max_halfwidth
    peer_factor = min(1.0, len(valid_peers) / config.full_confidence_peer_count)
    freshness = _freshness_confidence(statement_age, config)
    coverage = len(method_bands) / 2.0
    target_quality = _technical_quality(target)
    peer_quality = sum(_technical_quality(peer) * peer.source_confidence for peer in valid_peers) / len(valid_peers)
    width_factor = min(1.0, config.max_halfwidth / max(observed_halfwidth, 1e-12))
    v_conf = max(0.0, min(1.0,
        peer_factor * freshness * coverage * target.source_confidence * target_quality * peer_quality * width_factor
    ))
    target_pb = method_bands["PB"][1] / target.book_value_per_share
    target_pe = None
    if "PE" in method_bands:
        target_pe = method_bands["PE"][1] / target.earnings_per_share

    diagnostics = {
        "peer_tickers": [peer.ticker for peer in valid_peers],
        "peer_count": len(valid_peers),
        "excluded_peers": excluded,
        "method_bands": {name: {"low": band[0], "mid": band[1], "high": band[2]} for name, band in method_bands.items()},
        "method_peer_counts": method_peer_counts,
        "method_weights": {name: weights_by_method[name] for name in selected},
        "target_price_age_days": price_age,
        "target_statement_age_days": statement_age,
        "target_technical_quality": target_quality,
        "peer_technical_quality": peer_quality,
        "target_metrics": {
            "current_pb": target.current_pb,
            "current_pe": target.current_pe,
            "roe_ttm": target.roe_ttm,
            "technical_margin": target.technical_margin,
            "combined_ratio": target.combined_ratio,
            "investment_dependency": target.investment_dependency,
            "solvency_ratio": target.solvency_ratio,
        },
        "confidence_factors": {
            "peer_factor": peer_factor,
            "freshness_factor": freshness,
            "method_coverage": coverage,
            "source_confidence": target.source_confidence,
            "target_technical_quality": target_quality,
            "peer_technical_quality": peer_quality,
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
            "reason": "INSURANCE_VALUATION_BAND_TOO_WIDE",
            "valuation_score": 0.5,
            "z_val": None,
            "v_conf": 0.0,
        }
    return result


def combine_insurance_m2(
    valuation: Mapping[str, Any],
    *,
    follow_score: Any,
    follow_active: Any,
    config: InsuranceValuationConfig,
) -> dict[str, Any]:
    config = _validated_config(config)
    if not isinstance(valuation, Mapping):
        raise InsuranceValuationError("valuation mapping olmali")
    ticker = _strict_text("valuation.ticker", valuation.get("ticker"), uppercase=True)
    if type(follow_active) is not bool:
        raise InsuranceValuationError("follow_active Python bool olmali")
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
        "m2_source": "INSURANCE_PB_PE_TWO_AXIS_V1",
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
            "technical_margin": valuation.get("technical_margin"),
            "combined_ratio": valuation.get("combined_ratio"),
            "roe_ttm": valuation.get("roe_ttm"),
        },
    }


def evaluate_insurance_batch(
    snapshots: Iterable[InsuranceSnapshot],
    *,
    config: InsuranceValuationConfig,
    follow_contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    config = _validated_config(config)
    if not isinstance(follow_contexts, Mapping):
        raise InsuranceValuationError("follow_contexts mapping olmali")
    items = [_validated_snapshot(item) for item in snapshots]
    by_ticker: dict[str, InsuranceSnapshot] = {}
    for item in items:
        if item.ticker in by_ticker:
            raise InsuranceValuationError(f"yinelenen snapshot ticker: {item.ticker}")
        by_ticker[item.ticker] = item
    extra = set(follow_contexts) - set(by_ticker)
    if extra:
        raise InsuranceValuationError(
            "beklenmeyen follow_context ticker: " + ", ".join(sorted(repr(x) for x in extra))
        )
    results: list[dict[str, Any]] = []
    for ticker in sorted(by_ticker):
        target = by_ticker[ticker]
        peers = [
            item for other, item in sorted(by_ticker.items())
            if other != ticker and item.business_type == target.business_type and item.period_end == target.period_end
        ]
        valuation = value_insurance_snapshot(target, peers, config)
        context = follow_contexts.get(ticker, {"follow_score": 0.5, "follow_active": False})
        if not isinstance(context, Mapping):
            raise InsuranceValuationError(f"{ticker} follow context mapping olmali")
        m2 = combine_insurance_m2(
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
