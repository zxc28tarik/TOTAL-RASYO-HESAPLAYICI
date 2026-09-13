from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from scripts import materialize_w2_current_correction as correction
from scripts.materialize_current_total_rasyo import materialize


def test_committed_w2_correction_is_hash_bound_and_reproducible():
    receipt = correction.check()
    assert receipt["status"] == "CORRECTED_AND_VERIFIED"
    assert receipt["counts"] == {"core": 131, "ek9": 11, "m2": 48, "rejections": 805, "total": 2}
    assert all(value is False for value in receipt["policy"].values())


def test_total_materializer_rejects_naive_injected_clock(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        materialize(output_dir=tmp_path, materialized_at=datetime(2026, 9, 13))


def test_total_materializer_honors_frozen_core_path_and_receipt_metadata(tmp_path):
    source = correction.LIVE / "current_core_modules_v1/modules.jsonl"
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    alternate = tmp_path / "alternate-core.jsonl"
    alternate.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows if row["ticker"] != "RGYAS"),
        encoding="utf-8",
    )
    output = tmp_path / "total"
    receipt = materialize(
        output_dir=output,
        core_path=alternate,
        materialized_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        receipt_metadata={"test": "FROZEN_PATH"},
    )
    ranking = [json.loads(line) for line in (output / "ranking.jsonl").read_text().splitlines()]
    assert [row["ticker"] for row in ranking] == ["TABGD"]
    assert receipt["total_valid_count"] == 1
    assert receipt["explicit_rejection_count"] == 806
    assert receipt["correction"] == {"test": "FROZEN_PATH"}


def test_old_run_receipt_is_preserved_and_new_assembly_is_explicit():
    old = correction.read(correction.PRE / "current_total_rasyo_run_v1/receipt.json")
    new = correction.read(correction.LIVE / "current_total_rasyo_run_v1/receipt.json")
    assert old["contract"] == "CURRENT_TOTAL_RASYO_RUN_V1"
    assert old["stage_counts"]["m2"] == old["stage_counts"]["total_rasyo"] == 0
    assert new["contract"] == "CURRENT_TOTAL_RASYO_CORRECTION_ASSEMBLY_V1"
    assert new["mode"] == "FROZEN_COMPONENT_CORRECTION_NO_NEW_CAPTURE"
    assert new["stage_counts"]["m2"] == 48
    assert new["stage_counts"]["total_rasyo"] == 2
    assert new["correction"]["new_market_capture"] is False
