from __future__ import annotations

"""Public KAP financial-report discovery for historical PIT source building.

This module uses the public KAP web application's own disclosure endpoints.  It
is deliberately separate from the credentialed MKK API Portal client: no API
key, guessed private endpoint, current-state fallback, or financial-value
synthesis is allowed here.

The client only discovers disclosures whose KAP subject is exactly
``Finansal Rapor`` and preserves every publication/correction as a separate
version.  Selecting the version visible at a historical cutoff is a downstream
PIT operation.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import math
import re
import time
from typing import Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


KAP_BASE_URL = "https://www.kap.org.tr"
KAP_DISCLOSURE_QUERY_URL = f"{KAP_BASE_URL}/tr/api/disclosure/members/byCriteria"
KAP_DISCLOSURE_DETAIL_URL = f"{KAP_BASE_URL}/tr/api/notification/attachment-detail/{{disclosure_index}}"
KAP_QUERY_REFERER = f"{KAP_BASE_URL}/tr/bildirim-sorgu"
KAP_FINANCIAL_SUBJECT = "Finansal Rapor"
ISTANBUL = ZoneInfo("Europe/Istanbul")

MAX_QUERY_WINDOW_DAYS = 7
MAX_QUERY_RESULTS = 2000
MAX_RETRIES = 6
MAX_TIMEOUT_SECONDS = 120.0
MAX_RESPONSE_BYTES = 50_000_000
MAX_DETAIL_BODY_BYTES = 20_000_000

_TAXONOMY_TAG_RE = re.compile(
    r"\b(?:ifrs-full|kap-fr|tms|tfrs|trfrs|bobi-frs|bdk|oda)_[A-Za-z0-9][A-Za-z0-9._-]*\b"
)
_TICKER_RE = re.compile(r"^[A-Z0-9]{2,12}$")


class KapPublicFinancialError(RuntimeError):
    pass


@dataclass(frozen=True)
class KapFinancialDisclosureSummary:
    disclosure_index: int
    published_at: datetime
    stock_codes: tuple[str, ...]
    year: int
    rule_type: str
    period: str | None
    subject: str
    modify_status: str | None
    is_old_kap: bool
    raw_sha256: str


@dataclass(frozen=True)
class KapFinancialAttachment:
    object_id: str
    file_name: str
    file_extension: str | None


@dataclass(frozen=True)
class KapFinancialDisclosureDetail:
    disclosure_index: int
    disclosure_id: str
    published_at: datetime
    stock_codes: tuple[str, ...]
    title: str
    body_html: tuple[str, ...]
    attachments: tuple[KapFinancialAttachment, ...]
    related_disclosure_id: str | None
    is_changed: object
    raw_sha256: str


@dataclass(frozen=True)
class KapTaxonomyRow:
    row_index: int
    taxonomy_tags: tuple[str, ...]
    cells: tuple[str, ...]
    row_sha256: str


def _canonical_json_sha256(value: object) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise KapPublicFinancialError("KAP JSON kanoniklestirilemedi") from exc
    return hashlib.sha256(payload).hexdigest()


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KapPublicFinancialError(f"{field} dolu metin olmali")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise KapPublicFinancialError(f"{field} metin veya null olmali")
    text = value.strip()
    return text or None


def _strict_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise KapPublicFinancialError(f"{field} Python bool olmali")
    return value


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise KapPublicFinancialError(f"{field} pozitif tam sayi olmali")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise KapPublicFinancialError(f"{field} pozitif tam sayi olmali") from exc
    if number <= 0 or str(number) != str(value).strip():
        # JSON integer values stringify canonically.  The second check rejects
        # float-like/text aliases such as 1.0 that can blur source identity.
        if not (type(value) is int and value > 0):
            raise KapPublicFinancialError(f"{field} pozitif tam sayi olmali")
    return number


def _parse_kap_datetime(value: object, field: str) -> datetime:
    text = _required_text(value, field)
    parsed: datetime | None = None
    for fmt in ("%d.%m.%Y %H:%M:%S", "%Y.%m.%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        raise KapPublicFinancialError(f"{field} KAP datetime formati gecersiz: {text}")
    return parsed.replace(tzinfo=ISTANBUL)


def _stock_codes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, str):
        raise KapPublicFinancialError("stockCodes metin veya null olmali")
    parts = [part.strip().upper() for part in re.split(r"[,;\s]+", value) if part.strip()]
    result: list[str] = []
    for ticker in parts:
        if not _TICKER_RE.fullmatch(ticker) or not any(ch.isalpha() for ch in ticker):
            raise KapPublicFinancialError(f"stockCodes gecersiz ticker iceriyor: {ticker}")
        if ticker not in result:
            result.append(ticker)
    return tuple(result)


def _validate_public_url(url: str, field: str) -> str:
    text = _required_text(url, field)
    parsed = urlparse(text)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {"kap.org.tr", "www.kap.org.tr"}:
        raise ValueError(f"{field} resmi KAP HTTPS adresi olmali")
    return text


def _criteria_payload(from_date: date, to_date: date) -> dict[str, object]:
    return {
        "fromDate": from_date.isoformat(),
        "toDate": to_date.isoformat(),
        "memberType": "IGS",
        "mkkMemberOidList": [],
        "inactiveMkkMemberOidList": [],
        "disclosureClass": "",
        "subjectList": [],
        "isLate": "",
        "mainSector": "",
        "sector": "",
        "subSector": "",
        "marketOid": "",
        "index": "",
        "bdkReview": "",
        "bdkMemberOidList": [],
        "year": "",
        "term": "",
        "ruleType": "",
        "period": "",
        "fromSrc": False,
        "srcCategory": "",
        "disclosureIndexList": [],
    }


def _normalize_summary(item: object) -> KapFinancialDisclosureSummary:
    if not isinstance(item, Mapping):
        raise KapPublicFinancialError("KAP disclosure list elemani nesne olmali")
    if _required_text(item.get("subject"), "subject") != KAP_FINANCIAL_SUBJECT:
        raise KapPublicFinancialError("yalniz Finansal Rapor ozeti normalize edilebilir")
    if _required_text(item.get("disclosureClass"), "disclosureClass").upper() != "FR":
        raise KapPublicFinancialError("Finansal Rapor disclosureClass FR olmali")
    if _required_text(item.get("disclosureType"), "disclosureType").upper() != "FR":
        raise KapPublicFinancialError("Finansal Rapor disclosureType FR olmali")
    year = _positive_int(item.get("year"), "year")
    if year < 2000 or year > 2100:
        raise KapPublicFinancialError("year guvenli aralik disinda")
    period_raw = item.get("period")
    if period_raw is None:
        period = None
    elif isinstance(period_raw, (str, int)) and not isinstance(period_raw, bool):
        period = str(period_raw).strip() or None
    else:
        raise KapPublicFinancialError("period metin/tam sayi/null olmali")
    return KapFinancialDisclosureSummary(
        disclosure_index=_positive_int(item.get("disclosureIndex"), "disclosureIndex"),
        published_at=_parse_kap_datetime(item.get("publishDate"), "publishDate"),
        stock_codes=_stock_codes(item.get("stockCodes")),
        year=year,
        rule_type=_required_text(item.get("ruleType"), "ruleType"),
        period=period,
        subject=KAP_FINANCIAL_SUBJECT,
        modify_status=_optional_text(item.get("modifyStatus"), "modifyStatus"),
        is_old_kap=_strict_bool(item.get("isOldKap"), "isOldKap"),
        raw_sha256=_canonical_json_sha256(item),
    )


def _is_financial_statement_item(item: object) -> bool:
    if not isinstance(item, Mapping):
        raise KapPublicFinancialError("KAP disclosure list elemani nesne olmali")
    subject = item.get("subject")
    disclosure_class = item.get("disclosureClass")
    disclosure_type = item.get("disclosureType")
    return (
        isinstance(subject, str)
        and subject.strip() == KAP_FINANCIAL_SUBJECT
        and isinstance(disclosure_class, str)
        and disclosure_class.strip().upper() == "FR"
        and isinstance(disclosure_type, str)
        and disclosure_type.strip().upper() == "FR"
    )


def _normalize_attachment(item: object) -> KapFinancialAttachment:
    if not isinstance(item, Mapping):
        raise KapPublicFinancialError("KAP attachment nesne olmali")
    return KapFinancialAttachment(
        object_id=_required_text(item.get("objId"), "attachment.objId"),
        file_name=_required_text(item.get("fileName"), "attachment.fileName"),
        file_extension=_optional_text(item.get("fileExtension"), "attachment.fileExtension"),
    )


def _normalize_detail(payload: object, expected: KapFinancialDisclosureSummary | None = None) -> KapFinancialDisclosureDetail:
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], Mapping):
        raise KapPublicFinancialError("KAP detail cevabi tek elemanli JSON liste olmali")
    root = payload[0]
    disclosure = root.get("disclosure")
    if not isinstance(disclosure, Mapping):
        raise KapPublicFinancialError("KAP detail disclosure nesnesi eksik")
    basic = disclosure.get("disclosureBasic")
    if not isinstance(basic, Mapping):
        raise KapPublicFinancialError("KAP detail disclosureBasic nesnesi eksik")
    disclosure_index = _positive_int(basic.get("disclosureIndex"), "detail.disclosureIndex")
    published_at = _parse_kap_datetime(basic.get("publishDate"), "detail.publishDate")
    if _required_text(basic.get("disclosureClass"), "detail.disclosureClass").upper() != "FR":
        raise KapPublicFinancialError("KAP detail disclosureClass FR olmali")

    body = root.get("disclosureBody")
    if not isinstance(body, list) or not body or not all(isinstance(x, str) and x.strip() for x in body):
        raise KapPublicFinancialError("KAP financial detail disclosureBody dolu HTML listesi olmali")
    body_bytes = sum(len(x.encode("utf-8")) for x in body)
    if body_bytes > MAX_DETAIL_BODY_BYTES:
        raise KapPublicFinancialError("KAP financial detail body byte sinirini asiyor")

    attachments_raw = root.get("attachments", [])
    if not isinstance(attachments_raw, list):
        raise KapPublicFinancialError("KAP detail attachments liste olmali")
    attachments = tuple(_normalize_attachment(x) for x in attachments_raw)

    detail_stock_codes = _stock_codes(basic.get("stockCode") or basic.get("relatedStocks"))
    if expected is not None:
        if disclosure_index != expected.disclosure_index:
            raise KapPublicFinancialError("detail disclosureIndex list ozetiyle eslesmiyor")
        if published_at != expected.published_at:
            raise KapPublicFinancialError("detail publishDate list ozetiyle eslesmiyor")
        if expected.stock_codes and detail_stock_codes and not set(expected.stock_codes).intersection(detail_stock_codes):
            raise KapPublicFinancialError("detail ticker list ozetiyle eslesmiyor")

    return KapFinancialDisclosureDetail(
        disclosure_index=disclosure_index,
        disclosure_id=_required_text(basic.get("disclosureId"), "detail.disclosureId"),
        published_at=published_at,
        stock_codes=detail_stock_codes or (() if expected is None else expected.stock_codes),
        title=_required_text(basic.get("title"), "detail.title"),
        body_html=tuple(body),
        attachments=attachments,
        related_disclosure_id=_optional_text(basic.get("relatedDisclosureOid"), "detail.relatedDisclosureOid"),
        is_changed=basic.get("isChanged"),
        raw_sha256=_canonical_json_sha256(payload),
    )


def extract_taxonomy_rows(detail: KapFinancialDisclosureDetail) -> tuple[KapTaxonomyRow, ...]:
    """Preserve taxonomy-bearing HTML rows without inventing semantic facts.

    KAP financial forms vary by sector and taxonomy version.  This function
    therefore does *not* guess which numeric cell maps to which production
    metric.  It extracts taxonomy-bearing rows losslessly enough for a later,
    audited semantic-mapping stage.
    """

    if not isinstance(detail, KapFinancialDisclosureDetail):
        raise TypeError("detail KapFinancialDisclosureDetail olmali")
    rows: list[KapTaxonomyRow] = []
    row_index = 0
    for html in detail.body_html:
        soup = BeautifulSoup(html, "html.parser")
        for tr in soup.find_all("tr"):
            blob_parts = [tr.get_text(" ", strip=True)]
            for node in tr.find_all(True):
                for key, raw_value in node.attrs.items():
                    values: Sequence[object]
                    if isinstance(raw_value, list):
                        values = raw_value
                    else:
                        values = [raw_value]
                    for value in values:
                        blob_parts.append(str(key))
                        blob_parts.append(str(value))
            blob = " ".join(blob_parts)
            tags = tuple(dict.fromkeys(_TAXONOMY_TAG_RE.findall(blob)))
            if not tags:
                continue
            cells = tuple(cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"]))
            if not cells:
                cells = (tr.get_text(" ", strip=True),)
            canonical_row = json.dumps(
                {"taxonomy_tags": tags, "cells": cells},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            rows.append(
                KapTaxonomyRow(
                    row_index=row_index,
                    taxonomy_tags=tags,
                    cells=cells,
                    row_sha256=hashlib.sha256(canonical_row).hexdigest(),
                )
            )
            row_index += 1
    if not rows:
        raise KapPublicFinancialError("KAP Finansal Rapor detail icinde taxonomy satiri bulunamadi")
    return tuple(rows)


def taxonomy_tags(detail: KapFinancialDisclosureDetail) -> frozenset[str]:
    return frozenset(tag for row in extract_taxonomy_rows(detail) for tag in row.taxonomy_tags)


class KapPublicFinancialClient:
    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        session: Optional[requests.Session] = None,
        query_url: str = KAP_DISCLOSURE_QUERY_URL,
        detail_url_template: str = KAP_DISCLOSURE_DETAIL_URL,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        min_request_interval_seconds: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.query_url = _validate_public_url(query_url, "query_url")
        self.detail_url_template = _validate_public_url(detail_url_template, "detail_url_template")
        if "{disclosure_index}" not in self.detail_url_template:
            raise ValueError("detail_url_template {disclosure_index} icermeli")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds pozitif sonlu sayi olmali")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
            raise ValueError("timeout_seconds guvenli aralikta olmali")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= MAX_RETRIES:
            raise ValueError("max_retries guvenli aralikta tam sayi olmali")
        if isinstance(min_request_interval_seconds, bool) or not isinstance(min_request_interval_seconds, (int, float)):
            raise ValueError("min_request_interval_seconds sonlu sayi olmali")
        interval = float(min_request_interval_seconds)
        if not math.isfinite(interval) or interval < 0 or interval > 10:
            raise ValueError("min_request_interval_seconds guvenli aralikta olmali")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout
        self.max_retries = max_retries
        self.min_request_interval_seconds = interval
        self.sleeper = sleeper
        self.monotonic = monotonic
        self._last_request_started: float | None = None

    def _pace(self) -> None:
        now = self.monotonic()
        if self._last_request_started is not None:
            remaining = self.min_request_interval_seconds - (now - self._last_request_started)
            if remaining > 0:
                self.sleeper(remaining)
                now = self.monotonic()
        self._last_request_started = now

    def _request_json(self, method: str, url: str, *, payload: Mapping[str, object] | None = None, referer: str) -> object:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
            "Referer": referer,
            "User-Agent": "total-rasyo-hesaplayici/kap-public-financial-pit",
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                self._pace()
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=None if payload is None else dict(payload),
                    timeout=self.timeout_seconds,
                )
                if response.status_code in self.RETRY_STATUS and attempt < self.max_retries:
                    self.sleeper(min(8.0, 0.5 * (2**attempt)))
                    continue
                response.raise_for_status()
                content = getattr(response, "content", b"")
                if isinstance(content, (bytes, bytearray)) and len(content) > MAX_RESPONSE_BYTES:
                    raise KapPublicFinancialError("KAP JSON cevabi byte sinirini asiyor")
                try:
                    return response.json()
                except ValueError as exc:
                    raise KapPublicFinancialError("KAP public endpoint JSON olmayan cevap dondurdu") from exc
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self.sleeper(min(8.0, 0.5 * (2**attempt)))
        raise KapPublicFinancialError(
            f"KAP public endpoint {self.max_retries + 1} denemede alinamadi"
        ) from last_error

    def list_financial_reports(
        self,
        from_date: date,
        to_date: date,
        *,
        ticker_filter: Iterable[str] | None = None,
    ) -> tuple[KapFinancialDisclosureSummary, ...]:
        if not isinstance(from_date, date) or isinstance(from_date, datetime):
            raise TypeError("from_date date olmali")
        if not isinstance(to_date, date) or isinstance(to_date, datetime):
            raise TypeError("to_date date olmali")
        if to_date < from_date:
            raise ValueError("to_date from_date oncesinde olamaz")
        inclusive_days = (to_date - from_date).days + 1
        if inclusive_days > MAX_QUERY_WINDOW_DAYS:
            raise ValueError(f"tek KAP sorgusu en fazla {MAX_QUERY_WINDOW_DAYS} gun olabilir")
        allowed: set[str] | None = None
        if ticker_filter is not None:
            allowed = set()
            for raw in ticker_filter:
                ticker = _required_text(raw, "ticker_filter").upper()
                if not _TICKER_RE.fullmatch(ticker):
                    raise ValueError(f"ticker_filter gecersiz: {ticker}")
                allowed.add(ticker)
            if not allowed:
                raise ValueError("ticker_filter verilirse bos olamaz")

        raw = self._request_json(
            "POST",
            self.query_url,
            payload=_criteria_payload(from_date, to_date),
            referer=KAP_QUERY_REFERER,
        )
        if not isinstance(raw, list):
            raise KapPublicFinancialError("KAP disclosure query cevabi JSON liste olmali")
        if len(raw) >= MAX_QUERY_RESULTS:
            raise KapPublicFinancialError(
                f"KAP query sonuc sayisi {MAX_QUERY_RESULTS} sinirina ulasti; pencere daraltilmali"
            )

        summaries: list[KapFinancialDisclosureSummary] = []
        seen: dict[int, str] = {}
        for item in raw:
            if not _is_financial_statement_item(item):
                continue
            summary = _normalize_summary(item)
            if allowed is not None and not set(summary.stock_codes).intersection(allowed):
                continue
            previous_sha = seen.get(summary.disclosure_index)
            if previous_sha is not None and previous_sha != summary.raw_sha256:
                raise KapPublicFinancialError(
                    f"ayni disclosureIndex farkli payload ile geldi: {summary.disclosure_index}"
                )
            if previous_sha is None:
                seen[summary.disclosure_index] = summary.raw_sha256
                summaries.append(summary)
        summaries.sort(key=lambda x: (x.published_at, x.disclosure_index))
        return tuple(summaries)

    def discover_financial_reports(
        self,
        from_date: date,
        to_date: date,
        *,
        ticker_filter: Iterable[str] | None = None,
    ) -> tuple[KapFinancialDisclosureSummary, ...]:
        if not isinstance(from_date, date) or isinstance(from_date, datetime):
            raise TypeError("from_date date olmali")
        if not isinstance(to_date, date) or isinstance(to_date, datetime):
            raise TypeError("to_date date olmali")
        if to_date < from_date:
            raise ValueError("to_date from_date oncesinde olamaz")
        by_index: dict[int, KapFinancialDisclosureSummary] = {}
        cursor = from_date
        while cursor <= to_date:
            window_end = min(to_date, cursor + timedelta(days=MAX_QUERY_WINDOW_DAYS - 1))
            rows = self.list_financial_reports(cursor, window_end, ticker_filter=ticker_filter)
            for row in rows:
                previous = by_index.get(row.disclosure_index)
                if previous is not None and previous != row:
                    raise KapPublicFinancialError(
                        f"discovery ayni disclosureIndex icin farkli ozet gordu: {row.disclosure_index}"
                    )
                by_index[row.disclosure_index] = row
            cursor = window_end + timedelta(days=1)
        return tuple(sorted(by_index.values(), key=lambda x: (x.published_at, x.disclosure_index)))

    def fetch_detail(self, summary: KapFinancialDisclosureSummary) -> KapFinancialDisclosureDetail:
        if not isinstance(summary, KapFinancialDisclosureSummary):
            raise TypeError("summary KapFinancialDisclosureSummary olmali")
        url = self.detail_url_template.format(disclosure_index=summary.disclosure_index)
        payload = self._request_json(
            "GET",
            url,
            payload=None,
            referer=f"{KAP_BASE_URL}/tr/Bildirim/{summary.disclosure_index}",
        )
        return _normalize_detail(payload, expected=summary)
