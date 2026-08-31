from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.ingest.kap_bulk_financial_export import (
    KapBulkExportError,
    KapBulkExportReport,
    KapBulkFinancialCell,
)
from src.ingest.kap_bulk_semantic_adapter import bulk_cells_to_financial_facts


ISTANBUL = ZoneInfo("Europe/Istanbul")


def _report() -> KapBulkExportReport:
    return KapBulkExportReport(
        archive_name="fixture.zip",
        member_name="TEST_1_2021_1.xls",
        archive_sha256="a" * 64,
        member_sha256="b" * 64,
        member_size_bytes=123,
        notification_id=1,
        source_entity_code="TEST",
        company_name="TEST A.Ş.",
        published_at=datetime(2021, 4, 30, 18, tzinfo=ISTANBUL),
        report_year=2021,
        report_period="Q1",
        statement_scope="SOLO",
        presentation_currency="TRY",
        presentation_scale=1000,
        source_url="https://kap.org.tr/tr/Bildirim/1",
    )


def _cell() -> KapBulkFinancialCell:
    return KapBulkFinancialCell(
        notification_id=1,
        statement_scope="SOLO",
        table_role="general_role_210015",
        row_number=129,
        fact_code="general_role_210015:129",
        column_index=4,
        label_tr="TOPLAM VARLIKLAR",
        context_label="Cari Dönem | 31.03.2021",
        period_start=None,
        period_end=date(2021, 3, 31),
        raw_value_text="123",
        normalized_value=Decimal("123"),
        scaled_value=Decimal("123000"),
        currency="TRY",
        unit_scale=1000,
    )


def test_adapter_preserves_archive_and_exact_label_lineage() -> None:
    facts = bulk_cells_to_financial_facts(
        _report(),
        [_cell()],
        ticker="test",
        extracted_at=datetime(2021, 4, 30, 19, tzinfo=ISTANBUL),
    )

    assert len(facts) == 1
    assert facts[0].ticker == "TEST"
    assert facts[0].fact_code.startswith(
        "GENERAL_ROLE_210015:129|LABEL_SHA256="
    )
    assert facts[0].dimensions["archive_sha256"] == "a" * 64
    assert facts[0].dimensions["member_sha256"] == "b" * 64
    assert facts[0].scaled_value == Decimal("123000")


def test_adapter_rejects_capture_before_publication() -> None:
    report = _report()

    with pytest.raises(KapBulkExportError, match="yayin anindan once"):
        bulk_cells_to_financial_facts(
            report,
            [_cell()],
            ticker="TEST",
            extracted_at=report.published_at - timedelta(minutes=6),
        )
