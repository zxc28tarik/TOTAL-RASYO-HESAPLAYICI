from scripts.capture_bank_macro_cap_ovp import select_vintage


def test_latest_cutoff_eligible_ovp_is_selected_without_lookahead():
    assert select_vintage("2021-08-31T18:10:00+03:00")["ovp"] == "2021-2023"
    assert select_vintage("2021-09-30T18:10:00+03:00")["ovp"] == "2022-2024"
    assert select_vintage("2025-09-30T18:10:00+03:00")["ovp"] == "2026-2028"
