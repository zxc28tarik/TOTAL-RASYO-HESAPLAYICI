from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.ingest.api.mkk_kap import KapApiProtocolError
from src.ingest.api.semantic_facts import SemanticFactMapper
from src.ingest.kap_bulk_financial_export import KapBulkExportReport, KapBulkFinancialCell
from src.ingest.kap_bulk_insurance_finance_exact_semantic_mapping import (
    build_bulk_exact_financial_semantic_config,
)
from src.ingest.kap_bulk_semantic_adapter import bulk_cells_to_financial_facts


PUBLISHED = datetime(2022, 5, 5, 18, 0, tzinfo=timezone.utc)
MAPPED = datetime(2022, 5, 5, 19, 0, tzinfo=timezone.utc)


def _report() -> KapBulkExportReport:
    return KapBulkExportReport(
        archive_name="KAP_2022_3A.zip",
        member_name="TEST_1_2022_1.xls",
        archive_sha256="a" * 64,
        member_sha256="b" * 64,
        member_size_bytes=100,
        notification_id=1,
        source_entity_code="TEST",
        company_name="TEST A.Ş.",
        published_at=PUBLISHED,
        report_year=2022,
        report_period="Q1",
        statement_scope="CONSOLIDATED",
        presentation_currency="TRY",
        presentation_scale=1,
        source_url="https://kap.org.tr/tr/Bildirim/1",
    )


def _cell(
    role: str,
    row: int,
    label: str,
    context: str,
    *,
    value: str = "100",
    period_start: date | None = None,
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
        period_end=date(2022, 3, 31),
        raw_value_text=value,
        normalized_value=number,
        scaled_value=number,
        currency="TRY",
        unit_scale=1,
    )


def _map(raw_cell: KapBulkFinancialCell):
    raw = bulk_cells_to_financial_facts(
        _report(), [raw_cell], ticker="TEST", extracted_at=PUBLISHED
    )
    return SemanticFactMapper(build_bulk_exact_financial_semantic_config()).map_facts(
        raw, mapped_at=MAPPED
    )


def test_2022_plus_balance_sheet_shift_is_exactly_accepted() -> None:
    cases = [
        (40, "VARLIKLAR TOPLAMI", "TOTAL_ASSETS"),
        (62, "ÖZKAYNAKLAR", "TOTAL_EQUITY"),
        (63, "Ödenmiş Sermaye", "ISSUED_CAPITAL"),
        (15, "Finansman Kredileri", "CONSUMER_FINANCE_RECEIVABLES"),
        (19, "Kiralama İşlemleri (Net)", "LEASING_RECEIVABLES_NET"),
        (24, "Takipteki Alacaklar", "NPL_GROSS"),
    ]
    for row, label, expected in cases:
        out = _map(_cell(
            "finance_role_210014", row, label,
            "Cari Dönem 31.03.2022 | Toplam",
        ))
        assert [fact.canonical_field for fact in out] == [expected]


def test_2022_plus_income_statement_shift_is_exactly_accepted() -> None:
    cases = [
        (81, "DÖNEM NET KARI VEYA ZARARI", "NET_INCOME", "100"),
        (29, "BRÜT KAR (ZARAR)", "NET_FINANCE_INCOME", "100"),
        (21, "FİNANSMAN GİDERLERİ (-)", "FUNDING_COSTS", "-30"),
        (30, "ESAS FAALİYET GİDERLERİ (-)", "OPERATING_EXPENSES", "-20"),
    ]
    for row, label, expected, value in cases:
        out = _map(_cell(
            "finance_role_310020", row, label,
            "Cari Dönem 01.01.2022 - 31.03.2022",
            value=value, period_start=date(2022, 1, 1),
        ))
        assert [fact.canonical_field for fact in out] == [expected]
        if expected in {"FUNDING_COSTS", "OPERATING_EXPENSES"}:
            assert out[0].value > 0


def test_2022_plus_provision_shift_remains_label_and_member_locked() -> None:
    out = _map(_cell(
        "finance_role_210014", 25,
        "Beklenen Zarar Karşılıkları / Özel Karşılıklar (-)",
        "Cari Dönem 31.03.2022 | Toplam", value="-15",
    ))
    assert [(fact.canonical_field, fact.value) for fact in out] == [
        ("PROVISIONS", Decimal("15"))
    ]

    for row, label, context in [
        (23, "Beklenen Zarar Karşılıkları / Özel Karşılıklar (-)", "Cari Dönem 31.03.2022 | Toplam"),
        (25, "Özel Karşılıklar", "Cari Dönem 31.03.2022 | Toplam"),
        (25, "Beklenen Zarar Karşılıkları / Özel Karşılıklar (-)", "Cari Dönem 31.03.2022 | TP"),
    ]:
        with pytest.raises(KapApiProtocolError, match="hic kalem"):
            _map(_cell("finance_role_210014", row, label, context))


def test_unobserved_intermediate_shift_rows_still_fail_closed() -> None:
    mutations = [
        _cell("finance_role_210014", 39, "VARLIKLAR TOPLAMI", "Cari Dönem 31.03.2022 | Toplam"),
        _cell("finance_role_210014", 61, "ÖZKAYNAKLAR", "Cari Dönem 31.03.2022 | Toplam"),
        _cell(
            "finance_role_310020", 80, "DÖNEM NET KARI VEYA ZARARI",
            "Cari Dönem 01.01.2022 - 31.03.2022", period_start=date(2022, 1, 1),
        ),
        _cell(
            "finance_role_310020", 28, "BRÜT KAR (ZARAR)",
            "Cari Dönem 01.01.2022 - 31.03.2022", period_start=date(2022, 1, 1),
        ),
    ]
    for mutation in mutations:
        with pytest.raises(KapApiProtocolError, match="hic kalem"):
            _map(mutation)
