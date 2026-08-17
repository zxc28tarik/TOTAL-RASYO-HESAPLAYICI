from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.analytics.bist100_periodic_events import load_bist100_periodic_events_json


PATH = Path("data/backtest_sources/bist100_periodic_events_2021Q3_2026Q3.json")
SIGNALS = Path("data/backtest_sources/xu100_signal_dates_yahoo_2021-08_2026-07.csv")


def test_proved_periodic_source_loads_21_groups_and_180_replacement_pairs():
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    events = load_bist100_periodic_events_json(PATH)

    assert payload["event_group_count"] == 21
    assert payload["replacement_pair_count"] == 180
    assert len(events) == 21
    assert sum(len(event.included) for event in events) == 180
    assert all(len(event.included) == len(event.excluded) for event in events)
    assert all(len(event.source_sha256) == 64 for event in events)
    assert events[0].effective_date.isoformat() == "2021-07-01"
    assert events[-1].effective_date.isoformat() == "2026-07-01"


def test_each_in_window_periodic_effective_date_is_that_quarters_first_real_xu100_trade_day():
    events = load_bist100_periodic_events_json(PATH)
    signal_frame = pd.read_csv(SIGNALS, dtype=str, keep_default_na=False)
    first_by_quarter = {}
    for row in signal_frame.itertuples(index=False):
        period = pd.Period(row.month, freq="M")
        if period.month in {1, 4, 7, 10}:
            first_by_quarter[f"{period.year}Q{period.quarter}"] = row.signal_date

    # 2021Q3 predates the locked 2021-08 backtest start, so the signal-date
    # evidence file intentionally begins after that event. Every later event
    # must land exactly on the first real XU100 trading day of its quarter.
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    for row in payload["events"]:
        if row["quarter"] == "2021Q3":
            continue
        assert row["effective_date"] == first_by_quarter[row["quarter"]]


def test_periodic_proof_metadata_is_hash_locked_to_successful_actions_artifact():
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    proof = payload["proof"]
    assert proof["run_id"] == 31984304622
    assert proof["head_sha"] == "2a25a014d0a6f9357a34ddab43cc4356da665a6b"
    assert proof["artifact_id"] == 9273323409
    assert proof["artifact_digest"] == "sha256:13fd4717a6d2d3f5bd82ebf7dbdfc911492976a44d7b3126d59c112e2d9d80c3"
    assert proof["proved_json_sha256"] == "1199d41258f5f3564300c0b77d9770e472af2482f89fcf51fbc147a7bd067b61"
    assert proof["proved_tsv_sha256"] == "83066743c2169bc3a90b728d224a22a52276961046861b1bc17efac9512f2fe3"


def test_periodic_source_contains_identity_sensitive_pre_rename_tickers():
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    by_quarter = {row["quarter"]: row for row in payload["events"]}
    assert "EFORC" in by_quarter["2025Q2"]["included"]
    assert "IPEKE" in by_quarter["2025Q3"]["included"]
    assert "GRTHO" in by_quarter["2025Q2"]["included"]
