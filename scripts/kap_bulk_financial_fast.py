"""Experimental bulk parser with indexed grid traversal, original semantics unchanged.

This helper shares the original parser types/validation and preserves row/header
ordering. Differential real-source receipts must precede experimental use.
Production entry points continue using the original implementation.
"""
from datetime import datetime
import hashlib
from bs4 import BeautifulSoup
from src.ingest.kap_bulk_financial_export import (
    KapBulkExportReport, KapBulkFinancialCell, KapBulkExportError,
    _raw_text, _direct_rows, _table_grid, _ROW_RE, _cell_text, _DATE_RE, _decimal,
)

def parse_kap_bulk_financial_cells_fast(
    report: KapBulkExportReport, raw_html: bytes, *, parser_backend: str = "html.parser"
) -> tuple[KapBulkFinancialCell, ...]:
    if not isinstance(report, KapBulkExportReport):
        raise TypeError("report KapBulkExportReport olmali")
    raw, text = _raw_text(raw_html)
    if hashlib.sha256(raw).hexdigest() != report.member_sha256:
        raise KapBulkExportError("raw_html report.member_sha256 ile eslesmiyor")
    if parser_backend not in {"html.parser", "lxml"}:
        raise ValueError("unsupported experimental parser backend")
    soup = BeautifulSoup(text, parser_backend)
    output: list[KapBulkFinancialCell] = []
    for table in soup.select("table.financial-table"):
        roles = [name[4:] for name in table.get("class", ()) if name.startswith("tbl_")]
        if len(roles) != 1:
            raise KapBulkExportError("financial table role kimligi tek olmali")
        role = roles[0]
        rows = _direct_rows(table)
        grid = _table_grid(rows)
        # Index the same grid and ordering once instead of rescanning it per value.
        columns_by_cell = {}
        contexts_by_row = {}
        headers_by_column = {}
        for (rr, cc), cell in sorted(grid.items()):
            columns_by_cell.setdefault((rr, id(cell)), []).append(cc)
            classes = set(cell.get("class", ()))
            if "taxonomy-context-cell" in classes:
                contexts_by_row.setdefault(rr, []).append(cell)
            if classes.intersection({"context-header", "taxonomy-dimensional-header-cell"}):
                headers_by_column.setdefault(cc, []).append((rr, cell))
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
                columns = columns_by_cell.get((row_index, id(value_cell)), [])
                if not columns:
                    raise KapBulkExportError("value cell grid kolonu bulunamadi")
                column = columns[0]
                header_parts: list[str] = []
                seen: set[int] = set()
                for context_cell in contexts_by_row.get(row_index, ()):
                    if id(context_cell) in seen:
                        continue
                    seen.add(id(context_cell))
                    value = _cell_text(context_cell)
                    if value:
                        header_parts.append(value)
                for header_row, header in headers_by_column.get(column, ()):
                    if header_row >= row_index:
                        break
                    if id(header) in seen:
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
