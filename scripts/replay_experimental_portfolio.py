"""Run the existing portfolio engine on a wholly rejected P4 cohort.

This is a cash-conservation diagnostic, not evidence that Total Rasyo investment
performance was measured. A scored cohort requires the real execution/action
materializer and is refused by this deliberately narrow diagnostic entry point.
"""
import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
import gzip
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.analytics.monthly_total_rasyo_portfolio import MonthlyTotalRasyoSimulator, benchmark_dca

ROOT = Path(__file__).resolve().parents[1]
WAGES = ROOT / 'data/backtest_sources/minimum_wage_csgb_2021_2026.csv'
BENCHMARK = ROOT / 'data/backtest_sources/experimental_xu100_ohlc_v1'


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def benchmark_quotes(source_dir=BENCHMARK):
    raw = (source_dir / 'yahoo_chart.json').read_bytes()
    receipt = json.loads((source_dir / 'source_receipt.json').read_bytes())
    if sha256(raw).hexdigest() != receipt['sha256']:
        raise ValueError('BENCHMARK_SOURCE_HASH_MISMATCH')
    result = json.loads(raw)['chart']['result'][0]
    if result['meta']['symbol'] != 'XU100.IS' or result['meta']['currency'] != 'TRY':
        raise ValueError('BENCHMARK_IDENTITY_MISMATCH')
    quote = result['indicators']['quote'][0]
    rows = []
    for stamp, opening, close in zip(result['timestamp'], quote['open'], quote['close'], strict=True):
        if opening is None or close is None:
            continue
        day = datetime.fromtimestamp(stamp, timezone.utc).astimezone(ZoneInfo('Europe/Istanbul')).date().isoformat()
        rows.append(dict(trade_date=day, open=opening, close=close))
    return rows, receipt


def diagnostic(cells, wage_path=WAGES, benchmark_source=BENCHMARK):
    if not cells:
        raise ValueError('P4_INPUT_EMPTY')
    keys = [(r['signal_date'], r['ticker']) for r in cells]
    if len(keys) != len(set(keys)):
        raise ValueError('P4_DUPLICATE_MEMBERSHIP_KEY')
    if any(r['status'] != 'EXPLICIT_REJECTION' for r in cells):
        raise ValueError('SCORED_COHORT_REQUIRES_VERIFIED_EXECUTION_AND_ACTION_INPUTS')
    if any(r.get('final_score') is not None or r.get('decision') is not None for r in cells):
        raise ValueError('REJECTED_CELL_HAS_SCORE_OR_DECISION')
    grouped = {}
    for r in cells:
        signal = datetime.fromisoformat(r['signal_date']).date()
        cutoff = datetime.fromisoformat(r['knowledge_cutoff_at'])
        if cutoff.tzinfo is None or cutoff.date() >= signal:
            raise ValueError('P4_CUTOFF_NOT_PREVIOUS_SESSION')
        month = signal.strftime('%Y-%m')
        if r.get('month', month) != month:
            raise ValueError('P4_MONTH_SIGNAL_MISMATCH')
        grouped.setdefault(month, []).append(r)
    expected = [str(p) for p in pd.period_range('2021-08', '2026-07', freq='M')]
    if sorted(grouped) != expected or any(len(g) != 100 for g in grouped.values()):
        raise ValueError('P4_EXPECTED_60_MONTHS_100_MEMBERS')
    wages = list(csv.DictReader(wage_path.open(encoding='utf-8')))
    contributions = []
    cutoffs = {}
    for month, group in sorted(grouped.items()):
        dates = {r['signal_date'] for r in group}
        stamps = {r['knowledge_cutoff_at'] for r in group}
        if len(dates) != 1 or len(stamps) != 1:
            raise ValueError('P4_MONTH_INCONSISTENT_TIMESTAMPS')
        day = next(iter(dates))
        matches = [w for w in wages if w['valid_from'] <= day < w['valid_to']]
        if len(matches) != 1:
            raise ValueError('MINIMUM_WAGE_INTERVAL_NOT_UNIQUE')
        contributions.append(dict(signal_date=day, contribution=float(Decimal(matches[0]['net_min_wage']) * 2)))
        cutoffs[day] = next(iter(stamps))
    contribution_frame = pd.DataFrame(contributions)
    simulator = MonthlyTotalRasyoSimulator()
    trades, monthly = simulator.run(
        pd.DataFrame(columns=['signal_date', 'ticker', 'final_score', 'decision']),
        pd.DataFrame(columns=['trade_date', 'ticker', 'open', 'close']), contribution_frame)
    if not trades.empty or simulator.positions:
        raise ValueError('REJECTED_COHORT_CREATED_TRADES_OR_HOLDINGS')
    quotes, source = benchmark_quotes(benchmark_source)
    quote_map = {r['trade_date']: r for r in quotes}
    for row in contributions:
        day = row['signal_date']
        month_days = [q['trade_date'] for q in quotes if q['trade_date'][:7] == day[:7]]
        if not month_days or day != min(month_days):
            raise ValueError('SIGNAL_NOT_FIRST_BENCHMARK_SESSION')
    benchmark = benchmark_dca(contribution_frame, pd.DataFrame(quotes))
    ledger = []
    expected_cash = Decimal(0)
    independently_accumulated_units = Decimal(0)
    for row, bench in zip(monthly.itertuples(index=False), benchmark.itertuples(index=False), strict=True):
        day = row.date.date().isoformat()
        amount = Decimal(str(row.contribution)); starting = expected_cash; expected_cash += amount
        independently_accumulated_units += amount / Decimal(str(quote_map[day]['open']))
        independently_valued_benchmark = independently_accumulated_units * Decimal(str(quote_map[day]['close']))
        if abs(Decimal(str(bench.benchmark_value)) - independently_valued_benchmark) > Decimal('0.000001'):
            raise ValueError('BENCHMARK_INDEPENDENT_RECONCILIATION_FAILURE')
        if abs(Decimal(str(row.nav)) - expected_cash) > Decimal('0.000001') or row.cash < 0:
            raise ValueError('CASH_CONSERVATION_FAILURE')
        ledger.append(dict(month=day[:7], signal_date=day, knowledge_cutoff_at=cutoffs[day],
                           execution_at=day+'T10:00:00+03:00', contribution=float(amount),
                           cumulative_contribution=float(expected_cash), starting_cash=float(starting),
                           starting_holdings=[], ending_holdings=[], buys=[], sells=[],
                           valid_score_count=0, rejected_score_count=100, ending_cash=float(expected_cash),
                           nav=float(expected_cash), monthly_return_excluding_contribution=0.0,
                           cumulative_return_on_contributions=0.0,
                           xu100_execution_open=quote_map[day]['open'], xu100_valuation_close=quote_map[day]['close'],
                           xu100_units=float(bench.units), xu100_comparable_nav=float(bench.benchmark_value),
                           benchmark_mark_convention='SIGNAL_DAY_CLOSE', benchmark_source_sha256=source['sha256']))
    total = float(expected_cash); end_benchmark = ledger[-1]['xu100_comparable_nav']
    return dict(contract='EXPERIMENTAL_REJECTED_COHORT_CASH_DIAGNOSTIC_V1',
                profile='EXPERIMENTAL_RISK_ACCEPTED_5Y', status='DIAGNOSTIC_ONLY_NO_VALID_TOTAL_RASYO_SCORES',
                p5_strategy_performance_completed=False, p4_valid_score_count=0, p4_rejected_score_count=len(cells),
                production_portfolio_engine='MonthlyTotalRasyoSimulator',
                allocation_contract='Existing engine: max6; AL only; equal cash across AL targets; integer stock shares; fraction stays cash.',
                wage_source_sha256=sha256(wage_path.read_bytes()).hexdigest(), benchmark_source=source,
                benchmark_assumptions='Fractional index units bought at raw signal-day OPEN, marked at same signal-day CLOSE; secondary Yahoo source, zero fees and no interest. Final mark is July signal day, not July month-end.',
                economics=dict(total_contributions=total, ending_nav=total, absolute_profit_loss=0,
                               return_on_contributions_pct=0, xu100_comparable_ending_value=end_benchmark,
                               xu100_return_on_contributions_pct=(end_benchmark / total - 1) * 100,
                               portfolio_minus_xu100_value=total-end_benchmark,
                               flow_adjusted_max_drawdown_pct=0, best_month_return_pct=0, worst_month_return_pct=0,
                               buys=0, sells=0, turnover=0, cash_allocation_pct=100,
                               cash_drag_vs_benchmark_value=end_benchmark-total,
                               months_under_six_positions=60, months_without_valid_al=60),
                ledger=ledger, trade_ledger=[])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--p4-cells', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    raw = args.p4_cells.read_bytes()
    decoded = gzip.decompress(raw) if args.p4_cells.suffix == '.gz' else raw
    cells = [json.loads(line) for line in decoded.splitlines() if line]
    # Bind a real run to the historical cohort and the authorized exchange
    # schedule. The pure diagnostic above also supports focused test fixtures.
    from scripts.build_historical_m3_source_package import _historical_membership
    from src.analytics.historical_cutoff_execution_policy import build_authorized_cutoff_execution_schedule
    members = _historical_membership(ROOT).sort_values(['signal_date', 'ticker'])
    expected = [(str(r.signal_date), str(r.ticker)) for r in members.itertuples()]
    if [(r['signal_date'], r['ticker']) for r in cells] != expected:
        raise ValueError('P5_HISTORICAL_MEMBERSHIP_MISMATCH')
    index_path = ROOT / 'data/backtest_sources/m3_source_package/index_closes.csv.gz'
    indices = pd.read_csv(index_path)
    indices['trade_date'] = pd.to_datetime(indices.trade_date)
    calendar = indices.loc[indices.index_code.eq('XU100'), ['trade_date']].sort_values('trade_date')
    schedule = build_authorized_cutoff_execution_schedule(
        members[['month', 'signal_date', 'index_code']].drop_duplicates(), calendar)
    cutoffs = {pd.Timestamp(r.signal_date).date().isoformat(): r.cutoff_at.isoformat()
               for r in schedule.itertuples()}
    if any(r['knowledge_cutoff_at'] != cutoffs[r['signal_date']] for r in cells):
        raise ValueError('P5_AUTHORIZED_CUTOFF_MISMATCH')
    one, two = encode(diagnostic(cells)), encode(diagnostic(cells))
    assert one == two
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'portfolio_diagnostic.json').write_bytes(one)
    (args.output_dir / 'build_receipt.json').write_bytes(encode(dict(
        p4_input_sha256=sha256(raw).hexdigest(), output_sha256=sha256(one).hexdigest(),
        generator_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        portfolio_engine_sha256=sha256((ROOT / 'src/analytics/monthly_total_rasyo_portfolio.py').read_bytes()).hexdigest(),
        calendar_source_sha256=sha256(index_path.read_bytes()).hexdigest(),
        historical_membership_verified=True, authorized_cutoffs_verified=True,
        independent_rebuild_bytes_identical=True, strategy_performance_completed=False)))
    print(sha256(one).hexdigest())


if __name__ == '__main__':
    main()
