from __future__ import annotations

"""Fail-closed parser for KAP public bulk financial-table ZIP members."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import re
from typing import Optional
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag


ISTANBUL = ZoneInfo("Europe/Istanbul")
MAX_EXPORT_MEMBER_BYTES = 25_000_000
_MEMBER_RE = re.compile(
    r"^(?P<entity>[A-Z0-9-]{2,32})_(?P<notification_id>[1-9][0-9]*)_"
    r"(?P<year>[0-9]{4})_(?P<period>[1-4])\.xls$"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_DATE_RE = re.compile(r"([0-3][0-9]\.[01][0-9]\.[12][0-9]{3})")
_ROW_RE = re.compile(r"^(?P<role>[A-Za-z0-9_-]+)-row-(?P<row>[0-9]+)$")
_PERIODS = {"1": "Q1", "2": "Q2", "3": "Q3", "4": "Q4"}


class KapBulkExportError(ValueError):
    pass


@dataclass(frozen=True)
class KapBulkExportReport:
    archive_name: str
    member_name: str
    archive_sha256: str
    member_sha256: str
    member_size_bytes: int
    notification_id: int
    source_entity_code: str
    company_name: str
    published_at: datetime
    report_year: int
    report_period: str
    statement_scope: str
    presentation_currency: Optional[str]
    presentation_scale: int
    source_url: str


@dataclass(frozen=True)
class KapBulkFinancialCell:
    notification_id: int
    statement_scope: str
    table_role: str
    row_number: int
    fact_code: str
    column_index: int
    label_tr: str
    context_label: str
    period_start: Optional[date]
    period_end: date
    raw_value_text: str
    normalized_value: Decimal
    scaled_value: Decimal
    currency: Optional[str]
    unit_scale: int


def _raw_text(raw_html: bytes) -> tuple[bytes, str]:
    if not isinstance(raw_html, (bytes, bytearray)):
        raise KapBulkExportError("raw_html bytes olmali")
    raw = bytes(raw_html)
    if not raw or len(raw) > MAX_EXPORT_MEMBER_BYTES:
        raise KapBulkExportError("export member boyutu gecersiz")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise KapBulkExportError("export member UTF-8 olmali") from exc
    if not text.lstrip().lower().startswith("<html"):
        raise KapBulkExportError(".xls export UTF-8 HTML olmali")
    return raw, text


def _header_lines(soup: BeautifulSoup) -> list[str]:
    return [" ".join(line.split()) for line in soup.get_text("\n").splitlines() if line.strip()]


def _prefixed(lines: list[str], prefix: str) -> str:
    values = [line[len(prefix):].strip() for line in lines if line.startswith(prefix)]
    if len(values) != 1 or not values[0]:
        raise KapBulkExportError(f"{prefix.rstrip(':')} etiketi tam bir kez bulunmali")
    return values[0]


def _financial_header(soup: BeautifulSoup, label: str) -> Optional[str]:
    values: list[str] = []
    for cell in soup.select("table.financial-header-table td.financial-header-title"):
        if " ".join(cell.get_text(" ", strip=True).split()) != label:
            continue
        sibling = cell.find_next_sibling("td")
        if sibling is not None:
            text = " ".join(sibling.get_text(" ", strip=True).split())
            if text:
                values.append(text)
    distinct = list(dict.fromkeys(values))
    if len(distinct) > 1:
        raise KapBulkExportError(f"{label} celiskili birden fazla deger iceriyor")
    return distinct[0] if distinct else None


def _scope(value: Optional[str]) -> str:
    if value is None:
        raise KapBulkExportError("Finansal Tablo Niteligi eksik")
    normalized = " ".join(value.upper().split())
    if normalized in {"KONSOLİDE", "KONSOLIDE"}:
        return "CONSOLIDATED"
    if normalized in {"KONSOLİDE OLMAYAN", "KONSOLIDE OLMAYAN"}:
        return "SOLO"
    raise KapBulkExportError("Finansal Tablo Niteligi taninmiyor")


def _presentation(value: Optional[str]) -> tuple[Optional[str], int]:
    if value is None:
        raise KapBulkExportError("Sunum Para Birimi eksik")
    normalized = " ".join(value.upper().split())
    match = re.fullmatch(r"(?:(?P<scale>[0-9][0-9.]*)\s+)?(?P<currency>TL|TRY|USD|EUR)", normalized)
    if match is None:
        raise KapBulkExportError("Sunum Para Birimi taninmiyor")
    scale = int((match.group("scale") or "1").replace(".", ""))
    if scale <= 0 or scale > 10**12:
        raise KapBulkExportError("Sunum Para Birimi olcegi sinir disi")
    currency = match.group("currency")
    return ("TRY" if currency in {"TL", "TRY"} else currency, scale)


def parse_kap_bulk_export_report(
    *, archive_name: str, archive_sha256: str, member_name: str, raw_html: bytes
) -> KapBulkExportReport:
    if not isinstance(archive_name, str) or not archive_name.strip():
        raise KapBulkExportError("archive_name dolu olmali")
    if not isinstance(archive_sha256, str) or _SHA256_RE.fullmatch(archive_sha256) is None:
        raise KapBulkExportError("archive_sha256 64 karakter kucuk harf hex olmali")
    match = _MEMBER_RE.fullmatch(member_name)
    if match is None:
        raise KapBulkExportError("member_name KAP export kimligiyle eslesmiyor")
    raw, text = _raw_text(raw_html)
    header_text = text.split('<table class="financial-table', 1)[0]
    soup = BeautifulSoup(header_text, "html.parser")
    lines = _header_lines(soup)
    sent_text = _prefixed(lines, "Gönderim Tarihi:")
    if _prefixed(lines, "Bildirim Tipi:").upper() != "FR":
        raise KapBulkExportError("export bildirimi FR degil")
    year_text = _prefixed(lines, "Yıl:")
    period_text = _prefixed(lines, "Periyot:")
    if year_text != match.group("year") or period_text != match.group("period"):
        raise KapBulkExportError("dosya adi ile HTML yil/periyot kimligi eslesmiyor")
    try:
        published = datetime.strptime(sent_text, "%d.%m.%Y %H:%M:%S").replace(tzinfo=ISTANBUL)
    except ValueError as exc:
        raise KapBulkExportError("Gonderim Tarihi formati gecersiz") from exc
    headings = [" ".join(item.get_text(" ", strip=True).split()) for item in soup.find_all("h1")]
    indexes = [index for index, value in enumerate(headings) if value == "Finansal Rapor"]
    if len(indexes) != 1 or indexes[0] == 0:
        raise KapBulkExportError("Finansal Rapor basligi ve sirket unvani bulunamadi")
    company_name = headings[indexes[0] - 1]
    currency, scale = _presentation(_financial_header(soup, "Sunum Para Birimi"))
    notification_id = int(match.group("notification_id"))
    return KapBulkExportReport(
        archive_name=archive_name.strip(), member_name=member_name,
        archive_sha256=archive_sha256, member_sha256=hashlib.sha256(raw).hexdigest(),
        member_size_bytes=len(raw), notification_id=notification_id,
        source_entity_code=match.group("entity"), company_name=company_name,
        published_at=published, report_year=int(year_text),
        report_period=_PERIODS[period_text],
        statement_scope=_scope(_financial_header(soup, "Finansal Tablo Niteliği")),
        presentation_currency=currency, presentation_scale=scale,
        source_url=f"https://kap.org.tr/tr/Bildirim/{notification_id}",
    )


def _direct_rows(table: Tag) -> list[Tag]:
    parent = table.find("tbody", recursive=False) or table
    return [row for row in parent.find_all("tr", recursive=False)]


def _direct_cells(row: Tag) -> list[Tag]:
    return [cell for cell in row.find_all(["td", "th"], recursive=False)]


def _table_grid(rows: list[Tag]) -> dict[tuple[int, int], Tag]:
    grid: dict[tuple[int, int], Tag] = {}
    for row_index, row in enumerate(rows):
        column = 0
        for cell in _direct_cells(row):
            while (row_index, column) in grid:
                column += 1
            try:
                colspan = max(1, int(cell.get("colspan", 1)))
                rowspan = max(1, int(cell.get("rowspan", 1)))
            except (TypeError, ValueError) as exc:
                raise KapBulkExportError("HTML tablo span degeri gecersiz") from exc
            for rr in range(row_index, row_index + rowspan):
                for cc in range(column, column + colspan):
                    if (rr, cc) in grid:
                        raise KapBulkExportError("HTML tablo span cakismasi")
                    grid[(rr, cc)] = cell
            column += colspan
    return grid


def _cell_text(cell: Tag) -> str:
    preferred = cell.select_one(".content-tr")
    return " ".join((preferred or cell).get_text(" ", strip=True).split())


def _decimal(value: str) -> Decimal:
    text = value.strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        raise KapBulkExportError("sayisal hucre bos")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?[0-9]{1,3}(?:\.[0-9]{3})+", text):
        text = text.replace(".", "")
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise KapBulkExportError(f"sayisal hucre cozumlenemedi: {value!r}") from exc
    if not parsed.is_finite():
        raise KapBulkExportError("sayisal hucre sonlu olmali")
    return parsed


def parse_kap_bulk_financial_cells(
    report: KapBulkExportReport, raw_html: bytes
) -> tuple[KapBulkFinancialCell, ...]:
    if not isinstance(report, KapBulkExportReport):
        raise TypeError("report KapBulkExportReport olmali")
    raw, text = _raw_text(raw_html)
    if hashlib.sha256(raw).hexdigest() != report.member_sha256:
        raise KapBulkExportError("raw_html report.member_sha256 ile eslesmiyor")
    soup = BeautifulSoup(text, "html.parser")
    output: list[KapBulkFinancialCell] = []
    for table in soup.select("table.financial-table"):
        roles = [name[4:] for name in table.get("class", ()) if name.startswith("tbl_")]
        if len(roles) != 1:
            raise KapBulkExportError("financial table role kimligi tek olmali")
        role = roles[0]
        rows = _direct_rows(table)
        grid = _table_grid(rows)
        for row_index, row in enumerate(rows):
            classes = set(row.get("class", ()))
            if "data-input-row" not in classes:
                continue
            identities = [m for value in classes if (m := _ROW_RE.fullmatch(value))]
            if len(identities) != 1 or identities[0].group("role") != role:
                raise KapBulkExportError("data row role kimligi gecersiz")
            row_number = int(identities[0].group("row"))
            label_cell = row.select_one("td.taxonomy-field-title")
            label = "" if label_cell is None else _cell_text(label_cell)
            if not label:
                raise KapBulkExportError("data row Turkce etiketi bos")
            for value_cell in row.select("td.taxonomy-context-value"):
                numeric = value_cell.select_one("[title]")
                if numeric is None:
                    numeric = value_cell.select_one(".taxonomy-label-field")
                    raw_value = "" if numeric is None else _cell_text(numeric)
                else:
                    raw_value = str(numeric.get("title", "")).strip()
                if not raw_value:
                    continue
                columns = sorted(cc for (rr, cc), cell in grid.items() if rr == row_index and cell is value_cell)
                if not columns:
                    raise KapBulkExportError("value cell grid kolonu bulunamadi")
                column = columns[0]
                header_parts: list[str] = []
                seen: set[int] = set()
                for (rr, _), context_cell in sorted(grid.items()):
                    if rr != row_index or id(context_cell) in seen:
                        continue
                    if "taxonomy-context-cell" not in set(context_cell.get("class", ())):
                        continue
                    seen.add(id(context_cell))
                    value = _cell_text(context_cell)
                    if value:
                        header_parts.append(value)
                for header_row in range(row_index):
                    header = grid.get((header_row, column))
                    if header is None or id(header) in seen:
                        continue
                    if not set(header.get("class", ())).intersection({"context-header", "taxonomy-dimensional-header-cell"}):
                        continue
                    seen.add(id(header))
                    value = _cell_text(header)
                    if value:
                        header_parts.append(value)
                context = " | ".join(header_parts)
                dates = _DATE_RE.findall(context)
                if not dates:
                    raise KapBulkExportError("numeric cell period context tarihi icermiyor")
                parsed_dates = [datetime.strptime(value, "%d.%m.%Y").date() for value in dates]
                normalized = _decimal(raw_value)
                output.append(KapBulkFinancialCell(
                    notification_id=report.notification_id, statement_scope=report.statement_scope,
                    table_role=role, row_number=row_number, fact_code=f"{role}:{row_number}",
                    column_index=column, label_tr=label, context_label=context,
                    period_start=parsed_dates[0] if len(parsed_dates) > 1 else None,
                    period_end=parsed_dates[-1], raw_value_text=raw_value,
                    normalized_value=normalized,
                    scaled_value=normalized * report.presentation_scale,
                    currency=report.presentation_currency, unit_scale=report.presentation_scale,
                ))
    if not output:
        raise KapBulkExportError("financial export hic sayisal hucre uretmedi")
    keys = [(row.table_role, row.row_number, row.column_index, row.context_label) for row in output]
    if len(keys) != len(set(keys)):
        raise KapBulkExportError("financial export duplicate fact context iceriyor")
    return tuple(sorted(output, key=lambda row: (row.period_end, row.table_role, row.row_number, row.context_label)))
