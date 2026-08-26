from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pandas as pd

from src.ingest.historical_backtest_schedules import load_wage_schedule_csv


PATH = Path("data/backtest_sources/minimum_wage_csgb_2021_2026.csv")
KEY = "WAGE_TR_NET_CSGB_2021_2026_V1"


def _evidence_sha(row: pd.Series) -> str:
    canonical = "|".join(
        [
            str(row["valid_from"]),
            str(row["valid_to"]),
            str(row["net_min_wage"]),
            str(row["source_ref"]),
            str(row["evidence_text"]),
        ]
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def test_csgb_minimum_wage_source_is_hash_locked_and_ingestible():
    frame = pd.read_csv(PATH, dtype=str, keep_default_na=False)
    assert len(frame) == 8
    assert set(frame["schedule_key"]) == {KEY}
    assert set(frame["source"]) == {"CSGB_OFFICIAL_WEB_EVIDENCE_V1"}
    assert frame["source_ref"].str.startswith("https://www.csgb.gov.tr/").all()
    assert [_evidence_sha(row) for _, row in frame.iterrows()] == list(frame["source_sha256"])

    records = load_wage_schedule_csv(PATH)
    assert len(records) == 8
    assert all(row.schedule_key == KEY for row in records)


def test_csgb_minimum_wage_intervals_are_contiguous_and_cover_locked_window():
    records = sorted(load_wage_schedule_csv(PATH), key=lambda row: row.valid_from)
    assert records[0].valid_from.isoformat() == "2021-01-01"
    assert records[-1].valid_to is not None
    assert records[-1].valid_to.isoformat() == "2027-01-01"
    for left, right in zip(records, records[1:]):
        assert left.valid_to == right.valid_from

    expected = {
        "2021-01-01": Decimal("2825.90"),
        "2022-01-01": Decimal("4253.40"),
        "2022-07-01": Decimal("5500.35"),
        "2023-01-01": Decimal("8506.80"),
        "2023-07-01": Decimal("11402.32"),
        "2024-01-01": Decimal("17002.12"),
        "2025-01-01": Decimal("22104.67"),
        "2026-01-01": Decimal("28075.50"),
    }
    assert {row.valid_from.isoformat(): row.net_min_wage for row in records} == expected


def test_two_minimum_wage_monthly_contribution_uses_period_value():
    records = load_wage_schedule_csv(PATH)

    def contribution(day: str) -> Decimal:
        date = pd.Timestamp(day).date()
        matches = [
            row for row in records
            if row.valid_from <= date and (row.valid_to is None or date < row.valid_to)
        ]
        assert len(matches) == 1
        return matches[0].net_min_wage * Decimal("2")

    assert contribution("2021-08-02") == Decimal("5651.80")
    assert contribution("2022-07-01") == Decimal("11000.70")
    assert contribution("2023-07-03") == Decimal("22804.64")
    assert contribution("2026-07-01") == Decimal("56151.00")
