"""Partial CORE diagnostics from reconstructed, visible own-period KAP facts.

No database, price wing, family fallback, or Total Rasyo readiness assertion.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime
from decimal import Decimal
import calendar
import math
from pathlib import Path

import pandas as pd

from src.ingest.api.semantic_facts import SemanticFinancialFact
from src.ingest.company_fact_materializer import derive_company_quarters, build_quarter_ends
from src.ingest.kap_bulk_exact_semantic_mapping import build_bulk_exact_company_derivation_config
from src.analytics.company_ratio_pipeline import compute_company_core_ratios_from_frame
from src.analytics.historical_pit_ratio_replay import HistoricalPitRatioReplayResult
from src.analytics.historical_pit_rsc_replay import run_historical_pit_rsc_replay
from src.analytics.historical_pit_m1_replay import run_historical_pit_m1_replay
from src.analytics.historical_pit_ek1_replay import run_historical_pit_ek1_replay
from src.analytics.rsc_scoring import load_sector_config

ROOT = Path(__file__).resolve().parents[1]
# Economic family comes exclusively from dated report evidence. Technical
# general statements can describe holdings/GYO; changing the RSC routing does
# not rename their semantic facts or reinterpret their exact mapped fields.
ALLOWED_ECONOMIC_TECHNICAL_PAIRS = frozenset({
    ('HOLDING', 'HOLDING'), ('HOLDING', 'NONFIN'), ('GYO', 'NONFIN'),
})


def _time(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return parsed


def _day(value):
    return value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value))


def _json(value):
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, 'item'):
        return _json(value.item())
    return value


def _fact(raw):
    data = dict(raw)
    for name in ('published_at', 'mapped_at'):
        data[name] = _time(data[name])
    for name in ('period_end', 'period_start'):
        data[name] = _day(data[name]) if data[name] is not None else None
    data['value'] = Decimal(str(data['value']))
    return SemanticFinancialFact(**data)


def build_core_modules(reports, analysis_at, tickers):
    """Return deterministic JSON-safe diagnostics for every requested ticker.

    Shared ratio version tags identify the *as-of scoring cohort*, not a report
    version. Original version tags and all source lineage remain in quarters.
    Missing ratios and cohorts with fewer than five same-family observations
    per ratio are excluded before calling unchanged production scoring math.
    """
    analysis = _time(analysis_at)
    from scripts.experimental_financial_ticker_lineage import lineage_receipt
    from scripts.experimental_financial_entity_binding import entity_tokens, financial_entity_binding
    wanted = tuple(sorted(set(str(t).strip().upper() for t in tickers)))
    selected = {t: [] for t in wanted}
    identity = {t: lineage_receipt(t, analysis) for t in wanted}
    targets_by_source = {}
    for target, receipt in identity.items():
        for source in receipt['candidate_source_tickers']:
            targets_by_source.setdefault(source, []).append(target)
    for item in reports:
        report = item['report']
        ticker = report['source_entity_code']
        if _time(report['published_at']) <= analysis:
            targets = {target for token in entity_tokens(ticker) for target in targets_by_source.get(token, [])}
            for target in sorted(targets):
                selected[target].append(item)
    result = {'contract': 'EXPERIMENTAL_PARTIAL_CORE_MODULES_V1',
              'diagnostic_only': True, 'analysis_at': analysis.isoformat(),
              'allowed_economic_technical_pairs': sorted(ALLOWED_ECONOMIC_TECHNICAL_PAIRS),
              'cohort_policy': 'SHARED_ASOF_TAG_PRESERVES_SOURCE_VERSIONS_IN_QUARTERS_MIN_5_SAME_FAMILY_PER_RATIO',
              'per_ticker': {}}
    ratio_parts = []
    routing = {}
    for ticker in wanted:
        diag = {'status': 'NO_CORE_DIAGNOSTICS', 'reasons': [], 'quarters': [],
                'core_ratios': [], 'rsc': [], 'm1': [], 'ek1': [],
                'excluded_comparative_facts': 0, 'excluded_future_facts': 0}
        result['per_ticker'][ticker] = diag
        diag['financial_ticker_lineage'] = identity[ticker]
        diag['alias_fact_adaptations'] = []
        diag['alias_history_consumed'] = False
        diag['financial_entity_bindings'] = []
        diag['excluded_unproven_share_class_facts'] = 0
        visible = sorted(selected[ticker], key=lambda x: (
            _time(x['report']['published_at']), int(x['report']['notification_id']),
            x['report']['member_sha256']))
        if not visible:
            diag['reasons'].append('NO_VISIBLE_OWN_REPORT')
            continue
        # A newer ambiguous family cannot inherit an earlier confident one.
        newest = max(visible, key=lambda x: (int(x['report']['report_year']),
            int(x['report']['report_period'][1]), _time(x['report']['published_at']),
            int(x['report']['notification_id'])))
        family = newest.get('historical_family')
        diag['historical_family'] = family
        latest_period = {}
        for item in visible:
            report = item['report']
            latest_period[(int(report['report_year']), report['report_period'])] = item
        visible = list(latest_period.values())
        anchor_report = newest['report']
        anchor_month = int(anchor_report['report_period'][1]) * 3
        anchor_year = int(anchor_report['report_year'])
        anchor_end = date(anchor_year, anchor_month, calendar.monthrange(anchor_year, anchor_month)[1])
        # Match the existing derivation horizon before checking historical
        # scope/family consistency: an unused older regime is not an input.
        history = set(build_quarter_ends(anchor_end,
            build_bulk_exact_company_derivation_config('NONFIN').history_periods))
        bounded = []
        for item in visible:
            r = item['report']; month = int(r['report_period'][1]) * 3; year = int(r['report_year'])
            if date(year, month, calendar.monthrange(year, month)[1]) in history:
                bounded.append(item)
        diag['excluded_older_own_reports'] = len(visible) - len(bounded)
        visible = bounded
        aliases = [item for item in visible if ticker not in entity_tokens(item['report']['source_entity_code'])]
        if aliases and (len({item.get('historical_family') for item in visible}) != 1 or family is None):
            diag['reasons'].append('FINANCIAL_ALIAS_ECONOMIC_FAMILY_CONTINUITY_UNPROVEN')
            continue
        if len({item['report'].get('statement_scope') for item in visible}) > 1:
            diag['reasons'].append('OWN_REPORT_STATEMENT_SCOPE_CONFLICT')
            continue
        candidates = []
        for item in visible:
            report = item['report']
            binding = financial_entity_binding(ticker, analysis, report)
            diag['financial_entity_bindings'].append(binding)
            if binding['composite_entity']:
                expected_binding = {'contract':'ARCHIVED_ENTITY_TECHNICAL_TOKEN_V1',
                    'raw_source_entity_code':report['source_entity_code'],
                    'declared_tokens':list(entity_tokens(report['source_entity_code'])),
                    'technical_mapping_ticker':entity_tokens(report['source_entity_code'])[0],
                    'source_member_sha256':report['member_sha256'],
                    'original_dimensions_preserved':True,'share_or_price_basis_proven':False}
                if item.get('mapping_ticker_binding') != expected_binding:
                    diag['reasons'].append('COMPOSITE_TECHNICAL_MAPPING_BINDING_MISMATCH')
                    continue
            month = {'Q1': 3, 'Q2': 6, 'Q3': 9, 'Q4': 12}[report['report_period']]
            year = int(report['report_year'])
            own_end = date(year, month, calendar.monthrange(year, month)[1])
            if own_end > analysis.date():
                diag['reasons'].append('FUTURE_OWN_REPORT_PERIOD')
                continue
            for raw in item.get('facts', []):
                if _day(raw['period_end']) != own_end:
                    diag['excluded_comparative_facts'] += 1
                    continue
                if _time(raw['published_at']) > analysis:
                    diag['excluded_future_facts'] += 1
                    continue
                declared_mapping_ticker = item.get('report_mapping_ticker', report['source_entity_code'])
                if (declared_mapping_ticker not in entity_tokens(report['source_entity_code'])
                    or raw['ticker'] != declared_mapping_ticker or _time(raw['published_at']) != _time(report['published_at'])
                    or raw['disclosure_id'] != 'KAP:' + str(report['notification_id'])
                    or raw.get('statement_scope') != report.get('statement_scope')
                    or raw.get('dimensions', {}).get('member_sha256') != report['member_sha256']):
                    diag['reasons'].append('FACT_REPORT_IDENTITY_MISMATCH')
                    continue
                if binding['composite_entity'] and raw['canonical_field'] in {'ISSUED_CAPITAL','SHARES_OUT','SHARES_DILUTED'}:
                    diag['excluded_unproven_share_class_facts'] += 1
                    continue
                if raw['sector_family'] in {'NONFIN', 'HOLDING'}:
                    candidates.append((item, raw))
        technical = {raw['sector_family'] for _, raw in candidates}
        if len(technical) != 1:
            diag['reasons'].append('TECHNICAL_CORE_FAMILY_UNSUPPORTED_OR_CONFLICTING')
            continue
        tech = next(iter(technical))
        diag['technical_family'] = tech
        try:
            facts = []
            for item, raw in candidates:
                fact = _fact(raw)
                if fact.ticker != ticker:
                    # Only an ephemeral calculation copy gets a target ticker.
                    # Preserve source fact, dimensions, keys and hash untouched.
                    diag['alias_fact_adaptations'].append({'source_fact': raw,
                        'normalized_ticker': ticker, 'source_ticker': fact.ticker,
                        'official_event_sha256s': [e['event_sha256'] for e in identity[ticker]['events']]})
                    fact = replace(fact, ticker=ticker)
                facts.append(fact)
            diag['alias_history_consumed'] = bool(diag['alias_fact_adaptations'])
            quarters = derive_company_quarters(facts,
                config=build_bulk_exact_company_derivation_config(tech), ticker=ticker,
                analysis_at=analysis, anchor_period_end=max(f.period_end for f in facts))
            diag['quarters'] = [asdict(q) for q in quarters]
            rows = [dict(q.values, ticker=ticker, period_end=q.period_end,
                         version_tag=q.version_tag, unit_scale=1) for q in quarters]
            core = compute_company_core_ratios_from_frame(pd.DataFrame(rows),
                ratios_json_path=str(ROOT/'config/ratios.json'))
            diag['core_ratios'] = core.to_dict('records')
            if not core.empty and (~core.is_na).any():
                diag['status'] = 'CORE_ONLY_DIAGNOSTICS'
            else:
                diag['reasons'].append('NO_PRESENT_CORE_RATIO')
            if ((family, tech) not in ALLOWED_ECONOMIC_TECHNICAL_PAIRS
                    or any(item.get('historical_family') != family for item, _ in candidates)):
                diag['reasons'].append('HISTORICAL_ENGINE_FAMILY_EVIDENCE_MISSING_OR_TECHNICAL_MISMATCH')
                continue
            # Every input report must support the same economic family; no
            # transfer of today's report family to earlier ambiguous reports.
            valid_periods = {f.period_end for item, raw in candidates
                             if item.get('historical_family') == family
                             for f in [_fact(raw)]}
            core = core[core.period_end.isin(valid_periods) & ~core.is_na].copy()
            if not core.empty:
                core['version_tag'] = 'EXPERIMENTAL_ASOF_' + analysis.isoformat()
                core['family'] = family
                ratio_parts.append(core)
                routing[ticker] = family
        except (ValueError, TypeError, KeyError) as exc:
            diag['reasons'].append('CORE_DERIVATION_REJECTED:' + type(exc).__name__ + ':' + str(exc))
    if ratio_parts:
        ratios = pd.concat(ratio_parts, ignore_index=True)
        # Apply policy eligibility before counting peers, preventing an excluded
        # ratio from providing phantom peer depth.
        _, policies = load_sector_config(str(ROOT/'config/sectors.json'))
        ratios = ratios[[not policies.get(r.family, {}).get('allowed_ratios') or
                         r.ratio_name in policies[r.family]['allowed_ratios']
                         for r in ratios.itertuples()]].copy()
        counts = ratios.groupby(['period_end', 'ratio_name', 'family']).ticker.transform('nunique')
        ratios = ratios[counts >= 5].drop(columns='family')
        present = tuple(sorted(ratios.ticker.unique()))
        if present:
            active = {t: routing[t] for t in present}
            foundation = HistoricalPitRatioReplayResult(analysis, present, ratios,
                                                       ratios.iloc[:0].copy())
            rsc = run_historical_pit_rsc_replay(foundation, routing=active,
                ratios_json_path=str(ROOT/'config/ratios.json'),
                sectors_json_path=str(ROOT/'config/sectors.json'))
            m1 = run_historical_pit_m1_replay(rsc, asof_date=analysis.date())
            ek1 = run_historical_pit_ek1_replay(m1)
            for ticker in present:
                diag = result['per_ticker'][ticker]
                diag['rsc'] = rsc.rsc_summary[rsc.rsc_summary.ticker == ticker].to_dict('records')
                diag['m1'] = m1.m1_scores[m1.m1_scores.ticker == ticker].to_dict('records')
                diag['ek1'] = ek1.ek1_scores[ek1.ek1_scores.ticker == ticker].to_dict('records')
                diag['reasons'].append('PARTIAL_CORE_ONLY_VAL_NOT_MATERIALIZED')
    for diag in result['per_ticker'].values():
        if diag['core_ratios'] and not diag['rsc']:
            diag['reasons'].append('RSC_HISTORICAL_FAMILY_OR_PEER_DEPTH_MISSING')
        diag['reasons'] = sorted(set(diag['reasons']))
    return _json(result)
