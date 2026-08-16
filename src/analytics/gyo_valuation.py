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


class GyoValuationError(ValueError):
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
        raise GyoValuationError(f"{name} sonlu sayi olmali")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GyoValuationError(f"{name} sonlu sayi olmali") from exc
    if not math.isfinite(result) or abs(result) > MAX_ABS_NUMBER:
        raise GyoValuationError(f"{name} sonlu sayi sinirinda olmali")
    if minimum is not None:
        if strict_minimum and result <= minimum:
            raise GyoValuationError(f"{name} {minimum} degerinden buyuk olmali")
        if not strict_minimum and result < minimum:
            raise GyoValuationError(f"{name} {minimum} degerinden kucuk olamaz")
    if maximum is not None and result > maximum:
        raise GyoValuationError(f"{name} {maximum} degerini asamaz")
    return result


def _strict_text(name: str, value: Any, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GyoValuationError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _strict_date(name: str, value: Any) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise GyoValuationError(f"{name} date olmali")
    return value


def _aware_datetime(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GyoValuationError(f"{name} timezone iceren datetime olmali")
    return value


def _strict_int(name: str, value: Any, *, minimum: int = 0) -> int:
    if _is_bool_like(value) or not isinstance(value, int):
        raise GyoValuationError(f"{name} Python int olmali")
    if value < minimum:
        raise GyoValuationError(f"{name} {minimum} degerinden kucuk olamaz")
    return value


def _strict_sha256(name: str, value: Any) -> str:
    text = _strict_text(name, value).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise GyoValuationError(f"{name} 64 karakter hex SHA256 olmali")
    return text


def _stable_json_sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _quantile_linear(values: Sequence[float], q: float) -> float:
    if not values:
        raise GyoValuationError("quantile bos veriyle hesaplanamaz")
    quantile = _finite_number("quantile", q, minimum=0.0, maximum=1.0)
    ordered = sorted(_finite_number("pd_nav", value) for value in values)
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
        raise GyoValuationError("valuation band geometrisi gecersiz")
    log_scale = max(math.log(high / low), 1e-12)
    z = math.log(mid / price) / (log_scale / 2.0)
    score = 1.0 / (1.0 + math.exp(-1.6 * max(min(z, 20.0), -20.0)))
    return float(score), float(z)


@dataclass(frozen=True)
class GyoValuationConfig:
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
    minimum_pd_nav: float = 0.05
    maximum_pd_nav: float = 3.00
    max_nav_age_days: int = 200
    full_freshness_days: int = 95
    max_price_age_days: int = 7
    minimum_source_confidence: float = 0.40
    derived_nav_confidence_factor: float = 0.85
    max_halfwidth: float = 1.25
    band_width_shadow_mode: bool = True
    valuation_axis_weight: float = 0.65
    follow_axis_weight: float = 0.35

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GyoValuationConfig":
        if not isinstance(data, Mapping):
            raise GyoValuationError("GYO valuation config nesne olmali")
        allowed = {
            "valuation_profile", "valuation_version", "source_nav_profile", "source_nav_version",
            "share_basis", "currency", "lower_quantile", "upper_quantile",
            "minimum_peer_count", "full_confidence_peer_count", "minimum_pd_nav", "maximum_pd_nav",
            "max_nav_age_days", "full_freshness_days", "max_price_age_days",
            "minimum_source_confidence", "derived_nav_confidence_factor", "max_halfwidth",
            "band_width_shadow_mode", "valuation_axis_weight", "follow_axis_weight",
        }
        unknown = set(data) - allowed
        if unknown:
            raise GyoValuationError(
                "GYO valuation bilinmeyen alanlar: " + ", ".join(sorted(repr(value) for value in unknown))
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
            raise GyoValuationError("quantile sirasi lower < 0.5 < upper olmali")
        min_peers = _strict_int("minimum_peer_count", data.get("minimum_peer_count", 3), minimum=1)
        full_peers = _strict_int(
            "full_confidence_peer_count", data.get("full_confidence_peer_count", 8), minimum=min_peers
        )
        minimum_pd_nav = _finite_number(
            "minimum_pd_nav", data.get("minimum_pd_nav", 0.05), minimum=0.0, strict_minimum=True
        )
        maximum_pd_nav = _finite_number(
            "maximum_pd_nav", data.get("maximum_pd_nav", 3.0), minimum=minimum_pd_nav, strict_minimum=True
        )
        if minimum_pd_nav >= maximum_pd_nav:
            raise GyoValuationError("minimum_pd_nav maximum_pd_nav degerinden kucuk olmali")
        max_nav_age = _strict_int("max_nav_age_days", data.get("max_nav_age_days", 200), minimum=1)
        full_freshness = _strict_int("full_freshness_days", data.get("full_freshness_days", 95), minimum=0)
        if full_freshness > max_nav_age:
            raise GyoValuationError("full_freshness_days max_nav_age_days degerini asamaz")
        max_price_age = _strict_int("max_price_age_days", data.get("max_price_age_days", 7), minimum=0)
        if max_price_age > 31:
            raise GyoValuationError("max_price_age_days 31 gunu asamaz")
        minimum_source_confidence = _finite_number(
            "minimum_source_confidence", data.get("minimum_source_confidence", 0.40),
            minimum=0.0, maximum=1.0,
        )
        derived_factor = _finite_number(
            "derived_nav_confidence_factor", data.get("derived_nav_confidence_factor", 0.85),
            minimum=0.0, maximum=1.0,
        )
        max_halfwidth = _finite_number(
            "max_halfwidth", data.get("max_halfwidth", 1.25), minimum=0.0, strict_minimum=True
        )
        shadow = data.get("band_width_shadow_mode", True)
        if type(shadow) is not bool:
            raise GyoValuationError("band_width_shadow_mode Python bool olmali")
        valuation_weight = _finite_number(
            "valuation_axis_weight", data.get("valuation_axis_weight", 0.65), minimum=0.0, maximum=1.0
        )
        follow_weight = _finite_number(
            "follow_axis_weight", data.get("follow_axis_weight", 0.35), minimum=0.0, maximum=1.0
        )
        if not math.isclose(valuation_weight + follow_weight, 1.0, abs_tol=1e-12, rel_tol=0.0):
            raise GyoValuationError("M2 eksen agirliklari toplami 1 olmali")
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
            minimum_pd_nav=minimum_pd_nav,
            maximum_pd_nav=maximum_pd_nav,
            max_nav_age_days=max_nav_age,
            full_freshness_days=full_freshness,
            max_price_age_days=max_price_age,
            minimum_source_confidence=minimum_source_confidence,
            derived_nav_confidence_factor=derived_factor,
            max_halfwidth=max_halfwidth,
            band_width_shadow_mode=shadow,
            valuation_axis_weight=valuation_weight,
            follow_axis_weight=follow_weight,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "GyoValuationConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    @property
    def config_sha256(self) -> str:
        return _stable_json_sha({key: getattr(self, key) for key in self.__dataclass_fields__})


def _validated_config(config: GyoValuationConfig) -> GyoValuationConfig:
    if not isinstance(config, GyoValuationConfig):
        raise GyoValuationError("config GyoValuationConfig olmali")
    return GyoValuationConfig.from_dict({key: getattr(config, key) for key in config.__dataclass_fields__})


def validate_gyo_config(config: GyoValuationConfig) -> GyoValuationConfig:
    return _validated_config(config)


@dataclass(frozen=True)
class GyoSnapshot:
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
    property_portfolio_value: float
    nav_source_method: str
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
    def current_pd_nav(self) -> float:
        return self.market_cap / self.nav_total


def build_gyo_snapshot(
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
    property_portfolio_value: Any,
    nav_source_method: Any,
    source_confidence: Any,
    source_document_id: Any,
    source_sha256: Any,
    nav_profile: Any,
    nav_version: Any,
) -> GyoSnapshot:
    analysis = _aware_datetime("analysis_at", analysis_at)
    published = _aware_datetime("nav_published_at", nav_published_at)
    if published > analysis:
        raise GyoValuationError("nav_published_at analysis_at sonrasinda olamaz")
    nav_date = _strict_date("nav_asof_date", nav_asof_date)
    if nav_date > published.astimezone(ISTANBUL_TZ).date():
        raise GyoValuationError("nav_asof_date nav_published_at tarihinden sonra olamaz")
    price_date = _strict_date("price_trade_date", price_trade_date)
    if price_date > analysis.astimezone(ISTANBUL_TZ).date():
        raise GyoValuationError("price_trade_date analysis_at sonrasinda olamaz")
    method = _strict_text("nav_source_method", nav_source_method, uppercase=True)
    if method not in {"DIRECT", "DERIVED"}:
        raise GyoValuationError("nav_source_method DIRECT veya DERIVED olmali")
    return GyoSnapshot(
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
        property_portfolio_value=_finite_number(
            "property_portfolio_value", property_portfolio_value, minimum=0.0, strict_minimum=True
        ),
        nav_source_method=method,
        source_confidence=_finite_number("source_confidence", source_confidence, minimum=0.0, maximum=1.0),
        source_document_id=_strict_text("source_document_id", source_document_id),
        source_sha256=_strict_sha256("source_sha256", source_sha256),
        nav_profile=_strict_text("nav_profile", nav_profile),
        nav_version=_strict_int("nav_version", nav_version, minimum=1),
    )


def _validated_snapshot(snapshot: GyoSnapshot) -> GyoSnapshot:
    if not isinstance(snapshot, GyoSnapshot):
        raise GyoValuationError("snapshot GyoSnapshot olmali")
    return build_gyo_snapshot(**snapshot.__dict__)


def _freshness_confidence(age_days: int, config: GyoValuationConfig) -> float:
    if age_days <= config.full_freshness_days:
        return 1.0
    span = config.max_nav_age_days - config.full_freshness_days
    if span <= 0:
        return 1.0
    return max(0.0, 1.0 - (age_days - config.full_freshness_days) / span)


def _method_factor(method: str, config: GyoValuationConfig) -> float:
    return 1.0 if method == "DIRECT" else config.derived_nav_confidence_factor


def _base_result(target: GyoSnapshot, config: GyoValuationConfig) -> dict[str, Any]:
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
        "property_portfolio_value": target.property_portfolio_value,
        "nav_per_share": target.nav_per_share,
        "current_pd_nav": target.current_pd_nav,
        "nav_source_method": target.nav_source_method,
        "source_confidence": target.source_confidence,
        "source_document_id": target.source_document_id,
        "source_sha256": target.source_sha256,
        "nav_profile": target.nav_profile,
        "nav_version": target.nav_version,
        "valuation_profile": config.valuation_profile,
        "valuation_version": config.valuation_version,
        "config_sha256": config.config_sha256,
    }


def _insufficient(base: Mapping[str, Any], reason: str, diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **base,
        "status": STATUS_INSUFFICIENT,
        "reason": reason,
        "V_low": None,
        "V_mid": None,
        "V_high": None,
        "valuation_score": 0.5,
        "z_val": None,
        "v_conf": 0.0,
        "lower_halfwidth": None,
        "upper_halfwidth": None,
        "target_pd_nav": None,
        "diagnostics": dict(diagnostics),
    }


def value_gyo_snapshot(
    target: GyoSnapshot,
    peers: Iterable[GyoSnapshot],
    config: GyoValuationConfig,
) -> dict[str, Any]:
    target = _validated_snapshot(target)
    config = _validated_config(config)
    if target.nav_profile != config.source_nav_profile or target.nav_version != config.source_nav_version:
        raise GyoValuationError("target NAV profil/surum config ile uyusmuyor")
    analysis_date = target.analysis_at.astimezone(ISTANBUL_TZ).date()
    price_age = (analysis_date - target.price_trade_date).days
    nav_age = (analysis_date - target.nav_asof_date).days
    base = _base_result(target, config)
    common_diag = {"target_price_age_days": price_age, "target_nav_age_days": nav_age,
                   "peer_tickers": [], "excluded_peers": {}}
    if target.share_basis != config.share_basis:
        return _insufficient(base, "HEDEF_PAY_BAZI_UYUSMUYOR", {
            **common_diag, "target_share_basis": target.share_basis, "expected_share_basis": config.share_basis,
        })
    if target.currency != config.currency:
        return _insufficient(base, "HEDEF_NAV_PARA_BIRIMI_UYUSMUYOR", {
            **common_diag, "target_currency": target.currency, "expected_currency": config.currency,
        })
    if price_age < 0 or price_age > config.max_price_age_days:
        return _insufficient(base, "HEDEF_FIYAT_BAYAT", common_diag)
    if nav_age < 0 or nav_age > config.max_nav_age_days:
        return _insufficient(base, "HEDEF_NAV_BAYAT", common_diag)
    if target.source_confidence < config.minimum_source_confidence:
        return _insufficient(base, "HEDEF_NAV_KAYNAK_GUVENI_DUSUK", common_diag)
    if not config.minimum_pd_nav <= target.current_pd_nav <= config.maximum_pd_nav:
        return _insufficient(base, "HEDEF_PD_NAV_MODEL_ARALIGI_DISINDA", common_diag)

    peer_items: list[GyoSnapshot] = []
    seen: set[str] = set()
    excluded: dict[str, str] = {}
    for raw in peers:
        peer = _validated_snapshot(raw)
        if peer.ticker == target.ticker:
            raise GyoValuationError("leave-one-out ihlali: hedef emsal listesinde")
        if peer.ticker in seen:
            raise GyoValuationError(f"yinelenen peer ticker: {peer.ticker}")
        seen.add(peer.ticker)
        if peer.peer_group != target.peer_group:
            raise GyoValuationError(f"peer_group uyusmuyor: {peer.ticker}")
        if peer.nav_profile != config.source_nav_profile or peer.nav_version != config.source_nav_version:
            excluded[peer.ticker] = "NAV_PROFILE_MISMATCH"
            continue
        if peer.currency != config.currency:
            excluded[peer.ticker] = "CURRENCY_MISMATCH"
            continue
        if peer.share_basis != config.share_basis:
            excluded[peer.ticker] = "SHARE_BASIS_MISMATCH"
            continue
        peer_price_age = (analysis_date - peer.price_trade_date).days
        peer_nav_age = (analysis_date - peer.nav_asof_date).days
        if peer_price_age < 0 or peer_price_age > config.max_price_age_days:
            excluded[peer.ticker] = "FIYAT_BAYAT"
            continue
        if peer_nav_age < 0 or peer_nav_age > config.max_nav_age_days:
            excluded[peer.ticker] = "NAV_BAYAT"
            continue
        if peer.source_confidence < config.minimum_source_confidence:
            excluded[peer.ticker] = "NAV_KAYNAK_GUVENI_DUSUK"
            continue
        if not config.minimum_pd_nav <= peer.current_pd_nav <= config.maximum_pd_nav:
            excluded[peer.ticker] = "PD_NAV_MODEL_ARALIGI_DISINDA"
            continue
        peer_items.append(peer)

    peer_items.sort(key=lambda item: item.ticker)
    if len(peer_items) < config.minimum_peer_count:
        return _insufficient(base, "YETERSIZ_GYO_EMSALI", {
            **common_diag, "peer_tickers": [peer.ticker for peer in peer_items],
            "excluded_peers": excluded, "peer_count": len(peer_items),
        })

    ratios = [peer.current_pd_nav for peer in peer_items]
    ratio_low = _quantile_linear(ratios, config.lower_quantile)
    ratio_mid = _quantile_linear(ratios, 0.5)
    ratio_high = _quantile_linear(ratios, config.upper_quantile)
    low = target.nav_per_share * ratio_low
    mid = target.nav_per_share * ratio_mid
    high = target.nav_per_share * ratio_high
    if not (0 < low <= mid <= high):
        raise GyoValuationError("GYO PD/NAD band geometrisi gecersiz")
    valuation_score, z_val = _valuation_score(target.current_price, low, mid, high)
    lower_halfwidth = math.log(mid / low)
    upper_halfwidth = math.log(high / mid)
    max_observed_halfwidth = max(lower_halfwidth, upper_halfwidth)
    shadow_too_wide = max_observed_halfwidth > config.max_halfwidth

    peer_factor = min(1.0, len(peer_items) / config.full_confidence_peer_count)
    freshness = _freshness_confidence(nav_age, config)
    method_factor = _method_factor(target.nav_source_method, config)
    peer_quality = sum(
        peer.source_confidence * _method_factor(peer.nav_source_method, config) for peer in peer_items
    ) / len(peer_items)
    width_factor = min(1.0, config.max_halfwidth / max(max_observed_halfwidth, 1e-12))
    v_conf = max(0.0, min(1.0, peer_factor * freshness * target.source_confidence * method_factor * peer_quality * width_factor))

    diagnostics = {
        "peer_tickers": [peer.ticker for peer in peer_items],
        "peer_count": len(peer_items),
        "peer_pd_nav_values": {peer.ticker: peer.current_pd_nav for peer in peer_items},
        "excluded_peers": excluded,
        "peer_pd_nav_low": ratio_low,
        "peer_pd_nav_mid": ratio_mid,
        "peer_pd_nav_high": ratio_high,
        "target_price_age_days": price_age,
        "target_nav_age_days": nav_age,
        "target_nav_source_method": target.nav_source_method,
        "target_method_factor": method_factor,
        "peer_source_quality": peer_quality,
        "confidence_factors": {
            "peer_factor": peer_factor,
            "freshness_factor": freshness,
            "source_confidence": target.source_confidence,
            "method_factor": method_factor,
            "peer_source_quality": peer_quality,
            "width_factor": width_factor,
        },
        "aggregation": {
            "max_halfwidth": config.max_halfwidth,
            "observed_halfwidth": max_observed_halfwidth,
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
        "target_pd_nav": ratio_mid,
        "diagnostics": diagnostics,
    }
    if shadow_too_wide and not config.band_width_shadow_mode:
        return {
            **result,
            "status": STATUS_TOO_WIDE,
            "reason": "GYO_PD_NAV_BAND_TOO_WIDE",
            "valuation_score": 0.5,
            "z_val": None,
            "v_conf": 0.0,
        }
    return result


def combine_gyo_m2(
    valuation: Mapping[str, Any],
    *,
    follow_score: Any,
    follow_active: Any,
    config: GyoValuationConfig,
) -> dict[str, Any]:
    config = _validated_config(config)
    if not isinstance(valuation, Mapping):
        raise GyoValuationError("valuation mapping olmali")
    ticker = _strict_text("valuation.ticker", valuation.get("ticker"), uppercase=True)
    active = follow_active
    if type(active) is not bool:
        raise GyoValuationError("follow_active Python bool olmali")
    follow = _finite_number("follow_score", follow_score, minimum=0.0, maximum=1.0) if active else 0.5
    raw_valuation = _finite_number(
        "valuation_score", valuation.get("valuation_score", 0.5), minimum=0.0, maximum=1.0
    )
    conf = _finite_number("v_conf", valuation.get("v_conf", 0.0), minimum=0.0, maximum=1.0)
    usable = valuation.get("status") == STATUS_OK and conf > 0.0
    effective_valuation = 0.5 + (raw_valuation - 0.5) * conf if usable else 0.5
    m2 = config.valuation_axis_weight * effective_valuation + config.follow_axis_weight * follow
    return {
        "ticker": ticker,
        "analysis_at": valuation.get("analysis_at"),
        "nav_asof_date": valuation.get("nav_asof_date"),
        "m2": float(max(0.0, min(1.0, m2))),
        "m2_source": "GYO_PD_NAV_TWO_AXIS_V1",
        "valuation_usable": bool(usable),
        "score_inputs": {
            "valuation_score_raw": raw_valuation,
            "valuation_score_effective": effective_valuation,
            "valuation_confidence": conf,
            "valuation_weight": config.valuation_axis_weight,
            "follow_score": follow,
            "follow_active": active,
            "follow_weight": config.follow_axis_weight,
            "valuation_status": valuation.get("status"),
            "valuation_reason": valuation.get("reason"),
        },
    }


def evaluate_gyo_batch(
    snapshots: Iterable[GyoSnapshot],
    *,
    config: GyoValuationConfig,
    follow_contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    config = _validated_config(config)
    if not isinstance(follow_contexts, Mapping):
        raise GyoValuationError("follow_contexts mapping olmali")
    items = [_validated_snapshot(item) for item in snapshots]
    by_ticker: dict[str, GyoSnapshot] = {}
    for item in items:
        if item.ticker in by_ticker:
            raise GyoValuationError(f"yinelenen snapshot ticker: {item.ticker}")
        by_ticker[item.ticker] = item
    extra = set(follow_contexts) - set(by_ticker)
    if extra:
        raise GyoValuationError("beklenmeyen follow_context ticker: " + ", ".join(sorted(repr(x) for x in extra)))
    results: list[dict[str, Any]] = []
    for ticker in sorted(by_ticker):
        target = by_ticker[ticker]
        peers = [item for other, item in sorted(by_ticker.items()) if other != ticker and item.peer_group == target.peer_group]
        valuation = value_gyo_snapshot(target, peers, config)
        context = follow_contexts.get(ticker, {"follow_score": 0.5, "follow_active": False})
        if not isinstance(context, Mapping):
            raise GyoValuationError(f"{ticker} follow context mapping olmali")
        m2 = combine_gyo_m2(
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
        "source_nav_profile": config.source_nav_profile,
        "source_nav_version": config.source_nav_version,
        "config_sha256": config.config_sha256,
        "result_count": len(results),
        "results": results,
    }
