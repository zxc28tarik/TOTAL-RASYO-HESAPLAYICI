from scripts.materialize_current_price_level_basis import explicit_nominal_quote_unit_is_proven


def test_quote_unit_requires_explicit_one_try_for_every_class():
    assert explicit_nominal_quote_unit_is_proven([
        {"nominal_value_per_share_try": "1"},
        {"nominal_value_per_share_try": "1.00"},
    ]) is True


def test_quote_unit_rejects_cent_shares_and_missing_evidence():
    assert explicit_nominal_quote_unit_is_proven([
        {"nominal_value_per_share_try": "0.01"},
    ]) is False
    assert explicit_nominal_quote_unit_is_proven([]) is False
    assert explicit_nominal_quote_unit_is_proven([{}]) is False
