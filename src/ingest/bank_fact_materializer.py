from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from src.ingest.api.mkk_kap import KapApiConfigError, KapApiProtocolError
from src.ingest.api.semantic_facts import SemanticFinancialFact

MAX_DB_INT = 2_147_483_647
MAX_TARGET_PERIODS = 40
MAX_HISTORY_PERIODS = 80
MAX_ABS_METRIC = Decimal("1e100")
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class BankDerivationError(ValueError):
    pass


def _strict_text(name: str, value: Any, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KapApiConfigError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _optional_text(name: str, value: Any, *, uppercase: bool = False) -> Optional[str]:
    if value is None:
        return None
    return _strict_text(name, value, uppercase=uppercase)


def _strict_positive_int(name: str, value: Any, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise KapApiConfigError(f"{name} pozitif Python int olmali")
    if value > maximum:
        raise KapApiConfigError(f"{name} desteklenen siniri asiyor")
    return value


def _quarter_end(value: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise BankDerivationError("anchor_period_end date olmali")
    quarter = (value.month - 1) // 3
    next_month = quarter * 3 + 4
    year = value.year
    if next_month > 12:
        next_month -= 12
        year += 1
    return date(year, next_month, 1) - __import__("datetime").timedelta(days=1)


def _quarter_start(value: date) -> date:
    month = ((value.month - 1) // 3) * 3 + 1
    return date(value.year, month, 1)


def _shift_quarter_end(value: date, offset: int) -> date:
    q_index = value.year * 4 + ((value.month - 1) // 3) + offset
    year, quarter = divmod(q_index, 4)
    month = quarter * 3 + 3
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - __import__("datetime").timedelta(days=1)


def build_quarter_ends(anchor_period_end: date, count: int) -> tuple[date, ...]:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise BankDerivationError("count pozitif Python int olmali")
    anchor = _quarter_end(anchor_period_end)
    return tuple(_shift_quarter_end(anchor, offset) for offset in range(-(count - 1), 1))


@dataclass(frozen=True)
class BankDerivationConfig:
    derivation_profile: str
    derivation_version: int
    semantic_profile: str
    semantic_version: int
    total_equity_field: str
    shares_out_field: Optional[str]
    net_income_field: str
    issued_capital_field: Optional[str] = None
    share_nominal_value: Optional[Decimal] = None
    payout_ratio_field: Optional[str] = None
    dividends_paid_field: Optional[str] = None
    currency: Optional[str] = "TRY"
    target_periods: int = 8
    history_periods: int = 12
    roe_formula: str = "TTM_NET_INCOME_OVER_ENDPOINT_AVERAGE_EQUITY"
    payout_policy: str = "DIRECT_THEN_TTM_DIVIDENDS"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BankDerivationConfig":
        if not isinstance(data, Mapping):
            raise KapApiConfigError("bank derivation config nesne olmali")
        required = (
            "derivation_profile", "derivation_version", "semantic_profile",
            "semantic_version", "total_equity_field", "shares_out_field",
            "net_income_field",
        )
        missing = [key for key in required if key not in data]
        if missing:
            raise KapApiConfigError(f"bank derivation eksik alanlar: {missing}")
        profile = _strict_text("derivation_profile", data["derivation_profile"])
        version = _strict_positive_int(
            "derivation_version", data["derivation_version"], maximum=MAX_DB_INT
        )
        semantic_profile = _strict_text("semantic_profile", data["semantic_profile"])
        semantic_version = _strict_positive_int(
            "semantic_version", data["semantic_version"], maximum=MAX_DB_INT
        )
        target_periods = _strict_positive_int(
            "target_periods", data.get("target_periods", 8), maximum=MAX_TARGET_PERIODS
        )
        history_periods = _strict_positive_int(
            "history_periods", data.get("history_periods", 12), maximum=MAX_HISTORY_PERIODS
        )
        if history_periods < target_periods + 4:
            raise KapApiConfigError(
                "history_periods ROE lag4 icin target_periods + 4 olmali"
            )
        roe_formula = _strict_text(
            "roe_formula",
            data.get("roe_formula", "TTM_NET_INCOME_OVER_ENDPOINT_AVERAGE_EQUITY"),
            uppercase=True,
        )
        if roe_formula != "TTM_NET_INCOME_OVER_ENDPOINT_AVERAGE_EQUITY":
            raise KapApiConfigError("desteklenmeyen roe_formula")
        payout_policy = _strict_text(
            "payout_policy",
            data.get("payout_policy", "DIRECT_THEN_TTM_DIVIDENDS"),
            uppercase=True,
        )
        if payout_policy != "DIRECT_THEN_TTM_DIVIDENDS":
            raise KapApiConfigError("desteklenmeyen payout_policy")
        fields = {
            "total_equity_field": _strict_text(
                "total_equity_field", data["total_equity_field"], uppercase=True
            ),
            "shares_out_field": _optional_text(
                "shares_out_field", data["shares_out_field"], uppercase=True
            ),
            "issued_capital_field": _optional_text(
                "issued_capital_field", data.get("issued_capital_field"), uppercase=True
            ),
            "net_income_field": _strict_text(
                "net_income_field", data["net_income_field"], uppercase=True
            ),
            "payout_ratio_field": _optional_text(
                "payout_ratio_field", data.get("payout_ratio_field"), uppercase=True
            ),
            "dividends_paid_field": _optional_text(
                "dividends_paid_field", data.get("dividends_paid_field"), uppercase=True
            ),
        }
        non_null = [value for value in fields.values() if value is not None]
        if len(non_null) != len(set(non_null)):
            raise KapApiConfigError("bank canonical alanlari birbirinden farkli olmali")
        if fields["shares_out_field"] is None and fields["issued_capital_field"] is None:
            raise KapApiConfigError(
                "shares_out_field veya issued_capital_field alanlarindan biri zorunlu"
            )
        nominal_raw = data.get("share_nominal_value")
        nominal_value: Optional[Decimal] = None
        if nominal_raw is not None:
            if isinstance(nominal_raw, bool) or isinstance(nominal_raw, (list, tuple, dict, set)):
                raise KapApiConfigError("share_nominal_value pozitif sonlu sayi olmali")
            try:
                nominal_value = Decimal(str(nominal_raw))
            except Exception as exc:
                raise KapApiConfigError("share_nominal_value pozitif sonlu sayi olmali") from exc
            if not nominal_value.is_finite() or nominal_value <= 0 or nominal_value > MAX_ABS_METRIC:
                raise KapApiConfigError("share_nominal_value pozitif sonlu sayi olmali")
        if fields["issued_capital_field"] is not None and nominal_value is None:
            raise KapApiConfigError(
                "issued_capital_field icin share_nominal_value zorunlu"
            )
        if fields["issued_capital_field"] is None and nominal_value is not None:
            raise KapApiConfigError(
                "share_nominal_value yalniz issued_capital_field ile kullanilabilir"
            )
        return cls(
            derivation_profile=profile,
            derivation_version=version,
            semantic_profile=semantic_profile,
            semantic_version=semantic_version,
            share_nominal_value=nominal_value,
            currency=_optional_text("currency", data.get("currency", "TRY"), uppercase=True),
            target_periods=target_periods,
            history_periods=history_periods,
            roe_formula=roe_formula,
            payout_policy=payout_policy,
            **fields,
        )

    @classmethod
    def from_json_file(cls, path: str) -> "BankDerivationConfig":
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data)

    @property
    def required_fields(self) -> tuple[str, ...]:
        fields = [self.total_equity_field, self.net_income_field]
        if self.shares_out_field:
            fields.append(self.shares_out_field)
        if self.issued_capital_field:
            fields.append(self.issued_capital_field)
        if self.payout_ratio_field:
            fields.append(self.payout_ratio_field)
        if self.dividends_paid_field:
            fields.append(self.dividends_paid_field)
        return tuple(fields)


@dataclass(frozen=True)
class BankDerivedMetric:
    ticker: str
    period_end: date
    version_tag: str
    version_sequence: int
    published_at: datetime
    source_disclosure_id: str
    roe_ttm: Optional[float]
    bvps: Optional[float]
    payout_sus: Optional[float]
    lineage_sha256: str
    source_lineage: tuple[Mapping[str, Any], ...]
    derivation_profile: str
    derivation_version: int
    diagnostics: Mapping[str, Any]


def _validate_semantic_fact(fact: Any) -> SemanticFinancialFact:
    if not isinstance(fact, SemanticFinancialFact):
        raise BankDerivationError("facts yalniz SemanticFinancialFact icermeli")
    if not isinstance(fact.ticker, str) or not fact.ticker.strip():
        raise BankDerivationError("semantic fact ticker bos olamaz")
    for name, value in (("published_at", fact.published_at), ("mapped_at", fact.mapped_at)):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise BankDerivationError(f"semantic fact {name} timezone icermeli")
    if fact.published_at > fact.mapped_at + timedelta(minutes=5):
        raise BankDerivationError("semantic fact mapped_at yayin anindan once")
    if not isinstance(fact.period_end, date) or isinstance(fact.period_end, datetime):
        raise BankDerivationError("semantic fact period_end date olmali")
    if fact.period_start is not None:
        if not isinstance(fact.period_start, date) or isinstance(fact.period_start, datetime):
            raise BankDerivationError("semantic fact period_start date olmali")
        if fact.period_start > fact.period_end:
            raise BankDerivationError("semantic fact period_start period_end'den sonra")
    if fact.period_end > fact.published_at.astimezone(ISTANBUL_TZ).date():
        raise BankDerivationError("semantic fact period_end yayin anindan sonra")
    if isinstance(fact.version_sequence, bool) or not isinstance(fact.version_sequence, int) or fact.version_sequence < 0:
        raise BankDerivationError("semantic fact version_sequence gecersiz")
    if isinstance(fact.semantic_version, bool) or not isinstance(fact.semantic_version, int) or fact.semantic_version <= 0:
        raise BankDerivationError("semantic fact semantic_version gecersiz")
    if not isinstance(fact.value, Decimal) or not fact.value.is_finite() or abs(fact.value) > MAX_ABS_METRIC:
        raise BankDerivationError("semantic fact value sonlu Decimal sinirinda olmali")
    if fact.nature not in {"INSTANT", "YTD", "QUARTER", "TTM", "RATIO"}:
        raise BankDerivationError("semantic fact nature gecersiz")
    if not isinstance(fact.lineage_sha256, str) or not HEX64_RE.fullmatch(fact.lineage_sha256):
        raise BankDerivationError("semantic fact lineage_sha256 gecersiz")
    return fact


def _select_point_in_time(
    facts: Iterable[SemanticFinancialFact],
    *,
    config: BankDerivationConfig,
    ticker: str,
    analysis_at: datetime,
    anchor_period_end: date,
) -> dict[tuple[str, date], SemanticFinancialFact]:
    if not isinstance(analysis_at, datetime) or analysis_at.tzinfo is None or analysis_at.utcoffset() is None:
        raise BankDerivationError("analysis_at timezone iceren datetime olmali")
    if not isinstance(ticker, str) or not ticker.strip():
        raise BankDerivationError("ticker bos olmayan metin olmali")
    ticker_norm = ticker.strip().upper()
    allowed_periods = set(build_quarter_ends(anchor_period_end, config.history_periods))
    allowed_fields = set(config.required_fields)
    grouped: dict[tuple[str, date], list[SemanticFinancialFact]] = {}
    for raw_fact in facts:
        fact = _validate_semantic_fact(raw_fact)
        if fact.ticker != ticker_norm:
            continue
        if fact.semantic_profile != config.semantic_profile or fact.semantic_version != config.semantic_version:
            continue
        if fact.sector_family != "BANK":
            continue
        if fact.canonical_field not in allowed_fields or fact.period_end not in allowed_periods:
            continue
        if config.currency is not None and fact.currency != config.currency:
            continue
        if fact.published_at > analysis_at:
            continue
        grouped.setdefault((fact.canonical_field, fact.period_end), []).append(fact)

    selected: dict[tuple[str, date], SemanticFinancialFact] = {}
    for key, options in grouped.items():
        options.sort(
            key=lambda item: (
                item.published_at,
                item.version_sequence,
                item.lineage_sha256,
            ),
            reverse=True,
        )
        selected[key] = options[0]
    return selected


def _standalone_quarter_values(
    selected: Mapping[tuple[str, date], SemanticFinancialFact],
    *,
    field: str,
    slots: Sequence[date],
) -> dict[date, tuple[Optional[Decimal], tuple[SemanticFinancialFact, ...], Optional[str]]]:
    out: dict[date, tuple[Optional[Decimal], tuple[SemanticFinancialFact, ...], Optional[str]]] = {}
    for idx, period in enumerate(slots):
        fact = selected.get((field, period))
        if fact is None:
            out[period] = (None, (), "MISSING")
            continue
        if fact.nature == "QUARTER":
            out[period] = (fact.value, (fact,), None)
            continue
        if fact.nature == "YTD":
            if fact.period_start == _quarter_start(period):
                out[period] = (fact.value, (fact,), None)
                continue
            prev_period = slots[idx - 1] if idx > 0 else _shift_quarter_end(period, -1)
            prev = selected.get((field, prev_period))
            if (
                prev is not None
                and prev.nature == "YTD"
                and prev.period_start == fact.period_start
            ):
                out[period] = (fact.value - prev.value, (prev, fact), None)
            else:
                out[period] = (None, (fact,), "MISSING_PREVIOUS_YTD")
            continue
        out[period] = (None, (fact,), f"UNSUPPORTED_NATURE_{fact.nature}")
    return out


def _ttm_value(
    selected: Mapping[tuple[str, date], SemanticFinancialFact],
    standalone: Mapping[date, tuple[Optional[Decimal], tuple[SemanticFinancialFact, ...], Optional[str]]],
    *,
    field: str,
    slots: Sequence[date],
    index: int,
) -> tuple[Optional[Decimal], tuple[SemanticFinancialFact, ...], Optional[str]]:
    period = slots[index]
    direct = selected.get((field, period))
    if direct is not None and direct.nature == "TTM":
        return direct.value, (direct,), None
    if index < 3:
        return None, (), "INSUFFICIENT_QUARTERS"
    values: list[Decimal] = []
    lineage: dict[str, SemanticFinancialFact] = {}
    for slot in slots[index - 3:index + 1]:
        value, facts, reason = standalone[slot]
        if value is None:
            return None, tuple(lineage.values()), reason or "MISSING_QUARTER"
        values.append(value)
        for fact in facts:
            lineage[fact.lineage_sha256] = fact
    return sum(values, Decimal("0")), tuple(lineage.values()), None


def _float_or_none(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    if not value.is_finite() or abs(value) > MAX_ABS_METRIC:
        raise BankDerivationError("turetilmis metrik sayisal siniri asiyor")
    result = float(value)
    if not math.isfinite(result):
        raise BankDerivationError("turetilmis metrik float sinirini asiyor")
    return result


def _lineage_payload(facts: Iterable[SemanticFinancialFact]) -> tuple[Mapping[str, Any], ...]:
    unique = {fact.lineage_sha256: fact for fact in facts}
    return tuple(
        {
            "lineage_sha256": fact.lineage_sha256,
            "source": fact.source,
            "disclosure_id": fact.disclosure_id,
            "canonical_field": fact.canonical_field,
            "nature": fact.nature,
            "period_start": None if fact.period_start is None else fact.period_start.isoformat(),
            "period_end": fact.period_end.isoformat(),
            "published_at": fact.published_at.isoformat(),
            "version_tag": fact.version_tag,
            "version_sequence": fact.version_sequence,
            "value": str(fact.value),
        }
        for fact in sorted(
            unique.values(),
            key=lambda item: (
                item.canonical_field,
                item.period_end,
                item.published_at,
                item.lineage_sha256,
            ),
        )
    )


def _metric_hash(
    *,
    config: BankDerivationConfig,
    ticker: str,
    period_end: date,
    lineage: Sequence[Mapping[str, Any]],
) -> str:
    payload = {
        "derivation_profile": config.derivation_profile,
        "derivation_version": config.derivation_version,
        "ticker": ticker,
        "period_end": period_end.isoformat(),
        "lineage": [item["lineage_sha256"] for item in lineage],
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def derive_bank_metrics(
    facts: Iterable[SemanticFinancialFact],
    *,
    config: BankDerivationConfig,
    ticker: str,
    analysis_at: datetime,
    anchor_period_end: date,
) -> tuple[BankDerivedMetric, ...]:
    ticker_norm = ticker.strip().upper() if isinstance(ticker, str) else ticker
    selected = _select_point_in_time(
        facts,
        config=config,
        ticker=ticker,
        analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
    )
    history_slots = build_quarter_ends(anchor_period_end, config.history_periods)
    target_slots = history_slots[-config.target_periods:]
    slot_index = {period: idx for idx, period in enumerate(history_slots)}
    net_quarters = _standalone_quarter_values(
        selected, field=config.net_income_field, slots=history_slots
    )
    dividend_quarters = (
        _standalone_quarter_values(
            selected, field=config.dividends_paid_field, slots=history_slots
        )
        if config.dividends_paid_field
        else {}
    )

    metrics: list[BankDerivedMetric] = []
    for period in target_slots:
        idx = slot_index[period]
        used: dict[str, SemanticFinancialFact] = {}
        diagnostics: dict[str, Any] = {
            "roe_formula": config.roe_formula,
            "payout_policy": config.payout_policy,
        }

        equity = selected.get((config.total_equity_field, period))
        shares = (
            selected.get((config.shares_out_field, period))
            if config.shares_out_field
            else None
        )
        issued_capital = (
            selected.get((config.issued_capital_field, period))
            if config.issued_capital_field
            else None
        )
        lag4_period = _shift_quarter_end(period, -4)
        equity_lag4 = selected.get((config.total_equity_field, lag4_period))
        for fact in (equity, shares, issued_capital, equity_lag4):
            if fact is not None:
                used[fact.lineage_sha256] = fact

        shares_value: Optional[Decimal] = None
        shares_source: Optional[str] = None
        shares_reason: Optional[str] = None
        if shares is not None:
            if shares.value <= 0:
                shares_reason = "NONPOSITIVE_SHARES"
            else:
                shares_value = shares.value
                shares_source = "DIRECT_SHARES"
        elif issued_capital is not None:
            if issued_capital.value <= 0:
                shares_reason = "NONPOSITIVE_ISSUED_CAPITAL"
            elif config.share_nominal_value is None:
                raise BankDerivationError(
                    "issued capital fallback icin share_nominal_value eksik"
                )
            else:
                shares_value = issued_capital.value / config.share_nominal_value
                if shares_value <= 0:
                    shares_reason = "NONPOSITIVE_DERIVED_SHARES"
                    shares_value = None
                else:
                    shares_source = "ISSUED_CAPITAL_DIV_NOMINAL"
        else:
            shares_reason = "MISSING_SHARES_AND_ISSUED_CAPITAL"
        diagnostics["shares_source"] = shares_source
        diagnostics["shares_reason"] = shares_reason
        diagnostics["share_nominal_value"] = (
            None if config.share_nominal_value is None else str(config.share_nominal_value)
        )

        bvps_dec: Optional[Decimal] = None
        if equity is None:
            diagnostics["bvps_reason"] = "MISSING_EQUITY"
        elif shares_value is None:
            diagnostics["bvps_reason"] = shares_reason or "MISSING_SHARES"
        elif equity.value <= 0:
            diagnostics["bvps_reason"] = "NONPOSITIVE_EQUITY"
        else:
            bvps_dec = equity.value / shares_value
            diagnostics["bvps_reason"] = None

        ttm_income, income_facts, income_reason = _ttm_value(
            selected,
            net_quarters,
            field=config.net_income_field,
            slots=history_slots,
            index=idx,
        )
        for fact in income_facts:
            used[fact.lineage_sha256] = fact
        roe_dec: Optional[Decimal] = None
        if ttm_income is None:
            diagnostics["roe_reason"] = income_reason
        elif equity is None:
            diagnostics["roe_reason"] = "MISSING_EQUITY"
        elif equity_lag4 is None:
            diagnostics["roe_reason"] = "MISSING_LAG4_EQUITY"
        else:
            average_equity = (equity.value + equity_lag4.value) / Decimal("2")
            diagnostics["average_equity"] = str(average_equity)
            diagnostics["ttm_net_income"] = str(ttm_income)
            if average_equity <= 0:
                diagnostics["roe_reason"] = "NONPOSITIVE_AVERAGE_EQUITY"
            else:
                roe_dec = ttm_income / average_equity
                diagnostics["roe_reason"] = None

        payout_dec: Optional[Decimal] = None
        payout_reason: Optional[str] = None
        payout_source: Optional[str] = None
        direct_payout_invalid = False
        if config.payout_ratio_field:
            payout_fact = selected.get((config.payout_ratio_field, period))
            if payout_fact is not None:
                used[payout_fact.lineage_sha256] = payout_fact
                if payout_fact.nature != "RATIO":
                    payout_reason = "DIRECT_PAYOUT_NOT_RATIO"
                    direct_payout_invalid = True
                elif Decimal("0") <= payout_fact.value <= Decimal("1"):
                    payout_dec = payout_fact.value
                    payout_source = "DIRECT_RATIO"
                else:
                    payout_reason = "DIRECT_PAYOUT_OUT_OF_RANGE"
                    direct_payout_invalid = True
        if payout_dec is None and not direct_payout_invalid and config.dividends_paid_field:
            ttm_dividends, dividend_facts, dividend_reason = _ttm_value(
                selected,
                dividend_quarters,
                field=config.dividends_paid_field,
                slots=history_slots,
                index=idx,
            )
            for fact in dividend_facts:
                used[fact.lineage_sha256] = fact
            if ttm_dividends is None:
                payout_reason = payout_reason or dividend_reason
            elif ttm_income is None or ttm_income <= 0:
                payout_reason = payout_reason or "NONPOSITIVE_TTM_INCOME"
            else:
                candidate = abs(ttm_dividends) / ttm_income
                if Decimal("0") <= candidate <= Decimal("1"):
                    payout_dec = candidate
                    payout_source = "TTM_DIVIDENDS"
                    payout_reason = None
                else:
                    payout_reason = "DERIVED_PAYOUT_OUT_OF_RANGE"
        diagnostics["payout_source"] = payout_source
        diagnostics["payout_reason"] = payout_reason

        if not used:
            continue
        lineage = _lineage_payload(used.values())
        metric_hash = _metric_hash(
            config=config, ticker=ticker_norm, period_end=period, lineage=lineage
        )
        publication = max(fact.published_at for fact in used.values())
        if publication > analysis_at:
            raise BankDerivationError("turetilmis metric gelecekteki fact kullandi")
        version_sequence = max(fact.version_sequence for fact in used.values())
        diagnostics["lineage_count"] = len(lineage)
        diagnostics["analysis_at"] = analysis_at.isoformat()
        metrics.append(
            BankDerivedMetric(
                ticker=ticker_norm,
                period_end=period,
                version_tag=f"DERIVED_{metric_hash[:12].upper()}",
                version_sequence=version_sequence,
                published_at=publication,
                source_disclosure_id=f"SEMANTIC:{metric_hash}",
                roe_ttm=_float_or_none(roe_dec),
                bvps=_float_or_none(bvps_dec),
                payout_sus=_float_or_none(payout_dec),
                lineage_sha256=metric_hash,
                source_lineage=lineage,
                derivation_profile=config.derivation_profile,
                derivation_version=config.derivation_version,
                diagnostics=diagnostics,
            )
        )
    return tuple(metrics)


BANK_METRIC_INSERT_SQL = """
INSERT INTO core.bank_metrics_quarterly (
  ticker, period_end, version_tag, version_sequence,
  published_at, source_disclosure_id, roe_ttm, bvps, payout_sus,
  lineage_sha256, source_lineage, derivation_profile,
  derivation_version, derivation_diagnostics
) VALUES (
  %s, %s, %s, %s,
  %s, %s, %s, %s, %s,
  %s, %s::jsonb, %s,
  %s, %s::jsonb
)
ON CONFLICT (source_disclosure_id)
  WHERE source_disclosure_id IS NOT NULL
DO UPDATE SET
  inserted_at = GREATEST(
    core.bank_metrics_quarterly.inserted_at,
    EXCLUDED.inserted_at
  )
"""


def _persist_bank_metric_cursor(cur: Any, row: BankDerivedMetric) -> None:
    cur.execute(
        BANK_METRIC_INSERT_SQL,
        (
            row.ticker,
            row.period_end,
            row.version_tag,
            row.version_sequence,
            row.published_at,
            row.source_disclosure_id,
            row.roe_ttm,
            row.bvps,
            row.payout_sus,
            row.lineage_sha256,
            json.dumps(row.source_lineage, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            row.derivation_profile,
            row.derivation_version,
            json.dumps(row.diagnostics, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ),
    )


def persist_bank_derived_metrics(conn: Any, metrics: Iterable[BankDerivedMetric]) -> int:
    rows = tuple(metrics)
    if not rows:
        return 0
    with conn:
        with conn.cursor() as cur:
            for row in rows:
                _persist_bank_metric_cursor(cur, row)
    return len(rows)


def _semantic_fact_from_db_row(row: Mapping[str, Any]) -> SemanticFinancialFact:
    dimensions = row["dimensions"]
    if isinstance(dimensions, str):
        try:
            dimensions = json.loads(dimensions)
        except json.JSONDecodeError as exc:
            raise BankDerivationError("semantic dimensions gecersiz JSON") from exc
    if not isinstance(dimensions, Mapping):
        raise BankDerivationError("semantic dimensions nesne olmali")
    return SemanticFinancialFact(
        source=row["source"],
        disclosure_id=row["disclosure_id"],
        ticker=row["ticker"],
        published_at=row["published_at"],
        version_tag=row["version_tag"],
        version_sequence=int(row["version_sequence"]),
        sector_family=row["sector_family"],
        semantic_profile=row["semantic_profile"],
        semantic_version=int(row["semantic_version"]),
        canonical_field=row["canonical_field"],
        nature=row["nature"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        currency=row["currency"],
        statement_scope=row["statement_scope"],
        value=Decimal(str(row["value"])),
        source_fact_code=row["source_fact_code"],
        source_fact_key=row["source_fact_key"],
        source_mapping_profile=row["source_mapping_profile"],
        source_mapping_version=int(row["source_mapping_version"]),
        dimensions=dict(dimensions),
        lineage_sha256=row["lineage_sha256"],
        mapped_at=row["mapped_at"],
    )


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        if not isinstance(raw, str) or not raw.strip():
            raise BankDerivationError("ticker degerleri dolu metin olmali")
        ticker = raw.strip().upper()
        if ticker not in seen:
            result.append(ticker)
            seen.add(ticker)
    return result


def fetch_semantic_facts_batch_asof(
    conn: Any,
    *,
    config: BankDerivationConfig,
    tickers: Iterable[str],
    analysis_at: datetime,
    anchor_period_end: date,
) -> dict[str, tuple[SemanticFinancialFact, ...]]:
    if not isinstance(analysis_at, datetime) or analysis_at.tzinfo is None or analysis_at.utcoffset() is None:
        raise BankDerivationError("analysis_at timezone iceren datetime olmali")
    ticker_list = _normalize_tickers(tickers)
    if not ticker_list:
        return {}
    periods = build_quarter_ends(anchor_period_end, config.history_periods)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              source, disclosure_id, ticker, published_at, version_tag,
              version_sequence, sector_family, semantic_profile, semantic_version,
              canonical_field, nature, period_start, period_end, currency,
              statement_scope, value, source_fact_code, source_fact_key,
              source_mapping_profile, source_mapping_version, dimensions,
              lineage_sha256, mapped_at
            FROM core.semantic_financial_facts
            WHERE ticker = ANY(%s::text[])
              AND sector_family = 'BANK'
              AND semantic_profile = %s
              AND semantic_version = %s
              AND canonical_field = ANY(%s::text[])
              AND period_end BETWEEN %s AND %s
              AND published_at <= %s
            ORDER BY ticker, canonical_field, period_end, published_at,
                     version_sequence, lineage_sha256
            """,
            (
                ticker_list,
                config.semantic_profile,
                config.semantic_version,
                list(config.required_fields),
                periods[0],
                periods[-1],
                analysis_at,
            ),
        )
        names = [desc[0] for desc in cur.description]
        rows = [dict(zip(names, row)) for row in cur.fetchall()]
    grouped: dict[str, list[SemanticFinancialFact]] = {ticker: [] for ticker in ticker_list}
    for row in rows:
        ticker = str(row["ticker"]).strip().upper()
        if ticker not in grouped:
            raise BankDerivationError(f"semantic batch beklenmeyen ticker dondurdu: {ticker}")
        grouped[ticker].append(_semantic_fact_from_db_row(row))
    return {ticker: tuple(values) for ticker, values in grouped.items()}


def fetch_semantic_facts_asof(
    conn: Any,
    *,
    config: BankDerivationConfig,
    ticker: str,
    analysis_at: datetime,
    anchor_period_end: date,
) -> tuple[SemanticFinancialFact, ...]:
    ticker_norm = _normalize_tickers([ticker])[0]
    return fetch_semantic_facts_batch_asof(
        conn,
        config=config,
        tickers=[ticker_norm],
        analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
    )[ticker_norm]

def materialize_bank_metrics_asof(
    conn: Any,
    *,
    config: BankDerivationConfig,
    ticker: str,
    analysis_at: datetime,
    anchor_period_end: date,
    persist: bool = True,
) -> tuple[BankDerivedMetric, ...]:
    if type(persist) is not bool:
        raise BankDerivationError("persist Python bool olmali")
    facts = fetch_semantic_facts_asof(
        conn,
        config=config,
        ticker=ticker,
        analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
    )
    metrics = derive_bank_metrics(
        facts,
        config=config,
        ticker=ticker,
        analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
    )
    if persist:
        persist_bank_derived_metrics(conn, metrics)
    return metrics

@dataclass(frozen=True)
class BankMaterializationBatchReport:
    tickers_seen: int
    tickers_materialized: int
    tickers_rejected: int
    metrics_written: int
    rejected: Mapping[str, str]


def materialize_bank_metrics_batch(
    conn: Any,
    *,
    config: BankDerivationConfig,
    tickers: Iterable[str],
    analysis_at: datetime,
    anchor_period_end: date,
    persist: bool = True,
) -> BankMaterializationBatchReport:
    if type(persist) is not bool:
        raise BankDerivationError("persist Python bool olmali")
    normalized = _normalize_tickers(tickers)
    facts_by_ticker = fetch_semantic_facts_batch_asof(
        conn,
        config=config,
        tickers=normalized,
        analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
    )

    materialized = 0
    metrics_written = 0
    rejected: dict[str, str] = {}
    for ticker in normalized:
        try:
            metrics = derive_bank_metrics(
                facts_by_ticker.get(ticker, ()),
                config=config,
                ticker=ticker,
                analysis_at=analysis_at,
                anchor_period_end=anchor_period_end,
            )
            if not metrics:
                raise BankDerivationError("NO_DERIVABLE_METRICS")
            if persist:
                # Metric rows and stale rejection cleanup are one atomic unit.
                with conn:
                    with conn.cursor() as cur:
                        for row in metrics:
                            _persist_bank_metric_cursor(cur, row)
                        cur.execute(
                            """
                            DELETE FROM core.bank_metric_derivation_rejections
                            WHERE ticker = %s AND analysis_at = %s
                              AND anchor_period_end = %s
                              AND derivation_profile = %s
                              AND derivation_version = %s
                            """,
                            (
                                ticker, analysis_at, anchor_period_end,
                                config.derivation_profile, config.derivation_version,
                            ),
                        )
            materialized += 1
            metrics_written += len(metrics)
        except (BankDerivationError, KapApiProtocolError, ValueError, ArithmeticError) as exc:
            reason = str(exc) or type(exc).__name__
            rejected[ticker] = reason
            if persist:
                recorded_at = datetime.now(timezone.utc)
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO core.bank_metric_derivation_rejections (
                              ticker, analysis_at, anchor_period_end,
                              derivation_profile, derivation_version, reason,
                              first_rejected_at, last_rejected_at, attempts
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                            ON CONFLICT (
                              ticker, analysis_at, anchor_period_end,
                              derivation_profile, derivation_version
                            ) DO UPDATE SET
                              reason = EXCLUDED.reason,
                              last_rejected_at = EXCLUDED.last_rejected_at,
                              attempts = core.bank_metric_derivation_rejections.attempts + 1
                            """,
                            (
                                ticker, analysis_at, anchor_period_end,
                                config.derivation_profile, config.derivation_version,
                                reason, recorded_at, recorded_at,
                            ),
                        )
    return BankMaterializationBatchReport(
        tickers_seen=len(normalized),
        tickers_materialized=materialized,
        tickers_rejected=len(rejected),
        metrics_written=metrics_written,
        rejected=dict(rejected),
    )

