from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

from scripts.experimental_core_module_materializer import build_core_modules


def report(ticker='AAA', multiplier=1, family='HOLDING', period='Q1', technical='HOLDING'):
    end = '2025-03-31' if period == 'Q1' else '2025-06-30'
    published = '2025-05-10T09:00:00+00:00' if period == 'Q1' else '2025-08-10T09:00:00+00:00'
    facts = []
    for field, val in [('REVENUE', 100), ('COST_OF_SALES', 60), ('NET_INCOME', 12),
                       ('TOTAL_ASSETS', 500), ('TOTAL_EQUITY', 200),
                       ('CURRENT_ASSETS', 100), ('CURRENT_LIABILITIES', 30)]:
        flow = field in {'REVENUE', 'COST_OF_SALES', 'NET_INCOME'}
        digest = hashlib.sha256((ticker + field + period).encode()).hexdigest()
        facts.append(dict(source='KAP_BULK_FINANCIAL_EXPORT', disclosure_id='KAP:101', ticker=ticker,
            published_at=published, version_tag=ticker + '_SOURCE', version_sequence=1,
            sector_family=technical, semantic_profile='KAP_BULK_GENERAL_HOLDING_EXACT_LABEL_V1',
            semantic_version=1, canonical_field=field, nature='YTD' if flow else 'INSTANT',
            period_start='2025-01-01' if flow else None, period_end=end, currency='TRY',
            statement_scope='CONSOLIDATED', value=str(val * multiplier if flow else val),
            source_fact_code=field, source_fact_key=digest, source_mapping_profile='RAW',
            source_mapping_version=1, dimensions={'member_sha256': 'a'*64}, lineage_sha256=digest, mapped_at=published))
    return dict(report=dict(source_entity_code=ticker, published_at=published,
        notification_id=101, member_sha256='a'*64, report_year=2025, report_period=period,
        statement_scope='CONSOLIDATED'), facts=facts, historical_family=family)


CUTOFF = datetime(2025, 9, 1, tzinfo=timezone.utc)


def test_own_period_filter_and_future_reports_do_not_restate_prior_quarter():
    q1, q2 = report(), report(period='Q2')
    comparative = deepcopy(q1['facts'][0]); comparative['value'] = '999999'
    comparative['published_at'] = q2['report']['published_at']
    q2['facts'].append(comparative)
    future = deepcopy(q1); future['report']['published_at'] = '2026-01-01T00:00:00+00:00'
    result = build_core_modules([q1, q2, future], CUTOFF, ['AAA'])['per_ticker']['AAA']
    assert result['excluded_comparative_facts'] == 1
    assert result['quarters'][0]['values']['revenue'] == 100
    assert not result['rsc']
    assert 'RSC_HISTORICAL_FAMILY_OR_PEER_DEPTH_MISSING' in result['reasons']


def test_no_route_or_missing_facts_cannot_become_neutral_score():
    ambiguous = report(family=None)
    empty = report('BBB'); empty['facts'] = []
    result = build_core_modules([ambiguous, empty], CUTOFF, ['AAA', 'BBB', 'CCC'])
    assert result['per_ticker']['AAA']['core_ratios']
    assert all(not d['rsc'] and not d['m1'] and not d['ek1'] for d in result['per_ticker'].values())
    assert result['per_ticker']['CCC']['reasons'] == ['NO_VISIBLE_OWN_REPORT']
    assert 'ready' not in json.dumps(result).lower()


def test_wrong_report_member_binding_cannot_enter_core():
    wrong = report()
    for f in wrong['facts']:
        f['dimensions']['member_sha256'] = 'b'*64
    diag = build_core_modules([wrong], CUTOFF, ['AAA'])['per_ticker']['AAA']
    assert not diag['quarters'] and not diag['core_ratios']
    assert 'FACT_REPORT_IDENTITY_MISMATCH' in diag['reasons']


def test_shared_cohort_scores_real_peers_preserving_source_versions_and_determinism():
    reports = [report('T' + str(i), i+1) for i in range(5)]
    tickers = ['T' + str(i) for i in range(5)]
    result = build_core_modules(reports, CUTOFF, tickers)
    assert result == build_core_modules(list(reversed(reports)), CUTOFF, tickers)
    json.dumps(result, allow_nan=False)
    for d in result['per_ticker'].values():
        assert d['rsc'] and d['m1'] and d['ek1'], d['reasons']
        assert d['rsc'][0]['rsc_val_norm'] is None
        assert d['rsc'][0]['version_tag'].startswith('EXPERIMENTAL_ASOF_')
        assert d['quarters'][0]['version_tag'] != d['rsc'][0]['version_tag']
        assert 'PARTIAL_CORE_ONLY_VAL_NOT_MATERIALIZED' in d['reasons']


def test_general_technical_schema_retains_dated_holding_or_gyo_economic_family():
    for family in ('HOLDING', 'GYO'):
        reports = [report('T'+str(i), i+1, family=family, technical='NONFIN') for i in range(5)]
        result = build_core_modules(reports, CUTOFF, ['T'+str(i) for i in range(5)])
        for diag in result['per_ticker'].values():
            assert diag['technical_family'] == 'NONFIN'
            assert diag['historical_family'] == family
            assert diag['rsc'], diag['reasons']
            assert all(q['sector_family'] == 'NONFIN' for q in diag['quarters'])


def test_general_schema_never_automatically_routes_nonfin_or_specialist_bank():
    for family in (None, 'NONFIN', 'BANK', 'INSURANCE', 'FINANCIAL'):
        reports = [report('T'+str(i), family=family, technical='NONFIN') for i in range(5)]
        result = build_core_modules(reports, CUTOFF, ['T'+str(i) for i in range(5)])
        assert all(not d['rsc'] and d['core_ratios'] for d in result['per_ticker'].values())


def test_unused_old_scope_cannot_block_current_derivation_history():
    current = report()
    old = deepcopy(current)
    old['report'].update(report_year=2019, published_at='2019-05-10T09:00:00+00:00', statement_scope='SOLO')
    old['historical_family'] = None
    result = build_core_modules([old,current], CUTOFF, ['AAA'])['per_ticker']['AAA']
    assert result['excluded_older_own_reports'] == 1
    assert result['core_ratios']
    assert 'OWN_REPORT_STATEMENT_SCOPE_CONFLICT' not in result['reasons']


def test_forward_alias_uses_only_calculation_copy_and_preserves_raw_fact_identity():
    old = report('GRTRK')
    original = deepcopy(old)
    result = build_core_modules([old], CUTOFF, ['GRTHO'])['per_ticker']['GRTHO']
    assert result['core_ratios'] and result['alias_history_consumed']
    assert old == original
    assert all(q['ticker']=='GRTHO' for q in result['quarters'])
    for adaptation in result['alias_fact_adaptations']:
        assert adaptation['source_fact']['ticker']=='GRTRK'
        assert adaptation['normalized_ticker']=='GRTHO'
        assert adaptation['official_event_sha256s']
    reverse = build_core_modules([report('GRTHO')], CUTOFF, ['GRTRK'])['per_ticker']['GRTRK']
    assert not reverse['core_ratios']


def test_alias_cannot_bridge_a_changed_economic_family():
    old = report('PEGYO',family='GYO',technical='NONFIN')
    new = report('PEHOL',family='HOLDING',technical='NONFIN',period='Q2')
    result = build_core_modules([old,new], CUTOFF, ['PEHOL'])['per_ticker']['PEHOL']
    assert not result['quarters'] and not result['alias_history_consumed']
    assert 'FINANCIAL_ALIAS_ECONOMIC_FAMILY_CONTINUITY_UNPROVEN' in result['reasons']
