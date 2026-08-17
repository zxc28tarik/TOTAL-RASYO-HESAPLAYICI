from __future__ import annotations

import json
from pathlib import Path


SOURCE = Path("data/backtest_sources/bist100_nonperiodic_kap_catalog_2023-09_2026-08.json")


def test_official_nonperiodic_catalog_is_frozen_to_114_unique_kap_disclosures():
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    ids = payload["kap_ids"]
    assert payload["contract"] == "V24_BIST100_NONPERIODIC_KAP_CATALOG_V1"
    assert payload["publisher"] == "Borsa Istanbul Index Announcements"
    assert payload["source_raw_sha256"] == "84a44be0916871797705593c6338a814701372c0094370568cbf1a8950114027"
    assert payload["artifact_digest"] == "sha256:b1cd6a4456fd565e385248015d95f3c50251f45c6e30b1cf5c8812c7bc727426"
    assert payload["row_count"] == 114
    assert len(ids) == 114
    assert len(set(ids)) == 114
    assert all(type(value) is int and value > 0 for value in ids)
    # Known audited XU100 replacement must remain inside the official catalog.
    assert 1618229 in ids
