from __future__ import annotations

import json
from pathlib import Path

from src.analytics.bist100_nonperiodic_events import load_bist100_nonperiodic_events


SOURCE = Path("data/backtest_sources/bist100_nonperiodic_events_2021-08_2026-08.json")
KNOWN_DETAIL_SHA = "4a8aeacb95dd48072c08ae18909f41eddac2edbb55358992e3c76bdc59b3b966"


def test_audited_nonperiodic_source_loads_and_contains_known_kontr_bera_replacement():
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    assert payload["publisher"] == "KAP / Borsa Istanbul A.S."
    assert payload["event_count"] == len(payload["events"])
    events = load_bist100_nonperiodic_events(SOURCE)
    assert len(events) == payload["event_count"]
    known = [row for row in payload["events"] if row["disclosure_index"] == 1618229]
    assert len(known) == 1
    row = known[0]
    assert row["effective_date"] == "2026-06-18"
    assert row["included"] == ["BERA"]
    assert row["excluded"] == ["KONTR"]
    assert row["source_url"] == "https://www.kap.org.tr/tr/Bildirim/1618229"
    assert row["source_detail_sha256"] == KNOWN_DETAIL_SHA
    assert row["event_type"] == "NONPERIODIC_CONSTITUENT_CHANGE"


def test_nonperiodic_source_has_unique_disclosures_and_balanced_replacements():
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = payload["events"]
    ids = [row["disclosure_index"] for row in rows]
    assert len(ids) == len(set(ids))
    for row in rows:
        assert row["included"]
        assert row["excluded"]
        assert len(row["included"]) == len(row["excluded"])
        assert not (set(row["included"]) & set(row["excluded"]))
