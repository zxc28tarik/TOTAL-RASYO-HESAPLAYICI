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
SUPPORTED_BUSINESS_TYPES = frozenset({"FACTORING", "LEASING", "CONSUMER_FINANCE"})


class FinancialInstitutionMetricsIngestError(ValueError):
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
        raise FinancialInstitutionMetricsIngestError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _date(name: str, value: Any) -> date:
    if isinstance(value, datetime):
        raise FinancialInstitutionMetricsIngestError(f"{name} date olmali")
    if isinstance(value, date):
        result = value
    elif isinstance(value, str):
        try:
            result = date.fromisoformat(value.strip())
        except ValueError as exc:
            raise FinancialInstitutionMetricsIngestError(f"{name} ISO date olmali") from exc
    else:
        raise FinancialInstitutionMetricsIngestError(f"{name} date olmali")
    if (result.month, result.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
        raise FinancialInstitutionMetricsIngestError(f"{name} gercek takvim ceyrek sonu olmali")
    return result


def _aware_datetime(name: str, value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise FinancialInstitutionMetricsIngestError(f"{name} ISO datetime olmali") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FinancialInstitutionMetricsIngestError(f"{name} timezone iceren datetime olmali")
    return value.astimezone(timezone.utc)


def _strict_int(name: str, value: Any, *, minimum: int = 0) -> int:
    if _is_bool_like(value) or not isinstance(value, int):
        raise FinancialInstitutionMetricsIngestError(f"{name} Python int olmali")
    if value < minimum:
        raise FinancialInstitutionMetricsIngestError(f"{name} {minimum} degerinden kucuk olamaz")
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
        raise FinancialInstitutionMetricsIngestError(f"{name} sonlu sayi olmali")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FinancialInstitutionMetricsIngestError(f"{name} sonlu sayi olmali") from exc
    if not result.is_finite() or abs(result) > MAX_ABS_NUMBER:
        raise FinancialInstitutionMetricsIngestError(f"{name} sonlu sayi sinirinda olmali")
    if minimum is not None:
        if strict_minimum and result <= minimum:
            raise FinancialInstitutionMetricsIngestError(f"{name} {minimum} degerinden buyuk olmali")
        if not strict_minimum and result < minimum:
            raise FinancialInstitutionMetricsIngestError(f"{name} {minimum} degerinden kucuk olamaz")
    if maximum is not None and result > maximum:
        raise FinancialInstitutionMetricsIngestError(f"{name} {maximum} degerini asamaz")
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
        raise FinancialInstitutionMetricsIngestError(f"{name} 64 karakter hex SHA256 olmali")
    return text


def _json_object(name: str, value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise FinancialInstitutionMetricsIngestError(f"{name} JSON nesnesi olmali")
    for key in value:
        if not isinstance(key, str):
            raise FinancialInstitutionMetricsIngestError(f"{name} JSON anahtarlari metin olmali")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise FinancialInstitutionMetricsIngestError(f"{name} JSON serilestirilebilir olmali") from exc
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise FinancialInstitutionMetricsIngestError(f"{name} 1MB sinirini asamaz")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise FinancialInstitutionMetricsIngestError(f"{name} JSON nesnesi olmali")
    return decoded


def _canonical_sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FinancialInstitutionMetricsRecord:
    """
    Banka disi finansal kurulus metrik kaydi.

    Kaynak zinciri ZORUNLU: source_document_id + source_sha256 + lineage.
    Point-in-time secim icin published_at ve version_sequence tutulur; ayni
    period_end icin birden fazla surum geldiginde deterministik secim
    (published_at DESC, version_sequence DESC, source_document_id DESC) yapilir.
    """
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
    average_equity: Decimal
    net_income_ttm: Decimal
    total_assets: Decimal
    finance_receivables: Decimal
    npl_gross: Decimal | None
    provisions: Decimal | None
    net_finance_income_ttm: Decimal | None
    funding_cost_ttm: Decimal | None
    operating_expenses_ttm: Decimal | None
    capital_adequacy_ratio: Decimal | None
    source_confidence: Decimal
    source_type: str
    source_document_id: str
    source_uri: str | None
    source_sha256: str
    metrics_profile: str
    metrics_version: int
    lineage: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FinancialInstitutionMetricsRecord":
        if not isinstance(raw, Mapping):
            raise FinancialInstitutionMetricsIngestError("financial institution row mapping olmali")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(raw) - allowed
        if unknown:
            raise FinancialInstitutionMetricsIngestError(
                "financial institution bilinmeyen alanlar: "
                + ", ".join(sorted(repr(x) for x in unknown))
            )
        period = _date("period_end", raw.get("period_end"))
        published = _aware_datetime("published_at", raw.get("published_at"))
        if period > published.astimezone(ISTANBUL_TZ).date():
            raise FinancialInstitutionMetricsIngestError(
                "period_end published_at tarihinden sonra olamaz"
            )
        business = _strict_text("business_type", raw.get("business_type"), uppercase=True)
        if business not in SUPPORTED_BUSINESS_TYPES:
            raise FinancialInstitutionMetricsIngestError(
                "business_type FACTORING, LEASING veya CONSUMER_FINANCE olmali"
            )
        equity = _decimal("total_equity", raw.get("total_equity"),
                          minimum=Decimal("0"), strict_minimum=True)
        assets = _decimal("total_assets", raw.get("total_assets"),
                          minimum=Decimal("0"), strict_minimum=True)
        receivables = _decimal("finance_receivables", raw.get("finance_receivables"),
                               minimum=Decimal("0"))
        if equity > assets:
            raise FinancialInstitutionMetricsIngestError("total_equity total_assets'i asamaz")
        if receivables > assets:
            raise FinancialInstitutionMetricsIngestError("finance_receivables total_assets'i asamaz")
        npl = _optional_decimal("npl_gross", raw.get("npl_gross"), minimum=Decimal("0"))
        if npl is not None and npl > receivables:
            raise FinancialInstitutionMetricsIngestError("npl_gross finance_receivables'i asamaz")
        provisions = _optional_decimal("provisions", raw.get("provisions"), minimum=Decimal("0"))
        # Karsilik varsa takip alacagi da bilinmeli; aksi halde kapsam orani turetilemez
        # ve "karsilik var ama neye karsilik" belirsiz kalir.
        if provisions is not None and npl is None:
            raise FinancialInstitutionMetricsIngestError(
                "provisions verildiyse npl_gross de verilmeli"
            )
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
            shares_out=_decimal("shares_out", raw.get("shares_out"),
                                minimum=Decimal("0"), strict_minimum=True),
            share_basis=_strict_text("share_basis", raw.get("share_basis"), uppercase=True),
            total_equity=equity,
            average_equity=_decimal("average_equity", raw.get("average_equity"),
                                    minimum=Decimal("0"), strict_minimum=True),
            net_income_ttm=_decimal("net_income_ttm", raw.get("net_income_ttm")),
            total_assets=assets,
            finance_receivables=receivables,
            npl_gross=npl,
            provisions=provisions,
            net_finance_income_ttm=_optional_decimal(
                "net_finance_income_ttm", raw.get("net_finance_income_ttm")),
            funding_cost_ttm=_optional_decimal(
                "funding_cost_ttm", raw.get("funding_cost_ttm"), minimum=Decimal("0")),
            operating_expenses_ttm=_optional_decimal(
                "operating_expenses_ttm", raw.get("operating_expenses_ttm"), minimum=Decimal("0")),
            capital_adequacy_ratio=_optional_decimal(
                "capital_adequacy_ratio", raw.get("capital_adequacy_ratio"), minimum=Decimal("0")),
            source_confidence=_decimal("source_confidence", raw.get("source_confidence", 1),
                                       minimum=Decimal("0"), maximum=Decimal("1")),
            source_type=_strict_text("source_type", raw.get("source_type"), uppercase=True),
            source_document_id=_strict_text("source_document_id", raw.get("source_document_id")),
            source_uri=uri,
            source_sha256=_sha("source_sha256", raw.get("source_sha256")),
            metrics_profile=_strict_text("metrics_profile", raw.get("metrics_profile")),
            metrics_version=_strict_int("metrics_version", raw.get("metrics_version"), minimum=1),
            lineage=_json_object("lineage", raw.get("lineage", {})),
        )

    def canonical_dict(self) -> dict[str, Any]:
        def opt(value: Decimal | None) -> str | None:
            return None if value is None else str(value)
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
            "average_equity": str(self.average_equity),
            "net_income_ttm": str(self.net_income_ttm),
            "total_assets": str(self.total_assets),
            "finance_receivables": str(self.finance_receivables),
            "npl_gross": opt(self.npl_gross),
            "provisions": opt(self.provisions),
            "net_finance_income_ttm": opt(self.net_finance_income_ttm),
            "funding_cost_ttm": opt(self.funding_cost_ttm),
            "operating_expenses_ttm": opt(self.operating_expenses_ttm),
            "capital_adequacy_ratio": opt(self.capital_adequacy_ratio),
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


def load_financial_institution_metrics_jsonl(
    path: str | Path,
) -> list[FinancialInstitutionMetricsRecord]:
    source = Path(path)
    if not source.is_file():
        raise FinancialInstitutionMetricsIngestError(
            f"financial institution metrics dosyasi bulunamadi: {source}"
        )
    records: list[FinancialInstitutionMetricsRecord] = []
    seen: dict[str, str] = {}
    with source.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise FinancialInstitutionMetricsIngestError(
                    f"{source}:{line_no} gecersiz JSON"
                ) from exc
            record = FinancialInstitutionMetricsRecord.from_mapping(payload)
            # Ayni kimlik FARKLI icerikle gelirse sert reddedilir.
            onceki = seen.get(record.metrics_id)
            if onceki is not None and onceki != record.canonical_sha256:
                raise FinancialInstitutionMetricsIngestError(
                    f"{source}:{line_no} ayni metrics_id farkli icerikle geldi: {record.metrics_id}"
                )
            seen[record.metrics_id] = record.canonical_sha256
            records.append(record)
    return records


INSERT_SQL = """
INSERT INTO core.financial_institution_metrics_snapshots (
  metrics_id, ticker, period_end, published_at, version_tag, version_sequence,
  business_type, accounting_profile, accounting_version, currency,
  shares_out, share_basis, total_equity, average_equity, net_income_ttm,
  total_assets, finance_receivables, npl_gross, provisions,
  net_finance_income_ttm, funding_cost_ttm, operating_expenses_ttm,
  capital_adequacy_ratio, source_confidence, source_type, source_document_id,
  source_uri, source_sha256, metrics_profile, metrics_version, lineage,
  canonical_sha256
) VALUES (
  %(metrics_id)s, %(ticker)s, %(period_end)s, %(published_at)s, %(version_tag)s,
  %(version_sequence)s, %(business_type)s, %(accounting_profile)s,
  %(accounting_version)s, %(currency)s, %(shares_out)s, %(share_basis)s,
  %(total_equity)s, %(average_equity)s, %(net_income_ttm)s, %(total_assets)s,
  %(finance_receivables)s, %(npl_gross)s, %(provisions)s,
  %(net_finance_income_ttm)s, %(funding_cost_ttm)s, %(operating_expenses_ttm)s,
  %(capital_adequacy_ratio)s, %(source_confidence)s, %(source_type)s,
  %(source_document_id)s, %(source_uri)s, %(source_sha256)s, %(metrics_profile)s,
  %(metrics_version)s, %(lineage)s::jsonb, %(canonical_sha256)s
)
ON CONFLICT (metrics_id) DO NOTHING
"""


def persist_financial_institution_metrics_records(
    conn: Any, rows: Iterable[FinancialInstitutionMetricsRecord]
) -> int:
    """
    ATOMIK ve idempotent kalicilik.

    `with conn:` ZORUNLUDUR: psycopg2'de islemi commit eden budur. Yalnizca
    `with conn.cursor()` kullanilirsa satirlar yazilmis GORUNUR (persisted_count
    doner) ama commit edilmez ve baglanti kapaninca kaybolur -- sessizce yanlis
    basari raporu. Ayrica hepsi-ya-hicbiri atomikligi de buradan gelir.
    """
    payloads = []
    for record in rows:
        if not isinstance(record, FinancialInstitutionMetricsRecord):
            raise FinancialInstitutionMetricsIngestError(
                "kayit FinancialInstitutionMetricsRecord olmali"
            )
        # Kanonik olmayan kayit reddedilir (yeniden kurulum ayni nesneyi vermeli).
        if FinancialInstitutionMetricsRecord.from_mapping(record.canonical_dict()) != record:
            raise FinancialInstitutionMetricsIngestError(
                "financial institution metrics record kanonik degil"
            )
        data = record.canonical_dict()
        data["metrics_id"] = record.metrics_id
        data["canonical_sha256"] = record.canonical_sha256
        data["lineage"] = json.dumps(dict(record.lineage), sort_keys=True, separators=(",", ":"))
        payloads.append(data)
    if not payloads:
        return 0
    with conn:
        with conn.cursor() as cur:
            for payload in payloads:
                cur.execute(INSERT_SQL, payload)
    return len(payloads)
