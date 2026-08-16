from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


STATUS_OK = "OK"
STATUS_INSUFFICIENT = "YETERSIZ_VERI"
STATUS_TOO_WIDE = "BAND_TOO_WIDE"
MAX_ABS_NUMBER = 1e100
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")


class HoldingValuationError(ValueError):
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
        raise HoldingValuationError(f"{name} sonlu sayi olmali")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HoldingValuationError(f"{name} sonlu sayi olmali") from exc
    if not math.isfinite(result) or abs(result) > MAX_ABS_NUMBER:
        raise HoldingValuationError(f"{name} sonlu sayi sinirinda olmali")
    if minimum is not None:
        if strict_minimum and result <= minimum:
            raise HoldingValuationError(f"{name} {minimum} degerinden buyuk olmali")
        if not strict_minimum and result < minimum:
            raise HoldingValuationError(f"{name} {minimum} degerinden kucuk olamaz")
    if maximum is not None and result > maximum:
        raise HoldingValuationError(f"{name} {maximum} degerini asamaz")
    return result


def _strict_text(name: str, value: Any, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HoldingValuationError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _strict_date(name: str, value: Any) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise HoldingValuationError(f"{name} date olmali")
    return value


def _aware_datetime(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HoldingValuationError(f"{name} timezone iceren datetime olmali")
    return value


def _strict_int(name: str, value: Any, *, minimum: int = 0) -> int:
    if _is_bool_like(value) or not isinstance(value, int):
        raise HoldingValuationError(f"{name} Python int olmali")
    if value < minimum:
        raise HoldingValuationError(f"{name} {minimum} degerinden kucuk olamaz")
    return value


def _strict_sha256(name: str, value: Any) -> str:
    text = _strict_text(name, value).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise HoldingValuationError(f"{name} 64 karakter hex SHA256 olmali")
    return text


def _stable_json_sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _quantile_linear(values: Sequence[float], q: float) -> float:
    if not values:
        raise HoldingValuationError("quantile bos veriyle hesaplanamaz")
    quantile = _finite_number("quantile", q, minimum=0.0, maximum=1.0)
    ordered = sorted(_finite_number("discount", value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * quantile
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    fraction = pos - lo
    return ordered[lo] * (1.0 - fraction) + ordered[hi] * fraction


def _valuation_score(price: float, low: float, mid: float, high: float) -> tuple[float, float]:
    if not (0 < low <= mid <= high):
        raise HoldingValuationError("valuation band geometrisi gecersiz")
    log_scale = max(math.log(high / low), 1e-12)
    z = math.log(mid / price) / (log_scale / 2.0)
    score = 1.0 / (1.0 + math.exp(-1.6 * max(min(z, 20.0), -20.0)))
    return float(score), float(z)


@dataclass(frozen=True)
class HoldingValuationConfig:
    valuation_profile: str
    valuation_version: int
    source_nav_profile: str
    source_nav_version: int
    share_basis: str
    currency: str = "TRY"
    lower_quantile: float = 0.25
    upper_quantile: float = 0.75
    minimum_peer_count: int = 3
    full_confidence_peer_count: int = 8
    minimum_discount: float = -0.50
    maximum_discount: float = 0.90
    max_nav_age_days: int = 370
    full_freshness_days: int = 120
    max_price_age_days: int = 7
    minimum_source_confidence: float = 0.40
    max_halfwidth: float = 1.25
    band_width_shadow_mode: bool = True
    valuation_axis_weight: float = 0.65
    follow_axis_weight: float = 0.35

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HoldingValuationConfig":
        if not isinstance(data, Mapping):
            raise HoldingValuationError("HOLDING valuation config nesne olmali")
        allowed = {
            "valuation_profile", "valuation_version", "source_nav_profile", "source_nav_version", "share_basis", "currency",
            "lower_quantile", "upper_quantile", "minimum_peer_count",
            "full_confidence_peer_count", "minimum_discount", "maximum_discount",
            "max_nav_age_days", "full_freshness_days", "max_price_age_days",
            "minimum_source_confidence", "max_halfwidth", "band_width_shadow_mode",
            "valuation_axis_weight", "follow_axis_weight",
        }
        unknown = set(data) - allowed
        if unknown:
            raise HoldingValuationError(
                "HOLDING valuation bilinmeyen alanlar: "
                + ", ".join(sorted(repr(value) for value in unknown))
            )
        profile = _strict_text("valuation_profile", data.get("valuation_profile"))
        version = _strict_int("valuation_version", data.get("valuation_version"), minimum=1)
        source_profile = _strict_text("source_nav_profile", data.get("source_nav_profile"))
        source_version = _strict_int("source_nav_version", data.get("source_nav_version"), minimum=1)
        share_basis = _strict_text("share_basis", data.get("share_basis"), uppercase=True)
        currency = _strict_text("currency", data.get("currency", "TRY"), uppercase=True)
        lower = _finite_number("lower_quantile", data.get("lower_quantile", 0.25), minimum=0.0, maximum=1.0)
        upper = _finite_number("upper_quantile", data.get("upper_quantile", 0.75), minimum=0.0, maximum=1.0)
        if not 0.0 <= lower < 0.5 < upper <= 1.0:
            raise HoldingValuationError("quantile sirasi lower < 0.5 < upper olmali")
        min_peers = _strict_int("minimum_peer_count", data.get("minimum_peer_count", 3), minimum=1)
        full_peers = _strict_int(
            "full_confidence_peer_count", data.get("full_confidence_peer_count", 8), minimum=min_peers
        )
        minimum_discount = _finite_number(
            "minimum_discount", data.get("minimum_discount", -0.50), minimum=-0.95, maximum=0.95
        )
        maximum_discount = _finite_number(
            "maximum_discount", data.get("maximum_discount", 0.90), minimum=-0.95, maximum=0.98
        )
        if minimum_discount >= maximum_discount:
            raise HoldingValuationError("minimum_discount maximum_discount degerinden kucuk olmali")
        max_nav_age = _strict_int("max_nav_age_days", data.get("max_nav_age_days", 370), minimum=1)
        full_freshness = _strict_int("full_freshness_days", data.get("full_freshness_days", 120), minimum=0)
        if full_freshness > max_nav_age:
            raise HoldingValuationError("full_freshness_days max_nav_age_days degerini asamaz")
        max_price_age = _strict_int("max_price_age_days", data.get("max_price_age_days", 7), minimum=0)
        if max_price_age > 31:
            raise HoldingValuationError("max_price_age_days 31 gunu asamaz")
        minimum_source_confidence = _finite_number(
            "minimum_source_confidence", data.get("minimum_source_confidence", 0.40), minimum=0.0, maximum=1.0
        )
        max_halfwidth = _finite_number(
            "max_halfwidth", data.get("max_halfwidth", 1.25), minimum=0.0, strict_minimum=True
        )
        shadow = data.get("band_width_shadow_mode", True)
        if type(shadow) is not bool:
            raise HoldingValuationError("band_width_shadow_mode Python bool olmali")
        valuation_weight = _finite_number(
            "valuation_axis_weight", data.get("valuation_axis_weight", 0.65), minimum=0.0, maximum=1.0
        )
        follow_weight = _finite_number(
            "follow_axis_weight", data.get("follow_axis_weight", 0.35), minimum=0.0, maximum=1.0
        )
        if not math.isclose(valuation_weight + follow_weight, 1.0, abs_tol=1e-12, rel_tol=0.0):
            raise HoldingValuationError("M2 eksen agirliklari toplami 1 olmali")
        return cls(
            valuation_profile=profile,
            valuation_version=version,
            source_nav_profile=source_profile,
            source_nav_version=source_version,
            share_basis=share_basis,
            currency=currency,
            lower_quantile=lower,
            upper_quantile=upper,
            minimum_peer_count=min_peers,
            full_confidence_peer_count=full_peers,
            minimum_discount=minimum_discount,
            maximum_discount=maximum_discount,
            max_nav_age_days=max_nav_age,
            full_freshness_days=full_freshness,
            max_price_age_days=max_price_age,
            minimum_source_confidence=minimum_source_confidence,
            max_halfwidth=max_halfwidth,
            band_width_shadow_mode=shadow,
            valuation_axis_weight=valuation_weight,
            follow_axis_weight=follow_weight,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "HoldingValuationConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls.from_dict(payload)

    @property
    def config_sha256(self) -> str:
        return _stable_json_sha({
            "valuation_profile": self.valuation_profile,
            "valuation_version": self.valuation_version,
            "source_nav_profile": self.source_nav_profile,
            "source_nav_version": self.source_nav_version,
            "share_basis": self.share_basis,
            "currency": self.currency,
            "lower_quantile": self.lower_quantile,
            "upper_quantile": self.upper_quantile,
            "minimum_peer_count": self.minimum_peer_count,
            "full_confidence_peer_count": self.full_confidence_peer_count,
            "minimum_discount": self.minimum_discount,
            "maximum_discount": self.maximum_discount,
            "max_nav_age_days": self.max_nav_age_days,
            "full_freshness_days": self.full_freshness_days,
            "max_price_age_days": self.max_price_age_days,
            "minimum_source_confidence": self.minimum_source_confidence,
            "max_halfwidth": self.max_halfwidth,
            "band_width_shadow_mode": self.band_width_shadow_mode,
            "valuation_axis_weight": self.valuation_axis_weight,
            "follow_axis_weight": self.follow_axis_weight,
        })


def _validated_config(config: HoldingValuationConfig) -> HoldingValuationConfig:
    if not isinstance(config, HoldingValuationConfig):
        raise HoldingValuationError("config HoldingValuationConfig olmali")
    return HoldingValuationConfig.from_dict({
        "valuation_profile": config.valuation_profile,
        "valuation_version": config.valuation_version,
        "source_nav_profile": config.source_nav_profile,
        "source_nav_version": config.source_nav_version,
        "share_basis": config.share_basis,
        "currency": config.currency,
        "lower_quantile": config.lower_quantile,
        "upper_quantile": config.upper_quantile,
        "minimum_peer_count": config.minimum_peer_count,
        "full_confidence_peer_count": config.full_confidence_peer_count,
        "minimum_discount": config.minimum_discount,
        "maximum_discount": config.maximum_discount,
        "max_nav_age_days": config.max_nav_age_days,
        "full_freshness_days": config.full_freshness_days,
        "max_price_age_days": config.max_price_age_days,
        "minimum_source_confidence": config.minimum_source_confidence,
        "max_halfwidth": config.max_halfwidth,
        "band_width_shadow_mode": config.band_width_shadow_mode,
        "valuation_axis_weight": config.valuation_axis_weight,
        "follow_axis_weight": config.follow_axis_weight,
    })


def validate_holding_config(config: HoldingValuationConfig) -> HoldingValuationConfig:
    """Public fail-closed runtime validation for DB and CLI boundaries."""
    return _validated_config(config)


@dataclass(frozen=True)
class HoldingSnapshot:
    ticker: str
    analysis_at: datetime
    peer_group: str
    currency: str
    share_basis: str
    current_price: float
    price_trade_date: date
    nav_asof_date: date
    nav_published_at: datetime
    nav_total: float
    shares_out: float
    source_confidence: float
    source_document_id: str
    source_sha256: str
    nav_profile: str
    nav_version: int

    @property
    def nav_per_share(self) -> float:
        return self.nav_total / self.shares_out

    @property
    def market_cap(self) -> float:
        return self.current_price * self.shares_out

    @property
    def current_discount(self) -> float:
        return 1.0 - self.market_cap / self.nav_total


def build_holding_snapshot(
    *,
    ticker: Any,
    analysis_at: Any,
    peer_group: Any,
    currency: Any,
    share_basis: Any,
    current_price: Any,
    price_trade_date: Any,
    nav_asof_date: Any,
    nav_published_at: Any,
    nav_total: Any,
    shares_out: Any,
    source_confidence: Any,
    source_document_id: Any,
    source_sha256: Any,
    nav_profile: Any,
    nav_version: Any,
) -> HoldingSnapshot:
    analysis = _aware_datetime("analysis_at", analysis_at)
    published = _aware_datetime("nav_published_at", nav_published_at)
    if published > analysis:
        raise HoldingValuationError("nav_published_at analysis_at sonrasinda olamaz")
    nav_date = _strict_date("nav_asof_date", nav_asof_date)
    if nav_date > published.astimezone(ISTANBUL_TZ).date():
        raise HoldingValuationError("nav_asof_date nav_published_at tarihinden sonra olamaz")
    price_date = _strict_date("price_trade_date", price_trade_date)
    if price_date > analysis.astimezone(ISTANBUL_TZ).date():
        raise HoldingValuationError("price_trade_date analysis_at sonrasinda olamaz")
    return HoldingSnapshot(
        ticker=_strict_text("ticker", ticker, uppercase=True),
        analysis_at=analysis,
        peer_group=_strict_text("peer_group", peer_group, uppercase=True),
        currency=_strict_text("currency", currency, uppercase=True),
        share_basis=_strict_text("share_basis", share_basis, uppercase=True),
        current_price=_finite_number("current_price", current_price, minimum=0.0, strict_minimum=True),
        price_trade_date=price_date,
        nav_asof_date=nav_date,
        nav_published_at=published,
        nav_total=_finite_number("nav_total", nav_total, minimum=0.0, strict_minimum=True),
        shares_out=_finite_number("shares_out", shares_out, minimum=0.0, strict_minimum=True),
        source_confidence=_finite_number("source_confidence", source_confidence, minimum=0.0, maximum=1.0),
        source_document_id=_strict_text("source_document_id", source_document_id),
        source_sha256=_strict_sha256("source_sha256", source_sha256),
        nav_profile=_strict_text("nav_profile", nav_profile),
        nav_version=_strict_int("nav_version", nav_version, minimum=1),
    )


def _validated_snapshot(snapshot: HoldingSnapshot) -> HoldingSnapshot:
    if not isinstance(snapshot, HoldingSnapshot):
        raise HoldingValuationError("snapshot HoldingSnapshot olmali")
    return build_holding_snapshot(**snapshot.__dict__)


def _freshness_confidence(age_days: int, config: HoldingValuationConfig) -> float:
    if age_days <= config.full_freshness_days:
        return 1.0
    span = config.max_nav_age_days - config.full_freshness_days
    if span <= 0:
        return 1.0
    return max(0.0, 1.0 - (age_days - config.full_freshness_days) / span)


def _base_result(target: HoldingSnapshot, config: HoldingValuationConfig) -> dict[str, Any]:
    return {
        "ticker": target.ticker,
        "analysis_at": target.analysis_at,
        "peer_group": target.peer_group,
        "currency": target.currency,
        "share_basis": target.share_basis,
        "current_price": target.current_price,
        "price_trade_date": target.price_trade_date,
        "nav_asof_date": target.nav_asof_date,
        "nav_published_at": target.nav_published_at,
        "nav_total": target.nav_total,
        "shares_out": target.shares_out,
        "nav_per_share": target.nav_per_share,
        "current_discount": target.current_discount,
        "source_confidence": target.source_confidence,
        "source_document_id": target.source_document_id,
        "source_sha256": target.source_sha256,
        "nav_profile": target.nav_profile,
        "nav_version": target.nav_version,
        "valuation_profile": config.valuation_profile,
        "valuation_version": config.valuation_version,
        "config_sha256": config.config_sha256,
    }


def value_holding_snapshot(
    target: HoldingSnapshot,
    peers: Iterable[HoldingSnapshot],
    config: HoldingValuationConfig,
) -> dict[str, Any]:
    target = _validated_snapshot(target)
    config = _validated_config(config)
    if target.nav_profile != config.source_nav_profile or target.nav_version != config.source_nav_version:
        raise HoldingValuationError("target NAV profil/surum config ile uyusmuyor")
    analysis_date = target.analysis_at.astimezone(ISTANBUL_TZ).date()
    target_price_age = (analysis_date - target.price_trade_date).days
    target_nav_age = (analysis_date - target.nav_asof_date).days
    base = _base_result(target, config)
    if target.share_basis != config.share_basis:
        return {
            **base, "status": STATUS_INSUFFICIENT, "reason": "HEDEF_PAY_BAZI_UYUSMUYOR",
            "V_low": None, "V_mid": None, "V_high": None, "valuation_score": 0.5,
            "z_val": None, "v_conf": 0.0, "lower_halfwidth": None, "upper_halfwidth": None,
            "diagnostics": {"target_share_basis": target.share_basis, "expected_share_basis": config.share_basis,
                            "peer_tickers": [], "excluded_peers": {}},
        }
    if target.currency != config.currency:
        return {
            **base, "status": STATUS_INSUFFICIENT, "reason": "HEDEF_NAV_PARA_BIRIMI_UYUSMUYOR",
            "V_low": None, "V_mid": None, "V_high": None, "valuation_score": 0.5,
            "z_val": None, "v_conf": 0.0, "lower_halfwidth": None, "upper_halfwidth": None,
            "diagnostics": {"target_currency": target.currency, "expected_currency": config.currency,
                            "peer_tickers": [], "excluded_peers": {}},
        }
    if target_price_age < 0 or target_price_age > config.max_price_age_days:
        return {
            **base, "status": STATUS_INSUFFICIENT, "reason": "HEDEF_FIYAT_BAYAT",
            "V_low": None, "V_mid": None, "V_high": None, "valuation_score": 0.5,
            "z_val": None, "v_conf": 0.0, "lower_halfwidth": None, "upper_halfwidth": None,
            "diagnostics": {"target_price_age_days": target_price_age, "target_nav_age_days": target_nav_age,
                            "peer_tickers": [], "excluded_peers": {}},
        }
    if target_nav_age < 0 or target_nav_age > config.max_nav_age_days:
        return {
            **base, "status": STATUS_INSUFFICIENT, "reason": "HEDEF_NAV_BAYAT",
            "V_low": None, "V_mid": None, "V_high": None, "valuation_score": 0.5,
            "z_val": None, "v_conf": 0.0, "lower_halfwidth": None, "upper_halfwidth": None,
            "diagnostics": {"target_price_age_days": target_price_age, "target_nav_age_days": target_nav_age,
                            "peer_tickers": [], "excluded_peers": {}},
        }
    if target.source_confidence < config.minimum_source_confidence:
        return {
            **base, "status": STATUS_INSUFFICIENT, "reason": "HEDEF_NAV_KAYNAK_GUVENI_DUSUK",
            "V_low": None, "V_mid": None, "V_high": None, "valuation_score": 0.5,
            "z_val": None, "v_conf": 0.0, "lower_halfwidth": None, "upper_halfwidth": None,
            "diagnostics": {"target_price_age_days": target_price_age, "target_nav_age_days": target_nav_age,
                            "peer_tickers": [], "excluded_peers": {}},
        }

    peer_list: list[HoldingSnapshot] = []
    excluded: dict[str, str] = {}
    seen: set[str] = set()
    for raw in peers:
        peer = _validated_snapshot(raw)
        if peer.ticker == target.ticker:
            raise HoldingValuationError("leave-one-out peer listesi hedef ticker iceremez")
        if peer.ticker in seen:
            raise HoldingValuationError(f"yinelenen peer ticker: {peer.ticker}")
        seen.add(peer.ticker)
        if peer.analysis_at != target.analysis_at:
            raise HoldingValuationError("peer analysis_at hedefle ayni olmali")
        if peer.peer_group != target.peer_group:
            raise HoldingValuationError("peer_group hedefle ayni olmali")
        if peer.share_basis != config.share_basis:
            excluded[peer.ticker] = "SHARE_BASIS_MISMATCH"
            continue
        if peer.currency != config.currency:
            excluded[peer.ticker] = "CURRENCY_MISMATCH"
            continue
        if peer.nav_profile != config.source_nav_profile or peer.nav_version != config.source_nav_version:
            excluded[peer.ticker] = "NAV_PROFILE_MISMATCH"
            continue
        price_age = (analysis_date - peer.price_trade_date).days
        nav_age = (analysis_date - peer.nav_asof_date).days
        if price_age < 0 or price_age > config.max_price_age_days:
            excluded[peer.ticker] = "FIYAT_BAYAT"
            continue
        if nav_age < 0 or nav_age > config.max_nav_age_days:
            excluded[peer.ticker] = "NAV_BAYAT"
            continue
        if peer.source_confidence < config.minimum_source_confidence:
            excluded[peer.ticker] = "NAV_KAYNAK_GUVENI_DUSUK"
            continue
        discount = peer.current_discount
        if discount < config.minimum_discount or discount > config.maximum_discount:
            excluded[peer.ticker] = "ISKONTO_MODEL_ARALIGI_DISINDA"
            continue
        peer_list.append(peer)

    diagnostics: dict[str, Any] = {
        "target_price_age_days": target_price_age,
        "target_nav_age_days": target_nav_age,
        "target_freshness_confidence": _freshness_confidence(target_nav_age, config),
        "peer_tickers": sorted(peer.ticker for peer in peer_list),
        "excluded_peers": dict(sorted(excluded.items())),
        "peer_discounts": {peer.ticker: peer.current_discount for peer in sorted(peer_list, key=lambda p: p.ticker)},
    }
    if len(peer_list) < config.minimum_peer_count:
        return {
            **base, "status": STATUS_INSUFFICIENT, "reason": "YETERSIZ_HOLDING_EMSALI",
            "V_low": None, "V_mid": None, "V_high": None, "valuation_score": 0.5,
            "z_val": None, "v_conf": 0.0, "lower_halfwidth": None, "upper_halfwidth": None,
            "diagnostics": diagnostics,
        }

    discounts = [peer.current_discount for peer in peer_list]
    d_low = _quantile_linear(discounts, config.lower_quantile)
    d_mid = _quantile_linear(discounts, 0.5)
    d_high = _quantile_linear(discounts, config.upper_quantile)
    if not config.minimum_discount <= d_low <= d_mid <= d_high <= config.maximum_discount:
        raise HoldingValuationError("peer discount quantile geometrisi gecersiz")
    nav_ps = target.nav_per_share
    low = nav_ps * (1.0 - d_high)
    mid = nav_ps * (1.0 - d_mid)
    high = nav_ps * (1.0 - d_low)
    if not 0 < low <= mid <= high:
        raise HoldingValuationError("HOLDING valuation band geometrisi gecersiz")
    lower_halfwidth = math.log(mid / low)
    upper_halfwidth = math.log(high / mid)
    max_halfwidth = max(lower_halfwidth, upper_halfwidth)
    sample_conf = min(1.0, len(peer_list) / config.full_confidence_peer_count)
    target_freshness = _freshness_confidence(target_nav_age, config)
    peer_quality = math.fsum(
        peer.source_confidence * _freshness_confidence((analysis_date - peer.nav_asof_date).days, config)
        for peer in peer_list
    ) / len(peer_list)
    spread_conf = math.exp(-max_halfwidth)
    v_conf = min(max(sample_conf * target.source_confidence * target_freshness * peer_quality * spread_conf, 0.0), 1.0)
    valuation_score, z_val = _valuation_score(target.current_price, low, mid, high)
    too_wide = max_halfwidth > config.max_halfwidth
    diagnostics["aggregation"] = {
        "peer_count": len(peer_list),
        "discount_q_low": d_low,
        "discount_median": d_mid,
        "discount_q_high": d_high,
        "sample_confidence": sample_conf,
        "target_source_confidence": target.source_confidence,
        "target_freshness_confidence": target_freshness,
        "peer_quality_confidence": peer_quality,
        "spread_confidence": spread_conf,
        "max_halfwidth": max_halfwidth,
        "shadow_too_wide": too_wide,
    }
    status = STATUS_OK
    reason = None
    if too_wide and not config.band_width_shadow_mode:
        status = STATUS_TOO_WIDE
        reason = "HOLDING_DEGERLEME_BANDI_COK_GENIS"
        valuation_score = 0.5
        z_val = None
        v_conf = 0.0
    return {
        **base,
        "status": status,
        "reason": reason,
        "V_low": float(low),
        "V_mid": float(mid),
        "V_high": float(high),
        "valuation_score": float(valuation_score),
        "z_val": z_val,
        "v_conf": float(v_conf),
        "lower_halfwidth": float(lower_halfwidth),
        "upper_halfwidth": float(upper_halfwidth),
        "target_discount": float(d_mid),
        "diagnostics": diagnostics,
    }


def combine_holding_m2(
    valuation: Mapping[str, Any],
    *,
    follow_score: Any,
    follow_active: Any,
    config: HoldingValuationConfig,
) -> dict[str, Any]:
    if not isinstance(valuation, Mapping):
        raise HoldingValuationError("valuation mapping olmali")
    config = _validated_config(config)
    if type(follow_active) is not bool:
        raise HoldingValuationError("follow_active Python bool olmali")
    follow = _finite_number("follow_score", follow_score, minimum=0.0, maximum=1.0)
    status = valuation.get("status")
    if not isinstance(status, str):
        raise HoldingValuationError("valuation.status metin olmali")
    raw_val = _finite_number(
        "valuation.valuation_score", valuation.get("valuation_score"), minimum=0.0, maximum=1.0
    )
    confidence = _finite_number("valuation.v_conf", valuation.get("v_conf"), minimum=0.0, maximum=1.0)
    usable = status == STATUS_OK
    effective_val = 0.5 + confidence * (raw_val - 0.5) if usable else 0.5
    effective_follow = follow if follow_active else 0.5
    m2 = config.valuation_axis_weight * effective_val + config.follow_axis_weight * effective_follow
    m2 = min(max(m2, 0.0), 1.0)
    return {
        "ticker": valuation.get("ticker"),
        "analysis_at": valuation.get("analysis_at"),
        "nav_asof_date": valuation.get("nav_asof_date"),
        "m2": float(m2),
        "m2_source": "HOLDING_NAV_DISCOUNT_TWO_AXIS_V1",
        "valuation_usable": usable,
        "score_inputs": {
            "valuation_score_raw": raw_val,
            "valuation_score_effective": float(effective_val),
            "valuation_confidence": confidence,
            "valuation_status": status,
            "follow_score_raw": follow,
            "follow_score_effective": float(effective_follow),
            "follow_active": follow_active,
            "valuation_axis_weight": config.valuation_axis_weight,
            "follow_axis_weight": config.follow_axis_weight,
        },
        "diagnostics": {
            "valuation_reason": valuation.get("reason"),
            "z_val": valuation.get("z_val"),
            "V_low": valuation.get("V_low"),
            "V_mid": valuation.get("V_mid"),
            "V_high": valuation.get("V_high"),
            "target_discount": valuation.get("target_discount"),
        },
    }


def evaluate_holding_batch(
    snapshots: Iterable[HoldingSnapshot],
    *,
    config: HoldingValuationConfig,
    follow_contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    config = _validated_config(config)
    items: list[HoldingSnapshot] = []
    seen: set[str] = set()
    for raw in snapshots:
        snapshot = _validated_snapshot(raw)
        if snapshot.ticker in seen:
            raise HoldingValuationError(f"yinelenen ticker: {snapshot.ticker}")
        seen.add(snapshot.ticker)
        items.append(snapshot)
    contexts = {} if follow_contexts is None else follow_contexts
    if not isinstance(contexts, Mapping):
        raise HoldingValuationError("follow_contexts mapping olmali")
    unknown = set(contexts) - seen
    if unknown:
        raise HoldingValuationError(
            "follow_contexts beklenmeyen ticker iceriyor: "
            + ", ".join(sorted(repr(value) for value in unknown))
        )
    groups: dict[tuple[datetime, str], list[HoldingSnapshot]] = {}
    for item in items:
        groups.setdefault((item.analysis_at, item.peer_group), []).append(item)
    results: list[dict[str, Any]] = []
    for key in sorted(groups, key=lambda value: (value[0].isoformat(), value[1])):
        group = sorted(groups[key], key=lambda item: item.ticker)
        for target in group:
            peers = [peer for peer in group if peer.ticker != target.ticker]
            valuation = value_holding_snapshot(target, peers, config)
            context = contexts.get(target.ticker, {})
            if not isinstance(context, Mapping):
                raise HoldingValuationError(f"follow_contexts.{target.ticker} mapping olmali")
            m2 = combine_holding_m2(
                valuation,
                follow_score=context.get("follow_score", 0.5),
                follow_active=context.get("follow_active", False),
                config=config,
            )
            results.append({"ticker": target.ticker, "valuation": valuation, "m2": m2})
    results.sort(key=lambda row: (-float(row["m2"]["m2"]), row["ticker"]))
    return {
        "valuation_profile": config.valuation_profile,
        "valuation_version": config.valuation_version,
        "source_nav_profile": config.source_nav_profile,
        "source_nav_version": config.source_nav_version,
        "config_sha256": config.config_sha256,
        "result_count": len(results),
        "results": results,
    }
