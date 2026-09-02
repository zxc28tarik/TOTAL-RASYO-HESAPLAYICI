from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.ingest.api.mkk_kap import KapApiProtocolError
from src.ingest.api.semantic_facts import SemanticFactMapper
from src.ingest.kap_bulk_financial_export import KapBulkExportReport, KapBulkFinancialCell
from src.ingest.kap_bulk_insurance_finance_exact_semantic_mapping import (
    build_bulk_exact_financial_semantic_config,
    build_bulk_exact_insurance_semantic_config,
)
from src.ingest.kap_bulk_semantic_adapter import bulk_cells_to_financial_facts


PUBLISHED = datetime(2021, 5, 5, 18, 0, tzinfo=timezone.utc)
MAPPED = datetime(2021, 5, 5, 19, 0, tzinfo=timezone.utc)


def report() -> KapBulkExportReport:
    return KapBulkExportReport(
        archive_name="KAP_2021_3A.zip",
        member_name="TEST_1_2021_1.xls",
        archive_sha256="a" * 64,
        member_sha256="b" * 64,
        member_size_bytes=100,
        notification_id=1,
        source_entity_code="TEST",
        company_name="TEST A.Ş.",
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


def map_one(raw_cell: KapBulkFinancialCell, family: str):
    raw = bulk_cells_to_financial_facts(
        report(), [raw_cell], ticker="TEST", extracted_at=PUBLISHED
    )
    config = (
        build_bulk_exact_insurance_semantic_config()
        if family == "INSURANCE"
        else build_bulk_exact_financial_semantic_config()
    )
    return SemanticFactMapper(config).map_facts(raw, mapped_at=MAPPED)


def test_insurance_verified_2021q1_core_rows_map_exactly() -> None:
    cases = [
        ("insurance_role_210004", 138, "TOPLAM VARLIKLAR", "TOTAL_ASSETS", None),
        ("insurance_role_210004", 275, "ÖZSERMAYE TOPLAMI", "TOTAL_EQUITY", None),
        ("insurance_role_210004", 249, "ÖDENMİŞ SERMAYE", "ISSUED_CAPITAL", None),
        ("insurance_role_310010", 133, "DÖNEM NET KARI VEYA ZARARI", "NET_INCOME", date(2021, 1, 1)),
        ("insurance_role_310010", 101, "GENEL TEKNİK BÖLÜM DENGESİ", "TECHNICAL_RESULT_TOTAL", date(2021, 1, 1)),
        ("insurance_role_310010", 102, "YATIRIM GELİRLERİ", "INVESTMENT_INCOME", date(2021, 1, 1)),
    ]
    for role, row, label, field, start in cases:
        context = (
            "Cari Dönem 31.03.2021"
            if start is None
            else "Cari Dönem 01.01.2021 - 31.03.2021"
        )
        out = map_one(cell(role, row, label, context, period_start=start), "INSURANCE")
        assert [(x.canonical_field, x.value) for x in out] == [(field, Decimal("100"))]


def test_insurance_life_and_non_life_same_labels_are_row_locked() -> None:
    non_life = map_one(cell(
        "insurance_role_310010", 5,
        "Yazılan Primler (Reasürör Payı Düşülmüş Olarak)",
        "Cari Dönem 01.01.2021 - 31.03.2021",
        period_start=date(2021, 1, 1),
    ), "INSURANCE")
    life = map_one(cell(
        "insurance_role_310010", 43,
        "Yazılan Primler (Reasürör Payı Düşülmüş Olarak)",
        "Cari Dönem 01.01.2021 - 31.03.2021",
        period_start=date(2021, 1, 1),
    ), "INSURANCE")
    assert non_life[0].canonical_field == "WRITTEN_PREMIUMS_NON_LIFE"
    assert life[0].canonical_field == "WRITTEN_PREMIUMS_LIFE"

    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        map_one(cell(
            "insurance_role_310010", 6,
            "Yazılan Primler (Reasürör Payı Düşülmüş Olarak)",
            "Cari Dönem 01.01.2021 - 31.03.2021",
            period_start=date(2021, 1, 1),
        ), "INSURANCE")


def test_insurance_claims_and_expenses_are_abs_signed() -> None:
    claims = map_one(cell(
        "insurance_role_310010", 22,
        "Gerçekleşen Tazminatlar (Reasürör Payı Düşülmüş Olarak) (+/-)",
        "Cari Dönem 01.01.2021 - 31.03.2021",
        value="-25", period_start=date(2021, 1, 1),
    ), "INSURANCE")
    expenses = map_one(cell(
        "insurance_role_310010", 33, "Faaliyet Giderleri (-)",
        "Cari Dönem 01.01.2021 - 31.03.2021",
        value="-12", period_start=date(2021, 1, 1),
    ), "INSURANCE")
    assert claims[0].value == Decimal("25")
    assert expenses[0].value == Decimal("12")


def test_insurance_rejects_wrong_label_previous_period_and_quarter_context() -> None:
    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        map_one(cell(
            "insurance_role_210004", 275, "TOPLAM ÖZSERMAYE",
            "Cari Dönem 31.03.2021",
        ), "INSURANCE")
    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        map_one(cell(
            "insurance_role_210004", 275, "ÖZSERMAYE TOPLAMI",
            "Önceki Dönem 31.12.2020", period_end=date(2020, 12, 31),
        ), "INSURANCE")
    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        map_one(cell(
            "insurance_role_310010", 133, "DÖNEM NET KARI VEYA ZARARI",
            "Cari Dönem 3 Aylık 01.01.2021 - 31.03.2021",
            period_start=date(2021, 1, 1),
        ), "INSURANCE")


def test_financial_verified_2021q1_balance_rows_require_current_total() -> None:
    cases = [
        (37, "VARLIKLAR TOPLAMI", "TOTAL_ASSETS"),
        (58, "ÖZKAYNAKLAR", "TOTAL_EQUITY"),
        (59, "Ödenmiş Sermaye", "ISSUED_CAPITAL"),
        (9, "Faktoring Alacakları", "FACTORING_RECEIVABLES"),
        (12, "Finansman Kredileri", "CONSUMER_FINANCE_RECEIVABLES"),
        (16, "Kiralama İşlemleri (Net)", "LEASING_RECEIVABLES_NET"),
        (21, "Takipteki Alacaklar", "NPL_GROSS"),
    ]
    for row, label, field in cases:
        out = map_one(cell(
            "finance_role_210014", row, label,
            "Cari Dönem 31.03.2021 | Toplam",
        ), "FINANCIAL")
        assert out[0].canonical_field == field

        with pytest.raises(KapApiProtocolError, match="hic kalem"):
            map_one(cell(
                "finance_role_210014", row, label,
                "Cari Dönem 31.03.2021 | TP",
            ), "FINANCIAL")


def test_financial_ytd_core_and_abs_signs_are_exact() -> None:
    net = map_one(cell(
        "finance_role_310020", 77, "DÖNEM NET KARI VEYA ZARARI",
        "Cari Dönem 01.01.2021 - 31.03.2021",
        period_start=date(2021, 1, 1),
    ), "FINANCIAL")
    margin = map_one(cell(
        "finance_role_310020", 25, "BRÜT KAR (ZARAR)",
        "Cari Dönem 01.01.2021 - 31.03.2021",
        period_start=date(2021, 1, 1),
    ), "FINANCIAL")
    funding = map_one(cell(
        "finance_role_310020", 18, "FİNANSMAN GİDERLERİ (-)",
        "Cari Dönem 01.01.2021 - 31.03.2021",
        value="-30", period_start=date(2021, 1, 1),
    ), "FINANCIAL")
    operating = map_one(cell(
        "finance_role_310020", 26, "ESAS FAALİYET GİDERLERİ (-)",
        "Cari Dönem 01.01.2021 - 31.03.2021",
        value="-20", period_start=date(2021, 1, 1),
    ), "FINANCIAL")
    assert net[0].canonical_field == "NET_INCOME"
    assert margin[0].canonical_field == "NET_FINANCE_INCOME"
    assert funding[0].value == Decimal("30")
    assert operating[0].value == Decimal("20")


def test_financial_provisions_are_exact_abs_and_label_locked() -> None:
    out = map_one(cell(
        "finance_role_210014", 22,
        "Beklenen Zarar Karşılıkları / Özel Karşılıklar (-)",
        "Cari Dönem 31.03.2021 | Toplam", value="-15",
    ), "FINANCIAL")
    assert [(x.canonical_field, x.value) for x in out] == [("PROVISIONS", Decimal("15"))]

    for row, label in [
        (23, "Beklenen Zarar Karşılıkları / Özel Karşılıklar (-)"),
        (22, "Özel Karşılıklar"),
    ]:
        with pytest.raises(KapApiProtocolError, match="hic kalem"):
            map_one(cell(
                "finance_role_210014", row, label,
                "Cari Dönem 31.03.2021 | Toplam",
            ), "FINANCIAL")


def test_financial_ytd_rejects_wrong_role_previous_and_quarter_context() -> None:
    mutations = [
        cell(
            "finance_role_310010", 77, "DÖNEM NET KARI VEYA ZARARI",
            "Cari Dönem 01.01.2021 - 31.03.2021",
            period_start=date(2021, 1, 1),
        ),
        cell(
            "finance_role_310020", 77, "DÖNEM NET KARI VEYA ZARARI",
            "Önceki Dönem 01.01.2020 - 31.03.2020",
            period_start=date(2020, 1, 1), period_end=date(2020, 3, 31),
        ),
        cell(
            "finance_role_310020", 77, "DÖNEM NET KARI VEYA ZARARI",
            "Cari Dönem 3 Aylık 01.01.2021 - 31.03.2021",
            period_start=date(2021, 1, 1),
        ),
    ]
    for mutation in mutations:
        with pytest.raises(KapApiProtocolError, match="hic kalem"):
            map_one(mutation, "FINANCIAL")
