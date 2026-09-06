from datetime import datetime
import pytest
from scripts.materialize_experimental_p3_p4 import (
    select_report, financial_selection, apply_core_module_outputs,
    family_from_historical_market_route,
)
from scripts.materialize_experimental_financial_facts import economic_family

def report(year,period,publication,**extra):
    return dict(report_year=year,report_period=period,published_at=publication,
                notification_id=year*10+int(period[1]),archive_name='archive.zip',member_name=f'{year}_{period}.xls',**extra)

def test_future_statement_does_not_replace_visible_own_period():
    old=report(2022,'Q3','2022-11-09T18:00:00+03:00')
    future=report(2022,'Q4','2023-03-13T18:00:00+03:00')
    cutoff=datetime.fromisoformat('2023-03-01T18:10:00+03:00')
    assert select_report([old,future],cutoff)==old


def test_report_version_order_compares_publication_instants_not_offset_strings():
    early=report(2025,'Q1','2025-05-01T19:00:00+03:00')
    later=report(2025,'Q1','2025-05-01T17:00:00+00:00')
    assert select_report([later,early],datetime.fromisoformat('2025-06-01T00:00:00+00:00'))==later


def test_same_period_later_drift_version_is_not_silently_ignored():
    cutoff=datetime.fromisoformat('2026-04-30T18:10:00+03:00')
    old=report(2025,'Q4','2026-03-01T18:00:00+03:00')
    drift=report(2025,'Q4','2026-03-02T18:00:00+03:00')
    semantic={'facts':[{'period_end':'2025-12-31','published_at':old['published_at']}],
              'status':'SEMANTIC_FACTS_MATERIALIZED'}
    result=financial_selection('AAA',cutoff,{'AAA':[old]},
        {(old['archive_name'],old['member_name']):semantic},{'AAA':[drift]})
    assert result[2]==[]
    assert 'FINANCIAL_SOURCE_PRIMARY_BYTES_MISSING' in result[3]

def test_missing_primary_replacement_scoped_to_affected_ticker():
    cutoff=datetime.fromisoformat('2026-04-30T18:10:00+03:00')
    old=report(2025,'Q3','2025-11-01T18:00:00+03:00')
    drift=report(2025,'Q4','2026-03-01T18:00:00+03:00')
    semantic={'facts':[{'period_end':'2025-09-30','published_at':old['published_at']}],'status':'SEMANTIC_FACTS_MATERIALIZED'}
    index={(old['archive_name'],old['member_name']):semantic}
    a=financial_selection('AAA',cutoff,{'AAA':[old]},index,{'AAA':[drift]})
    b=financial_selection('BBB',cutoff,{'BBB':[old]},index,{'AAA':[drift]})
    assert a[2]==[] and 'FINANCIAL_SOURCE_PRIMARY_BYTES_MISSING' in a[3]
    assert b[2] and not b[3]

def test_comparative_figures_are_not_reused_as_own_period():
    r=report(2023,'Q1','2023-05-01T18:00:00+03:00')
    s={'facts':[{'period_end':'2022-03-31','published_at':r['published_at']}],'status':'SEMANTIC_FACTS_MATERIALIZED'}
    selected=financial_selection('AAA',datetime.fromisoformat('2023-06-01T18:10:00+03:00'),{'AAA':[r]},{('archive.zip',r['member_name']):s},{})
    assert not selected[2] and selected[3]==['SEMANTIC_MAPPING_UNRESOLVED']

def test_general_schema_is_not_current_sector_fallback():
    assert economic_family('EXAMPLE SANAYI A.S.',['general_role_210015']) is None
    assert economic_family('EXAMPLE GAYRİMENKUL YATIRIM ORTAKLIĞI A.Ş.',['general_role_210015'])=='GYO'
    assert economic_family('EXAMPLE HOLDİNG A.Ş.',['general_role_210015'])=='HOLDING'
    assert economic_family('EXAMPLE HOLDING SIGORTA A.S.',['general_role_210015']) is None


def test_real_core_outputs_are_wired_without_neutral_fill():
    values={key:None for key in ('M2','M1','M3','Ek4','Ek1','Ek9')}
    reasons={key:'MODULE_INPUT_INSUFFICIENT' for key in ('M1','M3','Ek4','Ek1','Ek9')}
    count=apply_core_module_outputs(values,reasons,{
        'm1':[{'m1':0.61,'good_count_ge8':8}],
        'ek1':[{'ek1':0.44,'good_count_ge8':8}],
    })
    assert count == 8
    assert values == {'M2':None,'M1':0.61,'M3':None,'Ek4':None,'Ek1':0.44,'Ek9':None}
    assert reasons['M1'] is None and reasons['Ek1'] is None


def test_core_outputs_reject_conflicting_veto_lineage():
    values={key:None for key in ('M2','M1','M3','Ek4','Ek1','Ek9')}
    reasons={key:'missing' for key in ('M1','M3','Ek4','Ek1','Ek9')}
    with pytest.raises(ValueError,match='GOOD_COUNT_CONFLICT'):
        apply_core_module_outputs(values,reasons,{
            'm1':[{'m1':0.61,'good_count_ge8':8}],
            'ek1':[{'ek1':0.44,'good_count_ge8':9}],
        })


def test_nonfinancial_broad_route_is_positive_dated_family_evidence():
    route={'mapping_version':'HISTORICAL_M3_SOURCE_PACKAGE_V1',
           'sector_index_code':'XUSIN','valid_from':'2020-07-27','valid_to':None,
           'source_id':'KAP_SEKTORLER_2026_08_24'}
    family,lineage=family_from_historical_market_route('AAA','2022-01-03',route,'a'*64)
    assert family == 'NONFIN'
    assert lineage['ticker'] == 'AAA' and lineage['cutoff_day'] == '2022-01-03'
    assert lineage['source_hash'] == 'a'*64


@pytest.mark.parametrize('code',['XUMAL','XU100','XBANK'])
def test_financial_or_generic_index_never_becomes_nonfin_by_exclusion(code):
    route={'mapping_version':'HISTORICAL_M3_SOURCE_PACKAGE_V1',
           'sector_index_code':code,'valid_from':'2020-07-27','valid_to':None,
           'source_id':'SOURCE'}
    assert family_from_historical_market_route('AAA','2022-01-03',route,'a'*64) == (None,None)


def test_wrong_route_version_cannot_supply_historical_family():
    route={'mapping_version':'CURRENT_SNAPSHOT','sector_index_code':'XUSIN'}
    assert family_from_historical_market_route('AAA','2022-01-03',route,'a'*64) == (None,None)


def test_forward_financial_alias_preserves_original_report_and_fact_identity():
    old=report(2024,'Q2','2024-08-01T18:00:00+03:00',source_entity_code='GRTRK')
    fact={'ticker':'GRTRK','period_end':'2024-06-30','published_at':old['published_at']}
    semantic={'facts':[fact],'status':'SEMANTIC_FACTS_MATERIALIZED'}
    index={(old['archive_name'],old['member_name']):semantic}
    result=financial_selection('GRTHO',datetime.fromisoformat('2024-10-01T18:10:00+03:00'),
        {'GRTRK':[old]},index,{})
    assert result[0] is old and result[2][0] is fact
    assert result[0]['source_entity_code']=='GRTRK' and result[2][0]['ticker']=='GRTRK'
    before=financial_selection('GRTHO',datetime.fromisoformat('2024-09-30T18:10:00+03:00'),
        {'GRTRK':[old]},index,{})
    assert before[0] is None


def test_alias_drift_uses_same_precedence_and_cannot_reverse_to_future_successor():
    old=report(2024,'Q2','2024-08-01T18:00:00+03:00',source_entity_code='GRTRK')
    drift=report(2024,'Q2','2024-08-02T18:00:00+03:00',source_entity_code='GRTRK')
    cutoff=datetime.fromisoformat('2024-10-01T18:10:00+03:00')
    result=financial_selection('GRTHO',cutoff,{'GRTRK':[old]}, {}, {'GRTRK':[drift]})
    assert 'FINANCIAL_SOURCE_PRIMARY_BYTES_MISSING' in result[3]
    assert financial_selection('GRTRK',cutoff,{'GRTHO':[old]}, {}, {})[0] is None


@pytest.mark.parametrize('kind',['alias','entity'])
def test_supplemental_alias_requires_hash_bound_artifact_and_no_duplicate(tmp_path,kind):
    import json
    from scripts.materialize_experimental_p3_p4 import append_semantic_alias, write_rows, sha
    catalog=tmp_path/'catalog';catalog.write_bytes(b'primary metadata')
    row={'report':{'archive_name':'original.zip','member_name':'GRTRK.xls'}}
    artifact=tmp_path/f'semantic_{kind}_reports.jsonl.gz';write_rows(artifact,[row])
    receipt=tmp_path/f'semantic_{kind}_receipt.json'
    receipt.write_text(json.dumps({'artifact_sha256':sha(artifact),'catalog_sha256':sha(catalog)}))
    assert append_semantic_alias(tmp_path,catalog,[],kind=kind)[0]==[row]
    with pytest.raises(ValueError,match='DUPLICATE'):
        append_semantic_alias(tmp_path,catalog,[row],kind=kind)
    artifact.write_bytes(artifact.read_bytes()+b' ')
    with pytest.raises(ValueError,match='ARTIFACT_HASH_MISMATCH'):
        append_semantic_alias(tmp_path,catalog,[],kind=kind)
