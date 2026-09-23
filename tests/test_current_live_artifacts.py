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


def test_current_total_artifact_uses_real_m2_and_keeps_incomplete_rows_fail_closed():
    m2_receipt = _receipt("data/live/current_nonfin_valuation_v1/receipt.json")
    receipt = _receipt("data/live/current_total_scores_v1/receipt.json")
    assert m2_receipt["usable_valuation_count"] == 407
    assert m2_receipt["follow_materialized_count"] == 406
    assert m2_receipt["m2_materialized_count"] == 406
    assert m2_receipt["neutral_follow_or_m2_materialized"] is False
    # Holdings and REITs are valued inside their own XUMAL cohort, never
    # against industrials, so the peer groups the run used must say so.
    assert m2_receipt["peer_groups"] == ["XUHIZ", "XUMAL", "XUSIN", "XUTEK"]
    # The current line runs its own config; the gate it used is on the record
    # and every other valuation parameter matches the frozen historical one.
    assert m2_receipt["valuation_config_path"] == "config/nonfin_valuation.current_full_bist_v1.json"
    assert m2_receipt["minimum_coverage_weight"] == 0.4
    assert m2_receipt["minimum_peer_count"] == 5
    assert m2_receipt["multiple_weights"] == {"PE": 0.3, "EV_EBIT": 0.3, "PS": 0.2, "PB": 0.2}
    assert receipt["universe_count"] == 807
    assert receipt["total_valid_count"] == receipt["ranking_count"] == 372
    assert receipt["explicit_rejection_count"] == 435
    assert receipt["total_valid_count"] + receipt["explicit_rejection_count"] == receipt["universe_count"]
    assert receipt["missing_module_counts"]["M2"] == 401
    assert receipt["neutral_fill"] is False
    assert receipt["weight_redistribution"] is False
