from datetime import date
from decimal import Decimal

from scripts.discover_kap_bulk_schema_signatures import (
    _serialize,
    observe_reports,
    role_namespace,
    technical_schema_signature,
    technical_schema_signature_sha256,
)
from src.ingest.kap_bulk_financial_export import KapBulkFinancialCell


def _cell(*, role: str, row: int, label: str, ytd: bool = False) -> KapBulkFinancialCell:
    return KapBulkFinancialCell(
        notification_id=1,
        statement_scope="CONSOLIDATED",
        table_role=role,
        row_number=row,
        fact_code=f"{role}:{row}",
        column_index=4,
        label_tr=label,
        context_label="Cari Dönem | 01.01.2021 - 31.03.2021" if ytd else "Cari Dönem | 31.03.2021",
        period_start=date(2021, 1, 1) if ytd else None,
        period_end=date(2021, 3, 31),
        raw_value_text="1",
        normalized_value=Decimal("1"),
        scaled_value=Decimal("1000"),
        currency="TRY",
        unit_scale=1000,
    )


def test_signature_is_deterministic_and_role_namespace_preserves_hyphenated_family() -> None:
    roles = ["par-banks_role_310019", "par-banks_role_210013"]
    assert technical_schema_signature(reversed(roles)) == tuple(sorted(roles))
    assert technical_schema_signature_sha256(roles) == technical_schema_signature_sha256(reversed(roles))
    assert role_namespace("par-banks_role_210013") == "par-banks"


def test_discovery_keeps_same_row_with_different_labels_separate() -> None:
    cells = (
        _cell(role="banks_role_210013", row=50, label="VARLIKLAR TOPLAMI"),
        _cell(role="banks_role_210013", row=50, label="BASKA ANLAM"),
        _cell(role="banks_role_310017", row=3, label="Faiz Gelirleri", ytd=True),
    )
    observed = observe_reports([("AKBNK", "AKBNK_1_2021_1.xls", cells)])
    serialized = _serialize(observed)

    assert len(serialized) == 1
    facts = serialized[0]["facts"]
    assert {(row["row_number"], row["label_tr"]) for row in facts} == {
        (50, "VARLIKLAR TOPLAMI"),
        (50, "BASKA ANLAM"),
        (3, "Faiz Gelirleri"),
    }
    interest = next(row for row in facts if row["label_tr"] == "Faiz Gelirleri")
    assert interest["instant_count"] == 0
    assert interest["ytd_count"] == 1
