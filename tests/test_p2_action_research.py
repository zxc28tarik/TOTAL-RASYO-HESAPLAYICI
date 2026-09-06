import pytest
import scripts.audit_p2_action_research_v1 as research


def test_actual_dated_sources_account_for_all_cells_without_inventing_completeness():
    receipt = research.audit()
    assert receipt["row_count"] == 12
    assert len(receipt["dated_share_sources"]) == 6
    assert receipt["runtime_action_bundles_created"] == 0
    assert receipt["rejection_counts"] == {"ACTION_COMPLETENESS_EVIDENCE_MISSING": 12}
    for cell in receipt["cells"]:
        assert cell["share_source_date"] <= cell["valuation_price_trade_date"]
        assert not cell["action_completeness_proven"]


def test_corrupt_archived_source_fails_before_replay(monkeypatch):
    original = research.read
    monkeypatch.setattr(research, "read", lambda name: original(name) + b"tamper")
    with pytest.raises(AssertionError):
        research.audit()
