from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.utils.missing_values import is_missing_like as _is_missing_like
from zoneinfo import ZoneInfo


STATUS_OK = "OK"
STATUS_INSUFFICIENT = "YETERSIZ_VERI"
STATUS_TOO_WIDE = "BAND_TOO_WIDE"
SUPPORTED_MULTIPLES = ("PE", "EV_EBIT", "PS", "PB")
MAX_ABS_NUMBER = 1e100


class NonfinValuationError(ValueError):
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
        raise NonfinValuationError(f"{name} sonlu sayi olmali")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NonfinValuationError(f"{name} sonlu sayi olmali") from exc
    if not math.isfinite(result) or abs(result) > MAX_ABS_NUMBER:
        raise NonfinValuationError(f"{name} sonlu sayi sinirinda olmali")
    if minimum is not None:
        if strict_minimum and result <= minimum:
            raise NonfinValuationError(f"{name} {minimum} degerinden buyuk olmali")
        if not strict_minimum and result < minimum:
            raise NonfinValuationError(f"{name} {minimum} degerinden kucuk olamaz")
    if maximum is not None and result > maximum:
        raise NonfinValuationError(f"{name} {maximum} degerini asamaz")
    return result


def _optional_number(name: str, value: Any) -> float | None:
    if _is_missing_like(value):
        return None
    return _finite_number(name, value)


def _strict_text(name: str, value: Any, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NonfinValuationError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _aware_datetime(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise NonfinValuationError(f"{name} timezone iceren datetime olmali")
    return value


def _strict_date(name: str, value: Any) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise NonfinValuationError(f"{name} date olmali")
    return value


def _previous_quarter_end(value: date) -> date:
    expected_days = {3: 31, 6: 30, 9: 30, 12: 31}
    month = value.month
    if month not in expected_days or value.day != expected_days[month]:
        raise NonfinValuationError("period_end takvim ceyrek sonu olmali")
    if month == 3:
        return date(value.year - 1, 12, 31)
    previous_month = month - 3
    if previous_month == 6:
        return date(value.year, 6, 30)
    if previous_month == 9:
        return date(value.year, 9, 30)
    return date(value.year, 3, 31)


def _validate_quarter_sequence(periods: Sequence[date]) -> None:
    if len(periods) != 4:
        raise NonfinValuationError("TTM icin tam dort bagimsiz ceyrek zorunlu")
    if len(set(periods)) != 4:
        raise NonfinValuationError("TTM ceyrekleri yinelenemez")
    ordered = sorted(periods)
    for idx in range(1, len(ordered)):
        if _previous_quarter_end(ordered[idx]) != ordered[idx - 1]:
            raise NonfinValuationError("TTM ceyrekleri takvimde bitisik olmali")


def _quantile_linear(values: Sequence[float], q: float) -> float:
    if not values:
        raise NonfinValuationError("quantile bos veriyle hesaplanamaz")
    quantile = _finite_number("quantile", q, minimum=0.0, maximum=1.0)
    ordered = sorted(_finite_number("peer multiple", value, minimum=0.0, strict_minimum=True) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * quantile
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    fraction = pos - lo
    return ordered[lo] * (1.0 - fraction) + ordered[hi] * fraction


def _weighted_geometric(values: Mapping[str, float], weights: Mapping[str, float]) -> float:
    if not values:
        raise NonfinValuationError("geometric aggregation bos olamaz")
    total_weight = math.fsum(weights[key] for key in values)
    if total_weight <= 0:
        raise NonfinValuationError("geometric aggregation agirligi pozitif olmali")
    return math.exp(math.fsum(weights[key] * math.log(values[key]) for key in values) / total_weight)


def _stable_json_sha(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class NonfinValuationConfig:
    valuation_profile: str
    valuation_version: int
    source_derivation_profile: str
    source_derivation_version: int
    multiple_weights: Mapping[str, float]
    lower_quantile: float = 0.25
    upper_quantile: float = 0.75
    minimum_peer_count: int = 5
    full_confidence_peer_count: int = 12
    minimum_coverage_weight: float = 0.50
    max_halfwidth: float = 1.25
    band_width_shadow_mode: bool = True
    valuation_axis_weight: float = 0.60
    follow_axis_weight: float = 0.40
    max_price_age_days: int = 7

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NonfinValuationConfig":
        if not isinstance(data, Mapping):
            raise NonfinValuationError("NONFIN valuation config nesne olmali")
        allowed = {
            "valuation_profile", "valuation_version", "source_derivation_profile",
            "source_derivation_version", "multiple_weights",
            "lower_quantile", "upper_quantile", "minimum_peer_count",
            "full_confidence_peer_count", "minimum_coverage_weight",
            "max_halfwidth", "band_width_shadow_mode",
            "valuation_axis_weight", "follow_axis_weight", "max_price_age_days",
        }
        unknown = set(data) - allowed
        if unknown:
            raise NonfinValuationError(
                "NONFIN valuation bilinmeyen alanlar: "
                + ", ".join(sorted(repr(value) for value in unknown))
            )
        profile = _strict_text("valuation_profile", data.get("valuation_profile"))
        version = data.get("valuation_version")
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise NonfinValuationError("valuation_version pozitif Python int olmali")
        source_profile = _strict_text(
            "source_derivation_profile", data.get("source_derivation_profile")
        )
        source_version = data.get("source_derivation_version")
        if isinstance(source_version, bool) or not isinstance(source_version, int) or source_version <= 0:
            raise NonfinValuationError("source_derivation_version pozitif Python int olmali")
        raw_weights = data.get("multiple_weights")
        if not isinstance(raw_weights, Mapping) or not raw_weights:
            raise NonfinValuationError("multiple_weights dolu nesne olmali")
        unknown_multiples = set(raw_weights) - set(SUPPORTED_MULTIPLES)
        if unknown_multiples:
            raise NonfinValuationError(
                "desteklenmeyen multiple: " + ", ".join(sorted(repr(value) for value in unknown_multiples))
            )
        weights = {
            key: _finite_number(f"multiple_weights.{key}", value, minimum=0.0, maximum=1.0)
            for key, value in raw_weights.items()
        }
        if not math.isclose(math.fsum(weights.values()), 1.0, abs_tol=1e-12, rel_tol=0.0):
            raise NonfinValuationError("multiple_weights toplami 1 olmali")
        lower = _finite_number("lower_quantile", data.get("lower_quantile", 0.25), minimum=0.0, maximum=1.0)
        upper = _finite_number("upper_quantile", data.get("upper_quantile", 0.75), minimum=0.0, maximum=1.0)
        if not lower < 0.5 < upper:
            raise NonfinValuationError("quantile sirasi lower < 0.5 < upper olmali")
        min_peers = data.get("minimum_peer_count", 5)
        full_peers = data.get("full_confidence_peer_count", 12)
        for name, value in (("minimum_peer_count", min_peers), ("full_confidence_peer_count", full_peers)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise NonfinValuationError(f"{name} pozitif Python int olmali")
        if full_peers < min_peers:
            raise NonfinValuationError("full_confidence_peer_count minimum_peer_count altinda olamaz")
        shadow = data.get("band_width_shadow_mode", True)
        if type(shadow) is not bool:
            raise NonfinValuationError("band_width_shadow_mode Python bool olmali")
        axis_weight = _finite_number("valuation_axis_weight", data.get("valuation_axis_weight", 0.60), minimum=0.0, maximum=1.0)
        follow_weight = _finite_number("follow_axis_weight", data.get("follow_axis_weight", 0.40), minimum=0.0, maximum=1.0)
        if not math.isclose(axis_weight + follow_weight, 1.0, abs_tol=1e-12, rel_tol=0.0):
            raise NonfinValuationError("M2 eksen agirliklari toplami 1 olmali")
        max_price_age_days = data.get("max_price_age_days", 7)
        if isinstance(max_price_age_days, bool) or not isinstance(max_price_age_days, int) or max_price_age_days < 0:
            raise NonfinValuationError("max_price_age_days negatif olmayan Python int olmali")
        if max_price_age_days > 31:
            raise NonfinValuationError("max_price_age_days 31 gunu asamaz")
        return cls(
            valuation_profile=profile,
            valuation_version=version,
            source_derivation_profile=source_profile,
            source_derivation_version=source_version,
            multiple_weights=weights,
            lower_quantile=lower,
            upper_quantile=upper,
            minimum_peer_count=min_peers,
            full_confidence_peer_count=full_peers,
            minimum_coverage_weight=_finite_number(
                "minimum_coverage_weight", data.get("minimum_coverage_weight", 0.50), minimum=0.0, maximum=1.0
            ),
            max_halfwidth=_finite_number("max_halfwidth", data.get("max_halfwidth", 1.25), minimum=0.0, strict_minimum=True),
            band_width_shadow_mode=shadow,
            valuation_axis_weight=axis_weight,
            follow_axis_weight=follow_weight,
            max_price_age_days=max_price_age_days,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "NonfinValuationConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls.from_dict(payload)

    @property
    def config_sha256(self) -> str:
        return _stable_json_sha({
            "valuation_profile": self.valuation_profile,
            "valuation_version": self.valuation_version,
            "source_derivation_profile": self.source_derivation_profile,
            "source_derivation_version": self.source_derivation_version,
            "multiple_weights": dict(self.multiple_weights),
            "lower_quantile": self.lower_quantile,
            "upper_quantile": self.upper_quantile,
            "minimum_peer_count": self.minimum_peer_count,
            "full_confidence_peer_count": self.full_confidence_peer_count,
            "minimum_coverage_weight": self.minimum_coverage_weight,
            "max_halfwidth": self.max_halfwidth,
            "band_width_shadow_mode": self.band_width_shadow_mode,
            "valuation_axis_weight": self.valuation_axis_weight,
            "follow_axis_weight": self.follow_axis_weight,
            "max_price_age_days": self.max_price_age_days,
        })


def _validated_config(config: "NonfinValuationConfig") -> "NonfinValuationConfig":
    if not isinstance(config, NonfinValuationConfig):
        raise NonfinValuationError("config NonfinValuationConfig olmali")
    try:
        weights = dict(config.multiple_weights)
    except (TypeError, ValueError) as exc:
        raise NonfinValuationError("multiple_weights nesne olmali") from exc
    return NonfinValuationConfig.from_dict({
        "valuation_profile": config.valuation_profile,
        "valuation_version": config.valuation_version,
        "source_derivation_profile": config.source_derivation_profile,
        "source_derivation_version": config.source_derivation_version,
        "multiple_weights": weights,
        "lower_quantile": config.lower_quantile,
        "upper_quantile": config.upper_quantile,
        "minimum_peer_count": config.minimum_peer_count,
        "full_confidence_peer_count": config.full_confidence_peer_count,
        "minimum_coverage_weight": config.minimum_coverage_weight,
        "max_halfwidth": config.max_halfwidth,
        "band_width_shadow_mode": config.band_width_shadow_mode,
        "valuation_axis_weight": config.valuation_axis_weight,
        "follow_axis_weight": config.follow_axis_weight,
        "max_price_age_days": config.max_price_age_days,
    })


@dataclass(frozen=True)
class NonfinQuarter:
    period_end: date
    revenue: float | None
    ebit: float | None
    net_income: float | None
    total_equity: float | None
    debt_st: float | None
    debt_lt: float | None
    cash_and_eq: float | None
    st_investments: float | None
    shares_out: float | None

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "NonfinQuarter":
        if not isinstance(row, Mapping):
            raise NonfinValuationError("quarter row mapping olmali")
        return cls(
            period_end=_strict_date("period_end", row.get("period_end")),
            revenue=_optional_number("revenue", row.get("revenue")),
            ebit=_optional_number("ebit", row.get("ebit")),
            net_income=_optional_number("net_income", row.get("net_income")),
            total_equity=_optional_number("total_equity", row.get("total_equity")),
            debt_st=_optional_number("debt_st", row.get("debt_st")),
            debt_lt=_optional_number("debt_lt", row.get("debt_lt")),
            cash_and_eq=_optional_number("cash_and_eq", row.get("cash_and_eq")),
            st_investments=_optional_number("st_investments", row.get("st_investments")),
            shares_out=_optional_number("shares_out", row.get("shares_out")),
        )


@dataclass(frozen=True)
class NonfinSnapshot:
    ticker: str
    analysis_at: datetime
    anchor_period_end: date
    sector_code: str
    current_price: float
    price_trade_date: date
    revenue_ttm: float | None
    ebit_ttm: float | None
    net_income_ttm: float | None
    total_equity: float | None
    net_debt: float | None
    shares_out: float

    @property
    def market_cap(self) -> float:
        return self.current_price * self.shares_out

    @property
    def enterprise_value(self) -> float | None:
        if self.net_debt is None:
            return None
        value = self.market_cap + self.net_debt
        return value if value > 0 else None

    def multiples(self) -> dict[str, float]:
        result: dict[str, float] = {}
        if self.net_income_ttm is not None and self.net_income_ttm > 0:
            result["PE"] = self.market_cap / self.net_income_ttm
        ev = self.enterprise_value
        if ev is not None and self.ebit_ttm is not None and self.ebit_ttm > 0:
            result["EV_EBIT"] = ev / self.ebit_ttm
        if self.revenue_ttm is not None and self.revenue_ttm > 0:
            result["PS"] = self.market_cap / self.revenue_ttm
        if self.total_equity is not None and self.total_equity > 0:
            result["PB"] = self.market_cap / self.total_equity
        return {
            key: _finite_number(f"multiple.{key}", value, minimum=0.0, strict_minimum=True)
            for key, value in result.items()
        }


def _validated_snapshot(snapshot: "NonfinSnapshot") -> "NonfinSnapshot":
    if not isinstance(snapshot, NonfinSnapshot):
        raise NonfinValuationError("snapshot NonfinSnapshot olmali")
    ticker = _strict_text("snapshot.ticker", snapshot.ticker, uppercase=True)
    analysis = _aware_datetime("snapshot.analysis_at", snapshot.analysis_at)
    anchor = _strict_date("snapshot.anchor_period_end", snapshot.anchor_period_end)
    _previous_quarter_end(anchor)  # validates true calendar quarter end
    sector = _strict_text("snapshot.sector_code", snapshot.sector_code, uppercase=True)
    price = _finite_number("snapshot.current_price", snapshot.current_price, minimum=0.0, strict_minimum=True)
    price_date = _strict_date("snapshot.price_trade_date", snapshot.price_trade_date)
    if price_date > analysis.astimezone(ZoneInfo("Europe/Istanbul")).date():
        raise NonfinValuationError("snapshot price_trade_date analysis_at sonrasinda olamaz")
    shares = _finite_number("snapshot.shares_out", snapshot.shares_out, minimum=0.0, strict_minimum=True)
    return NonfinSnapshot(
        ticker=ticker, analysis_at=analysis, anchor_period_end=anchor, sector_code=sector,
        current_price=price, price_trade_date=price_date,
        revenue_ttm=_optional_number("snapshot.revenue_ttm", snapshot.revenue_ttm),
        ebit_ttm=_optional_number("snapshot.ebit_ttm", snapshot.ebit_ttm),
        net_income_ttm=_optional_number("snapshot.net_income_ttm", snapshot.net_income_ttm),
        total_equity=_optional_number("snapshot.total_equity", snapshot.total_equity),
        net_debt=_optional_number("snapshot.net_debt", snapshot.net_debt),
        shares_out=shares,
    )


def build_nonfin_snapshot(
    *,
    ticker: Any,
    analysis_at: Any,
    sector_code: Any,
    current_price: Any,
    price_trade_date: Any,
    quarters: Iterable[Mapping[str, Any] | NonfinQuarter],
) -> NonfinSnapshot:
    ticker_text = _strict_text("ticker", ticker, uppercase=True)
    analysis = _aware_datetime("analysis_at", analysis_at)
    sector = _strict_text("sector_code", sector_code, uppercase=True)
    price = _finite_number("current_price", current_price, minimum=0.0, strict_minimum=True)
    trade_date = _strict_date("price_trade_date", price_trade_date)
    if trade_date > analysis.astimezone(ZoneInfo("Europe/Istanbul")).date():
        raise NonfinValuationError("price_trade_date analysis_at sonrasinda olamaz")
    parsed: list[NonfinQuarter] = []
    for item in quarters:
        if isinstance(item, NonfinQuarter):
            item = {
                "period_end": item.period_end, "revenue": item.revenue, "ebit": item.ebit,
                "net_income": item.net_income, "total_equity": item.total_equity,
                "debt_st": item.debt_st, "debt_lt": item.debt_lt,
                "cash_and_eq": item.cash_and_eq, "st_investments": item.st_investments,
                "shares_out": item.shares_out,
            }
        parsed.append(NonfinQuarter.from_mapping(item))
    parsed.sort(key=lambda row: row.period_end)
    _validate_quarter_sequence([row.period_end for row in parsed])
    latest = parsed[-1]
    if latest.shares_out is None or latest.shares_out <= 0:
        raise NonfinValuationError("son ceyrekte pozitif shares_out zorunlu")

    def sum_if_complete(field: str) -> float | None:
        values = [getattr(row, field) for row in parsed]
        if any(value is None for value in values):
            return None
        return math.fsum(float(value) for value in values if value is not None)

    debt_values = (latest.debt_st, latest.debt_lt, latest.cash_and_eq, latest.st_investments)
    net_debt = None
    if all(value is not None for value in debt_values):
        net_debt = float(latest.debt_st or 0) + float(latest.debt_lt or 0) - float(latest.cash_and_eq or 0) - float(latest.st_investments or 0)

    return NonfinSnapshot(
        ticker=ticker_text,
        analysis_at=analysis,
        anchor_period_end=latest.period_end,
        sector_code=sector,
        current_price=price,
        price_trade_date=trade_date,
        revenue_ttm=sum_if_complete("revenue"),
        ebit_ttm=sum_if_complete("ebit"),
        net_income_ttm=sum_if_complete("net_income"),
        total_equity=latest.total_equity,
        net_debt=net_debt,
        shares_out=float(latest.shares_out),
    )


def _implied_price(snapshot: NonfinSnapshot, multiple: str, peer_multiple: float) -> float | None:
    m = _finite_number("peer_multiple", peer_multiple, minimum=0.0, strict_minimum=True)
    equity_value: float | None = None
    if multiple == "PE" and snapshot.net_income_ttm is not None and snapshot.net_income_ttm > 0:
        equity_value = m * snapshot.net_income_ttm
    elif multiple == "EV_EBIT" and snapshot.ebit_ttm is not None and snapshot.ebit_ttm > 0 and snapshot.net_debt is not None:
        equity_value = m * snapshot.ebit_ttm - snapshot.net_debt
    elif multiple == "PS" and snapshot.revenue_ttm is not None and snapshot.revenue_ttm > 0:
        equity_value = m * snapshot.revenue_ttm
    elif multiple == "PB" and snapshot.total_equity is not None and snapshot.total_equity > 0:
        equity_value = m * snapshot.total_equity
    if equity_value is None or equity_value <= 0:
        return None
    price = equity_value / snapshot.shares_out
    if not math.isfinite(price) or price <= 0 or price > MAX_ABS_NUMBER:
        return None
    return float(price)


def _valuation_score(price: float, low: float, mid: float, high: float) -> tuple[float, float]:
    if not (0 < low <= mid <= high):
        raise NonfinValuationError("valuation band geometrisi gecersiz")
    log_scale = max(math.log(high / low), 1e-12)
    z = math.log(mid / price) / (log_scale / 2.0)
    score = 1.0 / (1.0 + math.exp(-1.6 * max(min(z, 20.0), -20.0)))
    return float(score), float(z)


def value_nonfin_snapshot(
    target: NonfinSnapshot,
    peers: Iterable[NonfinSnapshot],
    config: NonfinValuationConfig,
) -> dict[str, Any]:
    target = _validated_snapshot(target)
    config = _validated_config(config)
    analysis_date = target.analysis_at.astimezone(ZoneInfo("Europe/Istanbul")).date()
    target_price_age = (analysis_date - target.price_trade_date).days
    if target_price_age < 0 or target_price_age > config.max_price_age_days:
        return {
            "ticker": target.ticker, "analysis_at": target.analysis_at,
            "anchor_period_end": target.anchor_period_end, "sector_code": target.sector_code,
            "current_price": target.current_price, "price_trade_date": target.price_trade_date,
            "valuation_profile": config.valuation_profile, "valuation_version": config.valuation_version,
            "source_derivation_profile": config.source_derivation_profile,
            "source_derivation_version": config.source_derivation_version,
            "config_sha256": config.config_sha256, "coverage_weight": 0.0,
            "status": STATUS_INSUFFICIENT, "reason": "HEDEF_FIYAT_BAYAT",
            "V_low": None, "V_mid": None, "V_high": None, "valuation_score": 0.5,
            "z_val": None, "v_conf": 0.0, "lower_halfwidth": None, "upper_halfwidth": None,
            "diagnostics": {"target_price_age_days": target_price_age, "peer_tickers": [], "multiple_details": {}},
        }
    raw_peers = list(peers)
    peer_list: list[NonfinSnapshot] = []
    stale_peer_tickers: list[str] = []
    for raw_peer in raw_peers:
        peer = _validated_snapshot(raw_peer)
        if peer.ticker == target.ticker:
            raise NonfinValuationError("leave-one-out peer listesi hedef ticker iceremez")
        if peer.analysis_at != target.analysis_at:
            raise NonfinValuationError("peer analysis_at hedefle ayni olmali")
        if peer.anchor_period_end != target.anchor_period_end:
            raise NonfinValuationError("peer anchor_period_end hedefle ayni olmali")
        if peer.sector_code != target.sector_code:
            raise NonfinValuationError("peer sector_code hedefle ayni olmali")
        peer_age = (analysis_date - peer.price_trade_date).days
        if peer_age < 0 or peer_age > config.max_price_age_days:
            stale_peer_tickers.append(peer.ticker)
            continue
        peer_list.append(peer)

    target_multiples = target.multiples()
    peer_multiples = {peer.ticker: peer.multiples() for peer in peer_list}
    diagnostics: dict[str, Any] = {
        "target_multiples": target_multiples,
        "multiple_details": {},
        "peer_tickers": sorted(peer.ticker for peer in peer_list),
        "stale_peer_tickers": sorted(stale_peer_tickers),
        "target_price_age_days": target_price_age,
    }
    implied_low: dict[str, float] = {}
    implied_mid: dict[str, float] = {}
    implied_high: dict[str, float] = {}
    usable_weights: dict[str, float] = {}
    sample_conf_parts: dict[str, float] = {}

    for multiple, weight in config.multiple_weights.items():
        values = [items[multiple] for items in peer_multiples.values() if multiple in items]
        detail = {
            "configured_weight": weight,
            "peer_count": len(values),
            "usable": False,
        }
        diagnostics["multiple_details"][multiple] = detail
        if multiple not in target_multiples or len(values) < config.minimum_peer_count:
            continue
        q_low = _quantile_linear(values, config.lower_quantile)
        q_mid = _quantile_linear(values, 0.5)
        q_high = _quantile_linear(values, config.upper_quantile)
        prices = (
            _implied_price(target, multiple, q_low),
            _implied_price(target, multiple, q_mid),
            _implied_price(target, multiple, q_high),
        )
        if any(value is None for value in prices):
            detail.update({"peer_q_low": q_low, "peer_q_mid": q_mid, "peer_q_high": q_high})
            continue
        low, mid, high = (float(prices[0]), float(prices[1]), float(prices[2]))
        if not 0 < low <= mid <= high:
            continue
        implied_low[multiple] = low
        implied_mid[multiple] = mid
        implied_high[multiple] = high
        usable_weights[multiple] = weight
        sample_conf_parts[multiple] = min(1.0, len(values) / config.full_confidence_peer_count)
        detail.update({
            "usable": True,
            "peer_q_low": q_low,
            "peer_q_mid": q_mid,
            "peer_q_high": q_high,
            "implied_low": low,
            "implied_mid": mid,
            "implied_high": high,
        })

    coverage = math.fsum(usable_weights.values())
    base = {
        "ticker": target.ticker,
        "analysis_at": target.analysis_at,
        "anchor_period_end": target.anchor_period_end,
        "sector_code": target.sector_code,
        "current_price": target.current_price,
        "price_trade_date": target.price_trade_date,
        "valuation_profile": config.valuation_profile,
        "valuation_version": config.valuation_version,
        "source_derivation_profile": config.source_derivation_profile,
        "source_derivation_version": config.source_derivation_version,
        "config_sha256": config.config_sha256,
        "coverage_weight": float(coverage),
        "diagnostics": diagnostics,
    }
    if coverage < config.minimum_coverage_weight or not implied_mid:
        return {
            **base,
            "status": STATUS_INSUFFICIENT,
            "reason": "YETERSIZ_MULTIPLE_KAPSAMI",
            "V_low": None,
            "V_mid": None,
            "V_high": None,
            "valuation_score": 0.5,
            "z_val": None,
            "v_conf": 0.0,
            "lower_halfwidth": None,
            "upper_halfwidth": None,
        }

    low = _weighted_geometric(implied_low, usable_weights)
    mid = _weighted_geometric(implied_mid, usable_weights)
    high = _weighted_geometric(implied_high, usable_weights)
    if not 0 < low <= mid <= high:
        raise NonfinValuationError("aggregate valuation band geometrisi gecersiz")
    lower_halfwidth = math.log(mid / low)
    upper_halfwidth = math.log(high / mid)
    max_halfwidth = max(lower_halfwidth, upper_halfwidth)
    sample_conf = math.fsum(usable_weights[key] * sample_conf_parts[key] for key in usable_weights) / coverage
    spread_conf = math.exp(-max_halfwidth)
    v_conf = min(max(coverage * sample_conf * spread_conf, 0.0), 1.0)
    score, z_val = _valuation_score(target.current_price, low, mid, high)
    too_wide = max_halfwidth > config.max_halfwidth
    diagnostics["aggregation"] = {
        "usable_multiples": sorted(usable_weights),
        "coverage_weight": coverage,
        "sample_confidence": sample_conf,
        "spread_confidence": spread_conf,
        "max_halfwidth": max_halfwidth,
        "shadow_too_wide": too_wide,
    }
    status = STATUS_OK
    reason = None
    if too_wide and not config.band_width_shadow_mode:
        status = STATUS_TOO_WIDE
        reason = "NONFIN_DEGERLEME_BANDI_COK_GENIS"
        score = 0.5
        z_val = None
        v_conf = 0.0
    return {
        **base,
        "status": status,
        "reason": reason,
        "V_low": float(low),
        "V_mid": float(mid),
        "V_high": float(high),
        "valuation_score": float(score),
        "z_val": z_val,
        "v_conf": float(v_conf),
        "lower_halfwidth": float(lower_halfwidth),
        "upper_halfwidth": float(upper_halfwidth),
    }


def combine_nonfin_m2(
    valuation: Mapping[str, Any],
    *,
    follow_score: Any,
    follow_active: Any,
    config: NonfinValuationConfig,
) -> dict[str, Any]:
    if not isinstance(valuation, Mapping):
        raise NonfinValuationError("valuation mapping olmali")
    config = _validated_config(config)
    if type(follow_active) is not bool:
        raise NonfinValuationError("follow_active Python bool olmali")
    follow = _finite_number("follow_score", follow_score, minimum=0.0, maximum=1.0)
    status = valuation.get("status")
    if not isinstance(status, str):
        raise NonfinValuationError("valuation.status metin olmali")
    raw_val_score = _finite_number(
        "valuation.valuation_score", valuation.get("valuation_score"), minimum=0.0, maximum=1.0
    )
    confidence = _finite_number("valuation.v_conf", valuation.get("v_conf"), minimum=0.0, maximum=1.0)
    usable = status == STATUS_OK
    effective_val = 0.5 + confidence * (raw_val_score - 0.5) if usable else 0.5
    effective_follow = follow if follow_active else 0.5
    m2_score = (
        config.valuation_axis_weight * effective_val
        + config.follow_axis_weight * effective_follow
    )
    m2_score = min(max(m2_score, 0.0), 1.0)
    return {
        "ticker": valuation.get("ticker"),
        "analysis_at": valuation.get("analysis_at"),
        "anchor_period_end": valuation.get("anchor_period_end"),
        "m2": float(m2_score),
        "m2_source": "NONFIN_RELATIVE_TWO_AXIS_V1",
        "valuation_usable": usable,
        "score_inputs": {
            "valuation_score_raw": raw_val_score,
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
        },
    }


def evaluate_nonfin_batch(
    snapshots: Iterable[NonfinSnapshot],
    *,
    config: NonfinValuationConfig,
    follow_contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    config = _validated_config(config)
    items = list(snapshots)
    seen: set[str] = set()
    validated_items: list[NonfinSnapshot] = []
    for raw_snapshot in items:
        snapshot = _validated_snapshot(raw_snapshot)
        if snapshot.ticker in seen:
            raise NonfinValuationError(f"yinelenen ticker: {snapshot.ticker}")
        seen.add(snapshot.ticker)
        validated_items.append(snapshot)
    items = validated_items
    contexts = {} if follow_contexts is None else follow_contexts
    if not isinstance(contexts, Mapping):
        raise NonfinValuationError("follow_contexts mapping olmali")
    unknown_contexts = set(contexts) - seen
    if unknown_contexts:
        raise NonfinValuationError(
            "follow_contexts beklenmeyen ticker iceriyor: "
            + ", ".join(sorted(repr(value) for value in unknown_contexts))
        )
    groups: dict[tuple[datetime, date, str], list[NonfinSnapshot]] = {}
    for snapshot in items:
        groups.setdefault(
            (snapshot.analysis_at, snapshot.anchor_period_end, snapshot.sector_code), []
        ).append(snapshot)

    results: list[dict[str, Any]] = []
    for key in sorted(groups, key=lambda value: (value[0].isoformat(), value[1].isoformat(), value[2])):
        group = sorted(groups[key], key=lambda item: item.ticker)
        for target in group:
            peers = [peer for peer in group if peer.ticker != target.ticker]
            valuation = value_nonfin_snapshot(target, peers, config)
            context = contexts.get(target.ticker, {})
            if not isinstance(context, Mapping):
                raise NonfinValuationError(f"follow_contexts.{target.ticker} mapping olmali")
            m2_result = combine_nonfin_m2(
                valuation,
                follow_score=context.get("follow_score", 0.5),
                follow_active=context.get("follow_active", False),
                config=config,
            )
            results.append({"ticker": target.ticker, "valuation": valuation, "m2": m2_result})
    results.sort(key=lambda row: (-float(row["m2"]["m2"]), row["ticker"]))
    return {
        "valuation_profile": config.valuation_profile,
        "valuation_version": config.valuation_version,
        "source_derivation_profile": config.source_derivation_profile,
        "source_derivation_version": config.source_derivation_version,
        "config_sha256": config.config_sha256,
        "result_count": len(results),
        "results": results,
    }
