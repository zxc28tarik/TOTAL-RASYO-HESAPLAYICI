from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
import re
from typing import Iterable

from src.ingest.api.kap_financial_facts import KapFinancialFact
from src.ingest.kap_bulk_financial_export import (
    KapBulkExportError,
    KapBulkExportReport,
    KapBulkFinancialCell,
)


SOURCE = "KAP_BULK_FINANCIAL_EXPORT"
MAPPING_PROFILE = "KAP_BULK_HTML_EXACT_LABEL_V1"
MAPPING_VERSION = 1
_ROLE_ROW_RE = re.compile(r"^[A-Za-z0-9_-]+:[1-9][0-9]*$")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def exact_label_fact_code(role_row_code: str, label_tr: str) -> str:
    if not isinstance(role_row_code, str) or _ROLE_ROW_RE.fullmatch(role_row_code) is None:
        raise KapBulkExportError("bulk role:row fact_code gecersiz")
    if not isinstance(label_tr, str) or not label_tr.strip():
        raise KapBulkExportError("bulk Turkce etiketi bos")
    label = " ".join(label_tr.split())
    return f"{role_row_code.upper()}|LABEL_SHA256={sha256(label.encode('utf-8')).hexdigest()}"


def bulk_cells_to_financial_facts(
    report: KapBulkExportReport,
    cells: Iterable[KapBulkFinancialCell],
    *,
    ticker: str,
    extracted_at: datetime,
    version_tag: str = "BULK_CAPTURED_LATEST",
    version_sequence: int = 0,
) -> tuple[KapFinancialFact, ...]:
    if not isinstance(report, KapBulkExportReport):
        raise TypeError("report KapBulkExportReport olmali")
    ticker = str(ticker).strip().upper()
    if re.fullmatch(r"[A-Z0-9]{2,12}", ticker) is None:
        raise KapBulkExportError("ticker kanonik BIST kodu olmali")
    if not isinstance(extracted_at, datetime) or extracted_at.tzinfo is None or extracted_at.utcoffset() is None:
        raise KapBulkExportError("extracted_at timezone icermeli")
    if report.published_at > extracted_at + timedelta(minutes=5):
        raise KapBulkExportError("extracted_at yayin anindan once")
    if not isinstance(version_tag, str) or not version_tag.strip():
        raise KapBulkExportError("version_tag dolu metin olmali")
    if isinstance(version_sequence, bool) or not isinstance(version_sequence, int) or version_sequence < 0:
        raise KapBulkExportError("version_sequence gecersiz")
    rows = tuple(cells)
    if not rows:
        raise KapBulkExportError("en az bir bulk hucre gerekli")
    output: list[KapFinancialFact] = []
    for cell in rows:
        if not isinstance(cell, KapBulkFinancialCell):
            raise KapBulkExportError("cells yalniz KapBulkFinancialCell icermeli")
        if cell.notification_id != report.notification_id:
            raise KapBulkExportError("bulk hucre baska bildirime ait")
        if cell.statement_scope != report.statement_scope:
            raise KapBulkExportError("bulk hucre scope raporla eslesmiyor")
        if cell.currency != report.presentation_currency or cell.unit_scale != report.presentation_scale:
            raise KapBulkExportError("bulk hucre para birimi/olcegi raporla eslesmiyor")
        expected = f"{cell.table_role}:{cell.row_number}"
        if cell.fact_code != expected:
            raise KapBulkExportError("bulk hucre role/row kimligi celiskili")
        fact_code = exact_label_fact_code(cell.fact_code, cell.label_tr)
        dimensions: dict[str, object] = {
            "archive_name": report.archive_name,
            "archive_sha256": report.archive_sha256,
            "member_name": report.member_name,
            "member_sha256": report.member_sha256,
            "source_entity_code": report.source_entity_code,
            "company_name": report.company_name,
            "bulk_role_row_code": cell.fact_code,
            "table_role": cell.table_role,
            "row_number": cell.row_number,
            "column_index": cell.column_index,
            "label_tr": " ".join(cell.label_tr.split()),
            "context_label": cell.context_label,
        }
        key_context = {
            "fact_code": fact_code,
            "period_start": None if cell.period_start is None else cell.period_start.isoformat(),
            "period_end": cell.period_end.isoformat(),
            "currency": cell.currency,
            "statement_scope": cell.statement_scope,
            "dimensions": dimensions,
        }
        output.append(KapFinancialFact(
            source=SOURCE, disclosure_id=f"KAP:{report.notification_id}",
            mapping_profile=MAPPING_PROFILE, mapping_version=MAPPING_VERSION,
            fact_key=sha256(_canonical_json(key_context).encode("utf-8")).hexdigest(),
            ticker=ticker, published_at=report.published_at,
            version_tag=version_tag.strip(), version_sequence=version_sequence,
            fact_code=fact_code, period_start=cell.period_start, period_end=cell.period_end,
            currency=cell.currency, unit_scale=cell.unit_scale,
            raw_value_text=cell.raw_value_text, normalized_value=cell.normalized_value,
            scaled_value=cell.scaled_value, statement_scope=cell.statement_scope,
            dimensions=dimensions, extracted_at=extracted_at,
        ))
    keys = [item.fact_key for item in output]
    if len(keys) != len(set(keys)):
        raise KapBulkExportError("bulk adapter duplicate fact_key uretti")
    return tuple(sorted(output, key=lambda item: (item.period_end, item.fact_code, item.fact_key)))
