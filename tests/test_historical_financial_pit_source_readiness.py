from __future__ import annotations

import pytest

from src.analytics.historical_financial_pit_source_readiness import (
    REQUIRED_SOURCE_CLASSES,
    HistoricalFinancialPitSourceReadinessError,
    current_financial_pit_source_readiness,
    evaluate_historical_financial_pit_source_readiness,
)


def all_closed():
    return {key: "CLOSED" for key in REQUIRED_SOURCE_CLASSES}


def test_current_repo_state_is_not_ready_after_public_kap_acquisition_proof_only():
    result = current_financial_pit_source_readiness()
    assert result.contract == "HISTORICAL_FINANCIAL_PIT_SOURCE_READINESS_V1"
    assert result.statuses["KAP_FINANCIAL_REPORTS"] == "ACQUISITION_PROVEN"
    assert result.ready is False
    assert result.result == "NOT_READY"
    assert "KAP_FINANCIAL_REPORTS" in result.open_requirements
    assert "HOLDING_NAV" in result.open_requirements
    assert "BANK_ASSUMPTIONS" in result.open_requirements
    assert result.closed_requirements == ()


def test_only_exact_all_closed_source_class_set_can_be_ready():
    result = evaluate_historical_financial_pit_source_readiness(all_closed())
    assert result.ready is True
    assert result.result == "READY"
    assert result.open_requirements == ()
    assert result.closed_requirements == REQUIRED_SOURCE_CLASSES


@pytest.mark.parametrize("mutation", ["remove", "foreign"])
def test_source_class_set_mutations_fail_closed(mutation):
    statuses = all_closed()
    if mutation == "remove":
        statuses.pop("GYO_NAV")
    else:
        statuses["FAKE_CURRENT_STATE_FALLBACK"] = "CLOSED"
    with pytest.raises(HistoricalFinancialPitSourceReadinessError, match="exact"):
        evaluate_historical_financial_pit_source_readiness(statuses)


@pytest.mark.parametrize("status", ["OPEN", "ACQUISITION_PROVEN"])
def test_any_nonclosed_requirement_prevents_ready(status):
    statuses = all_closed()
    statuses["INSURANCE_METRICS"] = status
    result = evaluate_historical_financial_pit_source_readiness(statuses)
    assert result.ready is False
    assert result.result == "NOT_READY"
    assert result.open_requirements == ("INSURANCE_METRICS",)


def test_unknown_or_loose_status_alias_is_rejected():
    statuses = all_closed()
    statuses["BANK_QUARTER_SLOTS"] = "PASS"
    with pytest.raises(HistoricalFinancialPitSourceReadinessError, match="OPEN/ACQUISITION_PROVEN/CLOSED"):
        evaluate_historical_financial_pit_source_readiness(statuses)


def test_case_is_canonicalized_but_does_not_change_semantics():
    statuses = all_closed()
    statuses["M2_FOLLOW_CONTEXTS"] = " open "
    result = evaluate_historical_financial_pit_source_readiness(statuses)
    assert result.statuses["M2_FOLLOW_CONTEXTS"] == "OPEN"
    assert result.ready is False
