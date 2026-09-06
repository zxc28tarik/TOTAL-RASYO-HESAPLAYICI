"""Independent source-to-P4 audit; never claims a NAV/backtest audit implicitly."""
from __future__ import annotations
import argparse
import csv
from io import StringIO
from calendar import monthrange
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from zoneinfo import ZoneInfo
import gzip
import hashlib
import json
import math
import re
from pathlib import Path
import sys
from zipfile import ZipFile
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.build_historical_m3_source_package import _historical_membership
from src.analytics.historical_m3_source_package import verify_historical_m3_source_package
from src.analytics.historical_cutoff_execution_policy import build_authorized_cutoff_execution_schedule
from src.ingest.kap_bulk_financial_export import parse_kap_bulk_export_report

WEIGHTS={'M2':.40,'M1':.18,'M3':.12,'Ek4':.16,'Ek1':.08,'Ek9':.06}
CAT=ROOT/'data/backtest_sources/reconstructed_experimental_kap_v1'
M3_MANIFEST=ROOT/'data/backtest_sources/m3_source_package/manifest.json'
M3_ROUTES=ROOT/'data/backtest_sources/m3_source_package/sector_routes.csv.gz'

def encoded(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()

def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def rows(path):
    return [json.loads(line) for line in gzip.decompress(Path(path).read_bytes()).splitlines()]

def period_end(report):
    year=int(report['report_year']);month=int(report['report_period'][1])*3
    return date(year,month,monthrange(year,month)[1])

def independent_latest(reports,cutoff):
    eligible=[]
    for report in reports:
        published=datetime.fromisoformat(report['published_at'])
        end=period_end(report)
        if published<=cutoff and end<=cutoff.date():
            eligible.append(((end,published,int(report['notification_id'])),report))
    return max(eligible,key=lambda item:item[0])[1] if eligible else None

def validate_historical_family(cell,semantic,routes,routes_sha256):
    """Rebuild the family claim from its dated source instead of trusting P3."""
    family=cell.get('historical_family');source=cell.get('historical_family_source')
    lineage=cell.get('historical_family_lineage')
    if family is None:
        if source is not None or lineage is not None:raise ValueError('FAMILY_LINEAGE_WITHOUT_FAMILY')
        return
    if source=='DATED_REPORT':
        if lineage is not None or not semantic or family!=semantic.get('historical_family'):
            raise ValueError('CURRENT_OR_CHANGED_SECTOR')
        return
    if source!='HISTORICAL_M3_BROAD_SECTOR_ROUTE' or family!='NONFIN' or not lineage:
        raise ValueError('CURRENT_OR_CHANGED_SECTOR')
    signal=pd.Timestamp(cell['signal_date']).normalize()
    frame=routes.loc[routes.ticker.eq(cell['ticker'])].copy()
    frame['valid_from']=pd.to_datetime(frame.valid_from,errors='coerce').dt.normalize()
    frame['valid_to']=pd.to_datetime(frame.valid_to.replace('',pd.NA),errors='coerce').dt.normalize()
    matches=frame.loc[frame.valid_from.le(signal)&(frame.valid_to.isna()|frame.valid_to.gt(signal))]
    if len(matches)!=1:raise ValueError('HISTORICAL_FAMILY_ROUTE_INTERVAL_MISMATCH')
    route=matches.iloc[0];code=str(route.sector_index_code)
    if code not in {'XUSIN','XUHIZ','XUTEK'}:raise ValueError('HISTORICAL_FAMILY_NOT_POSITIVE_NONFIN_ROUTE')
    expected={'ticker':cell['ticker'],'cutoff_day':cell['signal_date'],'family':'NONFIN',
        'effective_from':route.valid_from.date().isoformat(),
        'effective_to':None if pd.isna(route.valid_to) else route.valid_to.date().isoformat(),
        'source_identity':str(route.source_id),
        'source_path':'data/backtest_sources/m3_source_package/sector_routes.csv.gz',
        'source_hash':routes_sha256,
        'mapping_version':'HISTORICAL_BROAD_INDEX_TO_ECONOMIC_FAMILY_V1',
        'sector_index_code':code}
    if lineage!=expected:raise ValueError('HISTORICAL_FAMILY_LINEAGE_MISMATCH')

@lru_cache(maxsize=1)
def official_code_change_sources():
    csv_path=ROOT/'data/backtest_sources/bist_ticker_code_changes_2021-08_2026-08.csv'
    provenance_path=csv_path.with_suffix('.provenance.json')
    csv_digest='8d71a1d442a1389fd0f0d65414d21fd10b6f95403aa29bda147c638a3e98fa4e'
    provenance_digest='63311bfb53e8db7f4eb45c2e89b90be3963348ba4a94646e1b0fd6c29156e3a4'
    if sha(csv_path)!=csv_digest or sha(provenance_path)!=provenance_digest:
        raise ValueError('OFFICIAL_FINANCIAL_ALIAS_SOURCE_HASH_MISMATCH')
    provenance=json.loads(provenance_path.read_bytes())
    events=list(csv.DictReader(StringIO(csv_path.read_text(encoding='utf-8'))))
    if provenance['publisher']!='Borsa Istanbul A.S.' or provenance['sheet']!='KOD_DEGISIKLIGI' or len(events)!=provenance['event_count'] or any(r['source_workbook_sha256']!=provenance['workbook_sha256'] for r in events):
        raise ValueError('OFFICIAL_FINANCIAL_ALIAS_PROVENANCE_MISMATCH')
    return events,csv_digest,provenance_digest

def independent_source_candidates(ticker,cutoff):
    events,csv_digest,provenance_digest=official_code_change_sources()
    upper=cutoff.astimezone(ZoneInfo('Europe/Istanbul')).date()
    candidates=[ticker];used=[];current=ticker
    while True:
        matches=[r for r in events if r['new_ticker']==current and date.fromisoformat(r['effective_date'])<=upper]
        if not matches:break
        if len(matches)!=1:raise ValueError('AMBIGUOUS_FINANCIAL_ALIAS')
        edge=matches[0];old=edge['old_ticker']
        if old in candidates:raise ValueError('CYCLIC_FINANCIAL_ALIAS')
        candidates.append(old);used.append(edge);current=old;upper=date.fromisoformat(edge['effective_date'])
    return candidates,used,csv_digest,provenance_digest

def independent_entity_tokens(source):
    if not isinstance(source,str) or re.fullmatch(r'[A-Z0-9]+(?:-[A-Z0-9]+)*',source) is None:
        return ()
    tokens=tuple(source.split('-'))
    return tokens if len(tokens)==len(set(tokens)) else ()

def validate_entity_binding(cell,candidates):
    selected=cell.get('selected_report');binding=cell.get('financial_entity_binding')
    if selected is None:
        if binding is not None:raise ValueError('ENTITY_BINDING_WITHOUT_SOURCE')
        return []
    source=selected['source_entity_code'];tokens=independent_entity_tokens(source)
    matched=sorted(set(candidates).intersection(tokens))
    if not matched:raise ValueError('FINANCIAL_ENTITY_TOKEN_MEMBERSHIP_MISSING')
    expected={'contract':'EXPERIMENTAL_ARCHIVED_FINANCIAL_ENTITY_BINDING_V1','target_ticker':cell['ticker'],'source_entity_code':source,'entity_tokens':list(tokens),'matched_source_tickers':matched,'archive_name':selected.get('archive_name'),'member_name':selected.get('member_name'),'archive_sha256':selected.get('archive_sha256'),'member_sha256':selected.get('member_sha256'),'composite_entity':len(tokens)>1,'financial_statement_identity_only':True,'price_transfer_allowed':False,'nominal_share_transfer_allowed':False,'risk_ids':['ARCHIVED_COMPOSITE_ENTITY_FINANCIAL_BINDING_NOT_SHARE_CLASS_PROOF'] if len(tokens)>1 else []}
    if binding!=expected:raise ValueError('FINANCIAL_ENTITY_BINDING_OR_RAW_LINEAGE_MISMATCH')
    return matched

def validate_financial_lineage(cell,required=False):
    lineage=cell.get('financial_ticker_lineage')
    if lineage is None:
        if required:raise ValueError('FINANCIAL_TICKER_LINEAGE_MISSING')
        return [cell['ticker']]
    cutoff=datetime.fromisoformat(cell['knowledge_cutoff_at'])
    candidates,events,csv_digest,provenance_digest=independent_source_candidates(cell['ticker'],cutoff)
    selected=(cell.get('selected_report') or {}).get('source_entity_code')
    expected={'contract':'EXPERIMENTAL_FORWARD_FINANCIAL_IDENTITY_CANDIDATES_V1','ticker':cell['ticker'],'cutoff':cutoff.isoformat(),'candidate_source_tickers':candidates,'events':events,'csv_sha256':csv_digest,'provenance_sha256':provenance_digest,'current_or_future_successor_fallback':False,'semantic_retag_performed':False,'raw_report_identity_must_be_preserved':True,'risk_ids':['HISTORICAL_CODE_CHANGE_ANNOUNCEMENT_TIMES_NOT_ENUMERATED'],'authoritative_pit_claim_allowed':False,'selected_source_ticker':selected,'alias_selected':selected is not None and cell['ticker'] not in independent_entity_tokens(selected)}
    if lineage!=expected or (selected is not None and not set(independent_entity_tokens(selected)).intersection(candidates)):
        raise ValueError('FINANCIAL_TICKER_LINEAGE_OR_FUTURE_ALIAS_MISMATCH')
    return candidates

def validate_thb_price(cell, thb_dir=None):
    """Verify a canonical THB close from actual CSV bytes, independently of M2 gate."""
    from src.analytics.historical_valuation_price_supplement import CATALOG
    cutoff=datetime.fromisoformat(cell['knowledge_cutoff_at'])
    canonical=next((item for item in CATALOG if item.ticker==cell['ticker'] and str(item.signal_date)==cell['signal_date']),None)
    if canonical is None or canonical.cutoff_local!=cutoff:
        raise ValueError('THB_CANONICAL_KEY_OR_CUTOFF_MISMATCH')
    expected={'ticker':canonical.ticker,'signal_date':str(canonical.signal_date),'trade_date':str(canonical.trade_date),'raw_close':canonical.raw_close,'archive_sha256':canonical.archive_sha256,'member_sha256':canonical.member_sha256}
    price=cell['price']
    if price!={**expected,'valuation_basis_verified':True}:
        raise ValueError('THB_PRICE_CANONICAL_IDENTITY_MISMATCH')
    gate=cell.get('m2_source_gate',{})
    if gate.get('raw_close_basis_verified') is not True or gate.get('price_source')!=expected:
        raise ValueError('THB_M2_GATE_LINEAGE_MISMATCH')
    if canonical.trade_date>cutoff.date() or canonical.trade_date>=canonical.signal_date:
        raise ValueError('THB_PRICE_AFTER_CUTOFF')
    directory=Path(thb_dir) if thb_dir is not None else ROOT/'data/backtest_sources/p2_raw_close_pit_v2'
    archive=directory/Path(canonical.archive_url).name
    if sha(archive)!=canonical.archive_sha256:
        raise ValueError('THB_RAW_ARCHIVE_HASH_MISMATCH')
    with ZipFile(archive) as bundle:
        if bundle.namelist().count(canonical.member)!=1:
            raise ValueError('THB_MEMBER_MISSING_OR_DUPLICATE')
        raw=bundle.read(canonical.member)
    if hashlib.sha256(raw).hexdigest()!=canonical.member_sha256:
        raise ValueError('THB_RAW_MEMBER_HASH_MISMATCH')
    # Required THB identifier/date/numeric headers are ASCII. Latin-1 preserves
    # every byte without replacing invalid characters in unrelated issuer names.
    matched=[]
    for row in csv.DictReader(StringIO(raw.decode('latin-1')),delimiter=';'):
        normalized={' '.join(key.split()):value for key,value in row.items() if key is not None}
        if normalized.get('ISLEM KODU')==canonical.ticker+'.E' and normalized.get('TARIH')==str(canonical.trade_date):
            matched.append(normalized)
    if len(matched)!=1 or Decimal(matched[0]['KAPANIS FIYATI'])!=Decimal(str(canonical.raw_close)):
        raise ValueError('THB_RAW_CSV_CLOSE_MISMATCH')
    return {'ticker':canonical.ticker,'signal_date':str(canonical.signal_date),'trade_date':str(canonical.trade_date),'archive_sha256':canonical.archive_sha256,'member_sha256':canonical.member_sha256,'close_verified_from_raw_csv':True,'action_completeness_claimed':False}

def validate_pair(p3,p4):
    if p3.get('profile','EXPERIMENTAL_RISK_ACCEPTED_5Y')!='EXPERIMENTAL_RISK_ACCEPTED_5Y' or p4.get('profile','EXPERIMENTAL_RISK_ACCEPTED_5Y')!='EXPERIMENTAL_RISK_ACCEPTED_5Y':raise ValueError('EXPERIMENTAL_PROFILE_CHANGED')
    if (p3['signal_date'],p3['ticker'])!=(p4['signal_date'],p4['ticker']):raise ValueError('P3_P4_MEMBERSHIP_KEY_MISMATCH')
    cutoff=datetime.fromisoformat(p3['knowledge_cutoff_at'])
    if cutoff.date()>=date.fromisoformat(p3['signal_date']):raise ValueError('SIGNAL_DAY_VALUATION_LEAKAGE')
    selected=p3.get('selected_report')
    if selected:
        if datetime.fromisoformat(selected['published_at'])>cutoff:raise ValueError('FUTURE_FINANCIAL_STATEMENT')
        allowed=validate_financial_lineage(p3)
        if p3.get('financial_entity_binding') is not None:
            validate_entity_binding(p3,allowed)
        elif selected['source_entity_code'] not in allowed:raise ValueError('CURRENT_TICKER_FALLBACK')
        if period_end(selected)>cutoff.date():raise ValueError('FUTURE_FINANCIAL_PERIOD')
    if p3['knowledge_cutoff_at']!=p4['knowledge_cutoff_at']:raise ValueError('P4_CUTOFF_MISMATCH')
    if p3['module_values']!=p4['module_values']:raise ValueError('P4_MODULE_VALUE_CHANGED')
    for value in p3['module_values'].values():
        if value is not None and (isinstance(value,bool) or not math.isfinite(float(value))):raise ValueError('NONFINITE_MODULE')
    if p3['status']=='SCORE_INPUT_READY':
        if p3['reasons'] or any(p3['module_values'].get(key) is None for key in WEIGHTS):raise ValueError('READY_WITH_MISSING_MODULES')
    elif p3['status']=='EXPLICIT_REJECTION':
        if not p3['reasons']:raise ValueError('REJECTION_WITHOUT_REASON')
    else:raise ValueError('UNKNOWN_P3_STATUS')
    price=p3.get('price')
    if p3.get('m2_source_gate',{}).get('raw_close_basis_verified') is True and (not price or 'raw_close' not in price):raise ValueError('THB_VERIFIED_GATE_PRICE_REPLACED')
    if price and (date.fromisoformat(price['trade_date'])>cutoff.date() or (price.get('ticker') if 'raw_close' in price else price.get('source_ticker'))!=p3['ticker']):raise ValueError('PRICE_LOOKAHEAD_OR_CURRENT_ALIAS')
    if p4['status']=='EXPLICIT_REJECTION':
        if p4['rank'] is not None or p4['final_score'] is not None or p4.get('decision')=='AL':raise ValueError('REJECTED_ROW_HAS_SCORE_OR_BUY')
    elif p4['status']=='SCORED':
        values=p3['module_values']
        if p3['status']!='SCORE_INPUT_READY' or any(values.get(k) is None for k in WEIGHTS):raise ValueError('HIDDEN_NEUTRAL_FILL')
        base=math.fsum(float(values[k])*weight for k,weight in WEIGHTS.items())
        expected=base*(.60 if p4['veto'] else 1.0)
        if not math.isfinite(float(p4['final_score'])) or not math.isclose(base,p4['base_score'],abs_tol=1e-12) or not math.isclose(expected,p4['final_score'],abs_tol=1e-12):raise ValueError('WEIGHTS_OR_VETO_MISMATCH')
    else:raise ValueError('UNKNOWN_P4_STATUS')
    if hashlib.sha256(encoded(p3)).hexdigest()!=p4['p3_cell_sha256']:raise ValueError('P3_CELL_HASH_MISMATCH')

def validate_selected_source(actual, expected):
    if actual != expected:
        raise ValueError('VISIBLE_OWN_PERIOD_SELECTION_MISMATCH')

def validate_rankings(month_rows):
    valid=sorted((r for r in month_rows if r['status']=='SCORED'),key=lambda r:(-r['final_score'],r['ticker']))
    if [r['rank'] for r in valid]!=list(range(1,len(valid)+1)):
        raise ValueError('RANK_OR_TIEBREAK_MISMATCH')

def validate_semantic_entity_mapping(semantic):
    report=semantic['report'];source=report['source_entity_code']
    binding=semantic.get('mapping_ticker_binding')
    if binding is None:
        if len(independent_entity_tokens(source))>1:
            raise ValueError('COMPOSITE_SEMANTIC_MAPPING_BINDING_MISSING')
        return source
    tokens=independent_entity_tokens(source)
    if not tokens:raise ValueError('INVALID_SEMANTIC_ENTITY_TOKENS')
    expected={'contract':'ARCHIVED_ENTITY_TECHNICAL_TOKEN_V1','raw_source_entity_code':source,'declared_tokens':list(tokens),'technical_mapping_ticker':tokens[0],'source_member_sha256':report['member_sha256'],'original_dimensions_preserved':True,'share_or_price_basis_proven':False}
    if binding!=expected or semantic.get('report_mapping_ticker')!=tokens[0]:
        raise ValueError('SEMANTIC_ENTITY_MAPPING_BINDING_MISMATCH')
    return tokens[0]

def validate_semantic_report_identity(report,catalog_report):
    if catalog_report is None:raise ValueError('SEMANTIC_REPORT_NOT_IN_ACCEPTED_CATALOG')
    for field in ('archive_name','member_name','archive_sha256','member_sha256','notification_id','source_entity_code','report_year','report_period','statement_scope','presentation_currency','presentation_scale','company_name'):
        if report.get(field)!=catalog_report.get(field):raise ValueError('SEMANTIC_REPORT_RAW_IDENTITY_MISMATCH')
    if datetime.fromisoformat(report['published_at'])!=datetime.fromisoformat(catalog_report['published_at']):
        raise ValueError('SEMANTIC_REPORT_RAW_PUBLICATION_MISMATCH')

def verified_semantic_rows(semantic_dir,source_hashes,catalog_hash,catalog_reports=None):
    semantic_dir=Path(semantic_dir);main_path=semantic_dir/'semantic_reports.jsonl.gz'
    if sha(main_path)!=source_hashes['semantic']:raise ValueError('SEMANTIC_SOURCE_HASH_MISMATCH')
    combined=rows(main_path)
    supplements=(('alias','EXPERIMENTAL_PREDECESSOR_SEMANTIC_SUPPLEMENT_V1'),('entity','EXPERIMENTAL_ARCHIVED_ENTITY_SEMANTIC_SUPPLEMENT_V1'))
    for kind,contract in supplements:
        artifact=semantic_dir/f'semantic_{kind}_reports.jsonl.gz';receipt_path=semantic_dir/f'semantic_{kind}_receipt.json';hash_key=f'semantic_{kind}'
        if not (artifact.exists() or receipt_path.exists() or hash_key in source_hashes):continue
        if not artifact.exists() or not receipt_path.exists():raise ValueError(f'SEMANTIC_{kind.upper()}_PAIR_MISSING')
        receipt=json.loads(receipt_path.read_bytes());digest=sha(artifact)
        if digest!=source_hashes.get(hash_key) or digest!=receipt.get('artifact_sha256') or sha(receipt_path)!=source_hashes.get(hash_key+'_receipt'):
            raise ValueError(f'SEMANTIC_{kind.upper()}_SOURCE_HASH_MISMATCH')
        if receipt.get('contract')!=contract or receipt.get('catalog_sha256')!=catalog_hash or receipt.get('authoritative_claim_allowed') is not False:
            raise ValueError(f'SEMANTIC_{kind.upper()}_CONTRACT_OR_CATALOG_MISMATCH')
        additional=rows(artifact)
        if len(additional)!=receipt['report_count'] or sum(len(r['facts']) for r in additional)!=receipt['fact_count']:
            raise ValueError(f'SEMANTIC_{kind.upper()}_COUNTS_MISMATCH')
        if kind=='entity':
            for report in additional:validate_semantic_entity_mapping(report)
        combined.extend(additional)
    keys=[(r['report']['archive_name'],r['report']['member_name']) for r in combined]
    if catalog_reports is not None:
        accepted={(r['archive_name'],r['member_name']):r for r in catalog_reports}
        for row,key in zip(combined,keys):validate_semantic_report_identity(row['report'],accepted.get(key))
    if len(keys)!=len(set(keys)):raise ValueError('DUPLICATE_MAIN_OR_ALIAS_SEMANTIC_REPORT')
    return combined

def verify_selected_raw_member(task):
    directory,chosen=task
    archive=chosen['archive_name'];member=chosen['member_name']
    with ZipFile(Path(directory)/archive) as bundle:
        if bundle.namelist().count(member)!=1:raise ValueError('SELECTED_MEMBER_MISSING_OR_DUPLICATE')
        raw=bundle.read(member)
    digest=hashlib.sha256(raw).hexdigest()
    if digest!=chosen['member_sha256']:raise ValueError('RAW_MEMBER_HASH_MISMATCH')
    report=parse_kap_bulk_export_report(archive_name=archive,archive_sha256=chosen['archive_sha256'],member_name=member,raw_html=raw)
    observed=asdict(report);observed['published_at']=report.published_at.isoformat()
    validate_semantic_report_identity(observed,chosen)
    return (archive,member),digest

def verified_selected_source_prepass(cells,catalog,raw_dir,original_hashes,workers=4):
    accepted={(r['archive_name'],r['member_name']):r for r in catalog}
    unique={}
    for cell in cells:
        chosen=cell.get('selected_report')
        if chosen is None:continue
        key=(chosen['archive_name'],chosen['member_name'])
        if accepted.get(key)!=chosen:raise ValueError('SELECTED_REPORT_NOT_IDENTICAL_TO_ACCEPTED_CATALOG')
        unique[key]=chosen
    archives={}
    for chosen in unique.values():
        name=chosen['archive_name']
        if name not in archives:archives[name]=sha(Path(raw_dir)/name)
        if archives[name]!=original_hashes.get(name) or archives[name]!=chosen['archive_sha256']:
            raise ValueError('ORIGINAL_ARCHIVE_HASH_MISMATCH')
    tasks=[(str(raw_dir),report) for _,report in sorted(unique.items())]
    if workers==1:
        members=dict(map(verify_selected_raw_member,tasks))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            members=dict(pool.map(verify_selected_raw_member,tasks,chunksize=8))
    return archives,members

def audit(artifact_dir,semantic_dir,raw_dir,workers=4):
    import pandas as pd
    artifact_dir=Path(artifact_dir);semantic_dir=Path(semantic_dir);raw_dir=Path(raw_dir)
    receipt=json.loads((artifact_dir/'receipt.json').read_bytes())
    for name,digest in receipt['outputs'].items():
        if sha(artifact_dir/name)!=digest:raise ValueError('MATERIALIZATION_FILE_HASH_MISMATCH:'+name)
    p3=rows(artifact_dir/'p3_cells.jsonl.gz');p4=rows(artifact_dir/'p4_cells.jsonl.gz')
    members=_historical_membership(ROOT).sort_values(['signal_date','ticker'])
    expected=[(str(r.signal_date),str(r.ticker)) for r in members.itertuples()]
    actual=[(r['signal_date'],r['ticker']) for r in p3]
    if actual!=expected or len(actual)!=6000 or len(set(actual))!=6000:raise ValueError('HISTORICAL_MEMBERSHIP_OR_ORDER_MISMATCH')
    if len(p4)!=6000:raise ValueError('P4_CELL_COUNT_MISMATCH')
    index_path=ROOT/'data/backtest_sources/m3_source_package/index_closes.csv.gz'
    index=pd.read_csv(index_path);index['trade_date']=pd.to_datetime(index.trade_date)
    calendar=index.loc[index.index_code.eq('XU100'),['trade_date']].sort_values('trade_date')
    verified=verify_historical_m3_source_package(
        manifest_path=M3_MANIFEST,repo_root=ROOT,
        historical_membership=members,trading_calendar=calendar,require_closed=True)
    if not verified.closed:raise ValueError('HISTORICAL_M3_SOURCE_PACKAGE_NOT_CLOSED')
    routes_sha256=sha(M3_ROUTES)
    if receipt['source_hashes'].get('m3_manifest')!=sha(M3_MANIFEST) or receipt['source_hashes'].get('m3_sector_routes')!=routes_sha256:
        raise ValueError('HISTORICAL_M3_PACKAGE_RECEIPT_HASH_MISMATCH')
    routes=pd.read_csv(M3_ROUTES,dtype=str,keep_default_na=False)
    schedule=build_authorized_cutoff_execution_schedule(members[['month','signal_date','index_code']].drop_duplicates(),calendar)
    times={pd.Timestamp(r.signal_date).date().isoformat():r for r in schedule.itertuples()}
    catalog=rows(CAT/'reports.jsonl.gz');by_ticker=defaultdict(list)
    for report in catalog:
        for token in independent_entity_tokens(report['source_entity_code']):by_ticker[token].append(report)
    drift=defaultdict(list)
    for observed in rows(CAT/'drift_dependency_metadata.jsonl.gz'):
        if not observed.get('source_entity_code'):continue
        for token in independent_entity_tokens(observed['source_entity_code']):
            drift[token].append({**observed,'published_at':observed['observed_published_at'],'notification_id':observed['observed_notification_id']})
    semantics=verified_semantic_rows(semantic_dir,receipt['source_hashes'],sha(CAT/'reports.jsonl.gz'),catalog)
    semantic_by_key={(r['report']['archive_name'],r['report']['member_name']):r for r in semantics}
    if sha(CAT/'reports.jsonl.gz')!=receipt['source_hashes']['catalog'] or sha(semantic_dir/'semantic_reports.jsonl.gz')!=receipt['source_hashes']['semantic']:raise ValueError('SEMANTIC_OR_CATALOG_SOURCE_HASH_MISMATCH')
    if sha(index_path)!=receipt['source_hashes']['index_prices']:raise ValueError('INDEX_SOURCE_HASH_MISMATCH')
    price_path=ROOT/'data/backtest_sources/yahoo_resolved/historical_member_prices_resolved_2020-07_2026-08.csv.gz'
    if sha(price_path)!=receipt['source_hashes']['stock_prices']:raise ValueError('STOCK_PRICE_SOURCE_HASH_MISMATCH')
    market=pd.read_csv(price_path,low_memory=False);market['trade_date']=pd.to_datetime(market.trade_date)
    market=market.loc[market.ticker.eq(market.price_source_ticker)&market.trade_date.isin(calendar.trade_date)]
    market_by_ticker={str(t):frame.sort_values('trade_date') for t,frame in market.groupby('ticker')}
    original_manifest=json.loads((ROOT/'data/backtest_sources/kap_bulk_financial_source_capture/archive_manifest.json').read_bytes())
    original_hashes={r['filename']:r['sha256'] for r in original_manifest['archives']}
    checked_archives,checked_members=verified_selected_source_prepass(p3,catalog,raw_dir,original_hashes,workers)
    checked_thb=[];monthly=defaultdict(list);facts_checked=0
    for cell,scored in zip(p3,p4):
        validate_pair(cell,scored)
        timing=times[cell['signal_date']]
        if cell['knowledge_cutoff_at']!=timing.cutoff_at.isoformat() or cell['execution_at']!=timing.execution_at.isoformat():raise ValueError('AUTHORIZED_CUTOFF_SCHEDULE_MISMATCH')
        cutoff=datetime.fromisoformat(cell['knowledge_cutoff_at']);candidate_tickers=validate_financial_lineage(cell,required=True)
        chosen=independent_latest([r for ticker in candidate_tickers for r in by_ticker[ticker]],cutoff)
        validate_selected_source(cell['selected_report'],chosen)
        validate_entity_binding(cell,candidate_tickers)
        reported_price=cell.get('price');ticker_prices=market_by_ticker.get(cell['ticker'])
        eligible_prices=ticker_prices.loc[ticker_prices.trade_date.le(pd.Timestamp(cutoff.date()))] if ticker_prices is not None else None
        if reported_price is not None and 'raw_close' in reported_price:
            checked_thb.append(validate_thb_price(cell))
        elif eligible_prices is not None and not eligible_prices.empty:
            latest_date=eligible_prices.trade_date.max();last_rows=eligible_prices.loc[eligible_prices.trade_date.eq(latest_date)]
            if not reported_price or reported_price['trade_date']!=latest_date.date().isoformat() or reported_price['file_sha256']!=receipt['source_hashes']['stock_prices'] or float(reported_price['close']) not in set(last_rows.close.astype(float)):
                raise ValueError('PRICE_SOURCE_VALUE_OR_LATEST_DATE_MISMATCH')
        elif reported_price is not None:raise ValueError('PRICE_WITHOUT_PRIMARY_ROW')
        newer=independent_latest([r for ticker in candidate_tickers for r in drift[ticker]],cutoff)
        if newer and (chosen is None or (period_end(newer),datetime.fromisoformat(newer['published_at']),int(newer['notification_id']))>(period_end(chosen),datetime.fromisoformat(chosen['published_at']),int(chosen['notification_id']))):
            if 'FINANCIAL_SOURCE_PRIMARY_BYTES_MISSING' not in cell['reasons'] or cell['own_period_semantic_facts']:raise ValueError('DRIFT_DEPENDENCY_SILENTLY_BYPASSED')
        if chosen:
            archive=chosen['archive_name'];member=chosen['member_name'];key=(archive,member)
            if archive not in checked_archives:
                digest=sha(raw_dir/archive)
                if digest!=original_hashes[archive] or digest!=chosen['archive_sha256']:raise ValueError('ORIGINAL_ARCHIVE_HASH_MISMATCH')
                checked_archives[archive]=digest
            if key not in checked_members:
                with ZipFile(raw_dir/archive) as bundle:raw=bundle.read(member)
                digest=hashlib.sha256(raw).hexdigest()
                if digest!=chosen['member_sha256']:raise ValueError('RAW_MEMBER_HASH_MISMATCH')
                report=parse_kap_bulk_export_report(archive_name=archive,archive_sha256=checked_archives[archive],member_name=member,raw_html=raw)
                if report.source_entity_code!=chosen['source_entity_code'] or report.published_at.isoformat()!=chosen['published_at'] or report.company_name!=chosen['company_name']:raise ValueError('RAW_REPORT_IDENTITY_MISMATCH')
                checked_members[key]=digest
            semantic=semantic_by_key.get(key)
            semantic_fact_ticker=validate_semantic_entity_mapping(semantic) if semantic else chosen['source_entity_code']
            fact_hashes={hashlib.sha256(encoded(f)).hexdigest() for f in semantic['facts']} if semantic else set()
            for fact in cell['own_period_semantic_facts']:
                if hashlib.sha256(encoded(fact)).hexdigest() not in fact_hashes:raise ValueError('FACT_NOT_IN_SOURCE_SEMANTIC_ARTIFACT')
                if fact['disclosure_id']!='KAP:'+str(chosen['notification_id']):raise ValueError('FACT_DISCLOSURE_ID_MISMATCH')
                if fact['ticker']!=semantic_fact_ticker or datetime.fromisoformat(fact['published_at'])>cutoff or datetime.fromisoformat(fact['published_at'])!=datetime.fromisoformat(chosen['published_at']) or fact['period_end']!=period_end(chosen).isoformat():raise ValueError('FACT_TICKER_PERIOD_OR_PUBLICATION_MISMATCH')
                for field in ('archive_name','archive_sha256','member_name','member_sha256','source_entity_code'):
                    if fact['dimensions'][field]!=chosen[field]:raise ValueError('FACT_RAW_LINEAGE_MISMATCH')
                if not Decimal(str(fact['value'])).is_finite():raise ValueError('NONFINITE_FINANCIAL_FACT')
                facts_checked+=1
        elif cell['own_period_semantic_facts']:raise ValueError('FACTS_WITHOUT_SELECTED_REPORT')
        validate_historical_family(cell,semantic if chosen else None,routes,routes_sha256)
        monthly[cell['month']].append(scored)
    if len(monthly)!=60:raise ValueError('MONTH_COUNT_MISMATCH')
    for month,month_rows in sorted(monthly.items()):
        if len(month_rows)!=100 or rows(artifact_dir/f'p4_{month}.jsonl.gz')!=month_rows:raise ValueError('MONTHLY_SLICE_MISMATCH')
        validate_rankings(month_rows)
    expected_monthly=[{'month':month,'total':len(group),'valid':sum(r['status']=='SCORED' for r in group),'rejected':sum(r['status']=='EXPLICIT_REJECTION' for r in group)} for month,group in sorted(monthly.items())]
    if receipt['p4_months']!=expected_monthly or receipt['p4_valid_total_scores']!=sum(r['valid'] for r in expected_monthly):raise ValueError('MONTHLY_SUMMARY_COUNTS_MISMATCH')
    status_counts=Counter(r['status'] for r in p3)
    if receipt['total_cells']!=6000 or receipt['score_input_ready']!=status_counts['SCORE_INPUT_READY'] or receipt['explicit_rejections']!=status_counts['EXPLICIT_REJECTION']:raise ValueError('P3_SUMMARY_COUNTS_MISMATCH')
    if receipt.get('financial_alias_selected_cells')!=sum(bool(r['financial_ticker_lineage']['alias_selected']) for r in p3):raise ValueError('FINANCIAL_ALIAS_COUNTS_MISMATCH')
    if receipt['authoritative_claim_allowed'] is not False:raise ValueError('AUTHORITATIVE_CLAIM_NOT_ALLOWED')
    return {'contract':'P6_PARTIAL_SOURCE_TO_REJECTION_AUDIT_V1','profile':'EXPERIMENTAL_RISK_ACCEPTED_5Y','result':'PASS','total_cells':6000,'months':60,'status_counts':dict(sorted(Counter(r['status'] for r in p3).items())),'verified_original_archives':checked_archives,'verified_selected_members':len(checked_members),'verified_fact_occurrences':facts_checked,'verified_canonical_thb_prices':checked_thb,'member_identity_inventory_sha256':hashlib.sha256(encoded([{'archive':a,'member':m,'sha256':digest} for (a,m),digest in sorted(checked_members.items())])).hexdigest(),'p3_sha256':sha(artifact_dir/'p3_cells.jsonl.gz'),'p4_sha256':sha(artifact_dir/'p4_cells.jsonl.gz'),'audit_producer_sha256':sha(__file__),'nav_trade_conservation_audited':False,'p6_full_completed':False,'authoritative_claim_allowed':False,'limitations':['Semantic numerical mapping is bound to the versioned semantic artifact, not independently remapped for every report by this audit.','No portfolio/NAV conservation claim until an actual P5 artifact is separately audited.']}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--artifact-dir',type=Path,required=True);parser.add_argument('--semantic-dir',type=Path,required=True);parser.add_argument('--raw-dir',type=Path,default=ROOT/'private/reconstructed_kap_archives');parser.add_argument('--output',type=Path,required=True);parser.add_argument('--workers',type=int,default=4);args=parser.parse_args()
    result=audit(args.artifact_dir,args.semantic_dir,args.raw_dir,args.workers);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_bytes(encoded(result));print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
