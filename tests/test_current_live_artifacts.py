from __future__ import annotations

from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _receipt(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_borsa_quote_unit_artifact_keeps_legal_share_count_separate():
    receipt = _receipt("data/live/current_quote_unit_v1/receipt.json")
    raw = gzip.decompress(
        (ROOT / "data/live/current_quote_unit_v1/pay_piyasasi_proseduru.pdf.gz").read_bytes()
    )
    assert receipt["contract"] == "BORSA_PAY_PRICE_PER_1_TRY_NOMINAL_V1"
    assert receipt["quoted_nominal_unit_try"] == 1
    assert receipt["legal_share_count_equated_to_nominal_try"] is False
    assert hashlib.sha256(raw).hexdigest() == receipt["source_pdf_sha256"]


def test_current_market_artifact_cutoff_covers_every_official_source_timestamp():
    receipt = _receipt("data/live/current_market_modules_v1/receipt.json")
    captured_at = datetime.fromisoformat(receipt["captured_at"])
    for source in receipt["index_source_hashes"].values():
        source_at = datetime.strptime(source["http_date"], "%a, %d %b %Y %H:%M:%S GMT")
        assert captured_at.replace(tzinfo=None) >= source_at
    assert receipt["m3_valid_count"] > 0
    assert receipt["ek4_valid_count"] > 0


def test_current_total_artifact_is_fail_closed_while_m2_is_missing():
    receipt = _receipt("data/live/current_total_scores_v1/receipt.json")
    assert receipt["universe_count"] == receipt["explicit_rejection_count"] == 807
    assert receipt["missing_module_counts"]["M2"] == 807
    assert receipt["total_valid_count"] == receipt["ranking_count"] == 0
    assert receipt["neutral_fill"] is False
    assert receipt["weight_redistribution"] is False
