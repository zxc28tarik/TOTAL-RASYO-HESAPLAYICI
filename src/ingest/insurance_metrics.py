from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

MAX_ABS_NUMBER = Decimal("1e100")
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
SUPPORTED_BUSINESS_TYPES = frozenset({"NON_LIFE", "LIFE_PENSION"})


class InsuranceMetricsIngestError(ValueError):
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


def _strict_text(name: str, value: Any, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InsuranceMetricsIngestError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _date(name: str, value: Any) -> date:
    if isinstance(value, datetime):
        raise InsuranceMetricsIngestError(f"{name} date olmali")
    if isinstance(value, date):
        result = value
    elif isinstance(value, str):
        try:
            result = date.fromisoformat(value.strip())
        except ValueError as exc:
            raise InsuranceMetricsIngestError(f"{name} ISO date olmali") from exc
    else:
        raise InsuranceMetricsIngestError(f"{name} date olmali")
    if (result.month, result.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
        raise InsuranceMetricsIngestError(f"{name} gercek takvim ceyrek sonu olmali")
    return result


def _aware_datetime(name: str, value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise InsuranceMetricsIngestError(f"{name} ISO datetime olmali") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InsuranceMetricsIngestError(f"{name} timezone iceren datetime olmali")
    return value.astimezone(timezone.utc)


def _strict_int(name: str, value: Any, *, minimum: int = 0) -> int:
    if _is_bool_like(value) or not isinstance(value, int):
        raise InsuranceMetricsIngestError(f"{name} Python int olmali")
    if value < minimum:
        raise InsuranceMetricsIngestError(f"{name} {minimum} degerinden kucuk olamaz")
    return value


def _decimal(
    name: str,
    value: Any,
    *,
    minimum: Decimal | None = None,
    strict_minimum: bool = False,
    maximum: Decimal | None = None,
) -> Decimal:
    if _is_bool_like(value) or isinstance(value, (list, tuple, dict, set)):
        raise InsuranceMetricsIngestError(f"{name} sonlu sayi olmali")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InsuranceMetricsIngestError(f"{name} sonlu sayi olmali") from exc
    if not result.is_finite() or abs(result) > MAX_ABS_NUMBER:
        raise InsuranceMetricsIngestError(f"{name} sonlu sayi sinirinda olmali")
    if minimum is not None:
        if strict_minimum and result <= minimum:
            raise InsuranceMetricsIngestError(f"{name} {minimum} degerinden buyuk olmali")
        if not strict_minimum and result < minimum:
            raise InsuranceMetricsIngestError(f"{name} {minimum} degerinden kucuk olamaz")
    if maximum is not None and result > maximum:
        raise InsuranceMetricsIngestError(f"{name} {maximum} degerini asamaz")
    return result


def _optional_decimal(
    name: str,
    value: Any,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> Decimal | None:
    if value is None:
        return None
    return _decimal(name, value, minimum=minimum, maximum=maximum)


def _sha(name: str, value: Any) -> str:
    text = _strict_text(name, value).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise InsuranceMetricsIngestError(f"{name} 64 karakter hex SHA256 olmali")
    return text


def _json_object(name: str, value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise InsuranceMetricsIngestError(f"{name} JSON nesnesi olmali")
    for key in value:
        if not isinstance(key, str):
            raise InsuranceMetricsIngestError(f"{name} JSON anahtarlari metin olmali")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InsuranceMetricsIngestError(f"{name} JSON serilestirilebilir olmali") from exc
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise InsuranceMetricsIngestError(f"{name} 1MB sinirini asamaz")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise InsuranceMetricsIngestError(f"{name} JSON nesnesi olmali")
    return decoded


def _canonical_sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InsuranceMetricsRecord:
    ticker: str
    period_end: date
    published_at: datetime
    version_tag: str
    version_sequence: int
    business_type: str
    accounting_profile: str
    accounting_version: int
    currency: str
    shares_out: Decimal
    share_basis: str
    total_equity: Decimal
    net_income_ttm: Decimal
    written_premiums_ttm: Decimal
    technical_result_ttm: Decimal
    investment_income_ttm: Decimal
    earned_premiums_ttm: Decimal | None
    net_claims_ttm: Decimal | None
    operating_expenses_ttm: Decimal | None
    solvency_ratio: Decimal | None
    source_confidence: Decimal
    source_type: str
    source_document_id: str
    source_uri: str | None
    source_sha256: str
    metrics_profile: str
    metrics_version: int
    lineage: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "InsuranceMetricsRecord":
        if not isinstance(raw, Mapping):
            raise InsuranceMetricsIngestError("insurance metrics row mapping olmali")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(raw) - allowed
        if unknown:
            raise InsuranceMetricsIngestError(
                "insurance metrics bilinmeyen alanlar: " + ", ".join(sorted(repr(x) for x in unknown))
            )
        period = _date("period_end", raw.get("period_end"))
        published = _aware_datetime("published_at", raw.get("published_at"))
        if period > published.astimezone(ISTANBUL_TZ).date():
            raise InsuranceMetricsIngestError("period_end published_at tarihinden sonra olamaz")
        business = _strict_text("business_type", raw.get("business_type"), uppercase=True)
        if business not in SUPPORTED_BUSINESS_TYPES:
            raise InsuranceMetricsIngestError("business_type NON_LIFE veya LIFE_PENSION olmali")
        earned = _optional_decimal("earned_premiums_ttm", raw.get("earned_premiums_ttm"), minimum=Decimal("0"))
        claims = _optional_decimal("net_claims_ttm", raw.get("net_claims_ttm"), minimum=Decimal("0"))
        expenses = _optional_decimal("operating_expenses_ttm", raw.get("operating_expenses_ttm"), minimum=Decimal("0"))
        combined = (earned, claims, expenses)
        if any(value is None for value in combined) and not all(value is None for value in combined):
            raise InsuranceMetricsIngestError(
                "earned_premiums_ttm, net_claims_ttm ve operating_expenses_ttm birlikte verilmeli"
            )
        if business == "LIFE_PENSION" and any(value is not None for value in combined):
            raise InsuranceMetricsIngestError("LIFE_PENSION combined ratio alanlari bu surumde kullanilmaz")
        if earned is not None and earned == 0:
            raise InsuranceMetricsIngestError("earned_premiums_ttm sifir olamaz")
        uri = raw.get("source_uri")
        if uri is not None:
            uri = _strict_text("source_uri", uri)
        return cls(
            ticker=_strict_text("ticker", raw.get("ticker"), uppercase=True),
            period_end=period,
            published_at=published,
            version_tag=_strict_text("version_tag", raw.get("version_tag", "ORIGINAL"), uppercase=True),
            version_sequence=_strict_int("version_sequence", raw.get("version_sequence", 1), minimum=1),
            business_type=business,
            accounting_profile=_strict_text("accounting_profile", raw.get("accounting_profile"), uppercase=True),
            accounting_version=_strict_int("accounting_version", raw.get("accounting_version"), minimum=1),
            currency=_strict_text("currency", raw.get("currency", "TRY"), uppercase=True),
            shares_out=_decimal("shares_out", raw.get("shares_out"), minimum=Decimal("0"), strict_minimum=True),
            share_basis=_strict_text("share_basis", raw.get("share_basis"), uppercase=True),
            total_equity=_decimal("total_equity", raw.get("total_equity"), minimum=Decimal("0"), strict_minimum=True),
            net_income_ttm=_decimal("net_income_ttm", raw.get("net_income_ttm")),
            written_premiums_ttm=_decimal(
                "written_premiums_ttm", raw.get("written_premiums_ttm"), minimum=Decimal("0"), strict_minimum=True
            ),
            technical_result_ttm=_decimal("technical_result_ttm", raw.get("technical_result_ttm")),
            investment_income_ttm=_decimal("investment_income_ttm", raw.get("investment_income_ttm")),
            earned_premiums_ttm=earned,
            net_claims_ttm=claims,
            operating_expenses_ttm=expenses,
            solvency_ratio=_optional_decimal("solvency_ratio", raw.get("solvency_ratio"), minimum=Decimal("0")),
            source_confidence=_decimal(
                "source_confidence", raw.get("source_confidence", 1), minimum=Decimal("0"), maximum=Decimal("1")
            ),
            source_type=_strict_text("source_type", raw.get("source_type"), uppercase=True),
            source_document_id=_strict_text("source_document_id", raw.get("source_document_id")),
            source_uri=uri,
            source_sha256=_sha("source_sha256", raw.get("source_sha256")),
            metrics_profile=_strict_text("metrics_profile", raw.get("metrics_profile")),
            metrics_version=_strict_int("metrics_version", raw.get("metrics_version"), minimum=1),
            lineage=_json_object("lineage", raw.get("lineage", {})),
        )

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "period_end": self.period_end.isoformat(),
            "published_at": self.published_at.isoformat(),
            "version_tag": self.version_tag,
            "version_sequence": self.version_sequence,
            "business_type": self.business_type,
            "accounting_profile": self.accounting_profile,
            "accounting_version": self.accounting_version,
            "currency": self.currency,
            "shares_out": str(self.shares_out),
            "share_basis": self.share_basis,
            "total_equity": str(self.total_equity),
            "net_income_ttm": str(self.net_income_ttm),
            "written_premiums_ttm": str(self.written_premiums_ttm),
            "technical_result_ttm": str(self.technical_result_ttm),
            "investment_income_ttm": str(self.investment_income_ttm),
            "earned_premiums_ttm": None if self.earned_premiums_ttm is None else str(self.earned_premiums_ttm),
            "net_claims_ttm": None if self.net_claims_ttm is None else str(self.net_claims_ttm),
            "operating_expenses_ttm": None if self.operating_expenses_ttm is None else str(self.operating_expenses_ttm),
            "solvency_ratio": None if self.solvency_ratio is None else str(self.solvency_ratio),
            "source_confidence": str(self.source_confidence),
            "source_type": self.source_type,
            "source_document_id": self.source_document_id,
            "source_uri": self.source_uri,
            "source_sha256": self.source_sha256,
            "metrics_profile": self.metrics_profile,
            "metrics_version": self.metrics_version,
            "lineage": dict(self.lineage),
        }

    @property
    def canonical_sha256(self) -> str:
        return _canonical_sha(self.canonical_dict())

    @property
    def metrics_id(self) -> str:
        identity = {
            "ticker": self.ticker,
            "period_end": self.period_end.isoformat(),
            "published_at": self.published_at.isoformat(),
            "version_tag": self.version_tag,
            "version_sequence": self.version_sequence,
            "metrics_profile": self.metrics_profile,
            "metrics_version": self.metrics_version,
            "accounting_profile": self.accounting_profile,
            "accounting_version": self.accounting_version,
            "source_document_id": self.source_document_id,
        }
        return _canonical_sha(identity)


def load_insurance_metrics_jsonl(path: str | Path) -> list[InsuranceMetricsRecord]:
    source = Path(path)
    if not source.is_file():
        raise InsuranceMetricsIngestError(f"insurance metrics dosyasi bulunamadi: {source}")
    rows: list[InsuranceMetricsRecord] = []
    by_id: dict[str, str] = {}
    with source.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InsuranceMetricsIngestError(f"satir {line_no} gecersiz JSON") from exc
            record = InsuranceMetricsRecord.from_mapping(raw)
            previous = by_id.get(record.metrics_id)
            if previous is not None:
                if previous != record.canonical_sha256:
                    raise InsuranceMetricsIngestError(
                        f"satir {line_no} ayni insurance metrics kimligi farkli icerik tasiyor"
                    )
                continue
            by_id[record.metrics_id] = record.canonical_sha256
            rows.append(record)
    if not rows:
        raise InsuranceMetricsIngestError("insurance metrics dosyasi bos")
    return rows


INSERT_SQL = """
INSERT INTO core.insurance_metrics_snapshots (
  metrics_id, ticker, period_end, published_at, version_tag, version_sequence,
  business_type, accounting_profile, accounting_version, currency, shares_out, share_basis,
  total_equity, net_income_ttm, written_premiums_ttm, technical_result_ttm,
  investment_income_ttm, earned_premiums_ttm, net_claims_ttm, operating_expenses_ttm,
  solvency_ratio, source_confidence, source_type, source_document_id, source_uri,
  source_sha256, metrics_profile, metrics_version, lineage, canonical_sha256
) VALUES (
  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s
)
ON CONFLICT (metrics_id) DO UPDATE
SET inserted_at = GREATEST(core.insurance_metrics_snapshots.inserted_at, now())
WHERE core.insurance_metrics_snapshots.canonical_sha256 = EXCLUDED.canonical_sha256
RETURNING canonical_sha256
"""


def persist_insurance_metrics_records(conn: Any, rows: Iterable[InsuranceMetricsRecord]) -> int:
    items = tuple(rows)
    if not items:
        return 0
    for row in items:
        if not isinstance(row, InsuranceMetricsRecord):
            raise InsuranceMetricsIngestError("persist rows InsuranceMetricsRecord olmali")
        if InsuranceMetricsRecord.from_mapping(row.canonical_dict()) != row:
            raise InsuranceMetricsIngestError("insurance metrics record kanonik degil")
    with conn:
        with conn.cursor() as cur:
            for row in items:
                cur.execute(INSERT_SQL, (
                    row.metrics_id, row.ticker, row.period_end, row.published_at, row.version_tag,
                    row.version_sequence, row.business_type, row.accounting_profile, row.accounting_version,
                    row.currency, row.shares_out, row.share_basis, row.total_equity, row.net_income_ttm,
                    row.written_premiums_ttm, row.technical_result_ttm, row.investment_income_ttm,
                    row.earned_premiums_ttm, row.net_claims_ttm, row.operating_expenses_ttm,
                    row.solvency_ratio, row.source_confidence, row.source_type, row.source_document_id,
                    row.source_uri, row.source_sha256, row.metrics_profile, row.metrics_version,
                    json.dumps(row.lineage, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
                    row.canonical_sha256,
                ))
                returned = cur.fetchone()
                if not returned or str(returned[0]).lower() != row.canonical_sha256:
                    raise InsuranceMetricsIngestError(
                        f"{row.ticker} ayni insurance metrics kimligi veritabaninda farkli icerik tasiyor"
                    )
    return len(items)
