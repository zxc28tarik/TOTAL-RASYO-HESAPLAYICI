import json
from datetime import date

import pytest

from scripts.run_current_total_rasyo import _require_current_artifact


def test_offline_runner_rejects_stale_price_receipt(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({
        "contract": "CURRENT_YAHOO_RAW_CLOSE_V1", "cutoff_date": "2026-09-07",
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="stale artifact cutoff_date"):
        _require_current_artifact(
            path, contract="CURRENT_YAHOO_RAW_CLOSE_V1",
            run_date=date(2026, 9, 8), date_key="cutoff_date",
        )


def test_offline_runner_accepts_same_date_exact_contract(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({
        "contract": "CURRENT_YAHOO_RAW_CLOSE_V1", "cutoff_date": "2026-09-08",
    }), encoding="utf-8")
    receipt = _require_current_artifact(
        path, contract="CURRENT_YAHOO_RAW_CLOSE_V1",
        run_date=date(2026, 9, 8), date_key="cutoff_date",
    )
    assert receipt["cutoff_date"] == "2026-09-08"
