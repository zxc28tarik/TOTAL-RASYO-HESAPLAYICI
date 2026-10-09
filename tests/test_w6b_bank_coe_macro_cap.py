"""W6-B — contract and mutation tests for the BANK coe/macro_cap audit."""
from __future__ import annotations

import json

import pytest

from scripts.audit_w6b_bank_coe_macro_cap import (
    AUDIT,
    CONTENT_FILES,
    W6BAuditError,
    build_coe_blocked,
    build_unlock_bound,
    derive,
    sha_bytes,
    verify_macro_cap,
)

RECEIPT = AUDIT / "receipt.json"

pytestmark = pytest.mark.skipif(
    not RECEIPT.exists(), reason="W6-B audit artifacts not materialized"
)


@pytest.fixture(scope="module")
def receipt() -> dict:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stored() -> dict:
    return {
        name: json.loads((AUDIT / name).read_text(encoding="utf-8"))
        for name in CONTENT_FILES
        if name.endswith(".json")
    }


# --------------------------------------------------------------------------
# macro_cap verification
# --------------------------------------------------------------------------


def test_macro_cap_is_verified_for_every_bank_cell(stored):
    macro = stored["macro_cap_verification.json"]
    assert macro["status"] == "RESOLVED_FROM_OFFICIAL_DATED_SOURCES"
    assert macro["covered_cells"] == 509
    assert all(check["verified"] for check in macro["checks"].values())


def test_macro_cap_vintages_are_dated_and_hashed(stored):
    vintages = stored["macro_cap_verification.json"]["vintages"]
    assert len(vintages) == 6
    assert sum(item["cells"] for item in vintages) == 509
    for item in vintages:
        assert len(item["source_sha256"]) == 64
        assert item["source_url"].startswith("https://")
        assert item["published_at"]


def test_the_missing_raw_pdf_bytes_are_declared_not_hidden(stored):
    caveat = stored["macro_cap_verification.json"]["provenance_caveat"]
    assert caveat["raw_source_bytes_committed"] is False
    assert caveat["consequence"]


# --------------------------------------------------------------------------
# macro_cap mutations
# --------------------------------------------------------------------------


RECEIPT_STUB = {
    "definition": "LAST/PREVIOUS-1",
    "sources": {
        "2021-2023": {
            "published_at": "2020-09-29T23:59:59+03:00",
            "gdp_last_billion_try": 7021,
            "gdp_previous_billion_try": 6310,
            "url": "https://example.invalid/a.pdf",
            "sha256": "a" * 64,
            "size_bytes": 1,
        },
        "2022-2024": {
            "published_at": "2021-09-05T23:59:59+03:00",
            "gdp_last_billion_try": 10287,
            "gdp_previous_billion_try": 9041,
            "url": "https://example.invalid/b.pdf",
            "sha256": "b" * 64,
            "size_bytes": 1,
        },
    },
    "raw_source_bytes_committed": False,
    "source_bytes_policy": "URL_AND_CAPTURE_SHA256_RECORDED",
}


def cell(**overrides) -> dict:
    row = {
        "month": "2021-08",
        "ticker": "AKBNK",
        "knowledge_cutoff_at": "2021-07-30T18:10:00+03:00",
        "ovp": "2021-2023",
        "ovp_published_at": "2020-09-29T23:59:59+03:00",
        "macro_cap": "0.112678288431061806656101426",
        "status": "OFFICIAL_CUTOFF_ELIGIBLE_MACRO_CAP",
        "source_sha256": "a" * 64,
        "source_file": "a.pdf",
    }
    row.update(overrides)
    return row


P3_STUB = {("2021-08", "AKBNK"): {"historical_family": "BANK"}}


def verify(rows, p3=None) -> dict:
    return verify_macro_cap(rows, RECEIPT_STUB, P3_STUB if p3 is None else p3)


def test_a_consistent_cell_verifies():
    assert verify([cell()])["status"] == "RESOLVED_FROM_OFFICIAL_DATED_SOURCES"


def test_a_wrong_macro_cap_value_is_caught():
    result = verify([cell(macro_cap="0.200000000000000000000000000")])
    assert result["status"] == "VERIFICATION_FAILED"
    assert result["checks"]["arithmetic_recomputed_from_recorded_gdp_pairs"]["verified"] is False


def test_a_vintage_published_after_the_cutoff_is_caught():
    # The 2022-2024 programme was published a year after this cutoff.
    result = verify(
        [
            cell(
                ovp="2022-2024",
                ovp_published_at="2021-09-05T23:59:59+03:00",
                macro_cap="0.137816613206503705342329388",
            )
        ]
    )
    assert result["status"] == "VERIFICATION_FAILED"
    assert result["checks"]["no_publication_after_cutoff"]["verified"] is False


def test_using_a_stale_vintage_when_a_newer_one_was_eligible_is_caught():
    result = verify(
        [cell(knowledge_cutoff_at="2022-01-31T18:10:00+03:00")]
    )
    assert result["status"] == "VERIFICATION_FAILED"
    checks = result["checks"]["latest_cutoff_eligible_vintage_selected"]
    assert checks["verified"] is False
    assert checks["violations"][0]["expected_vintage"] == "2022-2024"


def test_a_cell_outside_p3_is_caught():
    result = verify([cell()], p3={})
    assert result["status"] == "VERIFICATION_FAILED"
    assert result["checks"]["every_cell_exists_in_p3"]["verified"] is False


def test_a_non_bank_cell_is_caught():
    result = verify([cell()], p3={("2021-08", "AKBNK"): {"historical_family": "NONFIN"}})
    assert result["status"] == "VERIFICATION_FAILED"
    assert result["checks"]["every_cell_is_a_bank_family_cell"]["verified"] is False


def test_an_unknown_vintage_fails_closed():
    with pytest.raises(W6BAuditError, match="VINTAGE_NOT_IN_RECEIPT"):
        verify([cell(ovp="1999-2001")])


# --------------------------------------------------------------------------
# coe stays blocked
# --------------------------------------------------------------------------


def test_coe_is_blocked_with_exhausted_routes(stored):
    coe = stored["coe_blocked.json"]
    assert coe["status"] == "BLOCKED"
    assert coe["reason_code"] == "COE_METHODOLOGY_UNVERSIONED_AND_INPUT_LINEAGE_INCOMPLETE"
    assert coe["covered_cells"] == 0
    assert len(coe["exhausted_free_routes"]) == 3
    assert coe["usable_free_cutoff_dated_series_found"] is False
    assert coe["reopen_condition"]


def test_the_manual_demo_constant_is_never_carried_into_history(stored, receipt):
    coe = stored["coe_blocked.json"]
    assert coe["repo_source_status"] == "MANUAL_DEMO_ONLY"
    assert coe["rejected_manual_value"] == 0.3705
    assert coe["backfill_or_forward_knowledge_used"] is False
    assert receipt["policy"]["current_assumption_carried_to_history"] is False


def test_coe_status_follows_the_research_record():
    research = {
        "coe": {
            "rejection": "SOME_OTHER_REASON",
            "covered_cells": 0,
            "repo_source_status": "MANUAL_DEMO_ONLY",
            "demo_value_rejected": 0.1,
            "research_source": "https://example.invalid",
            "free_source_exhaustion": {
                "bist_index_source": "u1", "bist_historical_index_access": "o1",
                "evds_source": "u2", "evds_status": "o2",
                "tcmb_method": "m", "tcmb_input_disclosure": "o3",
                "usable_free_cutoff_dated_coe_series_found": False,
            },
        }
    }
    assert build_coe_blocked(research)["reason_code"] == "SOME_OTHER_REASON"


# --------------------------------------------------------------------------
# Unlock bound
# --------------------------------------------------------------------------


def test_no_total_score_unlocks_from_this_package(stored):
    unlock = stored["unlock_bound.json"]
    assert unlock["bank_cells"] == 509
    assert unlock["overlap_with_priority_cells"] == 0
    assert unlock["overlap_with_at_least_five_module_cells"] == 0
    assert unlock["overlap_with_at_least_four_module_cells"] == 0
    assert unlock["total_score_unlock_upper_bound"] == 0


def test_the_residual_blockers_are_named(stored):
    unlock = stored["unlock_bound.json"]
    assert unlock["module_missing_counts"]["M1"] == 509
    assert unlock["module_missing_counts"]["Ek1"] == 509
    assert unlock["module_missing_counts"]["M2"] == 509
    assert unlock["best_module_count_if_both_parameters_resolved"] == 4
    assert unlock["modules_required_for_a_total"] == 6
    reasons = unlock["residual_blockers"]["core_diagnostic_reasons"]
    assert reasons["TECHNICAL_CORE_FAMILY_UNSUPPORTED_OR_CONFLICTING"] == 453
    assert reasons["OWN_REPORT_STATEMENT_SCOPE_CONFLICT"] == 56


def test_the_priority_cohort_is_recomputed_not_quoted(stored):
    # 3017 must fall out of the module counts, not be copied from a prior receipt.
    assert stored["unlock_bound.json"]["priority_cells_total"] == 3017


def test_bank_work_does_not_block_nonfin(stored):
    assert stored["unlock_bound.json"]["does_not_block_nonfin"] is True


def test_overlap_is_measured_not_assumed():
    p4 = {
        ("2021-08", "AKBNK"): {
            "module_values": {"M1": 1, "M2": None, "M3": 1, "Ek1": 1, "Ek4": 1, "Ek9": 1}
        }
    }
    p3 = {
        ("2021-08", "AKBNK"): {
            "partial_core_diagnostic_status": "OK",
            "partial_core_diagnostic_reasons": [],
        }
    }
    unlock = build_unlock_bound([cell()], p3, p4)
    # This synthetic cell IS a priority cell, so the overlap must not stay zero.
    assert unlock["priority_cells_total"] == 1
    assert unlock["overlap_with_priority_cells"] == 1


# --------------------------------------------------------------------------
# Determinism and receipt binding
# --------------------------------------------------------------------------


def test_stored_artifacts_match_the_receipt(receipt):
    for name in CONTENT_FILES:
        assert sha_bytes((AUDIT / name).read_bytes()) == receipt["output_sha256"][name]


def test_second_derivation_is_byte_identical(receipt):
    content = derive()
    for name in CONTENT_FILES:
        assert sha_bytes(content[name]) == receipt["output_sha256"][name]


def test_receipt_declares_no_model_change_and_no_repair(receipt):
    policy = receipt["policy"]
    assert policy["model_changed"] is False
    assert policy["weights_changed"] is False
    assert policy["neutral_fill"] is False
    assert policy["cells_repaired"] == 0
    assert policy["backfill_or_forward_knowledge_used"] is False


def test_every_cell_row_stays_blocked_and_carries_its_evidence():
    rows = [
        json.loads(line)
        for line in (AUDIT / "cells.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 509
    assert all(row["cell_status"] == "BLOCKED" for row in rows)
    assert all(row["coe_status"] == "BLOCKED" for row in rows)
    assert all(len(row["ovp_source_sha256"]) == 64 for row in rows)
    assert all(row["ovp_published_at"] <= row["knowledge_cutoff_at"] for row in rows)
    assert all("M2" in row["modules_missing"] for row in rows)


def test_no_windows_path_separator_leaks_into_the_artifacts():
    """A str(Path) on Windows would embed a backslash and change every hash."""
    for name in CONTENT_FILES:
        assert "\\" not in (AUDIT / name).read_text(encoding="utf-8"), name


def test_receipt_source_paths_are_posix(receipt):
    assert all("\\" not in value for value in receipt["source_paths"].values())
