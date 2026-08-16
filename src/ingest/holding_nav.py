from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


MAX_ABS_NUMBER = Decimal("1e100")
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")


class HoldingNavIngestError(ValueError):
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
        raise HoldingNavIngestError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _parse_date(name: str, value: Any) -> date:
    if isinstance(value, datetime):
        raise HoldingNavIngestError(f"{name} date olmali")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value.strip())
        except ValueError as exc:
            raise HoldingNavIngestError(f"{name} ISO date olmali") from exc
        return parsed
    raise HoldingNavIngestError(f"{name} date olmali")


def _parse_aware_datetime(name: str, value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise HoldingNavIngestError(f"{name} ISO datetime olmali") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HoldingNavIngestError(f"{name} timezone iceren datetime olmali")
    return value


def _strict_int(name: str, value: Any, *, minimum: int = 0) -> int:
    if _is_bool_like(value) or not isinstance(value, int):
        raise HoldingNavIngestError(f"{name} Python int olmali")
    if value < minimum:
        raise HoldingNavIngestError(f"{name} {minimum} degerinden kucuk olamaz")
    return value


def _decimal(name: str, value: Any, *, minimum: Decimal | None = None, maximum: Decimal | None = None) -> Decimal:
    if _is_bool_like(value) or isinstance(value, (list, tuple, dict, set)):
        raise HoldingNavIngestError(f"{name} sonlu sayi olmali")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise HoldingNavIngestError(f"{name} sonlu sayi olmali") from exc
    if not result.is_finite() or abs(result) > MAX_ABS_NUMBER:
        raise HoldingNavIngestError(f"{name} sonlu sayi sinirinda olmali")
    if minimum is not None and result < minimum:
        raise HoldingNavIngestError(f"{name} {minimum} degerinden kucuk olamaz")
    if maximum is not None and result > maximum:
        raise HoldingNavIngestError(f"{name} {maximum} degerini asamaz")
    return result


def _sha256(name: str, value: Any) -> str:
    text = _strict_text(name, value).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise HoldingNavIngestError(f"{name} 64 karakter hex SHA256 olmali")
    return text


def _validate_json_keys(name: str, value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise HoldingNavIngestError(f"{name} JSON anahtarlari metin olmali: {path}")
            _validate_json_keys(name, item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_keys(name, item, f"{path}[{index}]")


def _json_object(name: str, value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise HoldingNavIngestError(f"{name} JSON nesnesi olmali")
    _validate_json_keys(name, value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HoldingNavIngestError(f"{name} JSON serilestirilebilir olmali") from exc
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise HoldingNavIngestError(f"{name} 1MB sinirini asamaz")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise HoldingNavIngestError(f"{name} JSON nesnesi olmali")
    return decoded


def _canonical_payload_sha(row: Mapping[str, Any]) -> str:
    encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class HoldingNavRecord:
    ticker: str
    nav_asof_date: date
    published_at: datetime
    version_tag: str
    version_sequence: int
    nav_total: Decimal
    shares_out: Decimal
    share_basis: str
    currency: str
    source_confidence: Decimal
    source_type: str
    source_document_id: str
    source_uri: str | None
    source_sha256: str
    nav_profile: str
    nav_version: int
    lineage: Mapping[str, Any]

    @property
    def nav_per_share(self) -> Decimal:
        return self.nav_total / self.shares_out

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "HoldingNavRecord":
        if not isinstance(raw, Mapping):
            raise HoldingNavIngestError("holding NAV row mapping olmali")
        allowed = {
            "ticker", "nav_asof_date", "published_at", "version_tag", "version_sequence",
            "nav_total", "shares_out", "nav_per_share", "share_basis", "currency", "source_confidence",
            "source_type", "source_document_id", "source_uri", "source_sha256",
            "nav_profile", "nav_version", "lineage",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise HoldingNavIngestError(
                "holding NAV bilinmeyen alanlar: " + ", ".join(sorted(repr(v) for v in unknown))
            )
        ticker = _strict_text("ticker", raw.get("ticker"), uppercase=True)
        nav_date = _parse_date("nav_asof_date", raw.get("nav_asof_date"))
        published = _parse_aware_datetime("published_at", raw.get("published_at")).astimezone(timezone.utc)
        if nav_date > published.astimezone(ISTANBUL_TZ).date():
            raise HoldingNavIngestError("nav_asof_date published_at tarihinden sonra olamaz")
        shares = _decimal("shares_out", raw.get("shares_out"), minimum=Decimal("1e-18"))
        raw_total = raw.get("nav_total")
        raw_per_share = raw.get("nav_per_share")
        if raw_total is None and raw_per_share is None:
            raise HoldingNavIngestError("nav_total veya nav_per_share zorunlu")
        if raw_total is not None:
            nav_total = _decimal("nav_total", raw_total, minimum=Decimal("1e-18"))
        else:
            nav_total = _decimal("nav_per_share", raw_per_share, minimum=Decimal("1e-18")) * shares
        if raw_per_share is not None:
            nav_per_share = _decimal("nav_per_share", raw_per_share, minimum=Decimal("1e-18"))
            expected = nav_total / shares
            tolerance = max(Decimal("1e-10"), abs(expected) * Decimal("1e-9"))
            if abs(nav_per_share - expected) > tolerance:
                raise HoldingNavIngestError("nav_total / shares_out ile nav_per_share uyusmuyor")
        source_uri = raw.get("source_uri")
        if source_uri is not None:
            source_uri = _strict_text("source_uri", source_uri)
        return cls(
            ticker=ticker,
            nav_asof_date=nav_date,
            published_at=published,
            version_tag=_strict_text("version_tag", raw.get("version_tag", "ORIGINAL"), uppercase=True),
            version_sequence=_strict_int("version_sequence", raw.get("version_sequence", 1), minimum=1),
            nav_total=nav_total,
            shares_out=shares,
            share_basis=_strict_text("share_basis", raw.get("share_basis"), uppercase=True),
            currency=_strict_text("currency", raw.get("currency", "TRY"), uppercase=True),
            source_confidence=_decimal(
                "source_confidence", raw.get("source_confidence", 1.0),
                minimum=Decimal("0"), maximum=Decimal("1"),
            ),
            source_type=_strict_text("source_type", raw.get("source_type"), uppercase=True),
            source_document_id=_strict_text("source_document_id", raw.get("source_document_id")),
            source_uri=source_uri,
            source_sha256=_sha256("source_sha256", raw.get("source_sha256")),
            nav_profile=_strict_text("nav_profile", raw.get("nav_profile")),
            nav_version=_strict_int("nav_version", raw.get("nav_version"), minimum=1),
            lineage=_json_object("lineage", raw.get("lineage")),
        )

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "nav_asof_date": self.nav_asof_date.isoformat(),
            "published_at": self.published_at.isoformat(),
            "version_tag": self.version_tag,
            "version_sequence": self.version_sequence,
            "nav_total": str(self.nav_total),
            "shares_out": str(self.shares_out),
            "nav_per_share": str(self.nav_per_share),
            "share_basis": self.share_basis,
            "currency": self.currency,
            "source_confidence": str(self.source_confidence),
            "source_type": self.source_type,
            "source_document_id": self.source_document_id,
            "source_uri": self.source_uri,
            "source_sha256": self.source_sha256,
            "nav_profile": self.nav_profile,
            "nav_version": self.nav_version,
            "lineage": dict(self.lineage),
        }

    @property
    def canonical_sha256(self) -> str:
        return _canonical_payload_sha(self.canonical_dict())


def load_holding_nav_jsonl(path: str | Path) -> tuple[HoldingNavRecord, ...]:
    p = Path(path)
    if not p.is_file():
        raise HoldingNavIngestError(f"holding NAV JSONL bulunamadi: {p}")
    rows: list[HoldingNavRecord] = []
    seen: dict[tuple[str, date, datetime, str, int, str], str] = {}
    with p.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            if len(raw_line.encode("utf-8")) > 2_000_000:
                raise HoldingNavIngestError(f"satir {line_no} 2MB sinirini asiyor")
            line = raw_line.strip()
            if not line:
                continue
            if len(rows) >= 100_000:
                raise HoldingNavIngestError("holding NAV JSONL 100000 satir sinirini asiyor")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise HoldingNavIngestError(f"satir {line_no} gecersiz JSON") from exc
            try:
                row = HoldingNavRecord.from_mapping(payload)
            except HoldingNavIngestError as exc:
                raise HoldingNavIngestError(f"satir {line_no}: {exc}") from exc
            key = (
                row.ticker, row.nav_asof_date, row.published_at,
                row.nav_profile, row.nav_version, row.source_document_id,
            )
            prior = seen.get(key)
            if prior is not None and prior != row.canonical_sha256:
                raise HoldingNavIngestError(f"satir {line_no}: ayni NAV kimligi farkli icerik tasiyor")
            if prior is None:
                rows.append(row)
                seen[key] = row.canonical_sha256
    if not rows:
        raise HoldingNavIngestError("holding NAV JSONL bos")
    return tuple(rows)


INSERT_SQL = """
INSERT INTO core.holding_nav_snapshots (
  ticker, nav_asof_date, published_at, version_tag, version_sequence,
  nav_total, shares_out, share_basis, currency, source_confidence, source_type,
  source_document_id, source_uri, source_sha256,
  nav_profile, nav_version, lineage, canonical_sha256
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
ON CONFLICT (
  ticker, nav_asof_date, published_at, nav_profile, nav_version, source_document_id
) DO UPDATE SET canonical_sha256 = EXCLUDED.canonical_sha256
WHERE core.holding_nav_snapshots.canonical_sha256 = EXCLUDED.canonical_sha256
RETURNING canonical_sha256
"""


def persist_holding_nav_records(conn: Any, rows: Iterable[HoldingNavRecord]) -> int:
    items = tuple(rows)
    if not items:
        return 0
    for row in items:
        if not isinstance(row, HoldingNavRecord):
            raise HoldingNavIngestError("persist rows HoldingNavRecord olmali")
        # Revalidate direct dataclass construction.
        if HoldingNavRecord.from_mapping(row.canonical_dict()) != row:
            raise HoldingNavIngestError("holding NAV record kanonik degil")
    with conn:
        with conn.cursor() as cur:
            for row in items:
                cur.execute(INSERT_SQL, (
                    row.ticker, row.nav_asof_date, row.published_at,
                    row.version_tag, row.version_sequence,
                    row.nav_total, row.shares_out, row.share_basis, row.currency,
                    row.source_confidence, row.source_type,
                    row.source_document_id, row.source_uri, row.source_sha256,
                    row.nav_profile, row.nav_version,
                    json.dumps(row.lineage, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
                    row.canonical_sha256,
                ))
                returned = cur.fetchone()
                if not returned or str(returned[0]).lower() != row.canonical_sha256:
                    raise HoldingNavIngestError(
                        f"{row.ticker} ayni NAV kimligi veritabaninda farkli icerik tasiyor"
                    )
    return len(items)
