from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from src.ingest.api.mkk_kap import KapApiConfigError
from src.ingest.api.semantic_facts import SemanticFinancialFact
from src.ingest.sector_routing import SUPPORTED_SECTOR_FAMILIES

MAX_DB_INT = 2_147_483_647
MAX_TARGET_PERIODS = 40
MAX_HISTORY_PERIODS = 80
MAX_ABS_VALUE = Decimal("1e100")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

NONBANK_FAMILIES = frozenset(SUPPORTED_SECTOR_FAMILIES - {"BANK"})
SUPPORTED_CORE_FIELDS = frozenset({
    "revenue", "cogs", "gross_profit", "ebit", "net_income", "interest_exp",
    "total_assets", "total_equity", "current_assets", "current_liabilities",
    "cash_and_eq", "st_investments", "receivables", "inventory",
    "debt_st", "debt_lt", "cfo", "capex", "shares_out", "shares_diluted",
})
FLOW_CORE_FIELDS = frozenset({
    "revenue", "cogs", "gross_profit", "ebit", "net_income", "interest_exp",
    "cfo", "capex",
})


class CompanyDerivationError(ValueError):
    pass


def _strict_text(name: str, value: Any, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KapApiConfigError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _strict_positive_int(name: str, value: Any, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise KapApiConfigError(f"{name} pozitif Python int olmali")
    if value > maximum:
        raise KapApiConfigError(f"{name} desteklenen siniri asiyor")
    return value


def _finite_decimal(name: str, value: Any, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, (list, tuple, dict, set)):
        raise KapApiConfigError(f"{name} sonlu sayi olmali")
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise KapApiConfigError(f"{name} sonlu sayi olmali") from exc
    if not number.is_finite() or abs(number) > MAX_ABS_VALUE:
        raise KapApiConfigError(f"{name} sonlu sayi sinirinda olmali")
    if positive and number <= 0:
        raise KapApiConfigError(f"{name} pozitif olmali")
    return number


def _quarter_end(value: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise CompanyDerivationError("period_end date olmali")
    quarter = (value.month - 1) // 3
    next_month = quarter * 3 + 4
    year = value.year
    if next_month > 12:
        next_month -= 12
        year += 1
    return date(year, next_month, 1) - timedelta(days=1)


def _shift_quarter_end(value: date, offset: int) -> date:
    anchor = _quarter_end(value)
    q_index = anchor.year * 4 + ((anchor.month - 1) // 3) + offset
    year, quarter = divmod(q_index, 4)
    month = quarter * 3 + 3
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def build_quarter_ends(anchor_period_end: date, count: int) -> tuple[date, ...]:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise CompanyDerivationError("count pozitif Python int olmali")
    anchor = _quarter_end(anchor_period_end)
    return tuple(_shift_quarter_end(anchor, i) for i in range(-(count - 1), 1))


@dataclass(frozen=True)
class CompanyDerivationConfig:
    derivation_profile: str
    derivation_version: int
    semantic_profile: str
    semantic_version: int
    sector_families: tuple[str, ...]
    field_map: Mapping[str, str]
    required_fields: tuple[str, ...]
    minimum_present_fields: tuple[str, ...]
    minimum_present_count: int
    target_periods: int = 8
    history_periods: int = 12
    currency: Optional[str] = "TRY"
    shares_out_field: Optional[str] = None
    issued_capital_field: Optional[str] = None
    share_nominal_value: Optional[Decimal] = None
    derive_gross_profit: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CompanyDerivationConfig":
        if not isinstance(data, Mapping):
            raise KapApiConfigError("company derivation config nesne olmali")
        allowed = {
            "derivation_profile", "derivation_version", "semantic_profile",
            "semantic_version", "sector_families", "field_map", "required_fields",
            "minimum_present_fields", "minimum_present_count", "target_periods",
            "history_periods", "currency", "shares_out_field",
            "issued_capital_field", "share_nominal_value", "derive_gross_profit",
        }
        unknown = set(data) - allowed
        if unknown:
            raise KapApiConfigError(
                "company derivation bilinmeyen alanlar: "
                + ", ".join(sorted(str(key) for key in unknown))
            )
        profile = _strict_text("derivation_profile", data.get("derivation_profile"))
        version = _strict_positive_int(
            "derivation_version", data.get("derivation_version"), maximum=MAX_DB_INT
        )
        semantic_profile = _strict_text("semantic_profile", data.get("semantic_profile"))
        semantic_version = _strict_positive_int(
            "semantic_version", data.get("semantic_version"), maximum=MAX_DB_INT
        )

        families_raw = data.get("sector_families")
        if not isinstance(families_raw, list) or not families_raw:
            raise KapApiConfigError("sector_families dolu liste olmali")
        families = tuple(_strict_text("sector_families", item, uppercase=True) for item in families_raw)
        if len(set(families)) != len(families):
            raise KapApiConfigError("sector_families yinelenemez")
        invalid_families = set(families) - NONBANK_FAMILIES
        if invalid_families:
            raise KapApiConfigError(
                "company derivation BANK veya bilinmeyen aile kabul etmez: "
                + ", ".join(sorted(invalid_families))
            )

        raw_map = data.get("field_map")
        if not isinstance(raw_map, Mapping) or not raw_map:
            raise KapApiConfigError("field_map dolu nesne olmali")
        field_map: dict[str, str] = {}
        for core_name, semantic_name in raw_map.items():
            core = _strict_text("field_map anahtari", core_name).lower()
            if core not in SUPPORTED_CORE_FIELDS or core in {"shares_out", "shares_diluted"}:
                raise KapApiConfigError(f"field_map desteklenmeyen core alan: {core}")
            field_map[core] = _strict_text(f"field_map.{core}", semantic_name, uppercase=True)
        if len(set(field_map.values())) != len(field_map):
            raise KapApiConfigError("field_map semantic alanlari yinelenemez")

        def parse_core_list(name: str) -> tuple[str, ...]:
            raw = data.get(name, [])
            if not isinstance(raw, list):
                raise KapApiConfigError(f"{name} liste olmali")
            values = tuple(_strict_text(name, item).lower() for item in raw)
            if len(set(values)) != len(values):
                raise KapApiConfigError(f"{name} yinelenemez")
            unknown_fields = set(values) - (set(field_map) | {"shares_out", "gross_profit"})
            if unknown_fields:
                raise KapApiConfigError(
                    f"{name} field_map disinda alan iceriyor: {sorted(unknown_fields)}"
                )
            return values

        required_fields = parse_core_list("required_fields")
        minimum_fields = parse_core_list("minimum_present_fields")
        minimum_count = _strict_positive_int(
            "minimum_present_count",
            data.get("minimum_present_count", 1),
            maximum=max(len(minimum_fields), 1),
        )
        if not minimum_fields:
            raise KapApiConfigError("minimum_present_fields bos olamaz")
        if minimum_count > len(minimum_fields):
            raise KapApiConfigError("minimum_present_count alan sayisini asamaz")

        target = _strict_positive_int(
            "target_periods", data.get("target_periods", 8), maximum=MAX_TARGET_PERIODS
        )
        history = _strict_positive_int(
            "history_periods", data.get("history_periods", 12), maximum=MAX_HISTORY_PERIODS
        )
        if history < target + 1:
            raise KapApiConfigError("history_periods YTD farki icin target_periods + 1 olmali")

        shares_out_field = data.get("shares_out_field")
        issued_capital_field = data.get("issued_capital_field")
        shares_out_field = None if shares_out_field is None else _strict_text(
            "shares_out_field", shares_out_field, uppercase=True
        )
        issued_capital_field = None if issued_capital_field is None else _strict_text(
            "issued_capital_field", issued_capital_field, uppercase=True
        )
        if shares_out_field is None and issued_capital_field is None:
            raise KapApiConfigError("shares_out_field veya issued_capital_field zorunlu")
        nominal = data.get("share_nominal_value")
        nominal_value = None
        if nominal is not None:
            nominal_value = _finite_decimal("share_nominal_value", nominal, positive=True)
        if issued_capital_field is not None and nominal_value is None:
            raise KapApiConfigError("issued_capital_field icin share_nominal_value zorunlu")
        if issued_capital_field is None and nominal_value is not None:
            raise KapApiConfigError("share_nominal_value yalniz issued_capital_field ile kullanilabilir")
        if shares_out_field and issued_capital_field and shares_out_field == issued_capital_field:
            raise KapApiConfigError("shares_out ve issued_capital semantic alanlari farkli olmali")
        reserved_semantic = {value for value in (shares_out_field, issued_capital_field) if value}
        overlap = reserved_semantic & set(field_map.values())
        if overlap:
            raise KapApiConfigError(
                "pay/senet semantic alanlari field_map ile cakismamali: "
                + ", ".join(sorted(overlap))
            )

        derive_gp = data.get("derive_gross_profit", True)
        if type(derive_gp) is not bool:
            raise KapApiConfigError("derive_gross_profit Python bool olmali")
        if ("gross_profit" in required_fields or "gross_profit" in minimum_fields):
            if "gross_profit" not in field_map and not derive_gp:
                raise KapApiConfigError(
                    "gross_profit gerekli ise field_map veya derive_gross_profit zorunlu"
                )
        currency = data.get("currency", "TRY")
        if currency is not None:
            currency = _strict_text("currency", currency, uppercase=True)

        return cls(
            derivation_profile=profile,
            derivation_version=version,
            semantic_profile=semantic_profile,
            semantic_version=semantic_version,
            sector_families=families,
            field_map=field_map,
            required_fields=required_fields,
            minimum_present_fields=minimum_fields,
            minimum_present_count=minimum_count,
            target_periods=target,
            history_periods=history,
            currency=currency,
            shares_out_field=shares_out_field,
            issued_capital_field=issued_capital_field,
            share_nominal_value=nominal_value,
            derive_gross_profit=derive_gp,
        )

    @classmethod
    def from_json_file(cls, path: str) -> "CompanyDerivationConfig":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    @property
    def required_semantic_fields(self) -> tuple[str, ...]:
        values = list(self.field_map.values())
        if self.shares_out_field:
            values.append(self.shares_out_field)
        if self.issued_capital_field:
            values.append(self.issued_capital_field)
        return tuple(dict.fromkeys(values))


def _ensure_runtime_config(config: Any) -> CompanyDerivationConfig:
    """Reject dataclass construction that bypassed ``from_dict`` validation."""
    if not isinstance(config, CompanyDerivationConfig):
        raise CompanyDerivationError("config CompanyDerivationConfig olmali")
    try:
        normalized = CompanyDerivationConfig.from_dict({
            "derivation_profile": config.derivation_profile,
            "derivation_version": config.derivation_version,
            "semantic_profile": config.semantic_profile,
            "semantic_version": config.semantic_version,
            "sector_families": list(config.sector_families),
            "field_map": dict(config.field_map),
            "required_fields": list(config.required_fields),
            "minimum_present_fields": list(config.minimum_present_fields),
            "minimum_present_count": config.minimum_present_count,
            "target_periods": config.target_periods,
            "history_periods": config.history_periods,
            "currency": config.currency,
            "shares_out_field": config.shares_out_field,
            "issued_capital_field": config.issued_capital_field,
            "share_nominal_value": (
                None if config.share_nominal_value is None
                else str(config.share_nominal_value)
            ),
            "derive_gross_profit": config.derive_gross_profit,
        })
    except (KapApiConfigError, TypeError, ValueError, OverflowError) as exc:
        raise CompanyDerivationError(f"config gecersiz: {exc}") from exc
    if normalized != config:
        raise CompanyDerivationError("config kanonik CompanyDerivationConfig degil")
    return config


@dataclass(frozen=True)
class CompanyDerivedQuarter:
    ticker: str
    sector_family: str
    period_end: date
    version_tag: str
    version_sequence: int
    published_at: datetime
    source_disclosure_id: str
    values: Mapping[str, Optional[float]]
    lineage_sha256: str
    source_lineage: tuple[Mapping[str, Any], ...]
    derivation_profile: str
    derivation_version: int
    is_complete: bool
    diagnostics: Mapping[str, Any]


def _validate_fact(fact: Any, config: CompanyDerivationConfig, analysis_at: datetime) -> SemanticFinancialFact:
    if not isinstance(fact, SemanticFinancialFact):
        raise CompanyDerivationError("facts yalniz SemanticFinancialFact icermeli")
    if not isinstance(fact.published_at, datetime) or fact.published_at.tzinfo is None or fact.published_at.utcoffset() is None:
        raise CompanyDerivationError("semantic published_at timezone icermeli")
    if fact.semantic_profile != config.semantic_profile or fact.semantic_version != config.semantic_version:
        raise CompanyDerivationError("semantic profil/surum derivation config ile uyusmuyor")
    if fact.sector_family not in config.sector_families:
        raise CompanyDerivationError("semantic sector_family derivation config disinda")
    if config.currency is not None and fact.currency not in {None, config.currency}:
        raise CompanyDerivationError("semantic para birimi derivation config ile uyusmuyor")
    if not isinstance(fact.value, Decimal) or not fact.value.is_finite() or abs(fact.value) > MAX_ABS_VALUE:
        raise CompanyDerivationError("semantic value sonlu Decimal sinirinda olmali")
    if not isinstance(fact.period_end, date) or isinstance(fact.period_end, datetime):
        raise CompanyDerivationError("semantic period_end date olmali")
    if not isinstance(fact.lineage_sha256, str) or not HEX64_RE.fullmatch(fact.lineage_sha256):
        raise CompanyDerivationError("semantic lineage_sha256 gecersiz")
    if isinstance(fact.version_sequence, bool) or not isinstance(fact.version_sequence, int) or fact.version_sequence < 0:
        raise CompanyDerivationError("semantic version_sequence gecersiz")
    return fact


def _candidate_key(fact: SemanticFinancialFact) -> tuple[Any, ...]:
    nature_priority = {"QUARTER": 3, "YTD": 2, "INSTANT": 2, "TTM": 1, "RATIO": 1}
    return (
        fact.published_at,
        fact.version_sequence,
        nature_priority.get(fact.nature, 0),
        fact.disclosure_id,
        fact.lineage_sha256,
    )


def _select_fact(
    candidates: Sequence[SemanticFinancialFact],
    *,
    preferred_disclosure_id: Optional[str] = None,
    nature: Optional[str] = None,
) -> Optional[SemanticFinancialFact]:
    rows = [row for row in candidates if nature is None or row.nature == nature]
    if preferred_disclosure_id is not None:
        preferred = [row for row in rows if row.disclosure_id == preferred_disclosure_id]
        if preferred:
            rows = preferred
    if not rows:
        return None
    return max(rows, key=_candidate_key)


def _lineage_item(fact: SemanticFinancialFact) -> Mapping[str, Any]:
    return {
        "source": fact.source,
        "disclosure_id": fact.disclosure_id,
        "published_at": fact.published_at.isoformat(),
        "version_tag": fact.version_tag,
        "version_sequence": fact.version_sequence,
        "canonical_field": fact.canonical_field,
        "nature": fact.nature,
        "period_start": None if fact.period_start is None else fact.period_start.isoformat(),
        "period_end": fact.period_end.isoformat(),
        "lineage_sha256": fact.lineage_sha256,
    }


def _quarter_flow_value(
    current: SemanticFinancialFact,
    *,
    candidates_by_field_period: Mapping[tuple[str, date], Sequence[SemanticFinancialFact]],
) -> tuple[Optional[Decimal], tuple[SemanticFinancialFact, ...], str]:
    if current.nature == "QUARTER":
        return current.value, (current,), "DIRECT_QUARTER"
    if current.nature != "YTD":
        return None, (current,), f"UNSUPPORTED_FLOW_NATURE_{current.nature}"
    if current.period_start is None:
        return None, (current,), "YTD_PERIOD_START_MISSING"
    if current.period_end.month == 3:
        return current.value, (current,), "YTD_Q1_DIRECT"
    prior_end = _shift_quarter_end(current.period_end, -1)
    prior_candidates = candidates_by_field_period.get((current.canonical_field, prior_end), ())
    prior = _select_fact(
        prior_candidates,
        preferred_disclosure_id=current.disclosure_id,
        nature="YTD",
    )
    if prior is None:
        prior = _select_fact(prior_candidates, nature="YTD")
    if prior is None:
        return None, (current,), "YTD_PRIOR_QUARTER_MISSING"
    if prior.period_start != current.period_start:
        return None, (current, prior), "YTD_PERIOD_START_MISMATCH"
    value = current.value - prior.value
    if not value.is_finite() or abs(value) > MAX_ABS_VALUE:
        return None, (current, prior), "YTD_DIFFERENCE_INVALID"
    return value, (current, prior), "YTD_DIFFERENCE"


def _as_float(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    result = float(value)
    if result == float("inf") or result == float("-inf") or result != result:
        raise CompanyDerivationError("derived value float sinirinda degil")
    return result


def derive_company_quarters(
    facts: Iterable[SemanticFinancialFact],
    *,
    config: CompanyDerivationConfig,
    ticker: str,
    analysis_at: datetime,
    anchor_period_end: date,
) -> tuple[CompanyDerivedQuarter, ...]:
    config = _ensure_runtime_config(config)
    if not isinstance(ticker, str) or not ticker.strip():
        raise CompanyDerivationError("ticker dolu metin olmali")
    ticker_norm = ticker.strip().upper()
    if not isinstance(analysis_at, datetime) or analysis_at.tzinfo is None or analysis_at.utcoffset() is None:
        raise CompanyDerivationError("analysis_at timezone iceren datetime olmali")
    try:
        raw_rows = tuple(facts)
    except TypeError as exc:
        raise CompanyDerivationError("facts iterable olmali") from exc
    known_rows: list[SemanticFinancialFact] = []
    for raw in raw_rows:
        if not isinstance(raw, SemanticFinancialFact):
            raise CompanyDerivationError("facts yalniz SemanticFinancialFact icermeli")
        if (
            not isinstance(raw.published_at, datetime)
            or raw.published_at.tzinfo is None
            or raw.published_at.utcoffset() is None
        ):
            raise CompanyDerivationError("semantic published_at timezone icermeli")
        if raw.published_at > analysis_at:
            continue
        known_rows.append(_validate_fact(raw, config, analysis_at))
    rows = tuple(known_rows)
    if any(row.ticker.strip().upper() != ticker_norm for row in rows):
        raise CompanyDerivationError("facts tek ticker icermeli")
    families = {row.sector_family for row in rows}
    if len(families) > 1:
        raise CompanyDerivationError("facts tek sector_family icermeli")

    history_periods = build_quarter_ends(anchor_period_end, config.history_periods)
    target_periods = set(build_quarter_ends(anchor_period_end, config.target_periods))
    allowed_periods = set(history_periods)
    rows = tuple(
        row for row in rows
        if row.period_end in allowed_periods
        and row.canonical_field in config.required_semantic_fields
    )

    by_field_period: dict[tuple[str, date], list[SemanticFinancialFact]] = {}
    for row in rows:
        by_field_period.setdefault((row.canonical_field, row.period_end), []).append(row)

    output: list[CompanyDerivedQuarter] = []
    for period in history_periods:
        if period not in target_periods:
            continue
        values: dict[str, Optional[Decimal]] = {name: None for name in SUPPORTED_CORE_FIELDS}
        used: list[SemanticFinancialFact] = []
        field_sources: dict[str, str] = {}
        field_reasons: dict[str, str] = {}

        for core_field, canonical_field in config.field_map.items():
            candidates = by_field_period.get((canonical_field, period), ())
            selected = _select_fact(candidates)
            if selected is None:
                field_reasons[core_field] = "MISSING"
                continue
            if core_field in FLOW_CORE_FIELDS:
                value, lineage_rows, reason = _quarter_flow_value(
                    selected, candidates_by_field_period=by_field_period
                )
                values[core_field] = value
                used.extend(lineage_rows)
                field_sources[core_field] = reason
                if value is None:
                    field_reasons[core_field] = reason
            else:
                if selected.nature != "INSTANT":
                    field_reasons[core_field] = f"EXPECTED_INSTANT_GOT_{selected.nature}"
                    continue
                values[core_field] = selected.value
                used.append(selected)
                field_sources[core_field] = "DIRECT_INSTANT"

        direct_shares = None
        direct_fact = None
        if config.shares_out_field:
            direct_fact = _select_fact(
                by_field_period.get((config.shares_out_field, period), ())
            )
            if direct_fact is not None:
                used.append(direct_fact)
                if direct_fact.nature != "INSTANT":
                    field_reasons["shares_out"] = f"EXPECTED_INSTANT_GOT_{direct_fact.nature}"
                elif direct_fact.value <= 0:
                    # Invalid direct data must not be hidden by issued-capital fallback.
                    field_reasons["shares_out"] = "DIRECT_SHARES_NON_POSITIVE"
                else:
                    direct_shares = direct_fact.value
                    field_sources["shares_out"] = "DIRECT_SHARES"
        if direct_fact is None and config.issued_capital_field:
            capital = _select_fact(
                by_field_period.get((config.issued_capital_field, period), ())
            )
            if capital is not None:
                used.append(capital)
                if capital.nature != "INSTANT":
                    field_reasons["shares_out"] = f"EXPECTED_INSTANT_GOT_{capital.nature}"
                elif capital.value <= 0:
                    field_reasons["shares_out"] = "ISSUED_CAPITAL_NON_POSITIVE"
                else:
                    assert config.share_nominal_value is not None
                    direct_shares = capital.value / config.share_nominal_value
                    field_sources["shares_out"] = "ISSUED_CAPITAL_OVER_NOMINAL"
        values["shares_out"] = direct_shares

        if (
            config.derive_gross_profit
            and values["gross_profit"] is None
            and values["revenue"] is not None
            and values["cogs"] is not None
        ):
            values["gross_profit"] = values["revenue"] - values["cogs"]
            field_sources["gross_profit"] = "REVENUE_MINUS_COGS"

        present_count = sum(
            1 for field in config.minimum_present_fields if values.get(field) is not None
        )
        if present_count < config.minimum_present_count:
            continue
        if not used:
            continue

        unique_used = {
            (row.disclosure_id, row.lineage_sha256): row
            for row in used
        }
        used_rows = tuple(
            sorted(unique_used.values(), key=lambda row: (
                row.published_at, row.version_sequence, row.disclosure_id,
                row.canonical_field, row.lineage_sha256,
            ))
        )
        publication = max(row.published_at for row in used_rows)
        version_sequence = max(row.version_sequence for row in used_rows)
        lineage = tuple(_lineage_item(row) for row in used_rows)
        float_values = {name: _as_float(value) for name, value in values.items()}
        missing_required = tuple(
            field for field in config.required_fields if float_values.get(field) is None
        )
        payload = {
            "ticker": ticker_norm,
            "period_end": period.isoformat(),
            "sector_family": used_rows[-1].sector_family,
            "derivation_profile": config.derivation_profile,
            "derivation_version": config.derivation_version,
            "values": {key: float_values[key] for key in sorted(float_values)},
            "source_lineage": lineage,
        }
        metric_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        diagnostics = {
            "is_complete": not missing_required,
            "missing_required_fields": list(missing_required),
            "minimum_present_count": present_count,
            "field_sources": field_sources,
            "field_reasons": field_reasons,
            "semantic_profile": config.semantic_profile,
            "semantic_version": config.semantic_version,
        }
        output.append(CompanyDerivedQuarter(
            ticker=ticker_norm,
            sector_family=used_rows[-1].sector_family,
            period_end=period,
            version_tag=f"DERIVED_{metric_hash[:12].upper()}",
            version_sequence=version_sequence,
            published_at=publication,
            source_disclosure_id=f"SEMANTIC:{metric_hash}",
            values=float_values,
            lineage_sha256=metric_hash,
            source_lineage=lineage,
            derivation_profile=config.derivation_profile,
            derivation_version=config.derivation_version,
            is_complete=not missing_required,
            diagnostics=diagnostics,
        ))
    return tuple(output)


COMPANY_METRIC_INSERT_SQL = """
INSERT INTO core.company_metrics_quarterly (
  ticker, sector_family, period_end, version_tag, version_sequence,
  published_at, source_disclosure_id, lineage_sha256, source_lineage,
  derivation_profile, derivation_version, is_complete, derivation_diagnostics,
  revenue, cogs, gross_profit, ebit, net_income, interest_exp,
  total_assets, total_equity, current_assets, current_liabilities,
  cash_and_eq, st_investments, receivables, inventory, debt_st, debt_lt,
  cfo, capex, shares_out, shares_diluted
) VALUES (
  %s, %s, %s, %s, %s,
  %s, %s, %s, %s::jsonb,
  %s, %s, %s, %s::jsonb,
  %s, %s, %s, %s, %s, %s,
  %s, %s, %s, %s,
  %s, %s, %s, %s, %s, %s,
  %s, %s, %s, %s
)
ON CONFLICT (source_disclosure_id)
DO UPDATE SET inserted_at = GREATEST(
  core.company_metrics_quarterly.inserted_at,
  EXCLUDED.inserted_at
)
"""


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _persist_company_metric_cursor(cur: Any, row: CompanyDerivedQuarter) -> None:
    v = row.values
    cur.execute(COMPANY_METRIC_INSERT_SQL, (
        row.ticker, row.sector_family, row.period_end, row.version_tag,
        row.version_sequence, row.published_at, row.source_disclosure_id,
        row.lineage_sha256, _json_text(row.source_lineage),
        row.derivation_profile, row.derivation_version, row.is_complete,
        _json_text(row.diagnostics),
        v.get("revenue"), v.get("cogs"), v.get("gross_profit"), v.get("ebit"),
        v.get("net_income"), v.get("interest_exp"), v.get("total_assets"),
        v.get("total_equity"), v.get("current_assets"), v.get("current_liabilities"),
        v.get("cash_and_eq"), v.get("st_investments"), v.get("receivables"),
        v.get("inventory"), v.get("debt_st"), v.get("debt_lt"), v.get("cfo"),
        v.get("capex"), v.get("shares_out"), v.get("shares_diluted"),
    ))


def persist_company_derived_quarters(conn: Any, rows: Iterable[CompanyDerivedQuarter]) -> int:
    metrics = tuple(rows)
    if not metrics:
        return 0
    with conn:
        with conn.cursor() as cur:
            for row in metrics:
                _persist_company_metric_cursor(cur, row)
    return len(metrics)


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        if not isinstance(raw, str) or not raw.strip():
            raise CompanyDerivationError("ticker degerleri dolu metin olmali")
        ticker = raw.strip().upper()
        if ticker not in seen:
            seen.add(ticker)
            result.append(ticker)
    return result


def _fact_from_db_row(row: Mapping[str, Any]) -> SemanticFinancialFact:
    dimensions = row["dimensions"]
    if isinstance(dimensions, str):
        try:
            dimensions = json.loads(dimensions)
        except json.JSONDecodeError as exc:
            raise CompanyDerivationError("semantic dimensions gecersiz JSON") from exc
    if not isinstance(dimensions, Mapping):
        raise CompanyDerivationError("semantic dimensions nesne olmali")
    return SemanticFinancialFact(
        source=row["source"], disclosure_id=row["disclosure_id"], ticker=row["ticker"],
        published_at=row["published_at"], version_tag=row["version_tag"],
        version_sequence=int(row["version_sequence"]), sector_family=row["sector_family"],
        semantic_profile=row["semantic_profile"], semantic_version=int(row["semantic_version"]),
        canonical_field=row["canonical_field"], nature=row["nature"],
        period_start=row["period_start"], period_end=row["period_end"],
        currency=row["currency"], statement_scope=row["statement_scope"],
        value=Decimal(str(row["value"])), source_fact_code=row["source_fact_code"],
        source_fact_key=row["source_fact_key"],
        source_mapping_profile=row["source_mapping_profile"],
        source_mapping_version=int(row["source_mapping_version"]),
        dimensions=dict(dimensions), lineage_sha256=row["lineage_sha256"],
        mapped_at=row["mapped_at"],
    )


def fetch_company_semantic_facts_batch_asof(
    conn: Any,
    *,
    config: CompanyDerivationConfig,
    tickers: Iterable[str],
    analysis_at: datetime,
    anchor_period_end: date,
) -> dict[str, tuple[SemanticFinancialFact, ...]]:
    config = _ensure_runtime_config(config)
    if not isinstance(analysis_at, datetime) or analysis_at.tzinfo is None or analysis_at.utcoffset() is None:
        raise CompanyDerivationError("analysis_at timezone iceren datetime olmali")
    ticker_list = _normalize_tickers(tickers)
    if not ticker_list:
        return {}
    periods = build_quarter_ends(anchor_period_end, config.history_periods)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source, disclosure_id, ticker, published_at, version_tag,
                   version_sequence, sector_family, semantic_profile, semantic_version,
                   canonical_field, nature, period_start, period_end, currency,
                   statement_scope, value, source_fact_code, source_fact_key,
                   source_mapping_profile, source_mapping_version, dimensions,
                   lineage_sha256, mapped_at
            FROM core.semantic_financial_facts
            WHERE ticker = ANY(%s::text[])
              AND sector_family = ANY(%s::text[])
              AND semantic_profile = %s
              AND semantic_version = %s
              AND canonical_field = ANY(%s::text[])
              AND period_end BETWEEN %s AND %s
              AND published_at <= %s
            ORDER BY ticker, canonical_field, period_end, published_at,
                     version_sequence, disclosure_id, lineage_sha256
            """,
            (
                ticker_list, list(config.sector_families), config.semantic_profile,
                config.semantic_version, list(config.required_semantic_fields),
                periods[0], periods[-1], analysis_at,
            ),
        )
        names = [item[0] for item in cur.description]
        rows = [dict(zip(names, row)) for row in cur.fetchall()]
    grouped: dict[str, list[SemanticFinancialFact]] = {ticker: [] for ticker in ticker_list}
    for row in rows:
        ticker = str(row["ticker"]).strip().upper()
        if ticker not in grouped:
            raise CompanyDerivationError(f"semantic batch beklenmeyen ticker dondurdu: {ticker}")
        grouped[ticker].append(_fact_from_db_row(row))
    return {ticker: tuple(items) for ticker, items in grouped.items()}


def fetch_active_company_tickers(conn: Any, config: CompanyDerivationConfig) -> list[str]:
    config = _ensure_runtime_config(config)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker
            FROM core.universe_stocks
            WHERE is_active = true
              AND upper(COALESCE(sector_code, 'NONFIN')) = ANY(%s::text[])
            ORDER BY ticker
            """,
            (list(config.sector_families),),
        )
        return _normalize_tickers(row[0] for row in cur.fetchall())


@dataclass(frozen=True)
class CompanyMaterializationBatchReport:
    tickers_seen: int
    tickers_materialized: int
    tickers_rejected: int
    metrics_written: int
    rejected: Mapping[str, str]


def materialize_company_metrics_batch(
    conn: Any,
    *,
    config: CompanyDerivationConfig,
    tickers: Iterable[str],
    analysis_at: datetime,
    anchor_period_end: date,
    persist: bool = True,
) -> CompanyMaterializationBatchReport:
    if type(persist) is not bool:
        raise CompanyDerivationError("persist Python bool olmali")
    normalized = _normalize_tickers(tickers)
    facts_by_ticker = fetch_company_semantic_facts_batch_asof(
        conn, config=config, tickers=normalized, analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
    )
    materialized = 0
    written = 0
    rejected: dict[str, str] = {}
    for ticker in normalized:
        try:
            metrics = derive_company_quarters(
                facts_by_ticker.get(ticker, ()), config=config, ticker=ticker,
                analysis_at=analysis_at, anchor_period_end=anchor_period_end,
            )
            if not metrics:
                raise CompanyDerivationError("NO_DERIVABLE_METRICS")
            if persist:
                with conn:
                    with conn.cursor() as cur:
                        for metric in metrics:
                            _persist_company_metric_cursor(cur, metric)
                        cur.execute(
                            """
                            DELETE FROM core.company_metric_derivation_rejections
                            WHERE ticker = %s AND analysis_at = %s
                              AND anchor_period_end = %s
                              AND derivation_profile = %s
                              AND derivation_version = %s
                            """,
                            (
                                ticker, analysis_at, _quarter_end(anchor_period_end),
                                config.derivation_profile, config.derivation_version,
                            ),
                        )
            materialized += 1
            written += len(metrics)
        except (CompanyDerivationError, ValueError, ArithmeticError) as exc:
            rejected[ticker] = str(exc)
            if persist:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO core.company_metric_derivation_rejections (
                              ticker, analysis_at, anchor_period_end,
                              derivation_profile, derivation_version,
                              reason, first_rejected_at, last_rejected_at, attempts
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                            ON CONFLICT (
                              ticker, analysis_at, anchor_period_end,
                              derivation_profile, derivation_version
                            ) DO UPDATE SET
                              reason = EXCLUDED.reason,
                              last_rejected_at = EXCLUDED.last_rejected_at,
                              attempts = core.company_metric_derivation_rejections.attempts + 1
                            """,
                            (
                                ticker, analysis_at, _quarter_end(anchor_period_end),
                                config.derivation_profile, config.derivation_version,
                                str(exc), analysis_at, analysis_at,
                            ),
                        )
    return CompanyMaterializationBatchReport(
        tickers_seen=len(normalized),
        tickers_materialized=materialized,
        tickers_rejected=len(rejected),
        metrics_written=written,
        rejected=rejected,
    )
