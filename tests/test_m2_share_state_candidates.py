from scripts.build_m2_share_state_candidates import build


def test_capital_corpus_is_extracted_without_unproved_share_derivation(tmp_path):
    receipt = build(tmp_path)
    assert receipt['semantic_fact_count_examined'] == 199969
    assert receipt['issued_capital_observation_count'] == 9487
    assert receipt['nominal_value_fact_count'] == 0
    assert receipt['direct_number_of_shares_fact_count'] == 0
    assert receipt['derived_share_count'] == 0
    assert receipt['usable_share_state_count'] == 0
    assert receipt['economic_conclusion'] == 'ISSUED_CAPITAL_ALONE_IS_NOT_SHARE_COUNT'
