from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest

from scripts.discover_kap_bulk_bank_schema_targeted import (
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
    return VerifiedArchive(
        path=Path("/tmp") / name,
        sha256="a" * 64,
        member_count=1,
    )


def test_target_raw_gate_accepts_bank_and_participation_bank_roles_only() -> None:
    assert _is_target_raw(b'<table class="financial-table tbl_banks_role_210013">')
    assert _is_target_raw(b'<table class="financial-table tbl_par-banks_role_210013">')
    assert not _is_target_raw(b'<table class="financial-table tbl_general_role_210015">')


def test_stream_gate_detects_marker_split_across_chunks() -> None:
    raw = b"prefix tbl_par-banks_role_210013 suffix"

    assert _stream_contains_target_role(BytesIO(raw), chunk_size=7)


def test_stream_gate_is_case_insensitive_and_rejects_non_bank_namespace() -> None:
    assert _stream_contains_target_role(
        BytesIO(b'<TABLE class="financial-table tbl_BANKS_ROLE_210013">'),
        chunk_size=11,
    )
    assert not _stream_contains_target_role(
        BytesIO(b'<table class="financial-table tbl_general_role_210015">'),
        chunk_size=9,
    )


def test_stream_gate_rejects_invalid_chunk_size() -> None:
    for value in (0, -1, True):
        with pytest.raises(ValueError, match="chunk_size"):
            _stream_contains_target_role(BytesIO(b"banks_role_210013"), chunk_size=value)


def test_target_cells_preserve_only_exact_bank_role_namespaces() -> None:
    cells = (
        _cell("banks_role_210013"),
        _cell("par-banks_role_310019"),
        _cell("general_role_210015"),
        _cell("insurance_role_210012"),
    )
    selected = _target_cells(cells)

    assert TARGET_ROLE_NAMESPACES == frozenset({"banks", "par-banks"})
    assert [cell.table_role for cell in selected] == [
        "banks_role_210013",
        "par-banks_role_310019",
    ]


def test_probe_archive_selector_keeps_full_verified_set_outside_scan_scope() -> None:
    verified = (
        _verified("KAP_2020_Y.zip"),
        _verified("KAP_2021_3A.zip"),
        _verified("KAP_2026_6A.zip"),
    )

    selected, names = _select_scan_archives(verified, ["KAP_2021_3A.zip"])

    assert names == frozenset({"KAP_2021_3A.zip"})
    assert [row.path.name for row in selected] == ["KAP_2021_3A.zip"]
    assert len(verified) == 3


def test_probe_archive_selector_defaults_to_all_verified_archives() -> None:
    verified = (_verified("KAP_2020_Y.zip"), _verified("KAP_2021_3A.zip"))

    selected, names = _select_scan_archives(verified, None)

    assert selected == verified
    assert names == frozenset({"KAP_2020_Y.zip", "KAP_2021_3A.zip"})


def test_probe_archive_selector_rejects_unknown_duplicate_and_empty_names() -> None:
    verified = (_verified("KAP_2021_3A.zip"),)

    with pytest.raises(ValueError, match="manifestte yok"):
        _select_scan_archives(verified, ["KAP_2099_Y.zip"])
    with pytest.raises(ValueError, match="duplicate"):
        _select_scan_archives(verified, ["KAP_2021_3A.zip", "KAP_2021_3A.zip"])
    with pytest.raises(ValueError, match="dolu metin"):
        _select_scan_archives(verified, [" "])
    with pytest.raises(ValueError, match="bos olamaz"):
        _select_scan_archives(verified, [])
