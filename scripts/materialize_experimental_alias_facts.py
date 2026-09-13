"""Materialize primary reports of officially linked predecessor codes separately."""
from __future__ import annotations
import argparse
from datetime import datetime
import gzip
import json
from pathlib import Path
from scripts.materialize_experimental_financial_facts import ROOT, encoded, sha, map_report
from scripts.experimental_financial_ticker_lineage import candidate_source_tickers
from scripts.build_historical_m3_source_package import _historical_membership


def build(raw_dir, output):
    catalog = ROOT / 'data/backtest_sources/reconstructed_experimental_kap_v1/reports.jsonl.gz'
    rows = [json.loads(line) for line in gzip.decompress(catalog.read_bytes()).splitlines()]
    members = _historical_membership(ROOT)
    exact = set(members.ticker)
    # Inclusion is only inventory expansion. Each actual cell separately checks
    # the effective edge at its own knowledge cutoff before selecting a report.
    last = datetime.fromisoformat('2026-07-01T00:00:00+03:00')
    aliases = set().union(*(set(candidate_source_tickers(t, last)) for t in exact)) - exact
    rows = sorted((r for r in rows if r['source_entity_code'] in aliases),
                  key=lambda r: (r['archive_name'], r['member_name']))
    archives = {r['archive_name']: r['archive_sha256'] for r in rows}
    for name, expected in archives.items():
        if sha(raw_dir / name) != expected:
            raise ValueError('ALIAS_ARCHIVE_HASH_MISMATCH')
    results = [map_report((str(raw_dir / r['archive_name']), r)) for r in rows]
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / 'semantic_alias_reports.jsonl.gz'
    artifact.write_bytes(gzip.compress(b''.join(encoded(r) for r in results), mtime=0))
    receipt = dict(contract='EXPERIMENTAL_PREDECESSOR_SEMANTIC_SUPPLEMENT_V1',
                   report_count=len(results), fact_count=sum(len(r['facts']) for r in results),
                   source_codes=sorted({r['source_entity_code'] for r in rows}),
                   catalog_sha256=sha(catalog), artifact_sha256=sha(artifact),
                   archive_hashes=archives,
                   producer_hashes={p: sha(ROOT / p) for p in (
                       'scripts/materialize_experimental_alias_facts.py',
                       'scripts/materialize_experimental_financial_facts.py',
                       'scripts/experimental_financial_ticker_lineage.py',
                       'scripts/kap_bulk_financial_native.py')},
                   authoritative_claim_allowed=False)
    (output / 'semantic_alias_receipt.json').write_bytes(encoded(receipt))
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--raw-dir', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(build(a.raw_dir, a.output_dir), indent=2))
