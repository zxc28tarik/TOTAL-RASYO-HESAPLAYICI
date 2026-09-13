from scripts.capture_bank_macro_cap_ovp import select_vintage
import json
from pathlib import Path


def test_latest_cutoff_eligible_ovp_is_selected_without_lookahead():
    assert select_vintage("2021-08-31T18:10:00+03:00")["ovp"] == "2021-2023"
    assert select_vintage("2021-09-30T18:10:00+03:00")["ovp"] == "2022-2024"
    assert select_vintage("2025-09-30T18:10:00+03:00")["ovp"] == "2026-2028"


def test_bank_coe_research_remains_fail_closed_after_free_source_search():
    receipt = json.loads(Path("data/audit/bank_free_input_research_v1/receipt.json").read_text())
    assert receipt["coe"]["free_source_exhaustion"]["usable_free_cutoff_dated_coe_series_found"] is False
    assert receipt["authoritative_bank_m2_claim_allowed"] is False
