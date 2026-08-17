from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


CSV_PATH = Path("data/backtest_sources/bist100_snapshot_2026-08-17.csv")
PROVENANCE_PATH = Path("data/backtest_sources/bist100_snapshot_2026-08-17.provenance.json")


def test_bist100_endpoint_is_exactly_100_unique_official_xu100_members():
    frame = pd.read_csv(CSV_PATH, dtype=str, keep_default_na=False)
    assert list(frame.columns) == ["ticker", "company_name", "snapshot_date"]
    assert len(frame) == 100
    assert frame["ticker"].nunique() == 100
    assert frame["ticker"].str.fullmatch(r"[A-Z0-9]+", na=False).all()
    assert frame["snapshot_date"].eq("2026-08-17").all()
    assert {"AEFES", "AKBNK", "GARAN", "THYAO", "YKBNK"}.issubset(set(frame["ticker"]))


def test_bist100_endpoint_provenance_hash_locks_official_current_index_report():
    payload = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    assert payload["publisher"] == "Borsa Istanbul A.S."
    assert payload["official_path_from_registry"] == "/datum/hisse_endeks_ds.csv"
    assert payload["raw_sha256"] == "04017af0e22aefd23b02f75cc96fa71a4f80b5c1dc6ee661dd57f1052e4712ef"
    assert payload["snapshot_date"] == "2026-08-17"
    assert payload["index_code"] == "XU100"
    assert payload["constituent_count"] == 100
    assert payload["github_actions"]["run_id"] == 31983513706
    assert payload["github_actions"]["artifact_id"] == 9273062288
    assert payload["github_actions"]["artifact_digest"] == "sha256:aefba0d193913f0c230e9175447af2754b0ebcdcd840d0d119e93760032a65c1"
    assert "Reverse-reconstruction endpoint only" in payload["historical_use"]
