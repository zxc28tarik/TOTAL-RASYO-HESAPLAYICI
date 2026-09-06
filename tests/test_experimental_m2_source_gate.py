from scripts.experimental_m2_source_gate import build_m2_source_gate
from src.analytics.historical_valuation_price_supplement import CATALOG


def test_all_twelve_original_thb_cells_reach_real_action_gate():
    for price in CATALOG:
        result = build_m2_source_gate(None, [], None, None,
            price.cutoff_local, price.ticker, 'GYO' if price.ticker == 'ASGYO' else 'HOLDING')
        assert result['status'] == 'SOURCE_GATE_REJECTED'
        assert result['production_gate'] == 'materialize_historical_price_level_v2'
        assert result['exact_error'] == 'ACTION_COMPLETENESS_EVIDENCE_MISSING'
        assert result['raw_close_basis_verified'] and result['dated_nominal_shares_verified']
        assert 'RAW_CLOSE_BASIS_EVIDENCE_MISSING' not in result['reasons']


def test_numeric_yahoo_close_and_capital_are_not_relabelled_as_verified_basis():
    result = build_m2_source_gate(None, [{'canonical_field':'ISSUED_CAPITAL','value':'100'}],
        None, {'close': 10, 'price_basis':'POINT_IN_TIME_MARKET_CLOSE_V1'},
        '2025-01-01T18:10:00+03:00', 'AAA', 'HOLDING')
    assert 'SOURCE_SHARE_BASIS_MISMATCH' in result['exact_error']
    assert 'RAW_CLOSE_BASIS_EVIDENCE_MISSING' in result['reasons']
    assert not result['raw_close_basis_verified'] and not result['dated_nominal_shares_verified']


def test_future_inputs_and_absent_bank_assumptions_reject_at_specific_boundary():
    future = build_m2_source_gate({'published_at':'2026-01-01T00:00:00+03:00'},[],None,None,
        '2025-01-01T18:10:00+03:00','AAA','HOLDING')
    assert future['exact_error'] == 'PIT_PUBLICATION_AFTER_CUTOFF'
    future_price = build_m2_source_gate(None,[],None,{'trade_date':'2025-01-02'},
        '2025-01-01T18:10:00+03:00','AAA','HOLDING')
    assert future_price['exact_error'] == 'PRICE_AFTER_CUTOFF'
    bank = build_m2_source_gate(None,[],None,None,'2025-01-01T18:10:00+03:00','AAA','BANK')
    assert bank['exact_error'] == 'BANK_PIT_ASSUMPTIONS_EVIDENCE_MISSING'
    assert bank['implementation_stage'] == 'M2_NOT_EXECUTED_SOURCE_CONTRACTS_MISSING'
