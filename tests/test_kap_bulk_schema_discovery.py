from datetime import date
from decimal import Decimal
import hashlib
from zipfile import ZipFile

import pytest

from scripts.discover_kap_bulk_schema_signatures import (
    _serialize,
    observe_reports,
    role_namespace,
    technical_schema_signature,
    technical_schema_signature_sha256,
    validate_archive_inputs,
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


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def test_archive_validation_rejects_incomplete_manifest_set_before_reading_files(tmp_path) -> None:
    manifest = {
        "archive_count": 2,
        "archives": [
            {"filename": "A.zip", "sha256": "0" * 64, "member_count": 1},
            {"filename": "B.zip", "sha256": "1" * 64, "member_count": 1},
        ],
    }
    with pytest.raises(ValueError, match="archive set manifestle uyusmuyor"):
        validate_archive_inputs([tmp_path / "A.zip"], manifest)


def test_archive_validation_rejects_sha_mutation(tmp_path) -> None:
    archive = tmp_path / "A.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("A_1.xls", b"original")
    manifest = {
        "archive_count": 1,
        "archives": [
            {"filename": "A.zip", "sha256": "f" * 64, "member_count": 1},
        ],
    }
    with pytest.raises(ValueError, match="archive sha256 manifestle uyusmuyor"):
        validate_archive_inputs([archive], manifest)


def test_archive_validation_rejects_member_count_mutation(tmp_path) -> None:
    archive = tmp_path / "A.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("A_1.xls", b"one")
    manifest = {
        "archive_count": 1,
        "archives": [
            {"filename": "A.zip", "sha256": _sha(archive), "member_count": 2},
        ],
    }
    with pytest.raises(ValueError, match="archive member_count manifestle uyusmuyor"):
        validate_archive_inputs([archive], manifest)
