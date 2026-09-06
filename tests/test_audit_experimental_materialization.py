from copy import deepcopy
from datetime import datetime
import hashlib
import pytest
from scripts.audit_experimental_materialization import encoded,independent_latest,validate_pair,validate_selected_source,validate_rankings


def base_report():
    return {'source_entity_code':'TEST','published_at':'2021-04-30T18:00:00+03:00','report_year':2021,'report_period':'Q1','notification_id':1,'archive_name':'fixture.zip','archive_sha256':'a'*64,'member_name':'TEST_1_2021_1.xls','member_sha256':'b'*64}


def pair():
    p3={'signal_date':'2021-06-01','ticker':'TEST','knowledge_cutoff_at':'2021-05-31T18:10:00+03:00','selected_report':base_report(),'module_values':dict.fromkeys(['M2','M1','M3','Ek4','Ek1','Ek9']),'status':'EXPLICIT_REJECTION','reasons':['MODULE_INPUT_INSUFFICIENT'],'price':None}
    p4={'signal_date':p3['signal_date'],'ticker':'TEST','knowledge_cutoff_at':p3['knowledge_cutoff_at'],'module_values':deepcopy(p3['module_values']),'status':'EXPLICIT_REJECTION','rank':None,'final_score':None,'decision':'UZAK','p3_cell_sha256':hashlib.sha256(encoded(p3)).hexdigest()}
    return p3,p4


def test_rejection_pair_validates_without_inventing_score():
    validate_pair(*pair())


@pytest.mark.parametrize('mutation',['future','ticker','member_key','rank','hidden_score'])
def test_cell_mutations_fail(mutation):
    p3,p4=pair()
    if mutation=='future':p3['selected_report']['published_at']='2021-06-01T09:00:00+03:00'
    if mutation=='ticker':p3['ticker']='OTHER'
    if mutation=='member_key':p3['selected_report']['member_name']='OTHER_1_2021_1.xls'
    if mutation=='rank':p4['rank']=1
    if mutation=='hidden_score':p4['final_score']=.5
    with pytest.raises(ValueError):validate_pair(p3,p4)


def test_report_hash_tampering_fails_even_when_cell_digest_was_rebuilt():
    expected=base_report();tampered=deepcopy(expected);tampered['member_sha256']='c'*64
    with pytest.raises(ValueError,match='SELECTION'):validate_selected_source(tampered,expected)


def test_selection_uses_own_period_before_late_old_period_publication():
    old=base_report();new={**old,'report_period':'Q2','published_at':'2021-08-10T18:00:00+03:00','notification_id':2}
    corrected_old={**old,'published_at':'2021-08-20T18:00:00+03:00','notification_id':3}
    future={**new,'published_at':'2021-09-02T18:00:00+03:00','notification_id':4}
    assert independent_latest([old,new,corrected_old,future],datetime.fromisoformat('2021-08-31T18:10:00+03:00'))==new


def test_rank_tie_break_mutation_fails():
    rows=[{'ticker':'AAA','status':'SCORED','final_score':.8,'rank':2},{'ticker':'BBB','status':'SCORED','final_score':.8,'rank':1}]
    with pytest.raises(ValueError,match='TIEBREAK'):validate_rankings(rows)


def canonical_thb_cell(evidence):
    price={'ticker':evidence.ticker,'signal_date':str(evidence.signal_date),'trade_date':str(evidence.trade_date),'raw_close':evidence.raw_close,'archive_sha256':evidence.archive_sha256,'member_sha256':evidence.member_sha256}
    return {'ticker':evidence.ticker,'signal_date':str(evidence.signal_date),'knowledge_cutoff_at':evidence.cutoff_local.isoformat(),'price':{**price,'valuation_basis_verified':True},'m2_source_gate':{'raw_close_basis_verified':True,'price_source':price}}


def test_all_twelve_canonical_thb_prices_verify_actual_csv_bytes():
    from scripts.audit_experimental_materialization import validate_thb_price
    from src.analytics.historical_valuation_price_supplement import CATALOG
    assert len(CATALOG)==12
    for evidence in CATALOG:
        result=validate_thb_price(canonical_thb_cell(evidence))
        assert result['close_verified_from_raw_csv'] is True
        assert result['action_completeness_claimed'] is False


@pytest.mark.parametrize('mutation',['cutoff','close','ticker','archive_hash','trade_date','gate'])
def test_thb_claim_mutations_rejected(mutation):
    from scripts.audit_experimental_materialization import validate_thb_price
    from src.analytics.historical_valuation_price_supplement import CATALOG
    cell=canonical_thb_cell(CATALOG[0])
    if mutation=='cutoff':cell['knowledge_cutoff_at']='2022-06-30T18:11:00+03:00'
    if mutation=='close':cell['price']['raw_close']+=1
    if mutation=='ticker':cell['price']['ticker']='OTHER'
    if mutation=='archive_hash':cell['price']['archive_sha256']='c'*64
    if mutation=='trade_date':cell['price']['trade_date']=cell['signal_date']
    if mutation=='gate':cell['m2_source_gate']['raw_close_basis_verified']=False
    with pytest.raises(ValueError,match='THB_'):validate_thb_price(cell)


def test_thb_changed_archive_bytes_rejected(tmp_path):
    from pathlib import Path
    from scripts.audit_experimental_materialization import validate_thb_price,ROOT
    from src.analytics.historical_valuation_price_supplement import CATALOG
    evidence=CATALOG[0];name=Path(evidence.archive_url).name
    source=ROOT/'data/backtest_sources/p2_raw_close_pit_v2'/name
    (tmp_path/name).write_bytes(source.read_bytes()+b'corruption')
    with pytest.raises(ValueError,match='THB_RAW_ARCHIVE_HASH_MISMATCH'):
        validate_thb_price(canonical_thb_cell(evidence),tmp_path)


def financial_alias_cell():
    from scripts.experimental_financial_ticker_lineage import lineage_receipt
    cutoff=datetime.fromisoformat('2021-08-31T18:10:00+03:00')
    receipt=lineage_receipt('AGESA',cutoff)
    receipt.update(selected_source_ticker='AVISA',alias_selected=True)
    return {'ticker':'AGESA','knowledge_cutoff_at':cutoff.isoformat(),'selected_report':{'source_entity_code':'AVISA'},'financial_ticker_lineage':receipt}


def test_independent_alias_audit_preserves_effective_predecessor():
    from scripts.audit_experimental_materialization import validate_financial_lineage,independent_source_candidates
    cell=financial_alias_cell()
    assert validate_financial_lineage(cell,required=True)==['AGESA','AVISA']
    assert independent_source_candidates('AGESA',datetime.fromisoformat('2021-07-31T18:10:00+03:00'))[0]==['AGESA']
    assert independent_source_candidates('AVISA',datetime.fromisoformat('2021-08-31T18:10:00+03:00'))[0]==['AVISA']


@pytest.mark.parametrize('mutation',['future_edge','raw_retag','provenance','reverse_edge'])
def test_financial_alias_mutations_rejected(mutation):
    from scripts.audit_experimental_materialization import validate_financial_lineage
    cell=financial_alias_cell()
    if mutation=='future_edge':cell['knowledge_cutoff_at']='2021-07-31T18:10:00+03:00'
    if mutation=='raw_retag':cell['selected_report']['source_entity_code']='AGESA'
    if mutation=='provenance':cell['financial_ticker_lineage']['csv_sha256']='0'*64
    if mutation=='reverse_edge':cell['ticker']='AVISA';cell['selected_report']['source_entity_code']='AGESA'
    with pytest.raises(ValueError,match='LINEAGE_OR_FUTURE_ALIAS'):
        validate_financial_lineage(cell,required=True)


def test_semantic_alias_hash_and_duplicate_guard(tmp_path):
    import gzip,json
    from scripts.audit_experimental_materialization import verified_semantic_rows,sha
    main={'report':{'archive_name':'a','member_name':'m'},'facts':[]}
    additional={'report':{'archive_name':'a','member_name':'old'},'facts':[]}
    (tmp_path/'semantic_reports.jsonl.gz').write_bytes(gzip.compress(encoded(main),mtime=0))
    alias=tmp_path/'semantic_alias_reports.jsonl.gz';alias.write_bytes(gzip.compress(encoded(additional),mtime=0))
    receipt_path=tmp_path/'semantic_alias_receipt.json'
    receipt={'contract':'EXPERIMENTAL_PREDECESSOR_SEMANTIC_SUPPLEMENT_V1','catalog_sha256':'a'*64,'authoritative_claim_allowed':False,'artifact_sha256':sha(alias),'report_count':1,'fact_count':0}
    receipt_path.write_bytes(encoded(receipt))
    hashes={'semantic':sha(tmp_path/'semantic_reports.jsonl.gz'),'semantic_alias':sha(alias),'semantic_alias_receipt':sha(receipt_path)}
    assert len(verified_semantic_rows(tmp_path,hashes,'a'*64))==2
    hashes['semantic_alias']='0'*64
    with pytest.raises(ValueError,match='ALIAS_SOURCE_HASH'):verified_semantic_rows(tmp_path,hashes,'a'*64)


def test_verified_thb_gate_cannot_be_replaced_by_generic_price():
    p3,p4=pair();p3['m2_source_gate']={'raw_close_basis_verified':True}
    p3['price']={'source_ticker':'TEST','trade_date':'2021-05-31','close':1.0}
    p4['p3_cell_sha256']=hashlib.sha256(encoded(p3)).hexdigest()
    with pytest.raises(ValueError,match='THB_VERIFIED_GATE_PRICE_REPLACED'):validate_pair(p3,p4)
