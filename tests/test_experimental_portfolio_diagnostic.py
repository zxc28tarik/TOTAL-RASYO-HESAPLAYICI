from copy import deepcopy
from datetime import date, timedelta
import shutil

import pytest

from scripts.replay_experimental_portfolio import BENCHMARK, benchmark_quotes, diagnostic, encode


@pytest.fixture(scope='module')
def rejected_cohort():
    quotes, _ = benchmark_quotes()
    months = {}
    for quote in quotes:
        day = quote['trade_date']
        if '2021-08' <= day[:7] <= '2026-07':
            months.setdefault(day[:7], day)
    # Synthetic rows test the diagnostic contract only, never production evidence.
    return [dict(month=month, signal_date=day, ticker=f'TEST{i:03}', status='EXPLICIT_REJECTION',
                 final_score=None, decision=None,
                 knowledge_cutoff_at=(date.fromisoformat(day)-timedelta(days=1)).isoformat()+'T18:10:00+03:00')
            for month, day in sorted(months.items()) for i in range(100)]


def test_real_wages_and_quotes_cash_conservation(rejected_cohort):
    result = diagnostic(rejected_cohort)
    assert encode(result) == encode(diagnostic(rejected_cohort))
    assert len(result['ledger']) == 60
    assert result['trade_ledger'] == []
    assert not result['p5_strategy_performance_completed']
    assert result['economics']['ending_nav'] == result['economics']['total_contributions']
    assert result['economics']['xu100_comparable_ending_value'] > 0
    assert all(row['ending_cash'] >= 0 for row in result['ledger'])


@pytest.mark.parametrize('mutation,reason', [
    ('score', 'REJECTED_CELL_HAS_SCORE'), ('decision', 'REJECTED_CELL_HAS_SCORE'),
    ('future_cutoff', 'P4_CUTOFF_NOT_PREVIOUS_SESSION'), ('duplicate', 'P4_DUPLICATE'),
    ('scored', 'SCORED_COHORT_REQUIRES'), ('missing', 'P4_EXPECTED_60_MONTHS')])
def test_input_corruption_never_creates_diagnostic_pass(rejected_cohort, mutation, reason):
    cells = deepcopy(rejected_cohort)
    if mutation == 'score': cells[0]['final_score'] = 0.9
    if mutation == 'decision': cells[0]['decision'] = 'AL'
    if mutation == 'future_cutoff': cells[0]['knowledge_cutoff_at'] = cells[0]['signal_date']+'T18:10:00+03:00'
    if mutation == 'duplicate': cells.append(deepcopy(cells[0]))
    if mutation == 'scored': cells[0]['status'] = 'SCORED'
    if mutation == 'missing': cells.pop()
    with pytest.raises(ValueError, match=reason): diagnostic(cells)


def test_changed_open_rawbytes_fail_hash_before_use(tmp_path):
    shutil.copyfile(BENCHMARK/'source_receipt.json', tmp_path/'source_receipt.json')
    (tmp_path/'yahoo_chart.json').write_bytes((BENCHMARK/'yahoo_chart.json').read_bytes()+b' ')
    with pytest.raises(ValueError, match='BENCHMARK_SOURCE_HASH_MISMATCH'):
        benchmark_quotes(tmp_path)
