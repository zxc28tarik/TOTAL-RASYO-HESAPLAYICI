from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest

from scripts.discover_kap_bulk_insurance_finance_schema_targeted import (
    TARGET_ROLE_NAMESPACES,
    _is_target_raw,
    _select_scan_archives,
    _stream_contains_target_role,
    _target_cells,
)
from scripts.discover_kap_bulk_schema_signatures import VerifiedArchive
from src.ingest.kap_bulk_financial_export import KapBulkFinancialCell


def _cell(role: str) -> KapBulkFinancialCell:
    return KapBulkFinancialCell(
        notification_id=1,
        statement_scope="CONSOLIDATED",
        table_role=role,
        row_number=1,
        fact_code=f"{role}:1",
        column_index=4,
        label_tr="Test",
        context_label="Cari Dönem | 31.03.2021",
        period_start=None,
        period_end=date(2021, 3, 31),
        raw_value_text="1",
        normalized_value=Decimal("1"),
        scaled_value=Decimal("1000"),
        currency="TRY",
        unit_scale=1000,
    )


def _verified(name: str) -> VerifiedArchive:
    return VerifiedArchive(path=Path("/tmp") / name, sha256="a" * 64, member_count=1)


def test_raw_gate_accepts_only_insurance_and_finance() -> None:
    assert _is_target_raw(b'tbl_insurance_role_210004')
    assert _is_target_raw(b'tbl_finance_role_210014')
    assert not _is_target_raw(b'tbl_general_role_210015')
    assert not _is_target_raw(b'tbl_banks_role_210011')


def test_stream_gate_is_chunk_safe_case_insensitive_and_exact_namespace() -> None:
    assert _stream_contains_target_role(BytesIO(b'xx INSURANCE_ROLE_310010 yy'), chunk_size=5)
    assert _stream_contains_target_role(BytesIO(b'xx finance_role_610006 yy'), chunk_size=7)
    assert not _stream_contains_target_role(BytesIO(b'xx general_role_610000 yy'), chunk_size=4)


def test_stream_gate_rejects_invalid_chunk_size() -> None:
    for value in (0, -1, True):
        with pytest.raises(ValueError, match="chunk_size"):
            _stream_contains_target_role(BytesIO(b"insurance_role_1"), chunk_size=value)


def test_target_cells_keep_only_exact_target_namespaces() -> None:
    selected = _target_cells((
        _cell("insurance_role_210004"),
        _cell("finance_role_210014"),
        _cell("general_role_210015"),
        _cell("banks_role_210011"),
    ))
    assert TARGET_ROLE_NAMESPACES == frozenset({"insurance", "finance"})
    assert [cell.table_role for cell in selected] == [
        "insurance_role_210004",
        "finance_role_210014",
    ]


def test_archive_selector_defaults_to_all_and_rejects_bad_names() -> None:
    verified = (_verified("KAP_2021_3A.zip"), _verified("KAP_2026_6A.zip"))
    selected, names = _select_scan_archives(verified, None)
    assert selected == verified
    assert names == frozenset({"KAP_2021_3A.zip", "KAP_2026_6A.zip"})

    with pytest.raises(ValueError, match="manifestte yok"):
        _select_scan_archives(verified, ["KAP_2099_Y.zip"])
    with pytest.raises(ValueError, match="duplicate"):
        _select_scan_archives(verified, ["KAP_2021_3A.zip", "KAP_2021_3A.zip"])
    with pytest.raises(ValueError, match="bos olamaz"):
        _select_scan_archives(verified, [])
