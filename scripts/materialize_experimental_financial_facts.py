"""Rebuild semantic facts from hash-verified primary bytes; never an original catalog.

Technical mapping family is deliberately separate from economic sector routing.
No current company master, alias fallback or cross-report period substitution.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from scripts.build_historical_m3_source_package import _historical_membership
from src.ingest.kap_bulk_financial_export import parse_kap_bulk_export_report, parse_kap_bulk_financial_cells
from src.ingest.kap_bulk_exact_semantic_mapping import build_bulk_exact_semantic_config
from src.ingest.kap_bulk_bank_exact_semantic_mapping import build_bulk_exact_bank_semantic_config
from src.ingest.kap_bulk_insurance_finance_exact_semantic_mapping import build_bulk_exact_insurance_semantic_config, build_bulk_exact_financial_semantic_config
from src.ingest.kap_bulk_semantic_adapter import bulk_cells_to_financial_facts, exact_label_fact_code
from src.ingest.api.semantic_facts import SemanticFactMapper
from scripts.kap_bulk_financial_native import parse_kap_bulk_financial_cells_native

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = datetime.fromisoformat('2026-09-06T00:00:00+00:00')
PROFILE = 'EXPERIMENTAL_RISK_ACCEPTED_5Y'

def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)+'\n').encode()

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def economic_family(name, roles):
    """Only explicit contemporaneous legal-name or specialist-schema evidence."""
    normalized=name.upper().replace('İ','I').replace('ı','I')
    candidates=set()
    if 'GAYRIMENKUL YATIRIM ORTAKLIGI' in normalized.replace('Ğ','G'): candidates.add('GYO')
    if 'HOLDING' in normalized or any(r.startswith('holding_role_') for r in roles): candidates.add('HOLDING')
    if any(r.startswith(('banks_role_', 'par-banks_role_')) for r in roles): candidates.add('BANK')
    if any(r.startswith('insurance_role_') for r in roles): candidates.add('INSURANCE')
    if any(r.startswith('finance_role_') for r in roles): candidates.add('FINANCIAL')
    if 'BANKASI' in normalized or 'BANK A.' in normalized: candidates.add('BANK')
    if 'SIGORTA' in normalized or 'EMEKLILIK' in normalized: candidates.add('INSURANCE')
    if any(x in normalized for x in ('FAKTORING','FINANSAL KIRALAMA','FINANSMAN A.')): candidates.add('FINANCIAL')
    return next(iter(candidates)) if len(candidates)==1 else None

def map_report(task):
    archive, row = task
    member=row['member_name']
    with ZipFile(archive) as z: raw=z.read(member)
    if hashlib.sha256(raw).hexdigest()!=row['member_sha256']: raise ValueError('MEMBER_HASH_MISMATCH')
    report=parse_kap_bulk_export_report(archive_name=row['archive_name'], archive_sha256=row['archive_sha256'], member_name=member, raw_html=raw)
    result={'report':asdict(report), 'profile':PROFILE, 'facts':[], 'numeric_cell_count':0, 'status':'SEMANTIC_MAPPING_UNRESOLVED'}
    try:
        cells=parse_kap_bulk_financial_cells_native(report,raw)
        roles=sorted({c.table_role for c in cells})
        family=economic_family(report.company_name,roles)
        result.update(roles=roles, historical_family=family, routing_policy='DATED_LEGAL_NAME_SPECIALIST_SCHEMA_V1', numeric_cell_count=len(cells))
        # Mapping is technical: general schema is not evidence of NONFIN economic family.
        configs=[build_bulk_exact_semantic_config('NONFIN'),build_bulk_exact_semantic_config('HOLDING'),build_bulk_exact_bank_semantic_config(),build_bulk_exact_insurance_semantic_config(),build_bulk_exact_financial_semantic_config()]
        mapped=[]
        for config in configs:
            codes={c for rule in config.fields for c in rule.source_codes}
            selected=[c for c in cells if exact_label_fact_code(c.fact_code,c.label_tr).upper() in codes]
            if not selected: continue
            facts=bulk_cells_to_financial_facts(report,selected,ticker=report.source_entity_code,extracted_at=CAPTURE)
            mapped.extend(SemanticFactMapper(config).map_facts(facts,mapped_at=CAPTURE))
        result['facts']=[asdict(f) for f in sorted(mapped,key=lambda f:(f.semantic_profile,f.period_end,f.canonical_field,f.lineage_sha256))]
        result['status']='SEMANTIC_FACTS_MATERIALIZED' if mapped else 'SEMANTIC_MAPPING_UNRESOLVED'
    except (ValueError,TypeError) as exc:
        result['reason']=type(exc).__name__+':'+str(exc)
    return result

def build(catalog, raw_dir, output, workers=4):
    import importlib.metadata
    if importlib.metadata.version('lxml')!='6.1.3': raise ValueError('INSTALL_PINNED_REQUIREMENTS_EXPERIMENTAL')
    rows=[json.loads(line) for line in gzip.decompress(catalog.read_bytes()).splitlines()]
    targets=set(_historical_membership(ROOT).ticker)
    rows=[r for r in rows if r['source_entity_code'] in targets]
    rows.sort(key=lambda r:(r['archive_name'],r['member_name']))
    expected={r['archive_name']:r['archive_sha256'] for r in rows}
    for name,digest in sorted(expected.items()):
        if sha(raw_dir/name)!=digest: raise ValueError('ARCHIVE_HASH_MISMATCH:'+name)
    output.mkdir(parents=True,exist_ok=True)
    count=0; mapped=0
    destination=output/'semantic_reports.jsonl.gz'
    with destination.open('wb') as dst, gzip.GzipFile(filename='',fileobj=dst,mode='wb',mtime=0) as compressed:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(map_report,((str(raw_dir/r['archive_name']),r) for r in rows),chunksize=4):
                compressed.write(encoded(result)); count+=1; mapped+=len(result['facts'])
                if count%100==0: print('semantic',count,'/',len(rows),'facts',mapped,flush=True)
    receipt={'contract':'RECONSTRUCTED_EXPERIMENTAL_SEMANTIC_FACTS_V1','profile':PROFILE,'report_count':count,'fact_count':mapped,'catalog_sha256':sha(catalog),'artifact_sha256':sha(destination),'generator_sha256':sha(__file__),'parser_backend':'lxml6.1.3','fast_parser_sha256':sha(ROOT/'scripts/kap_bulk_financial_fast.py'),'original_catalog_recovered':False,'historical_version_enumeration_complete':False,'risk_ids':['ORIGINAL_CATALOG_BYTES_UNAVAILABLE','SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED'],'archive_hashes':expected}
    (output/'semantic_receipt.json').write_bytes(encoded(receipt))
    return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--catalog',type=Path,required=True);p.add_argument('--raw-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--workers',type=int,default=4)
    a=p.parse_args();print(json.dumps(build(a.catalog,a.raw_dir,a.output_dir,a.workers),indent=2))
