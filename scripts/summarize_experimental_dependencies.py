"""Publish cell-level dependency evidence without converting missing inputs to scores."""
from collections import Counter
import argparse
import csv
import gzip
from io import StringIO
import json
from pathlib import Path
from scripts.materialize_experimental_financial_facts import encoded, sha


def build(artifact_dir):
    receipt = json.loads((artifact_dir / 'receipt.json').read_bytes())
    path = artifact_dir / 'p3_cells.jsonl.gz'
    if sha(path) != receipt['outputs'][path.name]:
        raise ValueError('P3_DEPENDENCY_SOURCE_HASH_MISMATCH')
    cells = [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]
    fields = ['month', 'ticker', 'cutoff', 'source_ticker', 'source_report',
              'own_period_fact_count', 'historical_family', 'price_observed',
              'raw_close_verified', 'dated_nominal_shares_verified', 'm2_source_gate',
              'm2_exact_error', 'M3', 'Ek4', 'Ek9', 'partial_core_status', 'reasons']
    stream = StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    for cell in cells:
        gate = cell['m2_source_gate']
        report = cell['selected_report'] or {}
        writer.writerow(dict(month=cell['month'], ticker=cell['ticker'],
            cutoff=cell['knowledge_cutoff_at'], source_ticker=report.get('source_entity_code'),
            source_report=report.get('notification_id'),
            own_period_fact_count=len(cell['own_period_semantic_facts']),
            historical_family=cell['historical_family'], price_observed=cell['price'] is not None,
            raw_close_verified=gate['raw_close_basis_verified'],
            dated_nominal_shares_verified=gate['dated_nominal_shares_verified'],
            m2_source_gate=gate['production_gate'], m2_exact_error=gate['exact_error'],
            **{k: cell['module_values'][k] for k in ('M3', 'Ek4', 'Ek9')},
            partial_core_status=cell['partial_core_diagnostic_status'],
            reasons=';'.join(cell['reasons'])))
    destination = artifact_dir / 'dependency_matrix.csv.gz'
    destination.write_bytes(gzip.compress(stream.getvalue().encode(), mtime=0))
    core_path = artifact_dir / 'core_diagnostics.jsonl.gz'
    if sha(core_path) != receipt['outputs'][core_path.name]:
        raise ValueError('CORE_DEPENDENCY_SOURCE_HASH_MISMATCH')
    core = [json.loads(line) for line in gzip.decompress(core_path.read_bytes()).splitlines()]
    diagnostics = [d for month in core for d in month['per_ticker'].values()]
    summary = dict(contract='EXPERIMENTAL_CELL_DEPENDENCY_MATRIX_V1',
        p3_sha256=sha(path), matrix_sha256=sha(destination), total_cells=len(cells),
        historical_family_counts=dict(sorted(Counter(c['historical_family'] or 'UNRESOLVED' for c in cells).items())),
        m2_gate_counts=dict(sorted(Counter(c['m2_source_gate']['production_gate'] or 'UNRESOLVED_FAMILY' for c in cells).items())),
        raw_close_verified_cells=sum(c['m2_source_gate']['raw_close_basis_verified'] for c in cells),
        dated_nominal_shares_verified_cells=sum(c['m2_source_gate']['dated_nominal_shares_verified'] for c in cells),
        partial_core_cells_with_quarters=sum(bool(d['quarters']) for d in diagnostics),
        partial_core_cells_with_present_ratios=sum(any(not r['is_na'] for r in d['core_ratios']) for d in diagnostics),
        partial_core_cells_with_rsc=sum(bool(d['rsc']) for d in diagnostics),
        partial_core_cells_with_m1_rows=sum(bool(d['m1']) for d in diagnostics),
        partial_core_cells_with_ek1_rows=sum(bool(d['ek1']) for d in diagnostics),
        global_original_catalog_rejection_count=sum('ORIGINAL_FINANCIAL_CATALOG_BYTES_MISSING' in c['reasons'] for c in cells),
        full_total_scores=receipt['p4_valid_total_scores'], authoritative_claim_allowed=False)
    (artifact_dir / 'dependency_summary.json').write_bytes(encoded(summary))
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--artifact-dir', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(build(args.artifact_dir), indent=2))
