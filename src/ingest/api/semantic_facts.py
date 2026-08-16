from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from src.ingest.api.kap_financial_facts import KapFinancialFact
from src.ingest.api.mkk_kap import KapApiConfigError, KapApiProtocolError

ALLOWED_NATURES = frozenset({"INSTANT", "YTD", "QUARTER", "TTM", "RATIO"})
ALLOWED_SIGNS = frozenset({"AS_IS", "ABS", "NEGATE"})
ALLOWED_PERIOD_START_POLICIES = frozenset({"ANY", "REQUIRED", "FORBIDDEN"})
MAX_MAPPING_VERSION = 2_147_483_647
MAX_FIELDS = 10_000
MAX_SOURCE_CODES_PER_FIELD = 1_000
MAX_DIMENSION_FILTER_BYTES = 65_536
MAX_ABS_SEMANTIC_VALUE = Decimal("1e100")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")


def _strict_text(name: str, value: Any, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KapApiConfigError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _optional_text(name: str, value: Any, *, uppercase: bool = False) -> Optional[str]:
    if value is None:
        return None
    return _strict_text(name, value, uppercase=uppercase)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise KapApiConfigError("dimensions_equals JSON olarak kanoniklestirilemiyor") from exc


def _normalize_json_value(value: Any) -> Any:
    """Only JSON-compatible values are allowed in dimension filters."""
    text = _canonical_json(value)
    if len(text.encode("utf-8")) > MAX_DIMENSION_FILTER_BYTES:
        raise KapApiConfigError("dimensions_equals desteklenen boyutu asiyor")
    return json.loads(text)


@dataclass(frozen=True)
class SemanticFieldRule:
    canonical_field: str
    source_codes: tuple[str, ...]
    nature: str
    sign: str = "AS_IS"
    statement_scope_priority: tuple[str, ...] = ()
    currency: Optional[str] = None
    dimensions_equals: Mapping[str, Any] | None = None
    period_start_policy: str = "ANY"

    @classmethod
    def from_dict(cls, canonical_field: str, data: Mapping[str, Any]) -> "SemanticFieldRule":
        canonical = _strict_text("canonical_field", canonical_field, uppercase=True)
        if not isinstance(data, Mapping):
            raise KapApiConfigError(f"fields.{canonical} nesne olmali")
        source_codes_raw = data.get("source_codes")
        if not isinstance(source_codes_raw, list) or not source_codes_raw:
            raise KapApiConfigError(f"fields.{canonical}.source_codes dolu liste olmali")
        if len(source_codes_raw) > MAX_SOURCE_CODES_PER_FIELD:
            raise KapApiConfigError(f"fields.{canonical}.source_codes cok buyuk")
        source_codes = tuple(
            _strict_text(f"fields.{canonical}.source_codes", value, uppercase=True)
            for value in source_codes_raw
        )
        if len(set(source_codes)) != len(source_codes):
            raise KapApiConfigError(f"fields.{canonical}.source_codes yinelenemez")

        nature = _strict_text(f"fields.{canonical}.nature", data.get("nature"), uppercase=True)
        if nature not in ALLOWED_NATURES:
            raise KapApiConfigError(
                f"fields.{canonical}.nature {sorted(ALLOWED_NATURES)} icinden olmali"
            )
        sign = _strict_text(
            f"fields.{canonical}.sign", data.get("sign", "AS_IS"), uppercase=True
        )
        if sign not in ALLOWED_SIGNS:
            raise KapApiConfigError(f"fields.{canonical}.sign gecersiz")

        scopes_raw = data.get("statement_scope_priority", [])
        if not isinstance(scopes_raw, list):
            raise KapApiConfigError(
                f"fields.{canonical}.statement_scope_priority liste olmali"
            )
        scopes = tuple(
            _strict_text(
                f"fields.{canonical}.statement_scope_priority", value, uppercase=True
            )
            for value in scopes_raw
        )
        if len(set(scopes)) != len(scopes):
            raise KapApiConfigError(
                f"fields.{canonical}.statement_scope_priority yinelenemez"
            )

        dimensions_raw = data.get("dimensions_equals", {})
        if not isinstance(dimensions_raw, Mapping):
            raise KapApiConfigError(f"fields.{canonical}.dimensions_equals nesne olmali")
        dimensions: dict[str, Any] = {}
        for key, value in dimensions_raw.items():
            key_text = _strict_text(f"fields.{canonical}.dimensions_equals anahtari", key)
            dimensions[key_text] = _normalize_json_value(value)

        period_policy = _strict_text(
            f"fields.{canonical}.period_start_policy",
            data.get("period_start_policy", "ANY"),
            uppercase=True,
        )
        if period_policy not in ALLOWED_PERIOD_START_POLICIES:
            raise KapApiConfigError(
                f"fields.{canonical}.period_start_policy gecersiz"
            )
        if nature in {"YTD", "QUARTER"} and period_policy == "FORBIDDEN":
            raise KapApiConfigError(
                f"fields.{canonical} sure alan kalemde period_start yasaklanamaz"
            )

        return cls(
            canonical_field=canonical,
            source_codes=source_codes,
            nature=nature,
            sign=sign,
            statement_scope_priority=scopes,
            currency=_optional_text(
                f"fields.{canonical}.currency", data.get("currency"), uppercase=True
            ),
            dimensions_equals=dimensions,
            period_start_policy=period_policy,
        )


def _rules_may_overlap(left: SemanticFieldRule, right: SemanticFieldRule) -> bool:
    if left.currency is not None and right.currency is not None and left.currency != right.currency:
        return False
    left_dims = left.dimensions_equals or {}
    right_dims = right.dimensions_equals or {}
    for key in set(left_dims) & set(right_dims):
        if left_dims[key] != right_dims[key]:
            return False
    if left.statement_scope_priority and right.statement_scope_priority:
        if set(left.statement_scope_priority).isdisjoint(right.statement_scope_priority):
            return False
    if {
        left.period_start_policy,
        right.period_start_policy,
    } == {"REQUIRED", "FORBIDDEN"}:
        return False
    return True


@dataclass(frozen=True)
class SemanticMappingConfig:
    mapping_profile: str
    mapping_version: int
    sector_family: str
    fields: tuple[SemanticFieldRule, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticMappingConfig":
        if not isinstance(data, Mapping):
            raise KapApiConfigError("semantic mapping config nesne olmali")
        profile = _strict_text("mapping_profile", data.get("mapping_profile"))
        version = data.get("mapping_version")
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise KapApiConfigError("mapping_version pozitif Python int olmali")
        if version > MAX_MAPPING_VERSION:
            raise KapApiConfigError("mapping_version PostgreSQL INT sinirini asiyor")
        sector = _strict_text("sector_family", data.get("sector_family"), uppercase=True)
        raw_fields = data.get("fields")
        if not isinstance(raw_fields, Mapping) or not raw_fields:
            raise KapApiConfigError("fields dolu nesne olmali")
        if len(raw_fields) > MAX_FIELDS:
            raise KapApiConfigError("fields desteklenen sayiyi asiyor")
        for name in raw_fields:
            if not isinstance(name, str) or not name.strip():
                raise KapApiConfigError("fields anahtarlari dolu metin olmali")
        rules = tuple(
            SemanticFieldRule.from_dict(name, rule)
            for name, rule in raw_fields.items()
        )
        canonical_names = [rule.canonical_field for rule in rules]
        if len(set(canonical_names)) != len(canonical_names):
            raise KapApiConfigError("canonical field adlari yinelenemez")

        by_code: dict[str, list[SemanticFieldRule]] = {}
        for rule in rules:
            for code in rule.source_codes:
                for previous in by_code.get(code, []):
                    if _rules_may_overlap(previous, rule):
                        raise KapApiConfigError(
                            "ayni source code icin semantic seciciler cakismamali: "
                            f"{code} -> {previous.canonical_field}, {rule.canonical_field}"
                        )
                by_code.setdefault(code, []).append(rule)
        return cls(
            mapping_profile=profile,
            mapping_version=version,
            sector_family=sector,
            fields=rules,
        )

    @classmethod
    def from_json_file(cls, path: str) -> "SemanticMappingConfig":
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data)


@dataclass(frozen=True)
class SemanticFinancialFact:
    source: str
    disclosure_id: str
    ticker: str
    published_at: datetime
    version_tag: str
    version_sequence: int
    sector_family: str
    semantic_profile: str
    semantic_version: int
    canonical_field: str
    nature: str
    period_start: Any
    period_end: Any
    currency: Optional[str]
    statement_scope: Optional[str]
    value: Decimal
    source_fact_code: str
    source_fact_key: str
    source_mapping_profile: str
    source_mapping_version: int
    dimensions: Mapping[str, Any]
    lineage_sha256: str
    mapped_at: datetime


def _dimensions_match(actual: Mapping[str, Any], required: Mapping[str, Any]) -> bool:
    return all(key in actual and actual[key] == value for key, value in required.items())


def _apply_sign(value: Decimal, sign: str) -> Decimal:
    if sign == "ABS":
        return abs(value)
    if sign == "NEGATE":
        return -value
    return value


def _validate_raw_fact(fact: Any) -> KapFinancialFact:
    if not isinstance(fact, KapFinancialFact):
        raise KapApiProtocolError("semantic mapping yalniz KapFinancialFact icermeli")
    for name, value in (("published_at", fact.published_at), ("extracted_at", fact.extracted_at)):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise KapApiProtocolError(f"raw fact {name} timezone icermeli")
    if fact.published_at > fact.extracted_at + timedelta(minutes=5):
        raise KapApiProtocolError("raw fact extracted_at yayin anindan once")
    if not isinstance(fact.period_end, date) or isinstance(fact.period_end, datetime):
        raise KapApiProtocolError("raw fact period_end date olmali")
    if fact.period_start is not None:
        if not isinstance(fact.period_start, date) or isinstance(fact.period_start, datetime):
            raise KapApiProtocolError("raw fact period_start date olmali")
        if fact.period_start > fact.period_end:
            raise KapApiProtocolError("raw fact period_start period_end'den sonra")
    if not isinstance(fact.fact_code, str) or not fact.fact_code.strip():
        raise KapApiProtocolError("raw fact fact_code dolu metin olmali")
    if not isinstance(fact.fact_key, str) or not HEX64_RE.fullmatch(fact.fact_key):
        raise KapApiProtocolError("raw fact fact_key gecersiz")
    if isinstance(fact.version_sequence, bool) or not isinstance(fact.version_sequence, int) or fact.version_sequence < 0:
        raise KapApiProtocolError("raw fact version_sequence gecersiz")
    if isinstance(fact.mapping_version, bool) or not isinstance(fact.mapping_version, int) or fact.mapping_version <= 0:
        raise KapApiProtocolError("raw fact mapping_version gecersiz")
    if not isinstance(fact.scaled_value, Decimal) or not fact.scaled_value.is_finite() or abs(fact.scaled_value) > MAX_ABS_SEMANTIC_VALUE:
        raise KapApiProtocolError("raw fact scaled_value sonlu Decimal sinirinda olmali")
    if not isinstance(fact.dimensions, Mapping):
        raise KapApiProtocolError("raw fact dimensions nesne olmali")
    _normalize_json_value(fact.dimensions)
    return fact


def _lineage_hash(
    fact: KapFinancialFact,
    rule: SemanticFieldRule,
    value: Decimal,
    config: SemanticMappingConfig,
) -> str:
    payload = {
        "source": fact.source,
        "disclosure_id": fact.disclosure_id,
        "source_fact_key": fact.fact_key,
        "source_mapping_profile": fact.mapping_profile,
        "source_mapping_version": fact.mapping_version,
        "semantic_profile": config.mapping_profile,
        "semantic_version": config.mapping_version,
        "canonical_field": rule.canonical_field,
        "nature": rule.nature,
        "value": str(value),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SemanticFactMapper:
    def __init__(self, config: SemanticMappingConfig) -> None:
        self.config = config
        self._by_source_code: dict[str, list[tuple[SemanticFieldRule, int]]] = {}
        for rule in config.fields:
            for priority, code in enumerate(rule.source_codes):
                self._by_source_code.setdefault(code, []).append((rule, priority))

    def map_facts(
        self,
        facts: Iterable[KapFinancialFact],
        *,
        mapped_at: datetime,
    ) -> tuple[SemanticFinancialFact, ...]:
        if not isinstance(mapped_at, datetime) or mapped_at.tzinfo is None or mapped_at.utcoffset() is None:
            raise ValueError("mapped_at timezone iceren datetime olmali")
        try:
            rows = tuple(facts)
        except TypeError as exc:
            raise KapApiProtocolError("semantic mapping facts iterable olmali") from exc
        if not rows:
            raise KapApiProtocolError("semantic mapping icin en az bir fact gerekli")

        rows = tuple(_validate_raw_fact(row) for row in rows)

        identity = {
            (
                row.source,
                row.disclosure_id,
                row.ticker,
                row.published_at,
                row.version_tag,
                row.version_sequence,
            )
            for row in rows
        }
        if len(identity) != 1:
            raise KapApiProtocolError("tek semantic mapping batch tek disclosure/surum icermeli")
        first = rows[0]
        if not isinstance(first.ticker, str) or not first.ticker.strip():
            raise KapApiProtocolError("semantic mapping icin ticker zorunlu")
        if first.published_at > mapped_at:
            raise KapApiProtocolError("published_at mapped_at sonrasinda; look-ahead reddedildi")

        candidates: dict[
            tuple[str, Any, Any, Optional[str]],
            list[tuple[tuple[int, int, str], SemanticFinancialFact]],
        ] = {}
        for fact in rows:
            if fact.period_end > fact.published_at.astimezone(ISTANBUL_TZ).date():
                raise KapApiProtocolError(
                    f"fact period_end yayin anindan sonra: {fact.fact_code} {fact.period_end}"
                )
            matches = self._by_source_code.get(fact.fact_code.strip().upper(), ())
            for rule, code_priority in matches:
                if rule.currency is not None and fact.currency != rule.currency:
                    continue
                if rule.statement_scope_priority:
                    if fact.statement_scope not in rule.statement_scope_priority:
                        continue
                    scope_priority = rule.statement_scope_priority.index(fact.statement_scope)
                else:
                    scope_priority = 0
                required_dimensions = rule.dimensions_equals or {}
                if not _dimensions_match(fact.dimensions, required_dimensions):
                    continue
                if rule.period_start_policy == "REQUIRED" and fact.period_start is None:
                    raise KapApiProtocolError(
                        f"{rule.canonical_field} period_start gerektiriyor"
                    )
                if rule.period_start_policy == "FORBIDDEN" and fact.period_start is not None:
                    raise KapApiProtocolError(
                        f"{rule.canonical_field} period_start kabul etmiyor"
                    )
                if rule.nature in {"YTD", "QUARTER"} and fact.period_start is None:
                    raise KapApiProtocolError(
                        f"{rule.canonical_field} sure alan kalem period_start gerektiriyor"
                    )

                value = _apply_sign(fact.scaled_value, rule.sign)
                semantic = SemanticFinancialFact(
                    source=fact.source,
                    disclosure_id=fact.disclosure_id,
                    ticker=first.ticker.strip().upper(),
                    published_at=fact.published_at,
                    version_tag=fact.version_tag,
                    version_sequence=fact.version_sequence,
                    sector_family=self.config.sector_family,
                    semantic_profile=self.config.mapping_profile,
                    semantic_version=self.config.mapping_version,
                    canonical_field=rule.canonical_field,
                    nature=rule.nature,
                    period_start=fact.period_start,
                    period_end=fact.period_end,
                    currency=fact.currency,
                    statement_scope=fact.statement_scope,
                    value=value,
                    source_fact_code=fact.fact_code,
                    source_fact_key=fact.fact_key,
                    source_mapping_profile=fact.mapping_profile,
                    source_mapping_version=fact.mapping_version,
                    dimensions=dict(fact.dimensions),
                    lineage_sha256=_lineage_hash(fact, rule, value, self.config),
                    mapped_at=mapped_at,
                )
                context = (
                    rule.canonical_field,
                    fact.period_start,
                    fact.period_end,
                    fact.currency,
                )
                priority = (code_priority, scope_priority, fact.fact_key)
                candidates.setdefault(context, []).append((priority, semantic))

        selected: list[SemanticFinancialFact] = []
        for context, options in candidates.items():
            options.sort(key=lambda item: item[0])
            best_priority = options[0][0][:2]
            best = [item[1] for item in options if item[0][:2] == best_priority]
            values = {item.value for item in best}
            if len(values) > 1:
                raise KapApiProtocolError(
                    "semantic mapping ayni oncelikte celiskili deger buldu: "
                    f"{context[0]} {context[2]}"
                )
            selected.append(min(best, key=lambda item: item.source_fact_key))

        if not selected:
            raise KapApiProtocolError("disclosure semantic mapping ile hic kalem uretmedi")
        return tuple(
            sorted(
                selected,
                key=lambda item: (
                    item.period_end,
                    item.canonical_field,
                    item.period_start or item.period_end,
                    item.lineage_sha256,
                ),
            )
        )
