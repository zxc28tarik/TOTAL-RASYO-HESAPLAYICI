"""Reconstruct experimental indexes from individually verified original KAP ZIPs.

This never replaces historical SHA256SUMS or claims original catalog identity.
Financial values are not invented: downstream consumers reopen hash-bound XLS.
"""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.ingest.kap_bulk_financial_export import parse_kap_bulk_export_report

CONTRACT = 'RECONSTRUCTED_SOURCE_CATALOG_V1'
PROFILE = 'EXPERIMENTAL_RISK_ACCEPTED_5Y'
RISKS = ['ORIGINAL_CATALOG_BYTES_UNAVAILABLE', 'SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED']
MANIFEST = ROOT / 'data/backtest_sources/kap_bulk_financial_source_capture/archive_manifest.json'
ROLES = re.compile(rb'class="([A-Za-z0-9_-]+)-row-[0-9]+(?: |")')

def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def inspect_archive(task):
    directory, expected = task
    path = Path(directory) / expected['filename']
    inventory = {'archive_name': expected['filename'], 'expected_sha256': expected['sha256'], 'expected_member_count': expected['member_count']}
    if not path.exists():
        return {**inventory, 'status': 'PRIMARY_ARCHIVE_BYTES_MISSING'}, [], []
    observed = sha(path)
    inventory.update(observed_sha256=observed, observed_size_bytes=path.stat().st_size)
    if observed != expected['sha256']:
        return {**inventory, 'status': 'IMMUTABLE_ARCHIVE_HASH_MISMATCH'}, [], []
    rows, errors = [], []
    with ZipFile(path) as bundle:
        members = sorted((i for i in bundle.infolist() if not i.is_dir()), key=lambda i: i.filename)
        if len(members) != expected['member_count'] or len({i.filename for i in members}) != len(members):
            raise ValueError('immutable manifest member count / duplicate mismatch')
        if sum(i.file_size for i in members) != expected['uncompressed_bytes']:
            raise ValueError('immutable manifest uncompressed size mismatch')
        for info in members:
            raw = bundle.read(info)
            lineage = {'archive_name': path.name, 'archive_sha256': observed, 'member_name': info.filename, 'member_sha256': hashlib.sha256(raw).hexdigest(), 'member_size_bytes': len(raw)}
            try:
                report = parse_kap_bulk_export_report(archive_name=path.name, archive_sha256=observed, member_name=info.filename, raw_html=raw)
                row = asdict(report)
                row['published_at'] = report.published_at.isoformat()
                roles = sorted({r.decode('ascii') for r in ROLES.findall(raw)})
                row.update(contract=CONTRACT, profile=PROFILE, risks=RISKS, technical_table_roles=roles, technical_role_namespaces=sorted({r.split('_role_')[0] for r in roles}), technical_schema_signature_sha256=hashlib.sha256(json.dumps(roles, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(), historical_version_enumeration_complete=False, semantic_mapping_status='NOT_IMPLIED_BY_CATALOG', raw_entity_code_interpretation='EXACT_EXPORT_ENTITY_CODE_NO_CURRENT_ALIAS_FALLBACK')
                rows.append(row)
            except Exception as exc:
                errors.append({**lineage, 'status': 'REPORT_METADATA_PARSE_REJECTED', 'error_type': type(exc).__name__, 'error': str(exc)})
    inventory.update(status='IMMUTABLE_ARCHIVE_VERIFIED', parsed_report_count=len(rows), rejected_member_count=len(errors), verified_member_count=len(rows)+len(errors))
    return inventory, rows, errors

def classifications():
    source = 'data/backtest_sources/kap_bulk_financial_source_capture/'
    return {'contract': 'MISSING_CATALOG_DEPENDENCY_CLASSIFICATION_V1', 'limitations': 'Missing original bytes prevent direct inspection and original serializer identification. Semantic role inferred from preserved package summary, README, parser probes, and selector implementation, not invented contents.', 'catalogs': [
        {'name': 'report_catalog.csv.gz', 'classification': 'DERIVED_RECONSTRUCTIBLE_METADATA', 'evidence': [source+'summary.json:report_count=16624 and unique_notification_ids=16624', source+'archive_manifest.json:16624 XLS members', 'src/ingest/kap_bulk_financial_export.py:parse_kap_bulk_export_report'], 'reason': 'Report identity/publication/period/entity metadata comes directly from original XLS headers and archive/member hashes. Exact old serialization remains unknown.'},
        {'name': 'target_report_catalog.csv.gz', 'classification': 'DERIVED_RECONSTRUCTIBLE_METADATA', 'evidence': [source+'summary.json:target_report_count=5489,target_ticker_count=209', source+'README.md:target historical reports subset'], 'reason': 'Target report subset is a derived index of raw report metadata and historical universe; reconstructed pipeline performs explicit exact entity selection and does not assert old subset identity.'},
        {'name': 'inventory_6000.csv.gz', 'classification': 'DERIVED_RECONSTRUCTIBLE_METADATA', 'evidence': [source+'summary.json:inventory source_presence_only=true,uses_pr25_visible_version_selector=true,status_counts'], 'reason': '6000 membership plus cutoff source-selection statuses, not primary financial facts. Historical-version risk remains authoritative blocker.'},
        {'name': 'experimental_inventory_6000.csv.gz', 'classification': 'DERIVED_RECONSTRUCTIBLE_METADATA', 'evidence': [source+'summary.json:experimental_inventory source_presence_only=true,uses_pr25_visible_version_selector=true'], 'reason': 'Explicit-risk source presence selection over archive reports, membership and cutoff schedule; new selection need not reproduce historical counts.'},
        {'name': 'enumeration_receipts.csv.gz', 'classification': 'EVIDENCE_ONLY', 'evidence': [source+'summary.json:historical_version_enumeration_complete=false', source+'README.md:PIT status'], 'reason': 'Enumeration audit evidence cannot be regenerated as proof of superseded historical versions from latest visible archives. Original schema/reproducer unknown; absence remains explicit evidence risk, not absence of all primary financial values.'}
    ], 'original_byte_identity_recovered': False, 'authoritative_gate_relaxed': False}

def build_drift_diagnostics(archive_dir, output_dir):
    """Observed replacements identify possible dependencies, never accepted financial sources."""
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    rows = []
    for expected in sorted(manifest['archives'], key=lambda item:item['filename']):
        path = Path(archive_dir)/expected['filename']
        if not path.exists():
            continue
        observed = sha(path)
        if observed == expected['sha256']:
            continue
        with ZipFile(path) as bundle:
            for name in sorted(bundle.namelist()):
                if not name.endswith('.xls'):
                    continue
                raw = bundle.read(name)
                row = {'archive_name': path.name, 'observed_archive_sha256': observed, 'expected_original_archive_sha256': expected['sha256'], 'member_name':name, 'observed_member_sha256':hashlib.sha256(raw).hexdigest(), 'source_accepted':False, 'original_publication_unknown':True, 'original_member_identity_unknown':True, 'purpose':'POSSIBLE_MISSING_ORIGINAL_SOURCE_DEPENDENCY_DIAGNOSTIC_ONLY'}
                try:
                    report=parse_kap_bulk_export_report(archive_name=path.name,archive_sha256=observed,member_name=name,raw_html=raw)
                    row.update(source_entity_code=report.source_entity_code,report_year=report.report_year,report_period=report.report_period,observed_published_at=report.published_at.isoformat(),observed_notification_id=report.notification_id)
                except Exception as exc:
                    row.update(parse_error_type=type(exc).__name__,parse_error=str(exc))
                rows.append(row)
    output=Path(output_dir)/'drift_dependency_metadata.jsonl.gz'
    output.write_bytes(gzip.compress(b''.join(map(canonical,rows)),compresslevel=9,mtime=0))
    return rows

def build(archive_dir, output_dir, workers=4):
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    tasks = [(str(archive_dir), item) for item in sorted(manifest['archives'], key=lambda x: x['filename'])]
    inventories, reports, errors = [], [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for inv, rows, rejected in pool.map(inspect_archive, tasks):
            inventories.append(inv); reports.extend(rows); errors.extend(rejected)
            print(inv['archive_name'], inv['status'], len(rows), len(rejected), flush=True)
    reports.sort(key=lambda r: (r['source_entity_code'], r['published_at'], r['notification_id'], r['archive_name'], r['member_name']))
    errors.sort(key=lambda r: (r['archive_name'], r['member_name']))
    (output_dir/'reports.jsonl.gz').write_bytes(gzip.compress(b''.join(map(canonical, reports)), compresslevel=9, mtime=0))
    (output_dir/'parse_errors.jsonl.gz').write_bytes(gzip.compress(b''.join(map(canonical, errors)), compresslevel=9, mtime=0))
    (output_dir/'archive_inventory.json').write_bytes(canonical(inventories))
    (output_dir/'catalog_classification.json').write_bytes(canonical(classifications()))
    build_drift_diagnostics(archive_dir, output_dir)
    producer_paths = ['scripts/reconstruct_experimental_kap_catalog.py', 'src/ingest/kap_bulk_financial_export.py']
    receipt = {'contract': CONTRACT, 'profile': PROFILE, 'risk_ids': RISKS, 'generator_version': 1, 'producer_sha256': {p: sha(ROOT/p) for p in producer_paths}, 'immutable_manifest_sha256': sha(MANIFEST), 'archive_status_counts': dict(sorted(Counter(i['status'] for i in inventories).items())), 'report_count': len(reports), 'parse_rejected_count': len(errors), 'source_entity_count': len({r['source_entity_code'] for r in reports}), 'ordering': ['source_entity_code', 'published_at', 'notification_id', 'archive_name', 'member_name'], 'serialization': 'UTF-8 sorted-key compact JSONL, LF, gzip level9 mtime0; pinned runtime gzip identity only', 'financial_values_in_catalog': False, 'raw_member_bytes_reopened_by_downstream': True, 'authoritative_pit_allowed': False, 'original_catalog_identity_claimed': False, 'outputs': {name: sha(output_dir/name) for name in ['archive_inventory.json', 'catalog_classification.json', 'parse_errors.jsonl.gz', 'reports.jsonl.gz', 'drift_dependency_metadata.jsonl.gz']}}
    (output_dir/'receipt.json').write_bytes(canonical(receipt))
    return receipt

def acquire_archives(archive_dir):
    """Public preserved release bytes; existing downloads reused, always rehashed by build."""
    import requests
    from concurrent.futures import ThreadPoolExecutor
    import shutil
    archive_dir = Path(archive_dir); archive_dir.mkdir(parents=True, exist_ok=True)
    assets = json.loads((ROOT/'data/audit/catalog_recovery_v3/release_archive_contents.json').read_text(encoding='utf-8'))['assets']
    def acquire(asset):
        path = archive_dir/asset['name']
        if not path.exists():
            previous = ROOT/'private/p2_action_archives'/asset['name']
            if previous.exists():
                shutil.copyfile(previous,path)
            else:
                with requests.get(asset['url'], stream=True, timeout=180) as response:
                    response.raise_for_status()
                    temporary = path.with_suffix('.zip.part')
                    with temporary.open('wb') as stream:
                        for block in response.iter_content(1024*1024):
                            stream.write(block)
                    temporary.replace(path)
        return {'filename': asset['name'], 'url': asset['url'], 'observed_sha256': sha(path), 'size_bytes': path.stat().st_size}
    with ThreadPoolExecutor(max_workers=4) as pool:
        return sorted(pool.map(acquire,[a for a in assets if a['name'].endswith('.zip')]), key=lambda a:a['filename'])

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--archive-dir', type=Path, default=ROOT/'private/reconstructed_kap_archives'); parser.add_argument('--output-dir', type=Path, required=True); parser.add_argument('--workers', type=int, default=4); parser.add_argument('--acquire', action='store_true')
    args = parser.parse_args()
    if args.acquire:
        acquire_archives(args.archive_dir)
    print(json.dumps(build(args.archive_dir,args.output_dir,args.workers), sort_keys=True))
if __name__ == '__main__':
    main()
