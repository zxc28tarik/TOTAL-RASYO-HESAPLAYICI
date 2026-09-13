import csv

import pytest

from scripts.build_kap_archive_semantic_checkpoint import FIELDS, _completed, _family


def _checkpoint(path, *, archive_name: str, archive_hash: str) -> None:
    row = {field: "" for field in FIELDS}
    row.update(
        archive_name=archive_name,
        archive_sha256=archive_hash,
        member_name="TEST_1_2021_1.xls",
        status="MAPPED",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)


def test_resume_rejects_checkpoint_from_other_archive(tmp_path) -> None:
    path = tmp_path / "partial.csv"
    _checkpoint(path, archive_name="same-name.zip", archive_hash="a" * 64)

    with pytest.raises(ValueError, match="baska archive"):
        _completed(
            path, archive_name="same-name.zip", archive_hash="b" * 64
        )


def test_resume_accepts_exact_archive_identity(tmp_path) -> None:
    path = tmp_path / "partial.csv"
    _checkpoint(path, archive_name="source.zip", archive_hash="a" * 64)

    assert _completed(
        path, archive_name="source.zip", archive_hash="a" * 64
    ) == {"TEST_1_2021_1.xls"}


def test_hyphenated_bank_role_remains_explicitly_unsupported() -> None:
    assert _family({"par-banks_role_210013"}) is None
