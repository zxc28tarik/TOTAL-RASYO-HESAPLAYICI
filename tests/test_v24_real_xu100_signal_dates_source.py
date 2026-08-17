from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


CSV_PATH = Path("data/backtest_sources/xu100_signal_dates_yahoo_2021-08_2026-07.csv")
PROVENANCE_PATH = Path("data/backtest_sources/xu100_signal_dates_yahoo_2021-08_2026-07.provenance.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_xu100_signal_date_source_is_60_months_complete_and_policy_unresolved():
    frame = pd.read_csv(CSV_PATH, dtype=str, keep_default_na=False)
    assert len(frame) == 60
    assert frame["month"].tolist() == [str(p) for p in pd.period_range("2021-08", "2026-07", freq="M")]
    assert frame["index_code"].eq("XU100").all()
    assert frame["cutoff_at"].eq("").all()
    assert frame["execution_at"].eq("").all()
    assert frame["cutoff_policy_status"].eq("UNRESOLVED").all()
    assert frame["signal_date"].is_unique


def test_xu100_signal_date_source_matches_recorded_actions_artifact_hash():
    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    assert provenance["signal_date_count"] == 60
    assert provenance["missing_months"] == []
    assert provenance["daily_row_count"] == 1250
    assert provenance["github_actions"]["run_id"] == 31983176710
    assert provenance["github_actions"]["artifact_id"] == 9272963739
    assert provenance["github_actions"]["artifact_digest"] == "sha256:24190cd23f30a4f3bbe6e7387a1e778075ee92c0eb4833b6469727a82bf2a55c"
    assert _sha256(CSV_PATH) == provenance["evidence_hashes"]["signal_dates_csv_sha256"]
    assert provenance["evidence_hashes"]["daily_csv_sha256"] == "1003cf5af05b5804cdf46bc09d55324031873ff383cf11b71649ca8b740a5a06"


def test_xu100_signal_dates_include_known_non_first-calendar-day_openings():
    frame = pd.read_csv(CSV_PATH, dtype=str, keep_default_na=False).set_index("month")
    assert frame.loc["2022-05", "signal_date"] == "2022-05-05"
    assert frame.loc["2024-01", "signal_date"] == "2024-01-02"
    assert frame.loc["2026-05", "signal_date"] == "2026-05-04"
