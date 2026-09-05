from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.ingest.api.mkk_kap import KapApiProtocolError
from src.ingest.api.semantic_facts import SemanticFactMapper
from src.ingest.kap_bulk_exact_semantic_mapping import build_bulk_exact_semantic_config
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


def _map_nonfin_single(cell: KapBulkFinancialCell):
    mapped_at = datetime(2021, 4, 30, 19, tzinfo=ISTANBUL)
    facts = bulk_cells_to_financial_facts(
        _report(),
        [cell],
        ticker="TEST",
        extracted_at=mapped_at,
    )
    return SemanticFactMapper(
        build_bulk_exact_semantic_config("NONFIN")
    ).map_facts(facts, mapped_at=mapped_at)


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


def test_exact_mapping_accepts_correct_role_row_and_label_identity() -> None:
    mapped = _map_nonfin_single(_cell())

    assert len(mapped) == 1
    assert mapped[0].canonical_field == "TOTAL_ASSETS"
    assert mapped[0].value == Decimal("123000")


@pytest.mark.parametrize("mutation", ["role", "row", "label"])
def test_exact_mapping_rejects_role_row_or_label_identity_mutation(mutation: str) -> None:
    cell = _cell()
    if mutation == "role":
        cell = replace(
            cell,
            table_role="holding_role_210015",
            fact_code="holding_role_210015:129",
        )
    elif mutation == "row":
        cell = replace(
            cell,
            row_number=130,
            fact_code="general_role_210015:130",
        )
    else:
        cell = replace(cell, label_tr="Ödenmiş Sermaye")

    with pytest.raises(KapApiProtocolError, match="hic kalem uretmedi"):
        _map_nonfin_single(cell)
