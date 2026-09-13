from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib

import pytest

from src.ingest.api.mkk_kap import KapApiProtocolError
from src.ingest.api.semantic_facts import SemanticFactMapper
from src.ingest.kap_bulk_bank_exact_semantic_mapping import (
    build_bulk_exact_bank_semantic_config,
)
from src.ingest.kap_bulk_financial_export import KapBulkExportReport, KapBulkFinancialCell
from src.ingest.kap_bulk_semantic_adapter import (
    bulk_cells_to_financial_facts,
    bulk_context_dimensions,
)


PUBLISHED = datetime(2021, 5, 5, 18, 0, tzinfo=timezone.utc)
MAPPED = datetime(2021, 5, 5, 19, 0, tzinfo=timezone.utc)


def report() -> KapBulkExportReport:
    return KapBulkExportReport(
        archive_name="KAP_2021_3A.zip",
        member_name="BANK_1_2021_1.xls",
        archive_sha256="a" * 64,
        member_sha256="b" * 64,
        member_size_bytes=100,
        notification_id=1,
        source_entity_code="BANK",
        company_name="BANK A.Ş.",
        published_at=PUBLISHED,
        report_year=2021,
        report_period="Q1",
        statement_scope="CONSOLIDATED",
        presentation_currency="TRY",
        presentation_scale=1,
        source_url="https://kap.org.tr/tr/Bildirim/1",
    )


def cell(
    role: str,
    row: int,
    label: str,
    context: str,
    *,
    value: str = "100",
    period_start: date | None = None,
    period_end: date = date(2021, 3, 31),
) -> KapBulkFinancialCell:
    number = Decimal(value)
    return KapBulkFinancialCell(
        notification_id=1,
        statement_scope="CONSOLIDATED",
        table_role=role,
        row_number=row,
        fact_code=f"{role}:{row}",
        column_index=3,
        label_tr=label,
        context_label=context,
        period_start=period_start,
        period_end=period_end,
        raw_value_text=value,
        normalized_value=number,
        scaled_value=number,
        currency="TRY",
        unit_scale=1,
    )


def map_one(raw_cell: KapBulkFinancialCell):
    raw = bulk_cells_to_financial_facts(
        report(), [raw_cell], ticker="AKBNK", extracted_at=PUBLISHED
    )
    return SemanticFactMapper(build_bulk_exact_bank_semantic_config()).map_facts(
        raw, mapped_at=MAPPED
    )


def test_context_dimensions_are_date_independent_and_structured() -> None:
    instant = cell(
        "banks_role_210011", 82, "ÖZKAYNAKLAR",
        "Cari Dönem 31.03.2021 | Toplam",
    )
    ytd = cell(
        "banks_role_310017", 64, "DÖNEM NET KARI VEYA ZARARI",
        "Cari Dönem 01.01.2021 - 31.03.2021",
        period_start=date(2021, 1, 1),
    )
    quarter = cell(
        "banks_role_310017", 64, "DÖNEM NET KARI VEYA ZARARI",
        "Cari Dönem 3 Aylık 01.01.2021 - 31.03.2021",
        period_start=date(2021, 1, 1),
    )
    previous = cell(
        "banks_role_210011", 82, "ÖZKAYNAKLAR",
        "Önceki Dönem 31.12.2020 | Toplam",
        period_end=date(2020, 12, 31),
    )

    assert bulk_context_dimensions(instant) == {
        "context_period_side": "CURRENT",
        "context_period_kind": "INSTANT",
        "context_member": "Toplam",
    }
    assert bulk_context_dimensions(ytd)["context_period_kind"] == "YTD"
    assert bulk_context_dimensions(quarter)["context_period_kind"] == "QUARTER"
    assert bulk_context_dimensions(previous)["context_period_side"] == "PREVIOUS"


def test_bank_total_equity_requires_current_total_column() -> None:
    out = map_one(cell(
        "banks_role_210011", 82, "ÖZKAYNAKLAR",
        "Cari Dönem 31.03.2021 | Toplam",
    ))
    assert [(row.canonical_field, row.value) for row in out] == [("TOTAL_EQUITY", Decimal("100"))]

    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        map_one(cell(
            "banks_role_210011", 82, "ÖZKAYNAKLAR",
            "Cari Dönem 31.03.2021 | TP",
        ))
    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        map_one(cell(
            "banks_role_210011", 82, "ÖZKAYNAKLAR",
            "Önceki Dönem 31.12.2020 | Toplam",
            period_end=date(2020, 12, 31),
        ))


def test_bank_net_income_rejects_three_month_context_for_ytd_semantic() -> None:
    out = map_one(cell(
        "banks_role_310017", 64, "DÖNEM NET KARI VEYA ZARARI",
        "Cari Dönem 01.01.2021 - 31.03.2021",
        period_start=date(2021, 1, 1),
    ))
    assert out[0].canonical_field == "NET_INCOME"

    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        map_one(cell(
            "banks_role_310017", 64, "DÖNEM NET KARI VEYA ZARARI",
            "Cari Dönem 3 Aylık 01.01.2021 - 31.03.2021",
            period_start=date(2021, 1, 1),
        ))


def test_current_dividend_is_row39_total_equity_only_and_abs_signed() -> None:
    out = map_one(cell(
        "banks_role_610003", 39, "Dağıtılan Temettü",
        "Cari Dönem 01.01.2021 - 31.03.2021 | Toplam Özkaynak",
        value="-25",
        period_start=date(2021, 1, 1),
    ))
    assert [(row.canonical_field, row.value) for row in out] == [("DIVIDENDS_PAID", Decimal("25"))]

    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        map_one(cell(
            "banks_role_610003", 19, "Dağıtılan Temettü",
            "Önceki Dönem 01.01.2020 - 31.03.2020 | Toplam Özkaynak",
            period_start=date(2020, 1, 1), period_end=date(2020, 3, 31),
        ))
    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        map_one(cell(
            "banks_role_610003", 39, "Dağıtılan Temettü",
            "Cari Dönem 01.01.2021 - 31.03.2021 | Geçmiş Dönem Karı / (Zararı)",
            period_start=date(2021, 1, 1),
        ))


def test_participation_bank_schema_shift_is_label_locked() -> None:
    old = map_one(cell(
        "par-banks_role_210013", 74, "ÖZKAYNAKLAR",
        "Cari Dönem 31.03.2021 | Toplam",
    ))
    shifted = map_one(cell(
        "par-banks_role_210013", 77, "ÖZKAYNAKLAR",
        "Cari Dönem 31.03.2021 | Toplam",
    ))
    assert old[0].canonical_field == shifted[0].canonical_field == "TOTAL_EQUITY"

    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        map_one(cell(
            "par-banks_role_210013", 74, "Krediler",
            "Cari Dönem 30.06.2026 | Toplam",
            period_end=date(2021, 3, 31),
        ))


def test_adapter_persists_structured_context_in_lineage_dimensions() -> None:
    raw = bulk_cells_to_financial_facts(
        report(),
        [cell(
            "par-banks_role_610005", 39, "Dağıtılan Temettü",
            "Cari Dönem 01.01.2021 - 31.03.2021 | Toplam Özkaynak",
            period_start=date(2021, 1, 1),
        )],
        ticker="ALBRK",
        extracted_at=PUBLISHED,
    )[0]
    assert raw.dimensions["context_period_side"] == "CURRENT"
    assert raw.dimensions["context_period_kind"] == "YTD"
    assert raw.dimensions["context_member"] == "Toplam Özkaynak"
    assert len(raw.fact_key) == 64
