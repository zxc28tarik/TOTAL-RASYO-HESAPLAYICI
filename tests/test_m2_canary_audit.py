from __future__ import annotations

from src.analytics.m2_canary_audit import audit_derivation_contract, audit_real_peer_cohort


def _quarter(*, profile="ACTUAL", share_source="ISSUED_CAPITAL_OVER_NOMINAL"):
    return {
        "derivation_profile": profile,
        "derivation_version": 1,
        "diagnostics": {
            "semantic_profile": "EXACT_LABELS",
            "semantic_version": 1,
            "field_sources": {"shares_out": share_source},
        },
    }


def test_profile_rename_and_assumed_nominal_are_both_rejected():
    result = audit_derivation_contract(
        _quarter(),
        {"source_derivation_profile": "EXPECTED", "source_derivation_version": 1},
    )
    assert result["compatible"] is False
    assert result["forced_profile_rename_allowed"] is False
    assert any(reason.startswith("SOURCE_DERIVATION_PROFILE_MISMATCH") for reason in result["blockers"])
    assert "UNPROVEN_SHARE_DERIVATION:ISSUED_CAPITAL_OVER_NOMINAL" in result["blockers"]


def test_mutating_only_profile_name_does_not_make_share_semantics_safe():
    result = audit_derivation_contract(
        _quarter(profile="EXPECTED"),
        {"source_derivation_profile": "EXPECTED", "source_derivation_version": 1},
    )
    assert result["compatible"] is False
    assert result["blockers"] == ["UNPROVEN_SHARE_DERIVATION:ISSUED_CAPITAL_OVER_NOMINAL"]


def test_real_peer_gate_counts_only_explicitly_certified_or_safe_share_sources():
    cohort = {
        "SMRTG": {"technical_family": "NONFIN", "quarters": [_quarter()] * 4},
        "UNSAFE": {"technical_family": "NONFIN", "quarters": [_quarter()] * 4},
        "SAFE": {"technical_family": "NONFIN", "quarters": [
            _quarter(share_source="EXPLICIT_CLASS_NOMINAL_SUM")
        ] * 4},
        "SHORT": {"technical_family": "NONFIN", "quarters": [_quarter()] * 3},
        "BANK": {"technical_family": "BANK", "quarters": [_quarter()] * 4},
    }
    result = audit_real_peer_cohort(
        cohort, target_ticker="SMRTG", externally_certified_share_tickers={"SMRTG"},
        minimum_peer_count=5,
    )
    assert result["financial_candidate_count"] == 3
    assert result["safe_share_candidates"] == ["SAFE", "SMRTG"]
    assert result["safe_peer_tickers"] == ["SAFE"]
    assert result["peer_gate_passed"] is False

