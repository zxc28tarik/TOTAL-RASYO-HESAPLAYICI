from __future__ import annotations

import json
from pathlib import Path


PATH = Path("data/backtest_sources/historical_universe_borsaistanbul_manifest.json")


def test_historical_universe_manifest_requires_all_three_official_source_families():
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    assert payload["publisher"] == "Borsa Istanbul A.S."
    assert payload["official_landing_page"].startswith("https://borsaistanbul.com/")
    assert payload["window"] == {"start_month": "2021-08", "end_month": "2026-07"}

    datasets = {row["dataset_key"]: row for row in payload["datasets"]}
    assert set(datasets) == {
        "FIRST_TRADING_DATE_AND_PRICE",
        "PERMANENT_DELISTINGS",
        "EQUITY_CODE_CHANGES",
    }
    assert all(row["required"] is True for row in datasets.values())
    assert all(row["acquisition_status"] == "RAW_FILE_REQUIRED" for row in datasets.values())
    assert all(row["local_raw_path"] is None for row in datasets.values())
    assert all(row["raw_sha256"] is None for row in datasets.values())


def test_manifest_explicitly_forbids_current_snapshot_survivorship_inference():
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    rules = "\n".join(payload["rules"])
    assert "Do not derive historical membership from core.universe_stocks" in rules
    assert "Do not invent raw file URLs" in rules
    assert "Hash each downloaded raw file" in rules
    assert "Temporary suspensions" in rules
