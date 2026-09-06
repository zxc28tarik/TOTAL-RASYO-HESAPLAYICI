"""Verify two complete primary-byte semantic builds and bind their producers.

This checks reproducibility and lineage, not historical version completeness.
The native parser's numerical equivalence evidence remains a separate sampled
differential audit against the unchanged production parser.
"""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

from scripts.materialize_experimental_financial_facts import ROOT, encoded, sha


def audit(first: Path, second: Path):
    files = ('semantic_reports.jsonl.gz', 'semantic_receipt.json',
             'semantic_alias_reports.jsonl.gz', 'semantic_alias_receipt.json')
    comparisons = {}
    for name in files:
        left, right = sha(first / name), sha(second / name)
        if left != right:
            raise ValueError('SEMANTIC_REBUILD_BYTES_DIFFER:' + name)
        comparisons[name] = left
    receipt = json.loads((first / 'semantic_receipt.json').read_bytes())
    if receipt['artifact_sha256'] != comparisons[files[0]]:
        raise ValueError('SEMANTIC_ARTIFACT_HASH_MISMATCH')
    generator = 'scripts/materialize_experimental_financial_facts.py'
    if receipt['generator_sha256'] != sha(ROOT / generator):
        raise ValueError('SEMANTIC_GENERATOR_CHANGED_SINCE_BUILD')
    producers = [generator, 'scripts/kap_bulk_financial_native.py',
                 'scripts/kap_bulk_financial_fast.py', 'requirements-experimental.txt',
                 'src/ingest/kap_bulk_financial_export.py',
                 'src/ingest/kap_bulk_exact_semantic_mapping.py',
                 'src/ingest/kap_bulk_bank_exact_semantic_mapping.py',
                 'src/ingest/kap_bulk_insurance_finance_exact_semantic_mapping.py',
                 'src/ingest/kap_bulk_semantic_adapter.py',
                 'src/ingest/api/semantic_facts.py']
    rows = [json.loads(line) for line in gzip.decompress((first / files[0]).read_bytes()).splitlines()]
    reports = Counter(r['status'] for r in rows)
    facts = Counter(f['semantic_profile'] for r in rows for f in r['facts'])
    if len(rows) != receipt['report_count'] or sum(facts.values()) != receipt['fact_count']:
        raise ValueError('SEMANTIC_RECEIPT_COUNT_MISMATCH')
    return dict(contract='EXPERIMENTAL_SEMANTIC_TWO_PRIMARY_READS_AUDIT_V1',
                result='PASS', independent_primary_byte_rebuilds=2,
                byte_identical_outputs=comparisons, report_count=len(rows),
                fact_count=sum(facts.values()), report_status_counts=dict(sorted(reports.items())),
                fact_profile_counts=dict(sorted(facts.items())),
                dated_family_report_counts=dict(sorted(Counter(r.get('historical_family') or 'UNRESOLVED' for r in rows).items())),
                producer_hashes={p: sha(ROOT / p) for p in producers},
                native_differential_evidence_sha256=sha(ROOT / 'data/backtest_sources/reconstructed_experimental_kap_v1/native_parser_differential.json'),
                original_catalog_recovered=False, authoritative_claim_allowed=False,
                limitations=['The earlier fast_parser_sha256 receipt field binds a helper; actual native parser and mapping producers are explicitly bound here.',
                             'Two identical rebuilds prove determinism, not independent semantic correctness or historical version completeness.'])


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--first', type=Path, required=True)
    p.add_argument('--second', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = audit(args.first, args.second)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded(result))
    print(json.dumps(result, indent=2))
