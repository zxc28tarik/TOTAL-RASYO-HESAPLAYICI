from scripts.audit_m2_free_evidence_exhaustion import family_blocker


def test_family_blockers_do_not_treat_capital_as_nav():
    assert family_blocker("HOLDING") == "PIT_NAV_SOURCE_MISSING"
    assert family_blocker("GYO") == "PIT_NAV_AND_PROPERTY_PORTFOLIO_SOURCE_MISSING"
    assert family_blocker("NONFIN") is None
