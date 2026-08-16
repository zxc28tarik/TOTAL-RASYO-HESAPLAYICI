from pathlib import Path

import pandas as pd
import pytest

from src.ingest.historical_universe import (
    HistoricalUniverseIngestError,
    UniverseMembershipRecord,
    load_historical_universe_csv,
    records_from_frame,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _row(**overrides):
    row = {
        "ticker": " aaa ",
        "valid_from": "2021-01-01",
        "valid_to": "2022-01-01",
        "is_tradable": True,
        "company_name": "AAA AS",
        "sector_index_code": "XBANK",
        "sector_code": "BANK",
        "source": "KAP_ARCHIVE",
        "source_ref": "snapshot-2021",
        "source_sha256": SHA_A,
    }
    row.update(overrides)
    return row


def test_record_is_canonical_and_hash_deterministic():
    a = UniverseMembershipRecord.from_mapping(_row())
    b = UniverseMembershipRecord.from_mapping(dict(reversed(list(_row().items()))))
    assert a.ticker == "AAA"
    assert a.row_sha256 == b.row_sha256
    assert len(a.row_sha256) == 64


def test_string_false_does_not_become_true():
    row = UniverseMembershipRecord.from_mapping(_row(is_tradable="False"))
    assert row.is_tradable is False


def test_invalid_boolean_is_rejected():
    with pytest.raises(HistoricalUniverseIngestError, match="true/false"):
        UniverseMembershipRecord.from_mapping(_row(is_tradable="maybe"))


def test_source_sha_must_be_real_64_hex():
    with pytest.raises(HistoricalUniverseIngestError, match="64-hex"):
        UniverseMembershipRecord.from_mapping(_row(source_sha256="not-a-sha"))


def test_half_open_interval_requires_valid_to_after_valid_from():
    with pytest.raises(HistoricalUniverseIngestError, match="valid_to"):
        UniverseMembershipRecord.from_mapping(_row(valid_to="2021-01-01"))


def test_adjacent_intervals_are_allowed_but_overlap_is_rejected():
    adjacent = pd.DataFrame([
        _row(valid_from="2021-01-01", valid_to="2022-01-01"),
        _row(valid_from="2022-01-01", valid_to=None, source_sha256=SHA_B, source_ref="snapshot-2022"),
    ])
    assert len(records_from_frame(adjacent)) == 2

    overlapping = adjacent.copy()
    overlapping.loc[1, "valid_from"] = "2021-12-31"
    with pytest.raises(HistoricalUniverseIngestError, match="cakisan"):
        records_from_frame(overlapping)


def test_exact_duplicate_collapses_but_conflicting_identity_fails():
    exact = pd.DataFrame([_row(), _row()])
    assert len(records_from_frame(exact)) == 1

    conflict = pd.DataFrame([_row(), _row(source_ref="different")])
    with pytest.raises(HistoricalUniverseIngestError, match=r"ayni ticker\+valid_from"):
        records_from_frame(conflict)


def test_csv_loader_preserves_explicit_false(tmp_path: Path):
    path = tmp_path / "history.csv"
    pd.DataFrame([_row(is_tradable="false", valid_to=None)]).to_csv(path, index=False)
    rows = load_historical_universe_csv(path)
    assert len(rows) == 1
    assert rows[0].is_tradable is False
