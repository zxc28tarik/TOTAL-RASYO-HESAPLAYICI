"""W10 — contract and mutation tests for the P7 version-enumeration audit."""
from __future__ import annotations

import json

import pytest

from scripts.audit_w10_p7_enumeration import (
    AUDIT,
    CONTENT_FILES,
    CONTRACT,
    RISK_ID,
    W10AuditError,
    build_enumeration_gap,
    build_issue24_criteria,
    build_sampled_month_probe,
    derive,
    sha_bytes,
)

RECEIPT = AUDIT / "receipt.json"

pytestmark = pytest.mark.skipif(
    not RECEIPT.exists(), reason="W10 audit artifacts not materialized"
)


@pytest.fixture(scope="module")
def receipt() -> dict:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stored() -> dict:
    return {
        name: json.loads((AUDIT / name).read_text(encoding="utf-8"))
        for name in CONTENT_FILES
    }


# --------------------------------------------------------------------------
# Issue #24 criteria
# --------------------------------------------------------------------------


def test_issue24_stays_blocked(stored):
    criteria = stored["issue24_criteria.json"]
    assert criteria["verdict"] == "BLOCKED"
    assert criteria["closure_allowed"] is False


def test_the_met_and_unmet_criteria_are_named(stored):
    criteria = stored["issue24_criteria.json"]
    assert criteria["criteria_total"] == 8
    assert criteria["criteria_met"] == 6
    assert set(criteria["criteria_unmet"]) == {
        "pit_version_identifiers",
        "sector_family_input_coverage",
    }


def test_source_identity_and_timestamps_are_actually_measured(stored):
    criteria = stored["issue24_criteria.json"]["criteria"]
    identity = criteria["real_source_identity_and_hashes"]
    assert identity["met"] is True
    assert identity["measured"]["selected_reports"] == 5990
    assert identity["measured"]["distinct_notification_ids"] == 2115
    timestamps = criteria["pit_publication_timestamps"]
    assert timestamps["met"] is True
    assert timestamps["measured"]["published_after_cutoff"] == 0


def test_no_selected_report_carries_a_version_identifier(stored):
    measured = stored["issue24_criteria.json"]["criteria"]["pit_version_identifiers"]["measured"]
    assert measured["with_version_metadata"] == 0
    assert measured["without_version_metadata"] == 5990
    assert measured["historical_version_enumeration_complete_true"] == 0


def test_the_unresolved_sector_families_are_counted_not_hidden(stored):
    families = stored["issue24_criteria.json"]["criteria"]["sector_family_input_coverage"]
    assert families["met"] is False
    assert families["measured"]["family_counts"]["None"] == 187


def test_criteria_verdict_follows_the_measurements():
    # A synthetic universe that satisfies everything must flip the verdict, so
    # the BLOCKED result cannot be a constant.
    cell = {
        "month": "2021-08",
        "ticker": "AAA",
        "status": "EXPLICIT_REJECTION",
        "knowledge_cutoff_at": "2021-07-30T18:10:00+03:00",
        "historical_family": "NONFIN",
        "selected_report": {
            "member_sha256": "a" * 64,
            "notification_id": 1,
            "archive_name": "KAP_2021_3A.zip",
            "published_at": "2021-05-05T22:31:20+03:00",
            "source_url": "https://kap.org.tr/tr/Bildirim/1",
            "historical_version_enumeration_complete": True,
        },
    }
    criteria = build_issue24_criteria([cell])
    assert criteria["criteria"]["pit_version_identifiers"]["met"] is True
    assert criteria["criteria"]["sector_family_input_coverage"]["met"] is True
    # Coverage still fails for a one-cell universe, so the audit stays honest.
    assert criteria["criteria"]["exact_60_month_coverage"]["met"] is False
    assert criteria["verdict"] == "BLOCKED"


def compliant_universe() -> list[dict]:
    """60 months x 100 members, every Issue #24 criterion satisfied."""
    cells = []
    for month_index in range(60):
        month = f"{2021 + month_index // 12}-{month_index % 12 + 1:02d}"
        for ticker_index in range(100):
            cells.append(
                {
                    "month": month,
                    "ticker": f"T{ticker_index:03d}",
                    "status": "EXPLICIT_REJECTION",
                    "knowledge_cutoff_at": "2026-07-30T18:10:00+03:00",
                    "historical_family": "NONFIN",
                    "selected_report": {
                        "member_sha256": "a" * 64,
                        "notification_id": month_index * 100 + ticker_index,
                        "archive_name": "KAP_2021_3A.zip",
                        "published_at": "2021-05-05T22:31:20+03:00",
                        "source_url": "https://kap.org.tr/tr/Bildirim/1",
                        "historical_version_enumeration_complete": True,
                    },
                }
            )
    return cells


def test_the_verdict_flips_when_every_criterion_is_satisfied():
    # A hardcoded BLOCKED verdict would be indistinguishable from a measured one
    # unless some input can actually satisfy the criteria.
    criteria = build_issue24_criteria(compliant_universe())
    assert criteria["criteria_unmet"] == []
    assert criteria["criteria_met"] == criteria["criteria_total"]
    assert criteria["closure_allowed"] is True
    assert criteria["verdict"] == "CLOSURE_CRITERIA_SATISFIED"


def test_a_report_published_after_its_cutoff_is_caught():
    cell = {
        "month": "2021-08",
        "ticker": "AAA",
        "status": "EXPLICIT_REJECTION",
        "knowledge_cutoff_at": "2021-07-30T18:10:00+03:00",
        "historical_family": "NONFIN",
        "selected_report": {
            "member_sha256": "a" * 64,
            "notification_id": 1,
            "archive_name": "KAP_2021_3A.zip",
            "published_at": "2021-08-05T22:31:20+03:00",
            "source_url": "https://kap.org.tr/tr/Bildirim/1",
        },
    }
    criteria = build_issue24_criteria([cell])
    timestamps = criteria["criteria"]["pit_publication_timestamps"]
    assert timestamps["met"] is False
    assert timestamps["measured"]["published_after_cutoff"] == 1


def test_an_example_source_would_be_caught():
    cell = {
        "month": "2021-08",
        "ticker": "AAA",
        "status": "EXPLICIT_REJECTION",
        "knowledge_cutoff_at": "2021-07-30T18:10:00+03:00",
        "historical_family": "NONFIN",
        "selected_report": {
            "member_sha256": "a" * 64,
            "notification_id": 1,
            "archive_name": "KAP_2021.example.zip",
            "published_at": "2021-05-05T22:31:20+03:00",
            "source_url": "https://kap.org.tr/tr/Bildirim/1",
        },
    }
    criteria = build_issue24_criteria([cell])
    assert criteria["criteria"]["no_example_or_fixture_fallback"]["met"] is False


# --------------------------------------------------------------------------
# Enumeration gap
# --------------------------------------------------------------------------


def test_the_gap_is_quantified_over_distinct_reports(stored):
    gap = stored["enumeration_gap.json"]
    assert gap["risk_id"] == RISK_ID
    assert gap["distinct_selected_reports"] == 2115
    assert gap["reports_with_a_known_version_chain"] == 0
    assert gap["reports_carrying_the_risk_flag"] == 5990


def test_the_sampled_rate_is_reported_with_its_caveat(stored):
    sample = stored["enumeration_gap.json"]["sampled_supersession_rate"]
    assert sample["financial_report_disclosures"] == 423
    assert sample["marked_superseded"] == 9
    assert sample["rate"] == pytest.approx(9 / 423)
    assert "not a retention guarantee" in sample["caveat"]
    assert stored["enumeration_gap.json"]["indicative_exposed_reports"] == 45


def test_the_authoritative_label_stays_disallowed(stored):
    gap = stored["enumeration_gap.json"]
    assert gap["authoritative_label_allowed"] is False
    assert gap["retained_profile"] == "EXPERIMENTAL_RISK_ACCEPTED_5Y"


def test_bulk_archives_are_shown_to_be_structurally_unable_to_answer(stored):
    why = stored["enumeration_gap.json"]["why_the_bulk_archives_cannot_answer_this"]
    assert why["original_disclosure_in_bulk_archive"] is False
    assert why["changed_numeric_slots"] == 4
    assert why["numeric_slots_compared"] == 437


def test_an_authoritative_label_cannot_be_smuggled_past_derive(monkeypatch):
    import scripts.audit_w10_p7_enumeration as auditor

    original = auditor.build_enumeration_gap

    def permissive(*args, **kwargs):
        gap = original(*args, **kwargs)
        gap["authoritative_label_allowed"] = True
        return gap

    monkeypatch.setattr(auditor, "build_enumeration_gap", permissive)
    with pytest.raises(W10AuditError, match="AUTHORITATIVE_LABEL"):
        auditor.derive()


def test_the_rate_follows_the_query_bytes():
    rows = [
        {"disclosureCategory": "FR", "modifyStatus": "DUZELTILEN"},
        {"disclosureCategory": "FR", "modifyStatus": None},
        {"disclosureCategory": "ODA", "modifyStatus": "DUZELTILEN"},
    ]
    pair = {
        "correction_relation": {"older": "1", "newer": "2"},
        "numeric_slots_compared": 1,
        "changed_numeric_slots": [],
    }
    gap = build_enumeration_gap([], rows, pair)
    # The non-financial ODA row must not inflate the financial-report rate.
    assert gap["sampled_supersession_rate"]["financial_report_disclosures"] == 2
    assert gap["sampled_supersession_rate"]["marked_superseded"] == 1
    # The report count must come from the cells handed in, not from a constant.
    assert gap["distinct_selected_reports"] == 0
    assert gap["selected_reports"] == 0
    assert gap["indicative_exposed_reports"] == 0


# --------------------------------------------------------------------------
# Sampled-month contamination probe
# --------------------------------------------------------------------------


def test_the_probe_finds_no_contamination_in_the_sampled_month(stored):
    probe = stored["sampled_month_probe.json"]
    assert probe["corrections_found"] == 19
    assert probe["corrections_touching_a_universe_ticker"] == 1
    assert probe["corrections_selected_by_a_cell"] == 0
    assert probe["contamination_detected"] is False
    assert "fifty-nine" in probe["conclusion"]


def test_the_probe_would_report_contamination_when_it_exists():
    cells = [
        {
            "ticker": "AAA",
            "selected_report": {"notification_id": 99},
        }
    ]
    rows = [
        {
            "disclosureCategory": "FR",
            "modifyStatus": "DUZENLENEN",
            "disclosureIndex": 99,
            "stockCodes": "AAA",
            "publishDate": "01.03.2023 10:00:00",
            "year": 2022,
            "period": 4,
        }
    ]
    receipt = {"url": "u", "response_sha256": "s", "http_status": 200}
    probe = build_sampled_month_probe(cells, rows, receipt)
    assert probe["contamination_detected"] is True
    assert probe["corrections_selected_by_a_cell"] == 1
    assert probe["findings"][0]["tickers_in_universe"] == ["AAA"]


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


def test_all_four_ledger_routes_are_graded(stored):
    routes = stored["routes.json"]
    assert len(routes["routes"]) == 4
    assert {route["status"] for route in routes["routes"]} == {
        "INSUFFICIENT_PROVEN",
        "DEMONSTRATED_RETENTION_COMPLETENESS_UNPROVEN",
        "WORKS_PER_DISCLOSURE_NO_GLOBAL_COMPLETENESS",
        "TIMESTAMPS_COMPLETE_VERSION_CHAIN_ABSENT",
    }
    assert all(route["evidence"] for route in routes["routes"])
    assert all(route["cost"].startswith("free") for route in routes["routes"])


def test_the_query_route_is_recorded_as_having_returned_superseded_rows(stored):
    route = next(
        item
        for item in stored["routes.json"]["routes"]
        if item["status"] == "DEMONSTRATED_RETENTION_COMPLETENESS_UNPROVEN"
    )
    assert route["evidence"]["superseded_rows_returned"] == 9
    assert route["evidence"]["http_status"] == 200


def test_no_paid_source_was_treated_as_the_blocker(stored, receipt):
    routes = stored["routes.json"]
    assert routes["paid_source_used"] is False
    assert receipt["policy"]["paid_source_used"] is False
    assert routes["verdict"] == "BLOCKED"
    assert routes["blocked_reason"] == RISK_ID
    assert routes["reopen_condition"]


def test_the_unexecuted_free_route_is_named_with_its_limit(stored):
    unexecuted = stored["routes.json"]["a_free_route_exists_but_is_unexecuted"]
    assert unexecuted["route"] == "official distribution/API enumeration"
    assert unexecuted["what_it_would_yield"]
    assert unexecuted["what_it_still_would_not_yield"]


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


def test_receipt_declares_an_offline_read_only_audit(receipt):
    policy = receipt["policy"]
    assert policy["network_access_performed"] is False
    assert policy["source_selection_changed"] is False
    assert policy["authoritative_label_applied"] is False
    assert policy["production_code_changed"] is False


def test_every_artifact_declares_the_same_contract(stored):
    assert {item["contract"] for item in stored.values()} == {CONTRACT}


def test_no_windows_path_separator_leaks_into_the_artifacts():
    """A str(Path) on Windows would embed a backslash and change every hash."""
    for name in CONTENT_FILES:
        assert "\\" not in (AUDIT / name).read_text(encoding="utf-8"), name


def test_receipt_source_paths_are_posix(receipt):
    assert all("\\" not in value for value in receipt["source_paths"].values())
