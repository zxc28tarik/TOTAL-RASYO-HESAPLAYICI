"""Publish cell-level dependency evidence without converting missing inputs to scores."""
from collections import Counter
import argparse
import csv
import gzip
from io import StringIO
import json
from pathlib import Path
from scripts.materialize_experimental_financial_facts import ROOT, encoded, sha


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
    details = artifact_dir / 'dependency_details'
    details.mkdir(parents=True, exist_ok=True)
    destination = details / 'dependency_matrix.csv.gz'
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
    import pandas as pd
    from src.analytics.historical_pit_ek9_replay import EK9_LOOKBACK_DAYS
    price_path = ROOT / 'data/backtest_sources/yahoo_resolved/historical_member_prices_resolved_2020-07_2026-08.csv.gz'
    index_path = ROOT / 'data/backtest_sources/m3_source_package/index_closes.csv.gz'
    if sha(price_path) != receipt['source_hashes']['stock_prices'] or sha(index_path) != receipt['source_hashes']['index_prices']:
        raise ValueError('MARKET_DEPENDENCY_SOURCE_HASH_MISMATCH')
    prices = pd.read_csv(price_path, low_memory=False)
    prices = prices[prices.ticker.eq(prices.price_source_ticker)]
    usable = prices.adj_close.fillna(prices.close)
    prices = prices[usable.notna() & usable.gt(0)]
    available = {str(t): set(group.trade_date) for t, group in prices.groupby('ticker')}
    index = pd.read_csv(index_path)
    calendar = sorted(set(index.loc[index.index_code.eq('XU100'), 'trade_date']))
    gaps = []
    for cell in cells:
        if 'STOCK_WINDOW_PRICE_MISSING' not in cell['market_module_lineage']['Ek9']['reasons']:
            continue
        window = [d for d in calendar if d <= cell['knowledge_cutoff_at'][:10]][-(EK9_LOOKBACK_DAYS + 1):]
        missing = sorted(set(window) - available.get(cell['ticker'], set()))
        if not missing:
            raise ValueError('EK9_MISSING_WINDOW_WITHOUT_OBSERVATION_GAP')
        gaps.append(dict(month=cell['month'], ticker=cell['ticker'],
                         window_start=window[0], window_end=window[-1], missing_dates=missing))
    gap_path = details / 'market_observation_gaps.jsonl.gz'
    gap_path.write_bytes(gzip.compress(b''.join(encoded(r) for r in gaps), mtime=0))
    summary['market_gap_artifact_sha256'] = sha(gap_path)
    summary['ek9_cells_with_verified_observation_gaps'] = len(gaps)
    summary['missing_market_date_cell_counts'] = dict(sorted(Counter(d for r in gaps for d in r['missing_dates']).items()))
    (artifact_dir / 'dependency_summary.json').write_bytes(encoded(summary))
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--artifact-dir', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(build(args.artifact_dir), indent=2))
