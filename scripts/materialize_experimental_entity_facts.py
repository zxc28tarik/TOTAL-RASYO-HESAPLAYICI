"""Map archived multi-instrument entities without inventing a stock/share basis.

The original member entity and dimensions stay unchanged. The existing semantic
API requires a single ticker token, so its calculation identity is the first
exact declared member token. Target membership binding is audited separately.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
from scripts import materialize_experimental_financial_facts as mapping

ROOT = mapping.ROOT


def map_entity_report(task):
    archive, row = task
    with ZipFile(archive) as bundle:
        raw = bundle.read(row['member_name'])
    if hashlib.sha256(raw).hexdigest() != row['member_sha256']:
        raise ValueError('ENTITY_MEMBER_HASH_MISMATCH')
    report = mapping.parse_kap_bulk_export_report(archive_name=row['archive_name'],
        archive_sha256=row['archive_sha256'], member_name=row['member_name'], raw_html=raw)
    tokens = report.source_entity_code.split('-')
    if len(tokens) < 2 or any(not t.isalnum() or not 2 <= len(t) <= 12 for t in tokens):
        raise ValueError('ENTITY_EXACT_MEMBER_TOKENS_REQUIRED')
    technical_ticker = tokens[0]
    result = dict(report=asdict(report), profile=mapping.PROFILE, facts=[],
        status='SEMANTIC_MAPPING_UNRESOLVED', numeric_cell_count=0,
        report_mapping_ticker=technical_ticker,
        mapping_ticker_binding=dict(contract='ARCHIVED_ENTITY_TECHNICAL_TOKEN_V1',
            raw_source_entity_code=report.source_entity_code, declared_tokens=tokens,
            technical_mapping_ticker=technical_ticker, source_member_sha256=report.member_sha256,
            original_dimensions_preserved=True, share_or_price_basis_proven=False))
    try:
        cells = mapping.parse_kap_bulk_financial_cells_native(report, raw)
        roles = sorted({c.table_role for c in cells})
        result.update(roles=roles, historical_family=mapping.economic_family(report.company_name, roles),
                      routing_policy='DATED_LEGAL_NAME_SPECIALIST_SCHEMA_V1', numeric_cell_count=len(cells))
        configs = [mapping.build_bulk_exact_semantic_config('NONFIN'),
                   mapping.build_bulk_exact_semantic_config('HOLDING'),
                   mapping.build_bulk_exact_bank_semantic_config(),
                   mapping.build_bulk_exact_insurance_semantic_config(),
                   mapping.build_bulk_exact_financial_semantic_config()]
        mapped = []
        for config in configs:
            codes = {c for rule in config.fields for c in rule.source_codes}
            selected = [c for c in cells if mapping.exact_label_fact_code(c.fact_code, c.label_tr).upper() in codes]
            if selected:
                facts = mapping.bulk_cells_to_financial_facts(report, selected,
                    ticker=technical_ticker, extracted_at=mapping.CAPTURE)
                mapped.extend(mapping.SemanticFactMapper(config).map_facts(facts, mapped_at=mapping.CAPTURE))
        result['facts'] = [asdict(f) for f in sorted(mapped,
            key=lambda f: (f.semantic_profile, f.period_end, f.canonical_field, f.lineage_sha256))]
        result['status'] = 'SEMANTIC_FACTS_MATERIALIZED' if mapped else 'SEMANTIC_MAPPING_UNRESOLVED'
    except (ValueError, TypeError) as exc:
        result['reason'] = type(exc).__name__ + ':' + str(exc)
    return result


def build(raw_dir, output, workers):
    catalog = ROOT / 'data/backtest_sources/reconstructed_experimental_kap_v1/reports.jsonl.gz'
    rows = [json.loads(line) for line in gzip.decompress(catalog.read_bytes()).splitlines()]
    targets = set(mapping._historical_membership(ROOT).ticker)
    rows = sorted((r for r in rows if '-' in r['source_entity_code']
                   and targets.intersection(r['source_entity_code'].split('-'))),
                  key=lambda r: (r['archive_name'], r['member_name']))
    archives = {r['archive_name']: r['archive_sha256'] for r in rows}
    for name, expected in archives.items():
        if mapping.sha(raw_dir / name) != expected:
            raise ValueError('ENTITY_ARCHIVE_HASH_MISMATCH')
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for item in pool.map(map_entity_report,
                ((str(raw_dir / r['archive_name']), r) for r in rows), chunksize=2):
            results.append(item)
            if len(results) % 50 == 0:
                print('entity', len(results), '/', len(rows), flush=True)
    artifact = output / 'semantic_entity_reports.jsonl.gz'
    artifact.write_bytes(gzip.compress(b''.join(mapping.encoded(r) for r in results), mtime=0))
    receipt = dict(contract='EXPERIMENTAL_ARCHIVED_ENTITY_SEMANTIC_SUPPLEMENT_V1',
        report_count=len(results), fact_count=sum(len(r['facts']) for r in results),
        catalog_sha256=mapping.sha(catalog), artifact_sha256=mapping.sha(artifact), archive_hashes=archives,
        source_codes=sorted({r['source_entity_code'] for r in rows}),
        producer_hashes={p: mapping.sha(ROOT / p) for p in (
            'scripts/materialize_experimental_entity_facts.py',
            'scripts/materialize_experimental_financial_facts.py', 'scripts/kap_bulk_financial_native.py')},
        authoritative_claim_allowed=False, share_or_price_basis_proven=False)
    (output / 'semantic_entity_receipt.json').write_bytes(mapping.encoded(receipt))
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--raw-dir', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--workers', type=int, default=4)
    args = p.parse_args()
    print(json.dumps(build(args.raw_dir, args.output_dir, args.workers), indent=2))
