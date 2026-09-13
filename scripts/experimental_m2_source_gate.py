"""Execute actual M2 basis prerequisites without fabricating engine inputs."""
from datetime import date
from functools import lru_cache
from pathlib import Path

from scripts.experimental_core_module_materializer import _time, _json
from src.analytics.historical_valuation_price_supplement import CATALOG, materialize_historical_price_level_v2
from src.analytics.price_level_adapter import normalize_price_level_input, valuation_basis_receipt
from src.analytics.price_level_action_evidence import SOURCE_SHARE_BASIS
from src.analytics.verified_yahoo_raw_close import (
    price_level_input_from_yahoo_receipt, verify_yahoo_raw_close,
)

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def verified_dated_share_sources():
    # Existing auditor verifies original XLS/PDF bytes, nominal extraction,
    # publication and all preserved source member hashes. No nominal assumption.
    from scripts.audit_p2_action_research_v1 import audit
    return tuple(audit()['dated_share_sources'])


def build_m2_source_gate(selected_report, ownfacts, dated_nominal_share_evidence,
                         pricecandidate, analysis, ticker, family):
    analysis = _time(analysis)
    result = {'status': 'SOURCE_GATE_REJECTED', 'reasons': [],
        'production_gate': None, 'exact_error': None,
        'implementation_stage': 'M2_NOT_EXECUTED_SOURCE_CONTRACTS_MISSING',
        'historical_family': family, 'raw_close_basis_verified': False,
        'dated_nominal_shares_verified': False, 'basis_receipt': None}
    if selected_report and _time(selected_report['published_at']) > analysis:
        result['reasons'].append('PIT_PUBLICATION_AFTER_CUTOFF')
        result['exact_error'] = 'PIT_PUBLICATION_AFTER_CUTOFF'
        return result
    if family == 'BANK':
        result.update(production_gate='historical_pit_bank_m2_replay.ResolvedBankAssumption prerequisite',
            exact_error='BANK_PIT_ASSUMPTIONS_EVIDENCE_MISSING',
            required_dependency='Dated ResolvedBankAssumption with effective_at<=analysis_at, BANK/TICKER scope and source lineage; none supplied to this materialization')
        result['reasons'].append('BANK_PIT_ASSUMPTIONS_EVIDENCE_MISSING')
        return result
    if family not in {'NONFIN','HOLDING','GYO','INSURANCE','FINANCIAL'}:
        result['reasons'].append('HISTORICAL_SECTOR_FAMILY_EVIDENCE_MISSING')
        return result
    canonical = next((p for p in CATALOG if p.ticker == ticker and p.cutoff_local == analysis), None)
    price_day = (pricecandidate or {}).get('trade_date', (pricecandidate or {}).get('price_trade_date'))
    if canonical is None and price_day is not None and date.fromisoformat(str(price_day)) > analysis.date():
        result['reasons'].append('PRICE_AFTER_CUTOFF')
        result['exact_error'] = 'PRICE_AFTER_CUTOFF'
        return result
    known = []
    if canonical is not None or dated_nominal_share_evidence is not None:
        known = [s for s in verified_dated_share_sources()
                 if s['ticker'] == ticker and _time(s['published_at']) <= analysis]
    source = None
    if dated_nominal_share_evidence is not None:
        source = next((s for s in known if s == dated_nominal_share_evidence), None)
        if source is None:
            result['reasons'].append('DATED_NOMINAL_SHARE_EVIDENCE_UNVERIFIED_OR_FUTURE')
    elif canonical is not None and known:
        source = max(known, key=lambda s: (s['source_date'], _time(s['published_at'])))
    if source:
        result['dated_nominal_shares_verified'] = True
        result['share_source'] = source
    else:
        result['reasons'].append('DATED_NOMINAL_SHARE_EVIDENCE_MISSING')
    verified_yahoo = None
    if canonical is None and pricecandidate is not None:
        try:
            verified_yahoo = verify_yahoo_raw_close(
                ticker=ticker, candidate=pricecandidate, analysis_at=analysis)
            result['raw_close_basis_verified'] = True
            result['price_source'] = verified_yahoo
        except (ValueError, TypeError, OSError) as exc:
            result['reasons'].append('RAW_CLOSE_BASIS_EVIDENCE_MISSING')
            result['price_evidence_error'] = str(exc)
    try:
        if canonical is not None and source:
            result['production_gate'] = 'materialize_historical_price_level_v2'
            archive = ROOT/'data/backtest_sources/p2_raw_close_pit_v2'/Path(canonical.archive_url).name
            result['price_source'] = {'ticker': ticker, 'signal_date': str(canonical.signal_date),
                'trade_date': str(canonical.trade_date), 'raw_close': canonical.raw_close,
                'archive_sha256': canonical.archive_sha256, 'member_sha256': canonical.member_sha256}
            # Set verified only after the production materializer has passed
            # canonical cutoff, archive and member validation to the action gate.
            value = materialize_historical_price_level_v2(evidence=canonical,
                analysis_at=analysis, raw_archive_bytes=archive.read_bytes(),
                shares_out=source['source_shares_out'], shares_basis_date=date.fromisoformat(source['source_date']),
                action_bundle=(pricecandidate or {}).get('action_bundle'))
            result['raw_close_basis_verified'] = True
        else:
            result['production_gate'] = 'normalize_price_level_input'
            if pricecandidate is None:
                result['reasons'].append('RAW_CLOSE_BASIS_EVIDENCE_MISSING')
                result['reasons'].append('PRICE_MISSING')
            candidate = (price_level_input_from_yahoo_receipt(
                verified_yahoo, (pricecandidate or {}).get('action_bundle'))
                if verified_yahoo else dict(pricecandidate or {}, ticker=ticker,
                    price_basis='UNVERIFIED_SOURCE_PRICE_BASIS'))
            value = normalize_price_level_input(ticker=ticker,
                shares_out=source['source_shares_out'] if source else None,
                source_date=date.fromisoformat(source['source_date']) if source else None,
                source_share_basis=SOURCE_SHARE_BASIS if source else 'UNVERIFIED_SOURCE_SHARE_BASIS',
                price=candidate, analysis_at=analysis)
        result.update(status='VERIFIED_BASIS', implementation_stage='M2_ENGINE_NOT_EXECUTED',
                      basis_receipt=valuation_basis_receipt(value))
    except (ValueError, TypeError, OSError) as exc:
        result['exact_error'] = str(exc)
        if canonical is not None and source and str(exc) == 'ACTION_COMPLETENESS_EVIDENCE_MISSING':
            result['raw_close_basis_verified'] = True
        result['reasons'].append(str(exc))
    result['reasons'] = sorted(set(result['reasons']))
    return _json(result)
