from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

MAX_ABS_NUMERIC = Decimal("1e100")
MAX_UNIT_SCALE = 10**12
MAX_DIMENSIONS_JSON_BYTES = 65536
MAX_DB_INT = 2_147_483_647
MAX_FACTS_PER_DISCLOSURE = 100_000
MAX_PUBLICATION_CLOCK_SKEW = timedelta(minutes=5)

from src.ingest.api.mkk_kap import (
    KapApiConfigError,
    KapApiProtocolError,
    KapDisclosureEnvelope,
    _get_path,
)


def _parse_date(name: str, value: Any, *, optional: bool = False) -> Optional[date]:
    if value is None and optional:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        raise KapApiProtocolError(f"{name} ISO tarih olmali")
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise KapApiProtocolError(f"{name} gecersiz tarih: {value!r}") from exc


def _parse_decimal(name: str, value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise KapApiProtocolError(f"{name} gercek sayi olmali")
    if isinstance(value, float) and not math.isfinite(value):
        raise KapApiProtocolError(f"{name} sonlu olmali")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise KapApiProtocolError(f"{name} sayiya cevrilemiyor: {value!r}") from exc
    if not parsed.is_finite():
        raise KapApiProtocolError(f"{name} sonlu olmali")
    if abs(parsed) > MAX_ABS_NUMERIC:
        raise KapApiProtocolError(f"{name} desteklenen sayisal siniri asiyor")
    return parsed


def _parse_positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise KapApiProtocolError(f"{name} pozitif tam sayi olmali")
    parsed = _parse_decimal(name, value)
    if parsed != parsed.to_integral_value() or parsed <= 0:
        raise KapApiProtocolError(f"{name} pozitif tam sayi olmali")
    result = int(parsed)
    if result > MAX_UNIT_SCALE:
        raise KapApiProtocolError(f"{name} desteklenen birim olcegi sinirini asiyor")
    return result


def _optional_text(value: Any, *, uppercase: bool = False) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise KapApiProtocolError("metin alani string olmali")
    text = value.strip()
    if not text:
        return None
    return text.upper() if uppercase else text


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise KapApiProtocolError("dimensions JSON olarak kanoniklestirilemiyor") from exc


@dataclass(frozen=True)
class KapFinancialFactConfig:
    mapping_profile: str
    mapping_version: int
    facts_path: str
    fields: Mapping[str, str]
    dimensions_path: Optional[str] = None
    version_tag_path: Optional[str] = None
    version_sequence_path: Optional[str] = None
    default_unit_scale: int = 1
    default_currency: Optional[str] = None
    default_statement_scope: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KapFinancialFactConfig":
        if not isinstance(data, Mapping):
            raise KapApiConfigError("fact mapping config nesne olmali")
        required = ("mapping_profile", "mapping_version", "facts_path", "fields")
        missing = [key for key in required if key not in data]
        if missing:
            raise KapApiConfigError(f"fact mapping eksik alanlar: {missing}")
        profile = data["mapping_profile"]
        if not isinstance(profile, str) or not profile.strip():
            raise KapApiConfigError("mapping_profile dolu metin olmali")
        version = data["mapping_version"]
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise KapApiConfigError("mapping_version pozitif Python int olmali")
        if version > MAX_DB_INT:
            raise KapApiConfigError("mapping_version PostgreSQL INT sinirini asiyor")
        facts_path = data["facts_path"]
        if not isinstance(facts_path, str) or not facts_path.strip():
            raise KapApiConfigError("facts_path dolu metin olmali")
        fields = data["fields"]
        if not isinstance(fields, Mapping):
            raise KapApiConfigError("fields nesne olmali")
        for key, path in fields.items():
            if not isinstance(key, str) or not isinstance(path, str) or not path.strip():
                raise KapApiConfigError("fields anahtar ve yollari dolu metin olmali")
        for key in ("fact_code", "value", "period_end"):
            if key not in fields:
                raise KapApiConfigError(f"fields.{key} zorunlu")
        default_unit = data.get("default_unit_scale", 1)
        if isinstance(default_unit, bool) or not isinstance(default_unit, int) or default_unit <= 0:
            raise KapApiConfigError("default_unit_scale pozitif Python int olmali")
        if default_unit > MAX_UNIT_SCALE:
            raise KapApiConfigError("default_unit_scale desteklenen siniri asiyor")

        def opt_path(name: str) -> Optional[str]:
            value = data.get(name)
            if value is None:
                return None
            if not isinstance(value, str) or not value.strip():
                raise KapApiConfigError(f"{name} dolu metin olmali")
            return value.strip()

        return cls(
            mapping_profile=profile.strip(),
            mapping_version=version,
            facts_path=facts_path.strip(),
            fields=dict(fields),
            dimensions_path=opt_path("dimensions_path"),
            version_tag_path=opt_path("version_tag_path"),
            version_sequence_path=opt_path("version_sequence_path"),
            default_unit_scale=default_unit,
            default_currency=_optional_text(data.get("default_currency"), uppercase=True),
            default_statement_scope=_optional_text(data.get("default_statement_scope"), uppercase=True),
        )


    @classmethod
    def from_json_file(cls, path: str) -> "KapFinancialFactConfig":
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data)


@dataclass(frozen=True)
class KapFinancialFact:
    source: str
    disclosure_id: str
    mapping_profile: str
    mapping_version: int
    fact_key: str
    ticker: Optional[str]
    published_at: datetime
    version_tag: str
    version_sequence: int
    fact_code: str
    period_start: Optional[date]
    period_end: date
    currency: Optional[str]
    unit_scale: int
    raw_value_text: str
    normalized_value: Decimal
    scaled_value: Decimal
    statement_scope: Optional[str]
    dimensions: Mapping[str, Any]
    extracted_at: datetime


class KapFinancialFactExtractor:
    def __init__(self, config: KapFinancialFactConfig) -> None:
        self.config = config

    def _fact_key(
        self,
        *,
        fact_code: str,
        period_start: Optional[date],
        period_end: date,
        currency: Optional[str],
        statement_scope: Optional[str],
        dimensions: Mapping[str, Any],
    ) -> str:
        context = {
            "fact_code": fact_code,
            "period_start": None if period_start is None else period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "currency": currency,
            "statement_scope": statement_scope,
            "dimensions": dimensions,
        }
        return hashlib.sha256(_canonical_json(context).encode("utf-8")).hexdigest()

    def extract(
        self,
        envelope: KapDisclosureEnvelope,
        *,
        extracted_at: datetime,
    ) -> tuple[KapFinancialFact, ...]:
        if extracted_at.tzinfo is None or extracted_at.utcoffset() is None:
            raise ValueError("extracted_at timezone icermeli")
        if envelope.published_at.tzinfo is None or envelope.published_at.utcoffset() is None:
            raise KapApiProtocolError("published_at timezone icermeli")
        if envelope.published_at > extracted_at + MAX_PUBLICATION_CLOCK_SKEW:
            raise KapApiProtocolError("published_at extracted_at sonrasinda; look-ahead reddedildi")
        payload = envelope.payload
        facts = _get_path(payload, self.config.facts_path, required=True)
        if not isinstance(facts, list):
            raise KapApiProtocolError("facts_path listeye cikmali")
        if not facts:
            raise KapApiProtocolError("facts_path bos liste olamaz")
        if len(facts) > MAX_FACTS_PER_DISCLOSURE:
            raise KapApiProtocolError("facts_path desteklenen fact sayisini asiyor")

        version_tag_raw = (
            _get_path(payload, self.config.version_tag_path)
            if self.config.version_tag_path
            else None
        )
        version_tag = _optional_text(version_tag_raw, uppercase=True) or "DISCLOSURE"
        sequence_raw = (
            _get_path(payload, self.config.version_sequence_path)
            if self.config.version_sequence_path
            else 0
        )
        if sequence_raw is None:
            sequence_raw = 0
        if isinstance(sequence_raw, bool):
            raise KapApiProtocolError("version_sequence negatif olmayan tam sayi olmali")
        sequence_decimal = _parse_decimal("version_sequence", sequence_raw)
        if sequence_decimal != sequence_decimal.to_integral_value() or sequence_decimal < 0:
            raise KapApiProtocolError("version_sequence negatif olmayan tam sayi olmali")
        if sequence_decimal > MAX_DB_INT:
            raise KapApiProtocolError("version_sequence PostgreSQL INT sinirini asiyor")
        version_sequence = int(sequence_decimal)

        by_key: dict[str, KapFinancialFact] = {}
        fields = self.config.fields
        for index, raw_fact in enumerate(facts):
            if not isinstance(raw_fact, Mapping):
                raise KapApiProtocolError(f"fact[{index}] nesne olmali")
            fact_code = _optional_text(
                _get_path(raw_fact, fields["fact_code"], required=True)
            )
            if not fact_code:
                raise KapApiProtocolError(f"fact[{index}].fact_code bos olamaz")
            raw_value = _get_path(raw_fact, fields["value"], required=True)
            normalized_value = _parse_decimal(f"fact[{index}].value", raw_value)
            period_end = _parse_date(
                f"fact[{index}].period_end",
                _get_path(raw_fact, fields["period_end"], required=True),
            )
            period_start = (
                _parse_date(
                    f"fact[{index}].period_start",
                    _get_path(raw_fact, fields.get("period_start")),
                    optional=True,
                )
                if fields.get("period_start")
                else None
            )
            if period_start is not None and period_start > period_end:
                raise KapApiProtocolError(f"fact[{index}] period_start period_end'den sonra")
            unit_scale = (
                _parse_positive_int(
                    f"fact[{index}].unit_scale",
                    _get_path(raw_fact, fields.get("unit_scale")),
                )
                if fields.get("unit_scale") and _get_path(raw_fact, fields.get("unit_scale")) is not None
                else self.config.default_unit_scale
            )
            currency = (
                _optional_text(_get_path(raw_fact, fields.get("currency")), uppercase=True)
                if fields.get("currency")
                else self.config.default_currency
            ) or self.config.default_currency
            statement_scope = (
                _optional_text(_get_path(raw_fact, fields.get("statement_scope")), uppercase=True)
                if fields.get("statement_scope")
                else self.config.default_statement_scope
            ) or self.config.default_statement_scope
            dimensions_raw = (
                _get_path(raw_fact, self.config.dimensions_path)
                if self.config.dimensions_path
                else {}
            )
            if dimensions_raw is None:
                dimensions_raw = {}
            if not isinstance(dimensions_raw, Mapping):
                raise KapApiProtocolError(f"fact[{index}].dimensions nesne olmali")
            dimensions = dict(dimensions_raw)
            dimensions_json = _canonical_json(dimensions)
            if len(dimensions_json.encode("utf-8")) > MAX_DIMENSIONS_JSON_BYTES:
                raise KapApiProtocolError(f"fact[{index}].dimensions cok buyuk")
            fact_key = self._fact_key(
                fact_code=fact_code,
                period_start=period_start,
                period_end=period_end,
                currency=currency,
                statement_scope=statement_scope,
                dimensions=dimensions,
            )
            scaled_value = normalized_value * unit_scale
            if not scaled_value.is_finite() or abs(scaled_value) > MAX_ABS_NUMERIC:
                raise KapApiProtocolError(f"fact[{index}].scaled_value desteklenen siniri asiyor")
            fact = KapFinancialFact(
                source=envelope.source,
                disclosure_id=envelope.disclosure_id,
                mapping_profile=self.config.mapping_profile,
                mapping_version=self.config.mapping_version,
                fact_key=fact_key,
                ticker=envelope.ticker,
                published_at=envelope.published_at,
                version_tag=version_tag,
                version_sequence=version_sequence,
                fact_code=fact_code,
                period_start=period_start,
                period_end=period_end,
                currency=currency,
                unit_scale=unit_scale,
                raw_value_text=str(raw_value),
                normalized_value=normalized_value,
                scaled_value=scaled_value,
                statement_scope=statement_scope,
                dimensions=dimensions,
                extracted_at=extracted_at,
            )
            previous = by_key.get(fact_key)
            if previous is not None and (
                previous.normalized_value != fact.normalized_value
                or previous.unit_scale != fact.unit_scale
            ):
                raise KapApiProtocolError(
                    f"ayni fact context farkli degerle geldi: {fact_code} {period_end}"
                )
            by_key.setdefault(fact_key, fact)
        return tuple(sorted(by_key.values(), key=lambda x: (x.period_end, x.fact_code, x.fact_key)))
