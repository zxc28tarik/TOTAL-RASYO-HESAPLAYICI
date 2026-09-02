from datetime import date, datetime
from decimal import Decimal
import hashlib
import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from src.ingest.kap_bulk_financial_export import (
    KapBulkExportError,
    KapBulkExportReport,
    parse_kap_bulk_export_report,
    parse_kap_bulk_financial_cells,
)


ARCHIVE = Path(os.environ.get("KAP_BULK_REAL_ARCHIVE", "__missing_kap_archive__"))
MEMBER = "AEFES_934813_2021_1.xls"
REAL_ARCHIVE = pytest.mark.skipif(
    not ARCHIVE.exists(), reason="real KAP archive is an external validation artifact"
)


@pytest.fixture(scope="module")
def parsed():
    raw_archive = ARCHIVE.read_bytes()
    with ZipFile(ARCHIVE) as bundle:
        raw = bundle.read(MEMBER)
    report = parse_kap_bulk_export_report(
        archive_name=ARCHIVE.name,
        archive_sha256=hashlib.sha256(raw_archive).hexdigest(),
        member_name=MEMBER,
        raw_html=raw,
    )
    return raw, report, parse_kap_bulk_financial_cells(report, raw)


@REAL_ARCHIVE
def test_real_kap_2021_q1_report_metadata(parsed) -> None:
    _, report, _ = parsed
    assert report.notification_id == 934813
    assert report.source_entity_code == "AEFES"
    assert report.report_year == 2021
    assert report.report_period == "Q1"
    assert report.statement_scope == "CONSOLIDATED"
    assert report.presentation_currency == "TRY"
    assert report.presentation_scale == 1000
    assert report.member_size_bytes == 1281611


@REAL_ARCHIVE
def test_real_kap_cash_cell_preserves_label_context_and_scale(parsed) -> None:
    _, _, cells = parsed
    cash = [
        row for row in cells
        if row.fact_code == "general_role_210015:4" and row.period_end == date(2021, 3, 31)
    ]
    assert len(cash) == 1
    assert cash[0].label_tr == "Nakit ve Nakit Benzerleri"
    assert cash[0].normalized_value == Decimal("7801366")
    assert cash[0].scaled_value == Decimal("7801366000")
    assert cash[0].period_start is None


@REAL_ARCHIVE
def test_member_name_period_mismatch_fails_closed(parsed) -> None:
    raw, report, _ = parsed
    with pytest.raises(KapBulkExportError, match="yil/periyot"):
        parse_kap_bulk_export_report(
            archive_name=report.archive_name,
            archive_sha256=report.archive_sha256,
            member_name="AEFES_934813_2021_2.xls",
            raw_html=raw,
        )


def _parse_member(member_name: str):
    raw_archive = ARCHIVE.read_bytes()
    with ZipFile(ARCHIVE) as bundle:
        raw = bundle.read(member_name)
    report = parse_kap_bulk_export_report(
        archive_name=ARCHIVE.name,
        archive_sha256=hashlib.sha256(raw_archive).hexdigest(),
        member_name=member_name,
        raw_html=raw,
    )
    return report, parse_kap_bulk_financial_cells(report, raw)


@REAL_ARCHIVE
def test_real_legacy_visible_values_without_title_are_parsed() -> None:
    _, cells = _parse_member("DOBUR_931938_2021_1.xls")

    cash = next(
        cell
        for cell in cells
        if cell.fact_code == "general_role_210015:4"
        and cell.period_end == date(2021, 3, 31)
    )

    assert cash.raw_value_text == "20.844.870"
    assert cash.normalized_value == Decimal("20844870")
    assert cash.scaled_value == Decimal("20844870")


@REAL_ARCHIVE
def test_real_hyphenated_bank_role_is_valid_parser_identity() -> None:
    _, cells = _parse_member("ALBRK-ALK_934792_2021_1.xls")

    assert cells
    assert {cell.table_role for cell in cells} == {
        "par-banks_role_210013",
        "par-banks_role_210501",
        "par-banks_role_310019",
        "par-banks_role_420005",
        "par-banks_role_510009",
        "par-banks_role_610005",
    }


def _synthetic_report(raw: bytes) -> KapBulkExportReport:
    return KapBulkExportReport(
        archive_name="fixture.zip",
        member_name="TEST_1_2021_1.xls",
        archive_sha256="a" * 64,
        member_sha256=hashlib.sha256(raw).hexdigest(),
        member_size_bytes=len(raw),
        notification_id=1,
        source_entity_code="TEST",
        company_name="TEST A.Ş.",
        published_at=datetime.fromisoformat("2021-04-30T18:00:00+03:00"),
        report_year=2021,
        report_period="Q1",
        statement_scope="SOLO",
        presentation_currency="TRY",
        presentation_scale=1,
        source_url="https://kap.org.tr/tr/Bildirim/1",
    )


def _single_cell_html(role: str, value_html: str) -> bytes:
    return f"""<html><body>
    <table class="financial-table tbl_{role}"><tbody>
      <tr><td></td><td></td><td></td><td class="context-header">Cari Dönem | 31.03.2021</td></tr>
      <tr class="{role}-row-4 data-input-row">
        <td></td>
        <td class="taxonomy-field-title"><div class="content-tr">Nakit ve Nakit Benzerleri</div></td>
        <td></td>
        <td class="taxonomy-context-value">{value_html}</td>
      </tr>
    </tbody></table></body></html>""".encode("utf-8")


def test_legacy_visible_turkish_number_is_parsed_without_title() -> None:
    raw = _single_cell_html(
        "general_role_210015",
        '<div class="taxonomy-label-field">20.844.870</div>',
    )

    cells = parse_kap_bulk_financial_cells(_synthetic_report(raw), raw)

    assert len(cells) == 1
    assert cells[0].raw_value_text == "20.844.870"
    assert cells[0].normalized_value == Decimal("20844870")


def test_hyphenated_role_identity_is_accepted() -> None:
    raw = _single_cell_html(
        "par-banks_role_210013",
        '<div class="taxonomy-label-field" title="5084874">5.084.874</div>',
    )

    cells = parse_kap_bulk_financial_cells(_synthetic_report(raw), raw)

    assert len(cells) == 1
    assert cells[0].table_role == "par-banks_role_210013"
    assert cells[0].normalized_value == Decimal("5084874")


def test_portable_report_metadata_contract() -> None:
    raw = b"""<html><body>
    <h1>TEST A.S.</h1><h1>Finansal Rapor</h1>
    <div>G\xc3\xb6nderim Tarihi: 30.04.2021 18:00:00</div>
    <div>Bildirim Tipi: FR</div><div>Y\xc4\xb1l: 2021</div><div>Periyot: 1</div>
    <table class="financial-header-table">
      <tr><td class="financial-header-title">Sunum Para Birimi</td><td>1.000 TL</td></tr>
      <tr><td class="financial-header-title">Finansal Tablo Niteli\xc4\x9fi</td><td>Konsolide</td></tr>
    </table>
    <table class="financial-table tbl_general_role_210015"></table>
    </body></html>"""

    report = parse_kap_bulk_export_report(
        archive_name="fixture.zip",
        archive_sha256="a" * 64,
        member_name="TEST_1_2021_1.xls",
        raw_html=raw,
    )

    assert report.company_name == "TEST A.S."
    assert report.report_period == "Q1"
    assert report.statement_scope == "CONSOLIDATED"
    assert report.presentation_currency == "TRY"
    assert report.presentation_scale == 1000
