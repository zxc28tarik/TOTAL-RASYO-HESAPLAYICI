"""W6 — contract and mutation tests for the KAP corporate-action gate audit.

W5 measured the peer-certification gate reachable at 0/60 cutoffs because no
non-empty action interval could be proven empty. This audit consumes a real,
gap-free KAP disclosure inventory to re-measure that bound. Every gate it
claims to apply gets a synthetic counter-case here.
"""
from __future__ import annotations

from datetime import date
import json

import pytest

from scripts.audit_w6_ca_gate_reachability import (
    AUDIT,
    CONTENT_FILES,
    CONTRACT,
    W6AuditError,
    build_systemic_bound,
    build_verdict,
    certify_ticker_window,
    day_fully_covered,
    derive,
    is_action_subject,
    sha_bytes,
    window_fully_covered,
)

RECEIPT = AUDIT / "receipt.json"

pytestmark = pytest.mark.skipif(
    not RECEIPT.exists(), reason="W6 CA gate audit artifacts not materialized"
)


@pytest.fixture(scope="module")
def receipt() -> dict:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stored() -> dict:
    return {
        name: json.loads((AUDIT / name).read_text(encoding="utf-8")) for name in CONTENT_FILES
    }


# --------------------------------------------------------------------------
# Subject matching — fail-closed
# --------------------------------------------------------------------------


def test_the_canonical_capital_change_subject_matches():
    assert is_action_subject("Sermaye Artırımı - Azaltımı İşlemlerine İlişkin Bildirim")


def test_the_same_event_classified_under_a_different_disclosure_type_still_matches():
    # The real event driving this audit: KLRHO's capital-increase registration
    # appeared with disclosureType "CA" in some rows and "ODA" in others for the
    # same subject text -- matching must be on subject, never on disclosureType.
    assert is_action_subject("Sermaye Artırımı - Azaltımı İşlemlerine İlişkin Bildirim")


def test_merger_and_demerger_subjects_match():
    assert is_action_subject("Birleşme İşlemlerine İlişkin Bildirim")
    assert is_action_subject("Bölünme İşlemlerine İlişkin Bildirim")
    assert is_action_subject("Bölünmeye İlişkin Bildirim")


def test_matching_is_case_insensitive():
    assert is_action_subject("Sermaye Artırımı - azaltımı işlemlerine ilişkin bildirim")


def test_unrelated_subjects_do_not_match():
    assert not is_action_subject("Finansal Rapor")
    assert not is_action_subject("Özel Durum Açıklaması (Genel)")
    assert not is_action_subject("Pay Bazında Devre Kesici Bildirimi")
    assert not is_action_subject(None)
    assert not is_action_subject("")


# --------------------------------------------------------------------------
# Coverage bookkeeping
# --------------------------------------------------------------------------


def test_day_fully_covered_true_inside_a_range():
    covered = [(date(2020, 1, 1), date(2020, 1, 7)), (date(2020, 1, 8), date(2020, 1, 14))]
    assert day_fully_covered(date(2020, 1, 5), covered)
    assert day_fully_covered(date(2020, 1, 8), covered)


def test_day_fully_covered_false_outside_any_range():
    covered = [(date(2020, 1, 1), date(2020, 1, 7))]
    assert not day_fully_covered(date(2020, 1, 8), covered)
    assert not day_fully_covered(date(2019, 12, 31), covered)


def test_window_fully_covered_across_adjacent_ranges():
    covered = [(date(2020, 1, 1), date(2020, 1, 7)), (date(2020, 1, 8), date(2020, 1, 20))]
    assert window_fully_covered(date(2020, 1, 3), date(2020, 1, 15), covered)


def test_window_fully_covered_false_across_a_real_gap():
    covered = [(date(2020, 1, 1), date(2020, 1, 5)), (date(2020, 1, 10), date(2020, 1, 20))]
    assert not window_fully_covered(date(2020, 1, 3), date(2020, 1, 15), covered)


def test_window_fully_covered_false_when_nothing_covers_the_start():
    covered = [(date(2020, 2, 1), date(2020, 2, 28))]
    assert not window_fully_covered(date(2020, 1, 1), date(2020, 2, 10), covered)


def test_the_captured_window_has_zero_gaps(stored):
    coverage = stored["coverage.json"]
    assert coverage["complete"] is True
    assert coverage["gaps"] == []
    assert coverage["windows_failed"] == 0
    assert coverage["windows_at_cap_unsplittable"] == 0


def test_apply_refuses_to_run_if_coverage_is_incomplete(monkeypatch):
    import scripts.audit_w6_ca_gate_reachability as module

    def fake_manifest():
        return {"coverage": {"complete": False, "gaps": [("2020-01-01", "2020-01-05")]}}

    monkeypatch.setattr(module, "load_manifest", fake_manifest)
    with pytest.raises(W6AuditError, match="KAP_CA_INVENTORY_INCOMPLETE"):
        module.derive()


# --------------------------------------------------------------------------
# Per-ticker window certification
# --------------------------------------------------------------------------


def _events(ticker: str, *dates: str) -> dict:
    return {ticker: [(date.fromisoformat(d), i, "Sermaye Artırımı - Azaltımı İşlemlerine İlişkin Bildirim")
                      for i, d in enumerate(dates)]}


def test_a_window_with_no_recorded_action_certifies():
    covered = [(date(2023, 1, 1), date(2023, 12, 31))]
    result = certify_ticker_window("AAA", date(2023, 1, 1), date(2023, 6, 30), {}, covered)
    assert result["certifiable"] is True
    assert result["reason"] == "NO_ACTION_IN_COVERED_WINDOW"


def test_an_action_strictly_inside_the_window_blocks_it():
    covered = [(date(2023, 1, 1), date(2023, 12, 31))]
    events = _events("AAA", "2023-03-15")
    result = certify_ticker_window("AAA", date(2023, 1, 1), date(2023, 6, 30), events, covered)
    assert result["certifiable"] is False
    assert result["reason"] == "ACTION_DETECTED_IN_WINDOW"
    assert result["events"][0]["date"] == "2023-03-15"


def test_the_interval_is_open_at_the_start_and_closed_at_the_end():
    covered = [(date(2023, 1, 1), date(2023, 12, 31))]
    events_at_start = _events("AAA", "2023-01-01")  # exactly window_start: excluded
    result = certify_ticker_window("AAA", date(2023, 1, 1), date(2023, 6, 30), events_at_start, covered)
    assert result["certifiable"] is True

    events_at_end = _events("AAA", "2023-06-30")  # exactly the cutoff: included
    result2 = certify_ticker_window("AAA", date(2023, 1, 1), date(2023, 6, 30), events_at_end, covered)
    assert result2["certifiable"] is False


def test_an_action_outside_the_window_does_not_block_it():
    covered = [(date(2023, 1, 1), date(2023, 12, 31))]
    events = _events("AAA", "2022-12-01", "2023-07-01")
    result = certify_ticker_window("AAA", date(2023, 1, 1), date(2023, 6, 30), events, covered)
    assert result["certifiable"] is True


def test_an_uncovered_window_never_certifies_even_with_no_matching_event():
    covered = [(date(2024, 1, 1), date(2024, 12, 31))]  # does not cover 2023 at all
    result = certify_ticker_window("AAA", date(2023, 1, 1), date(2023, 6, 30), {}, covered)
    assert result["certifiable"] is False
    assert result["reason"] == "INVENTORY_COVERAGE_GAP"


def test_a_zero_or_negative_length_interval_is_trivially_certifiable():
    covered = [(date(2023, 1, 1), date(2023, 12, 31))]
    result = certify_ticker_window("AAA", date(2023, 6, 30), date(2023, 6, 30), {}, covered)
    assert result["certifiable"] is True
    assert result["reason"] == "ZERO_OR_NEGATIVE_LENGTH_INTERVAL"


def test_klrho_own_known_event_reproduces(stored):
    rows = stored["certifications.json"]["rows"]
    klrho = [r for r in rows if r["ticker"] == "KLRHO"]
    assert klrho, "KLRHO must appear as a NONFIN candidate at some cutoff"
    blocked = [r for r in klrho if r["reason"] == "ACTION_DETECTED_IN_WINDOW"]
    assert blocked
    dates = {e["date"] for r in blocked for e in r["events"]}
    assert "2023-04-17" in dates  # the known KLRHO capital-action registration


# --------------------------------------------------------------------------
# Systemic bound
# --------------------------------------------------------------------------


def test_gate_reachable_needs_required_plus_one():
    certifications = {
        "rows": [
            {"cutoff": "2023-01-31", "ticker": t, "certifiable": True, "reason": "x"}
            for t in "ABCDE"
        ]
    }
    # 5 certifiable, required=5 -> need 6 (one could be the target itself)
    bound = build_systemic_bound(certifications, required=5)
    assert bound["gate_reachable_cutoffs"] == 0

    certifications["rows"].append(
        {"cutoff": "2023-01-31", "ticker": "F", "certifiable": True, "reason": "x"}
    )
    bound2 = build_systemic_bound(certifications, required=5)
    assert bound2["gate_reachable_cutoffs"] == 1


def test_non_certifiable_rows_are_excluded_from_the_count():
    certifications = {
        "rows": [
            {"cutoff": "2023-01-31", "ticker": t, "certifiable": True, "reason": "x"}
            for t in "ABCDEF"
        ] + [
            {"cutoff": "2023-01-31", "ticker": "G", "certifiable": False, "reason": "ACTION_DETECTED_IN_WINDOW"}
        ]
    }
    bound = build_systemic_bound(certifications, required=5)
    assert bound["cutoff_rows"][0]["newly_certifiable"] == 6
    assert bound["gate_reachable_cutoffs"] == 1


def test_the_stored_bound_reproduces_the_reported_gate_opening(stored):
    systemic = stored["systemic_bound.json"]
    assert systemic["cutoffs"] == 60
    assert systemic["gate_reachable_cutoffs"] == 60
    assert systemic["required_certifiable_per_cutoff"] == 6
    assert systemic["compared_to_w5"]["w5_gate_reachable_cutoffs"] == 0


def test_every_cutoff_has_comfortably_more_than_the_minimum(stored):
    systemic = stored["systemic_bound.json"]
    for row in systemic["cutoff_rows"]:
        assert row["newly_certifiable"] >= systemic["required_certifiable_per_cutoff"]
        assert row["gate_reachable"] is True


def test_reason_counts_are_exhaustive_over_every_row(stored):
    certifications = stored["certifications.json"]
    systemic = stored["systemic_bound.json"]
    assert sum(systemic["reason_counts"].values()) == len(certifications["rows"])


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------


def test_verdict_reflects_reachability_not_a_constant():
    opened = build_verdict({"complete": True, "gaps": []}, {"gate_reachable_cutoffs": 5, "cutoffs": 60})
    assert opened["status"] == "GATE_OPENED"
    closed = build_verdict({"complete": True, "gaps": []}, {"gate_reachable_cutoffs": 0, "cutoffs": 60})
    assert closed["status"] == "STILL_BLOCKED"


def test_verdict_never_claims_a_materialized_m2(stored):
    verdict = stored["verdict.json"]
    assert verdict["policy"]["m2_materialized"] is False
    assert verdict["policy"]["production_code_changed"] is False
    assert verdict["policy"]["peer_or_coverage_threshold_changed"] is False
    assert "separate" in verdict["note"].lower()


def test_the_stored_verdict_reports_the_gate_opened(stored):
    verdict = stored["verdict.json"]
    assert verdict["status"] == "GATE_OPENED"
    assert verdict["gate_reachable_cutoffs"] == 60
    assert verdict["total_cutoffs"] == 60
    assert verdict["coverage_complete"] is True


# --------------------------------------------------------------------------
# Receipt and reproducibility
# --------------------------------------------------------------------------


def test_stored_artifacts_match_the_receipt(receipt):
    for name in CONTENT_FILES:
        assert sha_bytes((AUDIT / name).read_bytes()) == receipt["output_sha256"][name]


def test_second_derivation_is_byte_identical(receipt):
    content = derive()
    for name in CONTENT_FILES:
        assert sha_bytes(content[name]) == receipt["output_sha256"][name]


def test_the_receipt_is_bound_to_the_manifest_hash(receipt):
    assert receipt["hash_mode"] == "LF_CANONICAL_SHA256_V1"
    manifest_path = (
        AUDIT.parents[1] / "backtest_sources/kap_monthly_ca_inventory_v1/capture_manifest.json"
    )
    assert sha_bytes(manifest_path.read_bytes()) == receipt["manifest_sha256"]


def test_every_artifact_declares_the_same_contract(stored):
    for name in ("certifications.json", "systemic_bound.json", "verdict.json"):
        assert stored[name]["contract"] == CONTRACT


def test_no_windows_path_separator_leaks_into_the_artifacts():
    for name in CONTENT_FILES + ("receipt.json",):
        assert "\\\\" not in (AUDIT / name).read_text(encoding="utf-8")
