import pytest
from scripts.experimental_financial_entity_binding import matching_financial_entities, financial_entity_binding
from scripts.experimental_core_module_materializer import build_core_modules
from tests.test_experimental_core_module_materializer import report, CUTOFF


def test_composite_exact_tokens_exclude_substrings_and_nonmembers():
    entities=['GARAN-TGB','GARANTI','ISATR-ISBTR-ISCTR-ISKUR-TIB','XGARAN','GARAN--TGB']
    assert matching_financial_entities('GARAN',CUTOFF,entities)==('GARAN-TGB',)
    assert matching_financial_entities('ISCTR',CUTOFF,entities)==('ISATR-ISBTR-ISCTR-ISKUR-TIB',)
    assert matching_financial_entities('GAR',CUTOFF,entities)==()
    with pytest.raises(ValueError,match='TOKEN_MEMBERSHIP_MISSING'):
        financial_entity_binding('GARAN',CUTOFF,{'source_entity_code':'XGARAN'})


def test_entity_binding_preserves_source_hash_and_never_authorizes_price_or_shares():
    r={'source_entity_code':'AAA-BBB','archive_sha256':'a'*64,'member_sha256':'b'*64}
    binding=financial_entity_binding('BBB',CUTOFF,r)
    assert binding['member_sha256']=='b'*64 and binding['archive_sha256']=='a'*64
    assert not binding['price_transfer_allowed'] and not binding['nominal_share_transfer_allowed']


def test_core_composite_mapping_retains_source_fact_and_excludes_class_capital():
    from copy import deepcopy
    r=report('AAA',family='HOLDING',technical='NONFIN')
    r['report']['source_entity_code']='AAA-BBB'
    r['report_mapping_ticker']='AAA'
    r['mapping_ticker_binding']={'contract':'ARCHIVED_ENTITY_TECHNICAL_TOKEN_V1',
        'raw_source_entity_code':'AAA-BBB','declared_tokens':['AAA','BBB'],
        'technical_mapping_ticker':'AAA','source_member_sha256':r['report']['member_sha256'],
        'original_dimensions_preserved':True,'share_or_price_basis_proven':False}
    capital=deepcopy(r['facts'][3]);capital.update(canonical_field='ISSUED_CAPITAL',value='1000')
    r['facts'].append(capital)
    original=deepcopy(r)
    result=build_core_modules([r],CUTOFF,['BBB'])['per_ticker']['BBB']
    assert result['core_ratios'] and result['excluded_unproven_share_class_facts']==1
    assert r==original
    assert all(q['values'].get('shares_out') is None for q in result['quarters'])
    assert all(a['source_fact']['ticker']=='AAA' for a in result['alias_fact_adaptations'])
    r['mapping_ticker_binding']['source_member_sha256']='b'*64
    rejected=build_core_modules([r],CUTOFF,['BBB'])['per_ticker']['BBB']
    assert not rejected['quarters']
    assert 'COMPOSITE_TECHNICAL_MAPPING_BINDING_MISMATCH' in rejected['reasons']
