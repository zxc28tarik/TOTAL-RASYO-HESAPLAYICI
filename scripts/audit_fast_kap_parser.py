"""Differential original/experimental parsing of immutable real KAP report bytes."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import gzip
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
import time
from zipfile import ZipFile
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from src.ingest.kap_bulk_financial_export import parse_kap_bulk_export_report,parse_kap_bulk_financial_cells
from scripts.kap_bulk_financial_fast import parse_kap_bulk_financial_cells_fast
from scripts.kap_bulk_financial_native import parse_kap_bulk_financial_cells_native
from scripts.reconstruct_experimental_kap_catalog import sha


def audit(backend='html.parser'):
    rows=[json.loads(line) for line in gzip.decompress((ROOT/'data/backtest_sources/reconstructed_experimental_kap_v1/reports.jsonl.gz').read_bytes()).splitlines()]
    picks={}
    for row in rows:
        picks.setdefault(tuple(row['technical_role_namespaces']),row)
        if row['source_entity_code']=='DOBUR' and row['archive_name']=='KAP_2021_3A.zip':
            picks[('general-DOBUR-regression',)]=row
    results=[]
    for family,row in sorted(picks.items()):
        path=ROOT/'private/reconstructed_kap_archives'/row['archive_name']
        if sha(path)!=row['archive_sha256']:
            raise ValueError('archive identity changed')
        with ZipFile(path) as bundle:
            raw=bundle.read(row['member_name'])
        if hashlib.sha256(raw).hexdigest()!=row['member_sha256']:
            raise ValueError('member identity changed')
        report=parse_kap_bulk_export_report(archive_name=path.name,archive_sha256=row['archive_sha256'],member_name=row['member_name'],raw_html=raw)
        start=time.perf_counter(); original=parse_kap_bulk_financial_cells(report,raw); old_seconds=time.perf_counter()-start
        start=time.perf_counter(); fast=parse_kap_bulk_financial_cells_native(report,raw) if backend=='native-lxml' else parse_kap_bulk_financial_cells_fast(report,raw,parser_backend=backend); fast_seconds=time.perf_counter()-start
        if original!=fast:
            raise ValueError(f'differential mismatch: {path.name}/{row["member_name"]}')
        payload=json.dumps([asdict(r) for r in original],ensure_ascii=False,sort_keys=True,default=str,separators=(',',':')).encode()
        result={'family':family,'archive':path.name,'archive_sha256':row['archive_sha256'],'member':row['member_name'],'member_sha256':row['member_sha256'],'cell_count':len(original),'output_sha256':hashlib.sha256(payload).hexdigest(),'original_seconds':old_seconds,'fast_seconds':fast_seconds,'dataclass_equality':True,'fast_backend':backend}
        results.append(result);print(json.dumps(result),flush=True)
    return {'contract':'EXPERIMENTAL_FAST_PARSER_REAL_DIFFERENTIAL_V1','reports':results,'timings_are_observations_not_reproducibility_claim':True,'sampled_equivalence_not_all_corpus_oracle':True,'python_version':sys.version,'beautifulsoup4_version':version('beautifulsoup4'),'lxml_version':version('lxml') if backend in {'lxml','native-lxml'} else None,'original_parser_sha256':sha(ROOT/'src/ingest/kap_bulk_financial_export.py'),'fast_parser_sha256':sha(ROOT/('scripts/kap_bulk_financial_native.py' if backend=='native-lxml' else 'scripts/kap_bulk_financial_fast.py')),'audit_producer_sha256':sha(Path(__file__))}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--backend',choices=['html.parser','lxml','native-lxml'],default='html.parser');parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=audit(args.backend);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_bytes((json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode())
if __name__=='__main__':
    main()
