"""Run V24-G's report-only frame auditor without inventing authoritative runs."""
import argparse
from collections import Counter
import gzip
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from scripts.build_historical_m3_source_package import _historical_membership
from scripts.replay_experimental_portfolio import BENCHMARK, WAGES, benchmark_quotes, encode
from src.analytics.historical_backtest_readiness import audit_backtest_readiness_frames
from src.analytics.historical_cutoff_execution_policy import build_authorized_cutoff_execution_schedule

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'data/backtest_sources'
STOCKS = BASE/'yahoo_resolved/historical_member_prices_resolved_2020-07_2026-08.csv.gz'
INDICES = BASE/'m3_source_package/index_closes.csv.gz'
MEMBERSHIP_FILES = ('bist100_snapshot_2026-08-17.csv', 'bist100_periodic_events_2021Q3_2026Q3.json',
                    'bist100_nonperiodic_events_2021-08_2026-08.json',
                    'bist_ticker_code_changes_2021-08_2026-08.csv', 'xu100_signal_dates_yahoo_2021-08_2026-07.csv')


def validate_p4_boundary(cells, members, schedule):
    expected = [(str(r.signal_date), str(r.ticker)) for r in members.sort_values(['signal_date','ticker']).itertuples()]
    if [(r['signal_date'], r['ticker']) for r in cells] != expected:
        raise ValueError('P4_HISTORICAL_MEMBERSHIP_MISMATCH')
    cutoffs = {pd.Timestamp(r.signal_date).date().isoformat(): r.cutoff_at.isoformat() for r in schedule.itertuples()}
    for row in cells:
        if row['knowledge_cutoff_at'] != cutoffs[row['signal_date']]:
            raise ValueError('P4_AUTHORIZED_CUTOFF_MISMATCH')
        if row['status'] not in {'SCORED','EXPLICIT_REJECTION'}:
            raise ValueError('P4_UNKNOWN_CELL_STATUS')
        if row['status'] == 'EXPLICIT_REJECTION' and (row.get('final_score') is not None or row.get('decision') is not None):
            raise ValueError('P4_REJECTED_CELL_HAS_SCORE_OR_DECISION')


def run_audit(cells):
    members = _historical_membership(ROOT).sort_values(['signal_date','ticker'])
    indices = pd.read_csv(INDICES)
    indices['trade_date'] = pd.to_datetime(indices.trade_date)
    calendar = indices.loc[indices.index_code.eq('XU100'),['trade_date']].sort_values('trade_date')
    schedule = build_authorized_cutoff_execution_schedule(
        members[['month','signal_date','index_code']].drop_duplicates(), calendar)
    validate_p4_boundary(cells, members, schedule)
    quotes, benchmark_source = benchmark_quotes()
    benchmark = pd.DataFrame(quotes).assign(index_code='XU100')
    # Membership evidence is an observation at each signal, so the interval
    # adapter covers exactly that day. It asserts nothing about intervening days.
    membership = members[['ticker','signal_date']].rename(columns={'signal_date':'valid_from'})
    membership['valid_from'] = pd.to_datetime(membership.valid_from)
    membership['valid_to'] = membership.valid_from + pd.Timedelta(days=1)
    prices = pd.read_csv(STOCKS, low_memory=False)
    prices = prices.loc[prices.ticker.eq(prices.price_source_ticker)].copy()
    prices['trade_date'] = pd.to_datetime(prices.trade_date)
    dates = set(pd.to_datetime(members.signal_date))
    prices = prices.loc[prices.trade_date.isin(dates)]
    price_counts = prices.groupby(['trade_date','ticker']).size().to_dict()
    missing_execution_cells = [dict(signal_date=str(r.signal_date),ticker=r.ticker,
        reason='EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING') for r in members.itertuples()
        if (pd.Timestamp(r.signal_date),r.ticker) not in price_counts]
    # Source artifact rows remain rejected. Null run_id means no registry
    # identity exists; no success/scope/persistence records are manufactured.
    results = pd.DataFrame([dict(run_id=None, analysis_at=r['knowledge_cutoff_at'], ticker=r['ticker'],
        final_score=r.get('final_score'), decision=r.get('decision'),
        total_rasyo_status=r.get('combiner_status',r['status'])) for r in cells])
    registry = pd.DataFrame(columns=['run_id','analysis_at','overall_status','persistence_status',
                                     'run_scope','company_count','universe_company_count'])
    report = audit_backtest_readiness_frames(index_prices=benchmark, membership=membership,
        prices_daily=prices, wages=pd.read_csv(WAGES), cutoffs=schedule,
        total_rasyo_results=results, run_registry=registry)
    if report.ready:
        raise ValueError('EMPTY_REGISTRY_MUST_NOT_REPORT_READY')
    findings = [{**r, 'signal_date': None if pd.isna(r['signal_date']) else pd.Timestamp(r['signal_date']).date().isoformat()}
                for r in report.findings.to_dict('records')]
    source_paths = [WAGES, STOCKS, INDICES, BENCHMARK/'yahoo_chart.json', *[BASE/name for name in MEMBERSHIP_FILES]]
    return dict(contract='EXPERIMENTAL_V24_G_FRAME_READINESS_AUDIT_V1',
        profile='EXPERIMENTAL_RISK_ACCEPTED_5Y', status='BLOCKED', ready=report.ready,
        production_auditor='src.analytics.historical_backtest_readiness.audit_backtest_readiness_frames',
        expected_months=report.expected_months, checked_months=report.checked_months,
        category_counts=report.category_counts(), findings=findings,
        p4_status_counts=dict(sorted(Counter(r['status'] for r in cells).items())),
        p4_rejection_reasons=dict(sorted(Counter(reason for r in cells for reason in r.get('reasons',[])).items())),
        registry_rows=0, registry_rows_created=0, database_writes_performed=False,
        execution_price_rows_observed=len(prices), benchmark_source=benchmark_source,
        missing_execution_cell_count=len(missing_execution_cells), missing_execution_cells=missing_execution_cells,
        source_hashes={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in source_paths},
        limitations=['V24-G requires authoritative FULL_UNIVERSE registry evidence; none exists for these experimental artifacts.',
                     'One-day membership intervals represent only verified signal-day membership; no tradability history is invented.',
                     'Stock and index execution observations are secondary Yahoo raw quote OPEN/CLOSE fields. Numeric availability is not certification of corporate-action basis or historical source completeness.',
                     'Rejected Total Rasyo rows remain rejected and cannot become authoritative through frame adaptation.'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--p4-cells', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    raw = args.p4_cells.read_bytes()
    parent = json.loads((args.p4_cells.parent/'receipt.json').read_bytes())
    if sha256(raw).hexdigest() != parent['outputs'][args.p4_cells.name]:
        raise ValueError('P4_ARTIFACT_RECEIPT_HASH_MISMATCH')
    cells = [json.loads(line) for line in gzip.decompress(raw).splitlines()]
    first, second = encode(run_audit(cells)), encode(run_audit(cells))
    assert first == second
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/'readiness_report.json').write_bytes(first)
    (args.output_dir/'build_receipt.json').write_bytes(encode(dict(
        p4_sha256=sha256(raw).hexdigest(), output_sha256=sha256(first).hexdigest(),
        generator_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        production_auditor_sha256=sha256((ROOT/'src/analytics/historical_backtest_readiness.py').read_bytes()).hexdigest(),
        independent_rebuild_bytes_identical=True, authoritative_registry_created=False)))
    print(sha256(first).hexdigest())


if __name__ == '__main__':
    main()
