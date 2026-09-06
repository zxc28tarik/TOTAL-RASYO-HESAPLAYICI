"""Source-bound experimental materialization, with per-cell dependency failures.

This entry point cannot relax authoritative gates, infer a current sector, or
turn partial module diagnostics into a Total Rasyo score. Reconstructed metadata
is an index over verified primary bytes, not recovered original evidence.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import gzip
import hashlib
import json
import math
from pathlib import Path

import pandas as pd
from scripts.build_historical_m3_source_package import _historical_membership
from scripts.materialize_experimental_financial_facts import encoded, sha
from scripts.experimental_financial_ticker_lineage import candidate_source_tickers, lineage_receipt
from scripts.experimental_financial_entity_binding import matching_financial_entities, financial_entity_binding
from src.analytics.historical_cutoff_execution_policy import build_authorized_cutoff_execution_schedule
from src.analytics.historical_pit_ek9_replay import run_historical_pit_ek9_replay
from src.analytics.historical_m3_source_package import verify_historical_m3_source_package
from src.analytics.total_rasyo_combine import combine_company_result
from src.analytics.total_rasyo_module_reader import CompanyModuleContext, ModuleComponent, READ_MODULE_KEYS

ROOT=Path(__file__).resolve().parents[1]
PROFILE='EXPERIMENTAL_RISK_ACCEPTED_5Y'
RISKS=['ORIGINAL_CATALOG_BYTES_UNAVAILABLE','SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED']
CAT=ROOT/'data/backtest_sources/reconstructed_experimental_kap_v1'
PRICES=ROOT/'data/backtest_sources/yahoo_resolved/historical_member_prices_resolved_2020-07_2026-08.csv.gz'
INDICES=ROOT/'data/backtest_sources/m3_source_package/index_closes.csv.gz'
M3_MANIFEST=ROOT/'data/backtest_sources/m3_source_package/manifest.json'
M3_ROUTES=ROOT/'data/backtest_sources/m3_source_package/sector_routes.csv.gz'

def read_rows(path):
    return [json.loads(line) for line in gzip.decompress(Path(path).read_bytes()).splitlines()]

def write_rows(path, rows):
    Path(path).write_bytes(gzip.compress(b''.join(encoded(r) for r in rows),mtime=0))

def quarter(row):
    return date(int(row['report_year']),int(row['report_period'][1])*3,1)+pd.offsets.QuarterEnd(0)

def report_precedence(row):
    # Compare instants, not ISO string offsets. This mirrors the existing public
    # KAP version selector within each financial period; no invented scope rank.
    publication=datetime.fromisoformat(row['published_at'])
    if publication.tzinfo is None or publication.utcoffset() is None:
        raise ValueError('REPORT_PUBLICATION_TIMEZONE_REQUIRED')
    return quarter(row),publication,int(row['notification_id'])

def select_report(reports, cutoff):
    visible=[r for r in reports if datetime.fromisoformat(r['published_at'])<=cutoff and quarter(r).date()<=cutoff.date()]
    if not visible: return None
    visible.sort(key=report_precedence,reverse=True)
    return visible[0]

def financial_selection(ticker, cutoff, catalog_by_ticker, semantic_by_key, drift_by_ticker):
    source_tickers=matching_financial_entities(ticker,cutoff,set(catalog_by_ticker)|set(drift_by_ticker))
    reports=[r for source in source_tickers for r in catalog_by_ticker.get(source,[])]
    chosen=select_report(reports,cutoff)
    drift=select_report([r for source in source_tickers for r in drift_by_ticker.get(source,[])],cutoff)
    reasons=[]
    if chosen is None:
        reasons.append('PIT_PUBLICATION_AFTER_CUTOFF' if reports else 'FINANCIAL_SOURCE_PRIMARY_BYTES_MISSING')
    if drift and (chosen is None or report_precedence(drift)>report_precedence(chosen)):
        reasons.append('FINANCIAL_SOURCE_PRIMARY_BYTES_MISSING')
    semantic=semantic_by_key.get((chosen['archive_name'],chosen['member_name'])) if chosen else None
    facts=[]
    if chosen and not reasons:
        if semantic is None or not semantic['facts']: reasons.append('SEMANTIC_MAPPING_UNRESOLVED')
        else:
            end=quarter(chosen).date().isoformat()
            # Comparative values printed in a subsequent report are not earlier-period inputs.
            facts=[f for f in semantic['facts'] if f['period_end']==end and datetime.fromisoformat(f['published_at'])<=cutoff]
            if not facts: reasons.append('SEMANTIC_MAPPING_UNRESOLVED')
    return chosen, semantic, facts, sorted(set(reasons)), drift

def validate_cells(cells, members):
    expected=[(str(r.signal_date),str(r.ticker)) for r in members.sort_values(['signal_date','ticker']).itertuples()]
    actual=[(r['signal_date'],r['ticker']) for r in cells]
    if actual!=expected or len(set(actual))!=6000: raise ValueError('HISTORICAL_MEMBERSHIP_OR_ORDER_MISMATCH')
    for r in cells:
        cutoff=datetime.fromisoformat(r['knowledge_cutoff_at'])
        if cutoff.date()>=date.fromisoformat(r['signal_date']): raise ValueError('SIGNAL_DAY_VALUATION_LEAKAGE')
        if r['selected_report'] and datetime.fromisoformat(r['selected_report']['published_at'])>cutoff: raise ValueError('FUTURE_FINANCIAL_STATEMENT')
        if r['status']=='SCORE_INPUT_READY' and r['reasons']: raise ValueError('READY_WITH_MISSING_INPUTS')
        if r['historical_family_source']=='CURRENT_SNAPSHOT': raise ValueError('CURRENT_SECTOR_FALLBACK')

def append_semantic_alias(semantic_dir, catalog_path, semantics, kind='alias'):
    if kind not in {'alias','entity'}: raise ValueError('UNKNOWN_SEMANTIC_SUPPLEMENT_KIND')
    alias_path=semantic_dir/f'semantic_{kind}_reports.jsonl.gz'
    alias_receipt_path=semantic_dir/f'semantic_{kind}_receipt.json'
    alias_sha=None
    if alias_path.exists() or alias_receipt_path.exists():
        if not alias_path.is_file() or not alias_receipt_path.is_file():
            raise ValueError('SEMANTIC_ALIAS_ARTIFACT_RECEIPT_PAIR_MISSING')
        alias_receipt=json.loads(alias_receipt_path.read_bytes())
        alias_sha=sha(alias_path)
        if alias_sha!=alias_receipt['artifact_sha256']:
            raise ValueError('SEMANTIC_ALIAS_ARTIFACT_HASH_MISMATCH')
        if sha(catalog_path)!=alias_receipt['catalog_sha256']:
            raise ValueError('SEMANTIC_ALIAS_CATALOG_LINEAGE_MISMATCH')
        semantics.extend(read_rows(alias_path))
    semantic_keys=[(r['report']['archive_name'],r['report']['member_name']) for r in semantics]
    if len(semantic_keys)!=len(set(semantic_keys)):
        raise ValueError('DUPLICATE_MAIN_OR_ALIAS_SEMANTIC_REPORT')
    return semantics,alias_sha


def apply_core_module_outputs(module_values, module_reasons, diagnostic):
    """Copy only actual PIT replay outputs; never synthesize a neutral score."""
    good_count = None
    for output_key, module_key, value_key in (
        ('m1', 'M1', 'm1'), ('ek1', 'Ek1', 'ek1')):
        rows = diagnostic.get(output_key) or []
        if not rows:
            continue
        row = rows[-1]
        value = row.get(value_key)
        if value is None:
            continue
        module_values[module_key] = float(value)
        module_reasons[module_key] = None
        row_count = row.get('good_count_ge8')
        if row_count is not None:
            parsed = int(row_count)
            if good_count is not None and good_count != parsed:
                raise ValueError('CORE_MODULE_GOOD_COUNT_CONFLICT')
            good_count = parsed
    return good_count


def family_from_historical_market_route(ticker, signal_day, route, routes_sha256):
    """Derive NONFIN only from a positive non-financial broad-index route.

    XUMAL deliberately stays unresolved because BANK, insurance, finance,
    holding and GYO all share that broad index.  This is therefore not a
    specialist-family exclusion fallback.
    """
    if not route or route.get('mapping_version') != 'HISTORICAL_M3_SOURCE_PACKAGE_V1':
        return None, None
    code = route.get('sector_index_code')
    if code not in {'XUSIN', 'XUHIZ', 'XUTEK'}:
        return None, None
    return 'NONFIN', {
        'ticker': str(ticker), 'cutoff_day': str(signal_day), 'family': 'NONFIN',
        'effective_from': route.get('valid_from'), 'effective_to': route.get('valid_to'),
        'source_identity': route.get('source_id'),
        'source_path': str(M3_ROUTES.relative_to(ROOT)).replace('\\','/'),
        'source_hash': routes_sha256,
        'mapping_version': 'HISTORICAL_BROAD_INDEX_TO_ECONOMIC_FAMILY_V1',
        'sector_index_code': code,
    }

def build(semantic_dir, output):
    from scripts.experimental_core_module_materializer import build_core_modules
    from scripts.experimental_historical_market_modules import build_market_modules
    from scripts.experimental_m2_source_gate import build_m2_source_gate, verified_dated_share_sources
    catalog_receipt=json.loads((CAT/'receipt.json').read_bytes())
    for name,digest in catalog_receipt['outputs'].items():
        if sha(CAT/name)!=digest: raise ValueError('CATALOG_OUTPUT_HASH_MISMATCH:'+name)
    semantic_receipt=json.loads((semantic_dir/'semantic_receipt.json').read_bytes())
    if sha(semantic_dir/'semantic_reports.jsonl.gz')!=semantic_receipt['artifact_sha256']: raise ValueError('SEMANTIC_ARTIFACT_HASH_MISMATCH')
    if sha(CAT/'reports.jsonl.gz')!=semantic_receipt['catalog_sha256']: raise ValueError('SEMANTIC_CATALOG_LINEAGE_MISMATCH')
    catalog=read_rows(CAT/'reports.jsonl.gz'); semantics=read_rows(semantic_dir/'semantic_reports.jsonl.gz')
    semantics,alias_sha=append_semantic_alias(semantic_dir,CAT/'reports.jsonl.gz',semantics)
    semantics,entity_sha=append_semantic_alias(semantic_dir,CAT/'reports.jsonl.gz',semantics,kind='entity')
    alias_receipt_path=semantic_dir/'semantic_alias_receipt.json'
    by_ticker=defaultdict(list); drift_by_ticker=defaultdict(list)
    for r in catalog: by_ticker[r['source_entity_code']].append(r)
    for r in read_rows(CAT/'drift_dependency_metadata.jsonl.gz'):
        # Observed replacement metadata is only a dependency warning; it is
        # never fed to the semantic mapper or historical fact selection.
        diagnostic=dict(r,published_at=r['observed_published_at'],notification_id=r['observed_notification_id'])
        drift_by_ticker[r['source_entity_code']].append(diagnostic)
    semantic_by_key={(r['report']['archive_name'],r['report']['member_name']):r for r in semantics}
    members=_historical_membership(ROOT).sort_values(['signal_date','ticker'])
    indices=pd.read_csv(INDICES); indices['trade_date']=pd.to_datetime(indices.trade_date)
    calendar=indices.loc[indices.index_code.eq('XU100'),['trade_date']].sort_values('trade_date')
    m3_package = verify_historical_m3_source_package(
        manifest_path=M3_MANIFEST, repo_root=ROOT,
        historical_membership=members, trading_calendar=calendar, require_closed=True)
    if not m3_package.closed:
        raise ValueError('HISTORICAL_M3_SOURCE_PACKAGE_NOT_CLOSED')
    sector_routes=pd.read_csv(M3_ROUTES,dtype=str,keep_default_na=False)
    schedule=build_authorized_cutoff_execution_schedule(members[['month','signal_date','index_code']].drop_duplicates(),calendar)
    prices=pd.read_csv(PRICES,low_memory=False); prices['trade_date']=pd.to_datetime(prices.trade_date)
    # Do not resolve old symbols from a current successor's price stream.
    prices=prices.loc[prices.ticker.eq(prices.price_source_ticker)].copy()
    price_sha=sha(PRICES)
    routes_sha=sha(M3_ROUTES)
    dated_shares=verified_dated_share_sources()
    cells=[]; p4=[]; candidates=[]; monthly=[]; core_audits=[]
    output.mkdir(parents=True,exist_ok=True)
    for timing in schedule.itertuples(index=False):
        signal=pd.Timestamp(timing.signal_date).date().isoformat(); cutoff=timing.cutoff_at.to_pydatetime(); end=pd.Timestamp(timing.previous_trading_date)
        tickers=tuple(sorted(members.loc[members.signal_date.eq(signal),'ticker']))
        universe=pd.DataFrame({'ticker':tickers})
        bounded_calendar=calendar.loc[calendar.trade_date.le(end)]
        bounded_prices=prices.loc[prices.ticker.isin(tickers)&prices.trade_date.le(end)&prices.trade_date.isin(bounded_calendar.trade_date)]
        # Existing production return-volatility math is independent of economic routing.
        market=build_market_modules(cutoff,tickers,bounded_calendar,bounded_prices,
                                    indices.loc[indices.trade_date.le(end)],
                                    sector_routes=sector_routes)
        ek9values={t:m['Ek9']['value'] for t,m in market['per_ticker'].items() if m['Ek9']['value'] is not None}
        core=build_core_modules(semantics,cutoff,tickers,family_routes=sector_routes)
        core_audits.append({'signal_date':signal,**core})
        month_p4=[]
        for ticker in tickers:
            selected, semantic, facts, reasons, drift=financial_selection(ticker,cutoff,by_ticker,semantic_by_key,drift_by_ticker)
            financial_lineage=lineage_receipt(ticker,cutoff)
            source_ticker=selected.get('source_entity_code',ticker) if selected else None
            financial_lineage['selected_source_ticker']=source_ticker
            entity_binding=financial_entity_binding(ticker,cutoff,selected) if selected else None
            alias_selected=entity_binding is not None and ticker not in entity_binding['entity_tokens']
            financial_lineage['alias_selected']=alias_selected
            # A dated family observation is independent of whether the next
            # financial statement's primary bytes are available.
            family=semantic.get('historical_family') if semantic else None
            family_lineage = None
            market_cell=market['per_ticker'][ticker]
            if family is None:
                family, family_lineage = family_from_historical_market_route(
                    ticker, signal, market_cell.get('route'), routes_sha)
            if family is None:
                reasons.append('HISTORICAL_SECTOR_FAMILY_EVIDENCE_MISSING')
            current=bounded_prices.loc[bounded_prices.ticker.eq(ticker)].sort_values('trade_date')
            price=None
            if not current.empty:
                p=current.iloc[-1]
                price={'trade_date':p.trade_date.date().isoformat(),'close':float(p.close),'source_ticker':str(p.price_source_ticker),'resolution':str(p.price_resolution),'file_sha256':price_sha,'valuation_basis_verified':False}
            if price is None: reasons.append('PRICE_MISSING')
            share_candidates=[s for s in dated_shares if s['ticker']==ticker and datetime.fromisoformat(s['published_at'])<=cutoff]
            share_source=max(share_candidates,key=lambda s:(s['source_date'],s['published_at'])) if share_candidates else None
            m2_gate=build_m2_source_gate(selected,facts,share_source,price,cutoff,ticker,family)
            if m2_gate['raw_close_basis_verified']:
                reasons=[r for r in reasons if r!='PRICE_MISSING']
                price={**m2_gate['price_source'],'valuation_basis_verified':True}
            reasons.extend(m2_gate['reasons'])
            if m2_gate['status']=='VERIFIED_BASIS':
                # New usable evidence must reach a real sector engine, not a
                # hard-coded rejection or an invented Total result.
                raise RuntimeError('VERIFIED_BASIS_REQUIRES_SECTOR_ENGINE_MATERIALIZATION:'+ticker)
            for key in ('M3','Ek4','Ek9'):
                if market_cell[key]['value'] is None: reasons.extend(market_cell[key]['reasons'])
            fact_values={f['canonical_field']:f['value'] for f in facts}
            if family in {'HOLDING','GYO'} and not {'TOTAL_EQUITY','ISSUED_CAPITAL'}.issubset(fact_values): reasons.append('MODULE_INPUT_INSUFFICIENT')
            # CORE diagnostics are not a full CORE+VAL module context.
            module_values={k:None for k in ('M2',*READ_MODULE_KEYS)}
            for key in ('M3','Ek4','Ek9'): module_values[key]=market_cell[key]['value']
            module_reasons={k:'MODULE_INPUT_INSUFFICIENT' for k in READ_MODULE_KEYS}
            for key in ('M3','Ek4','Ek9'): module_reasons[key]=';'.join(market_cell[key]['reasons']) or None
            core_diag=core['per_ticker'][ticker]
            good_count=apply_core_module_outputs(module_values,module_reasons,core_diag)
            reasons=sorted(set(reasons))
            cell={'contract':'EXPERIMENTAL_P3_CELL_V1','profile':PROFILE,'month':str(timing.month),'signal_date':signal,'knowledge_cutoff_at':cutoff.isoformat(),'execution_at':timing.execution_at.isoformat(),'ticker':ticker,'status':'EXPLICIT_REJECTION','reasons':reasons,'risk_ids':RISKS,'selected_report':selected,'unverified_newer_archive_dependency':drift,'historical_family':family,'historical_family_source':'DATED_REPORT' if family_lineage is None and family else ('HISTORICAL_M3_BROAD_SECTOR_ROUTE' if family else None),'historical_family_lineage':family_lineage,'own_period_semantic_facts':facts,'semantic_status':semantic['status'] if semantic else None,'price':price,'action_proof_status':'MISSING','module_values':module_values,'module_reasons':module_reasons}
            cells.append(cell)
            cell['m2_source_gate']=m2_gate
            cell['financial_ticker_lineage']=financial_lineage
            cell['financial_entity_binding']=entity_binding
            cell['m2_execution_stage']=m2_gate['implementation_stage']
            cell['market_module_lineage']=market_cell
            cell['partial_core_diagnostic_status']=core_diag['status']
            cell['partial_core_diagnostic_reasons']=core_diag['reasons']
            if family in {'HOLDING','GYO'}:
                candidates.append({**cell,'experimental_m2_profile':'EXPERIMENTAL_'+family+'_BOOK_EQUITY_TWO_AXIS_V1','book_equity':fact_values.get('TOTAL_EQUITY'),'issued_capital':fact_values.get('ISSUED_CAPITAL'),'canonical_nav':False})
            components={k:ModuleComponent(k,module_values[k],cutoff if module_values[k] is not None else None,'HISTORICAL_PIT_'+k.upper()+'_REPLAY' if module_values[k] is not None else None,module_values[k] is None,module_reasons[k]) for k in READ_MODULE_KEYS}
            context=CompanyModuleContext(ticker,components,good_count,good_count is None,
                'GOOD_COUNT_KAYNAGI_YOK' if good_count is None else None,end.date(),cutoff)
            result=combine_company_result(ticker=ticker,routed_engine=family or 'UNRESOLVED',engine_run=None,module_context=context)
            if result.final_score is not None: raise RuntimeError('UNPROVEN_M2_WAS_SCORED')
            scored={'contract':'EXPERIMENTAL_P4_CELL_V1','profile':PROFILE,'month':str(timing.month),'signal_date':signal,'knowledge_cutoff_at':cutoff.isoformat(),'ticker':ticker,'status':'EXPLICIT_REJECTION','rank':None,'decision':result.decision,'final_score':result.final_score,'base_score':result.base_score,'veto':result.veto_flag,'module_values':module_values,'module_count':sum(value is not None for value in module_values.values()),'good_count':good_count,'missing_modules':list(result.missing_modules),'reasons':reasons,'combiner_status':result.total_rasyo_status,'p3_cell_sha256':hashlib.sha256(encoded(cell)).hexdigest()}
            p4.append(scored);month_p4.append(scored)
        write_rows(output/f'p4_{timing.month}.jsonl.gz',month_p4)
        monthly.append({'month':str(timing.month),'total':100,'valid':sum(r['status']=='SCORED' for r in month_p4),'rejected':sum(r['status']=='EXPLICIT_REJECTION' for r in month_p4)})
        print(str(timing.month),'financial_sources',sum(bool(c['own_period_semantic_facts']) for c in cells[-100:]),'Ek9',len(ek9values),'Total',monthly[-1]['valid'],flush=True)
    validate_cells(cells,members)
    write_rows(output/'p3_cells.jsonl.gz',cells);write_rows(output/'p4_cells.jsonl.gz',p4);write_rows(output/'p2_candidates.jsonl.gz',candidates);write_rows(output/'core_diagnostics.jsonl.gz',core_audits)
    receipt={'contract':'EXPERIMENTAL_P3_P4_MATERIALIZATION_V1','profile':PROFILE,'risk_ids':RISKS,'total_cells':len(cells),'score_input_ready':sum(r['status']=='SCORE_INPUT_READY' for r in cells),'explicit_rejections':sum(r['status']=='EXPLICIT_REJECTION' for r in cells),'cells_with_own_period_financial_facts':sum(bool(r['own_period_semantic_facts']) for r in cells),'reason_counts':dict(sorted(Counter(x for r in cells for x in r['reasons']).items())),'p2_candidate_count':len(candidates),'p2_usable_count':0,'p4_months':monthly,'p4_valid_total_scores':sum(m['valid'] for m in monthly),'p5_eligible_for_strategy_performance':False,'original_993_reproduced':False,'financial_materialization_performed':True,'authoritative_claim_allowed':False,'m3_source_package':{'contract':m3_package.contract,'effective_status':m3_package.effective_status,'route_rows':m3_package.route_rows,'index_close_rows':m3_package.index_close_rows},'source_hashes':{'catalog':sha(CAT/'reports.jsonl.gz'),'semantic':sha(semantic_dir/'semantic_reports.jsonl.gz'),'stock_prices':price_sha,'index_prices':sha(INDICES),'m3_manifest':sha(M3_MANIFEST),'m3_sector_routes':sha(M3_ROUTES)},'producer_hashes':{name:sha(ROOT/name) for name in ['scripts/materialize_experimental_p3_p4.py','scripts/experimental_core_module_materializer.py','scripts/experimental_historical_market_modules.py','scripts/experimental_m2_source_gate.py','scripts/experimental_financial_ticker_lineage.py','src/analytics/historical_pit_m3_replay.py','src/analytics/historical_pit_ek4_replay.py','src/analytics/total_rasyo_combine.py','src/analytics/total_rasyo_score.py','src/analytics/historical_pit_ek9_replay.py','src/analytics/historical_m3_source_package.py']},'outputs':{p.name:sha(p) for p in sorted(output.glob('*.gz'))}}
    receipt['per_module_nonnull_counts']={key:sum(c['module_values'].get(key) is not None for c in cells) for key in ('M2',*READ_MODULE_KEYS)}
    receipt['producer_hashes'].update({name:sha(ROOT/name) for name in (
        'config/ratios.json','config/sectors.json',
        'src/ingest/company_fact_materializer.py',
        'src/analytics/company_ratio_pipeline.py','src/analytics/ratios_calc.py',
        'src/analytics/rsc_scoring.py','src/analytics/historical_pit_rsc_replay.py',
        'src/analytics/historical_pit_m1_replay.py','src/analytics/historical_pit_ek1_replay.py',
        'src/analytics/historical_cutoff_execution_policy.py',
        'src/analytics/price_level_adapter.py','src/analytics/price_level_action_evidence.py',
        'src/analytics/historical_valuation_price_supplement.py')})
    receipt['core_diagnostic_status_counts']=dict(sorted(Counter(c['partial_core_diagnostic_status'] for c in cells).items()))
    receipt['financial_alias_selected_cells']=sum(c['financial_ticker_lineage']['alias_selected'] for c in cells)
    receipt['core_financial_alias_adapter_implemented']=True
    receipt['core_alias_history_consumed_cells']=sum(bool(d.get('alias_history_consumed')) for audit in core_audits for d in audit['per_ticker'].values())
    if alias_sha is not None:
        receipt['source_hashes']['semantic_alias']=alias_sha
        receipt['source_hashes']['semantic_alias_receipt']=sha(alias_receipt_path)
    if entity_sha is not None:
        receipt['source_hashes']['semantic_entity']=entity_sha
        receipt['source_hashes']['semantic_entity_receipt']=sha(semantic_dir/'semantic_entity_receipt.json')
    receipt['composite_financial_entity_cells']=sum(bool(c['financial_entity_binding'] and c['financial_entity_binding']['composite_entity']) for c in cells)
    receipt['producer_hashes']['scripts/experimental_financial_entity_binding.py']=sha(ROOT/'scripts/experimental_financial_entity_binding.py')
    (output/'receipt.json').write_bytes(encoded(receipt));return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--semantic-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args();print(json.dumps(build(a.semantic_dir,a.output_dir),indent=2))
