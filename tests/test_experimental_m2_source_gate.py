from scripts.experimental_m2_source_gate import build_m2_source_gate
from src.analytics.historical_valuation_price_supplement import CATALOG
from src.analytics.verified_yahoo_raw_close import MANIFEST, ROOT, verify_yahoo_raw_close

import json
import pandas as pd
import pytest


def direct_yahoo_candidate():
    manifest = json.loads(MANIFEST.read_bytes())
    row = pd.read_csv(ROOT / manifest['resolved_prices'], nrows=1).iloc[0]
    return {
        'trade_date': str(row.trade_date), 'close': float(row.close),
        'adj_close': float(row.adj_close), 'yahoo_symbol': str(row.yahoo_symbol),
        'source_ticker': str(row.price_source_ticker),
        'resolution': str(row.price_resolution),
        'file_sha256': manifest['resolved_prices_sha256'],
    }


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


def test_hash_pinned_direct_yahoo_close_is_verified_but_does_not_invent_shares():
    candidate = direct_yahoo_candidate()
    result = build_m2_source_gate(None, [], None, candidate,
        '2021-08-31T18:10:00+03:00', 'ADEL', 'HOLDING')
    assert result['raw_close_basis_verified'] is True
    assert result['price_source']['contract'] == 'VERIFIED_YAHOO_RAW_CLOSE_RECEIPT_V1'
    assert result['price_source']['auto_adjust'] is False
    assert result['price_source']['raw_close'] == candidate['close']
    assert result['price_source']['adjusted_close_diagnostic'] == candidate['adj_close']
    assert 'RAW_CLOSE_BASIS_EVIDENCE_MISSING' not in result['reasons']
    assert result['dated_nominal_shares_verified'] is False
    assert 'SOURCE_SHARE_BASIS_MISMATCH' in result['exact_error']


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('close', 14.745232, 'YAHOO_RAW_CLOSE_VALUE_MISMATCH'),
    ('adj_close', 15.30, 'YAHOO_ADJ_CLOSE_DIAGNOSTIC_MISMATCH'),
    ('yahoo_symbol', 'WRONG.IS', 'YAHOO_SYMBOL_MISMATCH'),
    ('source_ticker', 'WRONG', 'YAHOO_PRICE_SOURCE_TICKER_MISMATCH'),
    ('resolution', 'BORSA_LINEAGE_YAHOO_ALIAS', 'YAHOO_PRICE_RESOLUTION_NOT_DIRECT'),
    ('file_sha256', '0' * 64, 'YAHOO_RESOLVED_FILE_HASH_MISMATCH'),
])
def test_yahoo_raw_close_mutations_fail_closed(field, value, message):
    candidate = direct_yahoo_candidate()
    candidate[field] = value
    with pytest.raises(ValueError, match=message):
        verify_yahoo_raw_close(ticker='ADEL', candidate=candidate,
            analysis_at=pd.Timestamp('2021-08-31T18:10:00+03:00').to_pydatetime())


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
