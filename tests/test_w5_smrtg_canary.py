"""W5 — contract and mutation tests for the SMRTG 2023-08 historical M2 canary.

Every gate the canary claims to apply gets a synthetic counter-case here.  A
gate that cannot be made to fail is a sentence, not a check, so the negative
cases matter more than the positive ones.
"""
from __future__ import annotations

from datetime import datetime
import json

import pytest

from scripts.audit_w5_smrtg_canary import (
    AUDIT,
    CONTENT_FILES,
    CONTRACT,
    CUTOFF,
    TARGET_TICKER,
    W5AuditError,
    assert_verdict_is_self_consistent,
    build_peer_certifiability,
    build_systemic_bound,
    build_verdict,
    classify_profile_mismatch,
    derive,
    nonfin_candidates,
    sha_bytes,
    share_derivation_of,
    verify_share_basis_supersession,
    verify_share_certification,
)

RECEIPT = AUDIT / "receipt.json"

pytestmark = pytest.mark.skipif(
    not RECEIPT.exists(), reason="W5 audit artifacts not materialized"
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
# Synthetic inputs
# --------------------------------------------------------------------------


def share_history(*items: tuple[str, str, str]) -> list:
    return [
        {
            "creationDate": created,
            "value": [
                {"nominalValueOfShares": nominal, "nominalValuePerShare": per_share},
            ],
        }
        for created, nominal, per_share in items
    ]


def clean_share_inputs() -> tuple[dict, dict, list]:
    """A minimal input set that passes every share/action check."""
    history = share_history(
        ("25/03/2022 17:21:27", "100.000", "1,00"),
        ("31/07/2023 17:49:42", "200.000", "1,00"),
        ("30/01/2026 09:24:46", "600.000", "1,00"),
    )
    receipt = {
        "contract": "ZERO_INTERVAL_VERIFIED_SHARE_BASIS_V1",
        "shares_out": 200000,
        "raw_close": 10.0,
        "market_cap": 2000000.0,
        "share_state_published_at": "2023-07-31T17:49:42+03:00",
        "nominal_value_per_share_assumed": False,
        "nonempty_action_completeness_claimed": False,
    }
    manifest = {
        "shares_basis_date": "2023-07-31",
        "events": [],
        "enumeration_complete": True,
        "completeness_scope": "EMPTY_OPEN_CLOSED_DATE_INTERVAL_(2023-07-31,2023-07-31]",
    }
    return receipt, manifest, history


def quarter(profile: str = "P", version: int = 1, shares: object = 100.0, route: str = "X") -> dict:
    return {
        "derivation_profile": profile,
        "derivation_version": version,
        "values": {"shares_out": shares},
        "diagnostics": {"semantic_profile": "S", "field_sources": {"shares_out": route}},
    }


def payload(quarters: int = 4, family: str = "NONFIN", **kwargs) -> dict:
    return {
        "technical_family": family,
        "quarters": [quarter(**kwargs) for _ in range(quarters)],
    }


# --------------------------------------------------------------------------
# W5-A — share and action evidence
# --------------------------------------------------------------------------


def test_the_share_action_gate_passes_on_real_evidence(stored):
    certification = stored["cell_verification.json"]["share_certification"]
    assert certification["share_action_gate_passed"] is True
    assert all(check["verified"] for check in certification["checks"].values())


def test_the_point_in_time_observation_is_the_one_selected(stored):
    certification = stored["cell_verification.json"]["share_certification"]
    assert certification["selected_observation"]["created_at"] == "2023-07-31T17:49:42"
    assert certification["selected_observation"]["derived_shares"] == 605880000.0
    assert certification["recorded_shares_out"] == 605880000


def test_the_post_cutoff_observation_is_excluded_and_would_have_mattered(stored):
    certification = stored["cell_verification.json"]["share_certification"]
    assert [row["created_at"] for row in certification["excluded_as_post_cutoff"]] == [
        "2026-01-30T09:24:46"
    ]
    assert certification["latest_known_observation"]["derived_shares"] == 1817640000.0
    assert certification["look_ahead_would_change_the_share_basis"] is True


def test_the_action_interval_is_empty_because_the_gap_is_zero(stored):
    check = stored["cell_verification.json"]["share_certification"]["checks"][
        "action_interval_is_empty"
    ]
    assert check["verified"] is True
    assert "2023-07-31" in check["evidence"]


def test_a_synthetic_clean_input_passes_every_check():
    result = verify_share_certification(*clean_share_inputs())
    assert result["share_action_gate_passed"] is True


def test_the_gate_fails_when_a_post_cutoff_basis_is_recorded():
    receipt, manifest, history = clean_share_inputs()
    receipt["shares_out"] = 600000  # the 2026 state
    result = verify_share_certification(receipt, manifest, history)
    assert result["checks"]["latest_eligible_observation_selected"]["verified"] is False
    assert result["checks"]["recorded_basis_is_pit_not_latest_known"]["verified"] is False
    assert result["share_action_gate_passed"] is False


def test_the_gate_fails_when_the_action_interval_is_not_empty():
    receipt, manifest, history = clean_share_inputs()
    manifest["events"] = [{"type": "BONUS_ISSUE", "date": "2023-07-30"}]
    result = verify_share_certification(receipt, manifest, history)
    assert result["checks"]["action_interval_is_empty"]["verified"] is False
    assert result["share_action_gate_passed"] is False


def test_the_gate_fails_when_the_interval_enumeration_is_incomplete():
    receipt, manifest, history = clean_share_inputs()
    manifest["enumeration_complete"] = False
    assert verify_share_certification(receipt, manifest, history)["share_action_gate_passed"] is False


def test_the_gate_fails_when_the_basis_date_is_not_the_cutoff_date():
    receipt, manifest, history = clean_share_inputs()
    manifest["shares_basis_date"] = "2023-07-28"
    assert verify_share_certification(receipt, manifest, history)["share_action_gate_passed"] is False


def test_the_gate_fails_when_a_per_share_nominal_was_assumed():
    receipt, manifest, history = clean_share_inputs()
    receipt["nominal_value_per_share_assumed"] = True
    result = verify_share_certification(receipt, manifest, history)
    assert result["checks"]["nominal_value_per_share_not_assumed"]["verified"] is False
    assert result["share_action_gate_passed"] is False


def test_the_gate_fails_when_a_non_empty_interval_is_claimed_complete():
    receipt, manifest, history = clean_share_inputs()
    receipt["nonempty_action_completeness_claimed"] = True
    result = verify_share_certification(receipt, manifest, history)
    assert result["checks"]["non_empty_action_completeness_not_claimed"]["verified"] is False
    assert result["share_action_gate_passed"] is False


def test_a_post_cutoff_observation_is_never_eligible():
    receipt, manifest, history = clean_share_inputs()
    result = verify_share_certification(receipt, manifest, history)
    eligible = [row for row in result["observations"] if row["at_or_before_cutoff"]]
    assert [row["created_at"] for row in eligible] == [
        "2022-03-25T17:21:27",
        "2023-07-31T17:49:42",
    ]


# --------------------------------------------------------------------------
# W5-A — the unsafe artifact route and its supersession
# --------------------------------------------------------------------------


def test_the_artifact_carries_a_stale_unsafe_share_basis(stored):
    supersession = stored["cell_verification.json"]["share_basis_supersession"]
    assert supersession["artifact_share_derivation"] == "ISSUED_CAPITAL_OVER_NOMINAL"
    assert supersession["artifact_share_derivation_is_safe"] is False
    assert supersession["artifact_shares_out"] == 306000000.0
    assert supersession["certified_shares_out"] == 605880000
    assert supersession["bases_differ"] is True
    assert supersession["certified_over_artifact_ratio"] == pytest.approx(1.98)


def test_the_recorded_market_cap_proves_which_basis_was_used(stored):
    supersession = stored["cell_verification.json"]["share_basis_supersession"]
    assert supersession["checks"]["market_cap_follows_the_certified_basis"]["verified"] is True
    assert (
        supersession["checks"]["market_cap_does_not_follow_the_artifact_basis"]["verified"] is True
    )
    assert supersession["certified_basis_supersedes_artifact"] is True
    assert supersession["recorded_market_cap"] == pytest.approx(
        supersession["certified_shares_out"] * supersession["raw_close"]
    )


def test_supersession_is_refused_when_the_market_cap_follows_the_artifact():
    receipt, manifest, history = clean_share_inputs()
    receipt["market_cap"] = 100.0 * 10.0  # the artifact basis, not the certified one
    certification = verify_share_certification(receipt, manifest, history)
    result = verify_share_basis_supersession(payload(), certification, receipt)
    assert result["checks"]["market_cap_follows_the_certified_basis"]["verified"] is False
    assert result["certified_basis_supersedes_artifact"] is False


def test_supersession_is_refused_when_the_share_action_gate_failed():
    receipt, manifest, history = clean_share_inputs()
    receipt["nominal_value_per_share_assumed"] = True
    certification = verify_share_certification(receipt, manifest, history)
    result = verify_share_basis_supersession(payload(), certification, receipt)
    assert certification["share_action_gate_passed"] is False
    assert result["certified_basis_supersedes_artifact"] is False


# --------------------------------------------------------------------------
# W5-B — the derivation profile mismatch
# --------------------------------------------------------------------------


def test_the_mismatch_origin_is_config_and_resolved(stored):
    profile = stored["cell_verification.json"]["derivation_profile"]
    assert profile["origin"] == "CONFIG"
    assert profile["resolved"] is True
    assert profile["is_data_defect"] is False
    assert profile["is_routing_or_code_defect"] is False
    assert profile["artifact_profile"] == "KAP_BULK_GENERAL_HOLDING_CORE_EXACT_V1"
    assert profile["default_config"]["profile"] == "KAP_NONBANK_CORE_EXAMPLE"
    assert profile["exact_config"]["profile"] == profile["artifact_profile"]


def test_the_profile_was_not_renamed_or_forced(stored):
    profile = stored["cell_verification.json"]["derivation_profile"]
    assert profile["forced_profile_rename_allowed"] is False
    assert "SOURCE_DERIVATION_PROFILE_MISMATCH" in " ".join(profile["default_config"]["blockers"])
    assert not [
        blocker
        for blocker in profile["exact_config"]["blockers"]
        if blocker.startswith("SOURCE_DERIVATION_PROFILE_MISMATCH")
    ]


def test_an_unclaimed_profile_is_reported_unresolved():
    target = payload(profile="ARTIFACT_A", version=1)
    result = classify_profile_mismatch(
        target,
        {"source_derivation_profile": "OTHER", "source_derivation_version": 1},
        {"source_derivation_profile": "ALSO_OTHER", "source_derivation_version": 1},
    )
    assert result["origin"] == "UNRESOLVED"
    assert result["resolved"] is False
    assert result["is_config"] is False


def test_a_matching_profile_at_the_wrong_version_does_not_resolve_it():
    target = payload(profile="ARTIFACT_A", version=2)
    result = classify_profile_mismatch(
        target,
        {"source_derivation_profile": "OTHER", "source_derivation_version": 1},
        {"source_derivation_profile": "ARTIFACT_A", "source_derivation_version": 1},
    )
    assert result["origin"] == "UNRESOLVED"
    assert result["resolved"] is False


def test_no_mismatch_at_all_is_reported_as_none():
    target = payload(profile="ARTIFACT_A", version=1, route="EXPLICIT_CLASS_NOMINAL_SUM")
    result = classify_profile_mismatch(
        target,
        {"source_derivation_profile": "ARTIFACT_A", "source_derivation_version": 1},
        {"source_derivation_profile": "ARTIFACT_A", "source_derivation_version": 1},
    )
    assert result["origin"] == "NONE"
    assert result["resolved"] is True


def test_an_unsafe_share_route_is_always_carried_as_a_residual_blocker():
    target = payload(profile="ARTIFACT_A", version=1, route="ISSUED_CAPITAL_OVER_NOMINAL")
    result = classify_profile_mismatch(
        target,
        {"source_derivation_profile": "ARTIFACT_A", "source_derivation_version": 1},
        {"source_derivation_profile": "ARTIFACT_A", "source_derivation_version": 1},
    )
    assert result["residual_share_derivation_blocker"] == [
        "UNPROVEN_SHARE_DERIVATION:ISSUED_CAPITAL_OVER_NOMINAL"
    ]


# --------------------------------------------------------------------------
# The cohort
# --------------------------------------------------------------------------


def test_the_cohort_reproduces_the_prior_canary_receipt(stored):
    cell = stored["cell_verification.json"]
    assert cell["prior_receipt_reproduced"] is True
    assert cell["peer_cohort"]["financial_candidate_count"] == 75
    assert cell["peer_cohort"]["safe_peer_count"] == 0
    assert cell["peer_cohort"]["unsafe_share_candidate_count"] == 74
    assert cell["peer_cohort"]["peer_gate_passed"] is False


def test_the_only_safe_candidate_is_the_externally_certified_target(stored):
    cohort = stored["cell_verification.json"]["peer_cohort"]
    assert cohort["safe_share_candidates"] == [TARGET_TICKER]
    assert cohort["safe_peer_tickers"] == []


def test_the_peer_threshold_is_not_relaxed(stored):
    cell = stored["cell_verification.json"]
    assert cell["thresholds"]["minimum_peer_count"] == 5
    assert cell["thresholds"]["relaxed"] is False
    assert cell["peer_cohort"]["minimum_peer_count"] == 5


def test_derive_refuses_a_cohort_that_no_longer_reproduces_the_prior_receipt(monkeypatch):
    import scripts.audit_w5_smrtg_canary as module

    real = module.audit_real_peer_cohort

    def drifted(*args, **kwargs):
        cohort = dict(real(*args, **kwargs))
        cohort["safe_peer_count"] = cohort["safe_peer_count"] + 1
        return cohort

    monkeypatch.setattr(module, "audit_real_peer_cohort", drifted)
    with pytest.raises(W5AuditError, match="PRIOR_CANARY_COHORT_NOT_REPRODUCED"):
        module.derive()


def test_derive_refuses_a_scan_that_disagrees_with_the_production_helper(monkeypatch):
    import scripts.audit_w5_smrtg_canary as module

    real = module.nonfin_candidates

    def narrower(per_ticker):
        # A local copy of the admission rule that has silently drifted.
        return real(per_ticker)[:-1]

    monkeypatch.setattr(module, "nonfin_candidates", narrower)
    with pytest.raises(W5AuditError, match="SCAN_AND_PRODUCTION_COHORT_DISAGREE"):
        module.derive()


def test_the_cohort_admission_rule_needs_four_quarters_and_the_nonfin_family():
    per_ticker = {
        "AAA": payload(quarters=4),
        "BBB": payload(quarters=3),
        "CCC": payload(quarters=8, family="BANK"),
        "DDD": {"technical_family": "NONFIN", "quarters": "not-a-list"},
    }
    assert nonfin_candidates(per_ticker) == ["AAA"]


def test_the_share_route_is_read_from_the_latest_quarter():
    target = payload(route="ISSUED_CAPITAL_OVER_NOMINAL")
    assert share_derivation_of(target) == "ISSUED_CAPITAL_OVER_NOMINAL"
    assert share_derivation_of({"quarters": []}) is None
    assert share_derivation_of({"quarters": [{"diagnostics": {}}]}) is None


# --------------------------------------------------------------------------
# Peer certifiability
# --------------------------------------------------------------------------


def test_no_peer_is_zero_interval_certifiable_at_this_cutoff(stored):
    peers = stored["peer_certifiability.json"]
    assert peers["unsafe_peers"] == 74
    assert peers["zero_interval_certifiable_peers"] == []
    assert peers["reason_counts"] == {
        "NON_EMPTY_ACTION_INTERVAL_UNPROVEN": 59,
        "NO_USABLE_PRE_CUTOFF_SHARE_CLASS_OBSERVATION": 15,
    }


def test_the_bounded_unblock_target_names_the_five_shortest_windows(stored):
    target = stored["peer_certifiability.json"]["bounded_unblock_target"]
    assert target["peers_required"] == 5
    assert [row["ticker"] for row in target["candidates"]] == [
        "BRSAN",
        "QUAGR",
        "TUKAS",
        "TTRAK",
        "ZOREN",
    ]
    assert [row["days"] for row in target["candidates"]] == sorted(
        row["days"] for row in target["candidates"]
    )


def test_the_action_inventory_is_recorded_as_unable_to_certify_them(stored):
    detail = stored["peer_certifiability.json"]["why_the_action_inventory_cannot_certify_them"]
    assert detail["peers_with_zero_rows_over_six_years"] == ["BRSAN", "QUAGR", "TUKAS", "ZOREN"]
    assert "absence of evidence" in detail["detail"]


def test_a_same_day_observation_is_the_only_thing_that_certifies_a_peer():
    cohort = {"unsafe_share_tickers": ["SAME_DAY", "ONE_DAY", "NONE_AT_ALL"], "minimum_peer_count": 1}
    observations = {
        "SAME_DAY": [datetime(2023, 7, 31, 9, 0, 0)],
        "ONE_DAY": [datetime(2023, 7, 30, 9, 0, 0)],
    }
    result = build_peer_certifiability(cohort, observations, {})
    reasons = {row["ticker"]: row["blocking_reason"] for row in result["rows"]}
    assert reasons == {
        "SAME_DAY": "ZERO_INTERVAL_CERTIFIABLE",
        "ONE_DAY": "NON_EMPTY_ACTION_INTERVAL_UNPROVEN",
        "NONE_AT_ALL": "NO_USABLE_PRE_CUTOFF_SHARE_CLASS_OBSERVATION",
    }
    assert result["zero_interval_certifiable_peers"] == ["SAME_DAY"]


def test_a_post_cutoff_observation_does_not_certify_a_peer():
    cohort = {"unsafe_share_tickers": ["FUTURE"], "minimum_peer_count": 1}
    observations = {"FUTURE": [datetime(2026, 1, 30, 9, 0, 0)]}
    result = build_peer_certifiability(cohort, observations, {})
    assert result["rows"][0]["blocking_reason"] == "NO_USABLE_PRE_CUTOFF_SHARE_CLASS_OBSERVATION"
    assert result["rows"][0]["latest_pre_cutoff_observation"] is None


def test_actions_inside_the_window_are_counted():
    cohort = {"unsafe_share_tickers": ["ACT"], "minimum_peer_count": 1}
    observations = {"ACT": [datetime(2023, 7, 1, 9, 0, 0)]}
    actions = {"ACT": ["2023-06-30", "2023-07-01", "2023-07-15", "2023-07-31", "2023-08-01"]}
    row = build_peer_certifiability(cohort, observations, actions)["rows"][0]
    # Open-closed: the observation day itself is excluded, the cutoff day included.
    assert row["yahoo_action_rows_inside_interval"] == 2
    assert row["yahoo_action_rows_all_time"] == 5


# --------------------------------------------------------------------------
# The systemic bound
# --------------------------------------------------------------------------


def test_no_cutoff_in_the_window_can_reach_the_peer_gate(stored):
    systemic = stored["systemic_bound.json"]
    assert systemic["cutoffs"] == 60
    assert systemic["gate_reachable_cutoffs"] == 0
    assert systemic["route_a_safe_share_derivation"]["reachable"] is False
    assert systemic["route_b_zero_interval_certification"]["reachable"] is False


def test_the_safe_derivation_route_contributes_no_cell_anywhere(stored):
    route = stored["systemic_bound.json"]["route_a_safe_share_derivation"]
    assert route["safe_derivation_cells"] == 0
    assert route["nonfin_candidate_cells"] == 4203
    assert route["share_derivation_counts"] == {
        "ISSUED_CAPITAL_OVER_NOMINAL": 4089,
        "NULL": 114,
    }


def test_only_four_cutoffs_hold_even_one_certifiable_ticker(stored):
    route = stored["systemic_bound.json"]["route_b_zero_interval_certification"]
    assert route["certifiable_distribution"] == {"0": 56, "1": 4}
    assert route["best_cutoff_certifiable"] == 1
    assert route["required_certifiable_per_cutoff"] == 6
    assert [row["cutoff_date"] for row in route["cutoffs_with_any"]] == [
        "2021-08-31",
        "2023-05-31",
        "2023-07-31",
        "2026-04-30",
    ]


def test_the_gate_needs_one_more_certifiable_ticker_than_peers_required():
    per_cutoff = [
        {
            "analysis_at": "2023-07-31T18:10:00+03:00",
            "cutoff_date": "2023-07-31",
            "nonfin_candidates": 6,
            "share_derivation_counts": {"ISSUED_CAPITAL_OVER_NOMINAL": 6},
            "candidate_tickers": ["A", "B", "C", "D", "E", "F"],
        }
    ]
    same_day = {ticker: [datetime(2023, 7, 31, 9, 0, 0)] for ticker in "ABCDE"}
    # Five certifiable is one short: one of them is the target and cannot be its own peer.
    assert build_systemic_bound(per_cutoff, same_day, 5)["gate_reachable_cutoffs"] == 0
    same_day["F"] = [datetime(2023, 7, 31, 9, 0, 0)]
    assert build_systemic_bound(per_cutoff, same_day, 5)["gate_reachable_cutoffs"] == 1


def test_a_safe_derivation_cell_makes_the_first_route_reachable():
    per_cutoff = [
        {
            "analysis_at": "2023-07-31T18:10:00+03:00",
            "cutoff_date": "2023-07-31",
            "nonfin_candidates": 2,
            "share_derivation_counts": {
                "EXPLICIT_CLASS_NOMINAL_SUM": 1,
                "ISSUED_CAPITAL_OVER_NOMINAL": 1,
            },
            "candidate_tickers": ["A", "B"],
        }
    ]
    route = build_systemic_bound(per_cutoff, {}, 5)["route_a_safe_share_derivation"]
    assert route["safe_derivation_cells"] == 1
    assert route["nonfin_candidate_cells"] == 2
    assert route["reachable"] is True


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------


def test_the_verdict_is_blocked_on_the_peer_cohort_alone(stored):
    verdict = stored["verdict.json"]
    assert verdict["status"] == "BLOCKED"
    assert verdict["m2_admissible"] is False
    assert verdict["active_blockers"] == [
        "VERIFIED_SHARE_PEER_COHORT_INSUFFICIENT:safe_peers=0;required=5"
    ]


def test_the_canary_produced_no_score_of_any_kind(stored, receipt):
    verdict = stored["verdict.json"]
    for claim in (
        "m2_materialized",
        "total_materialized",
        "neutral_m2_materialized",
        "real_follow_period_materialized",
    ):
        assert verdict[claim] is False
    assert verdict["thresholds_relaxed"] is False
    assert receipt["policy"]["neutral_fill"] is False
    assert receipt["policy"]["cells_repaired"] == 0
    assert receipt["policy"]["production_code_changed"] is False


def test_the_three_things_this_audit_resolved_are_named(stored):
    resolved = stored["verdict.json"]["resolved_by_this_audit"]
    assert resolved["share_action_gate"] is True
    assert resolved["derivation_profile_origin"] == "CONFIG"
    assert resolved["derivation_profile_resolved"] is True
    assert resolved["unsafe_artifact_share_route_superseded"] is True


def test_both_reopen_conditions_are_recorded(stored):
    conditions = stored["verdict.json"]["reopen_conditions"]
    assert [row["id"] for row in conditions] == ["GENERAL", "BOUNDED_AT_THIS_CUTOFF"]
    assert len(conditions[1]["candidates"]) == 5


def test_the_verdict_follows_its_gates_rather_than_a_constant():
    def cell(peer_gate=True, share_gate=True, resolved=True, residual=(), superseded=True):
        return {
            "peer_cohort": {
                "peer_gate_passed": peer_gate,
                "safe_peer_count": 5 if peer_gate else 0,
                "minimum_peer_count": 5,
            },
            "share_certification": {"share_action_gate_passed": share_gate},
            "share_basis_supersession": {"certified_basis_supersedes_artifact": superseded},
            "derivation_profile": {
                "resolved": resolved,
                "origin": "CONFIG" if resolved else "UNRESOLVED",
                "residual_share_derivation_blocker": list(residual),
            },
        }

    peers = {"bounded_unblock_target": {"candidates": []}}
    systemic = {"conclusion": "x"}

    clear = build_verdict(cell(), peers, systemic)
    assert clear["status"] == "ADMISSIBLE_FOR_M2"
    assert clear["m2_admissible"] is True
    assert clear["active_blockers"] == []
    # Even a clear verdict materializes nothing.
    assert clear["m2_materialized"] is False

    for name, kwargs in (
        ("peer", {"peer_gate": False}),
        ("share", {"share_gate": False}),
        ("profile", {"resolved": False}),
    ):
        verdict = build_verdict(cell(**kwargs), peers, systemic)
        assert verdict["status"] == "BLOCKED", name
        assert verdict["active_blockers"], name


def test_an_unsafe_route_blocks_unless_the_certification_supersedes_it():
    base = {
        "peer_cohort": {"peer_gate_passed": True, "safe_peer_count": 5, "minimum_peer_count": 5},
        "share_certification": {"share_action_gate_passed": True},
        "derivation_profile": {
            "resolved": True,
            "origin": "CONFIG",
            "residual_share_derivation_blocker": [
                "UNPROVEN_SHARE_DERIVATION:ISSUED_CAPITAL_OVER_NOMINAL"
            ],
        },
    }
    peers = {"bounded_unblock_target": {"candidates": []}}
    systemic = {"conclusion": "x"}

    not_superseded = build_verdict(
        {**base, "share_basis_supersession": {"certified_basis_supersedes_artifact": False}},
        peers,
        systemic,
    )
    assert not_superseded["status"] == "BLOCKED"
    assert any(
        blocker.startswith("TARGET_SHARE_DERIVATION_UNPROVEN")
        for blocker in not_superseded["active_blockers"]
    )

    superseded = build_verdict(
        {**base, "share_basis_supersession": {"certified_basis_supersedes_artifact": True}},
        peers,
        systemic,
    )
    assert superseded["status"] == "ADMISSIBLE_FOR_M2"


def test_a_blocked_verdict_without_a_named_blocker_is_refused():
    verdict = {
        "status": "BLOCKED",
        "m2_admissible": False,
        "active_blockers": [],
        "m2_materialized": False,
        "total_materialized": False,
        "neutral_m2_materialized": False,
        "real_follow_period_materialized": False,
    }
    with pytest.raises(W5AuditError, match="BLOCKED_VERDICT_WITHOUT_A_NAMED_BLOCKER"):
        assert_verdict_is_self_consistent(verdict)


def test_an_unblocked_verdict_still_carrying_blockers_is_refused():
    verdict = {
        "status": "ADMISSIBLE_FOR_M2",
        "m2_admissible": True,
        "active_blockers": ["VERIFIED_SHARE_PEER_COHORT_INSUFFICIENT:safe_peers=0;required=5"],
        "m2_materialized": False,
        "total_materialized": False,
        "neutral_m2_materialized": False,
        "real_follow_period_materialized": False,
    }
    with pytest.raises(W5AuditError, match="UNBLOCKED_VERDICT_STILL_CARRIES_BLOCKERS"):
        assert_verdict_is_self_consistent(verdict)


def test_a_verdict_claiming_work_the_canary_did_not_do_is_refused():
    verdict = {
        "status": "BLOCKED",
        "m2_admissible": False,
        "active_blockers": ["VERIFIED_SHARE_PEER_COHORT_INSUFFICIENT:safe_peers=0;required=5"],
        "m2_materialized": True,
        "total_materialized": False,
        "neutral_m2_materialized": False,
        "real_follow_period_materialized": False,
    }
    with pytest.raises(W5AuditError, match="CANARY_CLAIMS_WORK_IT_DID_NOT_DO:m2_materialized"):
        assert_verdict_is_self_consistent(verdict)


def test_a_status_that_disagrees_with_admissibility_is_refused():
    verdict = {
        "status": "BLOCKED",
        "m2_admissible": True,
        "active_blockers": ["X"],
        "m2_materialized": False,
        "total_materialized": False,
        "neutral_m2_materialized": False,
        "real_follow_period_materialized": False,
    }
    with pytest.raises(W5AuditError, match="VERDICT_STATUS_AND_ADMISSIBILITY_DISAGREE"):
        assert_verdict_is_self_consistent(verdict)


# --------------------------------------------------------------------------
# Baseline and receipt
# --------------------------------------------------------------------------


def test_the_baseline_records_zero_historical_m2_before_this_audit(stored):
    baseline = stored["baseline.json"]
    assert baseline["historical_pit_m2_before"] == 0
    assert baseline["prior_canary"]["m2_materialized"] is False
    assert baseline["live_results_that_must_not_change"]["m2"] == 48
    assert baseline["live_results_that_must_not_change"]["total"] == 2


def test_stored_artifacts_match_the_receipt(receipt):
    for name in CONTENT_FILES:
        assert sha_bytes((AUDIT / name).read_bytes()) == receipt["output_sha256"][name]


def test_second_derivation_is_byte_identical(receipt):
    content = derive()
    for name in CONTENT_FILES:
        assert sha_bytes(content[name]) == receipt["output_sha256"][name]


def test_the_receipt_binds_every_source_it_read(receipt):
    assert receipt["hash_mode"] == "LF_CANONICAL_SHA256_V1"
    assert set(receipt["source_sha256"]) == set(receipt["source_paths"])
    assert len(receipt["source_sha256"]) == 10
    assert receipt["coverage"]["status"] == "BLOCKED"
    assert receipt["coverage"]["cutoffs_scanned"] == 60
    assert receipt["coverage"]["gate_reachable_cutoffs"] == 0
    assert receipt["profile"] == "EXPERIMENTAL_RISK_ACCEPTED_5Y"


def test_every_artifact_declares_the_same_contract(stored):
    for name in ("baseline.json", "cell_verification.json", "verdict.json"):
        assert stored[name]["contract"] == CONTRACT


def test_the_target_cell_is_the_one_the_ledger_names(stored):
    cell = stored["cell_verification.json"]
    assert cell["ticker"] == "SMRTG"
    assert cell["month"] == "2023-08"
    assert cell["knowledge_cutoff_at"] == CUTOFF


def test_no_windows_path_separator_leaks_into_the_artifacts():
    for name in CONTENT_FILES + ("receipt.json",):
        assert "\\\\" not in (AUDIT / name).read_text(encoding="utf-8")


def test_receipt_source_paths_are_posix(receipt):
    for path in receipt["source_paths"].values():
        assert "\\" not in path
