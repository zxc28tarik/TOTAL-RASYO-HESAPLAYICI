from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Iterable, Optional, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ISTANBUL = ZoneInfo("Europe/Istanbul")
MAX_PUBLIC_KAP_HTML_BYTES = 25_000_000
_NOTIFICATION_PATH_RE = re.compile(r"/(?:tr/)?Bildirim/(\d+)(?:$|[?#/])", re.IGNORECASE)
_QUERY_NOTIFICATION_RE = re.compile(r"/(?:tr/)?Bildirim/(\d+)(?:$|[?#/])", re.IGNORECASE)


class PublicKapPitError(ValueError):
    pass


@dataclass(frozen=True)
class PublicKapSnapshot:
    notification_id: int
    source_url: str
    fetched_at: datetime
    raw_sha256: str
    raw_size_bytes: int
    raw_html: bytes


@dataclass(frozen=True)
class PublicKapNotificationMetadata:
    notification_id: int
    ticker: str
    published_at: datetime
    disclosure_type: str
    source_url: str
    raw_sha256: str
    report_year: Optional[int] = None
    report_period: Optional[str] = None
    is_correction: Optional[bool] = None
    previous_notification_date: Optional[str] = None


@dataclass(frozen=True)
class PublicKapFinancialReport:
    notification_id: int
    ticker: str
    published_at: datetime
    report_year: int
    report_period: str
    disclosure_type: str
    source_url: str
    raw_sha256: str
    is_correction: Optional[bool] = None
    previous_notification_date: Optional[str] = None

    @property
    def report_key(self) -> tuple[str, int, str]:
        return (self.ticker, self.report_year, self.report_period)


class _TextAndLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.hrefs.append(value.strip())

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.text.append(value)


def _require_aware(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PublicKapPitError(f"{name} timezone-aware datetime olmali")
    return value


def _notification_id(value: object) -> int:
    if isinstance(value, bool):
        raise PublicKapPitError("notification_id pozitif int olmali")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PublicKapPitError("notification_id pozitif int olmali") from exc
    if parsed <= 0:
        raise PublicKapPitError("notification_id pozitif int olmali")
    return parsed


def _source_url(value: object, notification_id: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PublicKapPitError("source_url dolu metin olmali")
    text = value.strip()
    parsed = urlparse(text)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {"kap.org.tr", "www.kap.org.tr"}:
        raise PublicKapPitError("source_url resmi KAP HTTPS URL'i olmali")
    match = _NOTIFICATION_PATH_RE.search(parsed.path + ("/" if not parsed.path.endswith("/") else ""))
    if not match or int(match.group(1)) != notification_id:
        raise PublicKapPitError("source_url notification_id ile eslesmiyor")
    return text


def capture_public_kap_snapshot(
    *,
    notification_id: int,
    source_url: str,
    raw_html: bytes,
    fetched_at: datetime,
) -> PublicKapSnapshot:
    nid = _notification_id(notification_id)
    url = _source_url(source_url, nid)
    fetched = _require_aware("fetched_at", fetched_at)
    if not isinstance(raw_html, (bytes, bytearray)):
        raise PublicKapPitError("raw_html bytes olmali")
    payload = bytes(raw_html)
    if not payload:
        raise PublicKapPitError("raw_html bos olamaz")
    if len(payload) > MAX_PUBLIC_KAP_HTML_BYTES:
        raise PublicKapPitError("raw_html guvenli boyut sinirini asiyor")
    try:
        payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PublicKapPitError("raw_html UTF-8 olmali") from exc
    return PublicKapSnapshot(
        notification_id=nid,
        source_url=url,
        fetched_at=fetched,
        raw_sha256=hashlib.sha256(payload).hexdigest(),
        raw_size_bytes=len(payload),
        raw_html=payload,
    )


def extract_notification_ids_from_query_html(raw_html: bytes | str) -> tuple[int, ...]:
    if isinstance(raw_html, bytes):
        try:
            text = raw_html.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise PublicKapPitError("query HTML UTF-8 olmali") from exc
    elif isinstance(raw_html, str):
        text = raw_html
    else:
        raise PublicKapPitError("query HTML bytes veya str olmali")
    parser = _TextAndLinkParser()
    parser.feed(text)
    out: list[int] = []
    seen: set[int] = set()
    for href in parser.hrefs:
        match = _QUERY_NOTIFICATION_RE.search(href + ("/" if not href.endswith("/") else ""))
        if not match:
            continue
        nid = int(match.group(1))
        if nid not in seen:
            seen.add(nid)
            out.append(nid)
    return tuple(out)


def _next_value(tokens: Sequence[str], label: str) -> str:
    matches = [index for index, token in enumerate(tokens) if token == label]
    if len(matches) != 1:
        raise PublicKapPitError(f"{label} etiketi tam bir kez bulunmali")
    index = matches[0] + 1
    if index >= len(tokens):
        raise PublicKapPitError(f"{label} degeri eksik")
    return tokens[index]


def _optional_next_value(tokens: Sequence[str], label: str) -> Optional[str]:
    matches = [index for index, token in enumerate(tokens) if token == label]
    if not matches:
        return None
    if len(matches) != 1:
        raise PublicKapPitError(f"{label} etiketi birden fazla bulundu")
    index = matches[0] + 1
    if index >= len(tokens):
        raise PublicKapPitError(f"{label} degeri eksik")
    return tokens[index]


def _parse_correction_flag(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized.startswith("evet") or normalized.startswith("yes"):
        return True
    if normalized.startswith("hayır") or normalized.startswith("hayir") or normalized.startswith("no"):
        return False
    raise PublicKapPitError("duzeltme bayragi taninmiyor")


def parse_public_kap_notification_metadata(
    snapshot: PublicKapSnapshot,
    *,
    expected_ticker: str,
) -> PublicKapNotificationMetadata:
    if not isinstance(snapshot, PublicKapSnapshot):
        raise TypeError("snapshot PublicKapSnapshot olmali")
    if not isinstance(expected_ticker, str) or not expected_ticker.strip():
        raise PublicKapPitError("expected_ticker dolu metin olmali")
    ticker = expected_ticker.strip().upper()
    text = snapshot.raw_html.decode("utf-8", errors="strict")
    parser = _TextAndLinkParser()
    parser.feed(text)
    tokens = parser.text
    if ticker not in {token.strip().upper() for token in tokens}:
        raise PublicKapPitError("expected_ticker snapshot icinde dogrulanamadi")

    published_text = _next_value(tokens, "Gönderim Tarihi")
    try:
        published = datetime.strptime(published_text, "%d.%m.%Y %H:%M:%S").replace(tzinfo=ISTANBUL)
    except ValueError as exc:
        raise PublicKapPitError("Gönderim Tarihi beklenen formatta degil") from exc
    if published > snapshot.fetched_at.astimezone(ISTANBUL):
        raise PublicKapPitError("published_at fetched_at sonrasinda; kaynak zamani tutarsiz")

    disclosure_type = _next_value(tokens, "Bildirim Tipi").strip().upper()
    if not disclosure_type:
        raise PublicKapPitError("Bildirim Tipi bos olamaz")

    year_text = _next_value(tokens, "Yıl").strip()
    report_year: Optional[int]
    if year_text in {"-", "--"}:
        report_year = None
    elif re.fullmatch(r"\d{4}", year_text):
        report_year = int(year_text)
    else:
        raise PublicKapPitError("Yıl dort haneli veya bos isaret olmali")

    period_text = _next_value(tokens, "Periyot").strip()
    report_period = None if period_text in {"", "-", "--"} else period_text

    correction = _parse_correction_flag(_optional_next_value(tokens, "Yapılan Açıklama Düzeltme mi?"))
    previous_date = _optional_next_value(tokens, "Konuya İlişkin Daha Önce Yapılan Açıklamanın Tarihi")
    if previous_date is not None:
        previous_date = previous_date.strip() or None
        if previous_date in {"-", "--"}:
            previous_date = None
    if correction is True:
        if previous_date is None:
            raise PublicKapPitError("duzeltme bildirimi onceki bildirim tarihini icermeli")
        try:
            previous = datetime.strptime(previous_date, "%d.%m.%Y").date()
        except ValueError as exc:
            raise PublicKapPitError("onceki bildirim tarihi GG.AA.YYYY formatinda olmali") from exc
        if previous > published.date():
            raise PublicKapPitError("onceki bildirim tarihi published_at sonrasinda olamaz")

    return PublicKapNotificationMetadata(
        notification_id=snapshot.notification_id,
        ticker=ticker,
        published_at=published,
        disclosure_type=disclosure_type,
        source_url=snapshot.source_url,
        raw_sha256=snapshot.raw_sha256,
        report_year=report_year,
        report_period=report_period,
        is_correction=correction,
        previous_notification_date=previous_date,
    )


def parse_public_kap_financial_report(
    snapshot: PublicKapSnapshot,
    *,
    expected_ticker: str,
) -> PublicKapFinancialReport:
    meta = parse_public_kap_notification_metadata(snapshot, expected_ticker=expected_ticker)
    if meta.disclosure_type != "FR":
        raise PublicKapPitError("bildirim finansal rapor (FR) degil")
    if meta.report_year is None:
        raise PublicKapPitError("FR bildirimi Yıl icermeli")
    if meta.report_period is None:
        raise PublicKapPitError("FR bildirimi Periyot icermeli")
    return PublicKapFinancialReport(
        notification_id=meta.notification_id,
        ticker=meta.ticker,
        published_at=meta.published_at,
        report_year=meta.report_year,
        report_period=meta.report_period,
        disclosure_type=meta.disclosure_type,
        source_url=meta.source_url,
        raw_sha256=meta.raw_sha256,
        is_correction=meta.is_correction,
        previous_notification_date=meta.previous_notification_date,
    )


def snapshot_manifest_row(snapshot: PublicKapSnapshot) -> dict[str, object]:
    if not isinstance(snapshot, PublicKapSnapshot):
        raise TypeError("snapshot PublicKapSnapshot olmali")
    return {
        "contract": "PUBLIC_KAP_PIT_RAW_SNAPSHOT_V1",
        "notification_id": snapshot.notification_id,
        "source_url": snapshot.source_url,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "raw_sha256": snapshot.raw_sha256,
        "raw_size_bytes": snapshot.raw_size_bytes,
    }


def _validate_report_identity(row: PublicKapFinancialReport) -> None:
    _notification_id(row.notification_id)
    _source_url(row.source_url, row.notification_id)
    _require_aware("published_at", row.published_at)
    if not isinstance(row.ticker, str) or not row.ticker.strip() or row.ticker != row.ticker.strip().upper():
        raise PublicKapPitError("ticker kanonik buyuk harf olmali")
    if row.disclosure_type != "FR":
        raise PublicKapPitError("financial report disclosure_type FR olmali")
    if isinstance(row.report_year, bool) or not isinstance(row.report_year, int) or not 1900 <= row.report_year <= 2100:
        raise PublicKapPitError("report_year gecersiz")
    if not isinstance(row.report_period, str) or not row.report_period.strip():
        raise PublicKapPitError("report_period dolu metin olmali")
    if not isinstance(row.raw_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", row.raw_sha256):
        raise PublicKapPitError("raw_sha256 64 karakter kucuk harf hex olmali")


def validate_financial_report_set(records: Iterable[PublicKapFinancialReport]) -> tuple[PublicKapFinancialReport, ...]:
    rows = tuple(records)
    by_id: dict[int, PublicKapFinancialReport] = {}
    for row in rows:
        if not isinstance(row, PublicKapFinancialReport):
            raise TypeError("records yalniz PublicKapFinancialReport icermeli")
        _validate_report_identity(row)
        prior = by_id.get(row.notification_id)
        if prior is not None:
            if prior != row:
                raise PublicKapPitError("ayni notification_id farkli icerikle tekrarlandi")
            raise PublicKapPitError("duplicate notification_id")
        by_id[row.notification_id] = row
    return tuple(sorted(rows, key=lambda item: (item.published_at, item.notification_id)))


def select_visible_financial_report_versions(
    records: Iterable[PublicKapFinancialReport],
    *,
    cutoff_at: datetime,
) -> tuple[PublicKapFinancialReport, ...]:
    cutoff = _require_aware("cutoff_at", cutoff_at)
    rows = validate_financial_report_set(records)
    visible = [row for row in rows if row.published_at <= cutoff]
    latest: dict[tuple[str, int, str], PublicKapFinancialReport] = {}
    for row in visible:
        key = row.report_key
        prior = latest.get(key)
        if prior is None or (row.published_at, row.notification_id) > (prior.published_at, prior.notification_id):
            latest[key] = row
    return tuple(sorted(latest.values(), key=lambda item: item.report_key))
