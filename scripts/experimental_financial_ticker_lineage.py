"""Forward identity candidates from the preserved official code-change sheet.

Candidate discovery only: never retags facts, assumes a family, or splices prices.
"""
import csv
import hashlib
import io
import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.experimental_core_module_materializer import _time

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT/'data/backtest_sources/bist_ticker_code_changes_2021-08_2026-08.csv'
PROVENANCE = CSV.with_suffix('.provenance.json')
CSV_SHA = '8d71a1d442a1389fd0f0d65414d21fd10b6f95403aa29bda147c638a3e98fa4e'
PROVENANCE_SHA = '63311bfb53e8db7f4eb45c2e89b90be3963348ba4a94646e1b0fd6c29156e3a4'


def candidate_source_tickers(ticker, cutoff, *, csv_path=CSV, provenance_path=PROVENANCE):
    """Exact ticker first, then already-effective official predecessors.

    Returned predecessors are financial identity candidates, not proof that an
    individual report is usable. Its own publication, scope and source lineage
    must still pass; mergers outside the code-change sheet are never inferred.
    """
    return tuple(lineage_receipt(ticker, cutoff, csv_path=csv_path,
                                provenance_path=provenance_path)['candidate_source_tickers'])


def lineage_receipt(ticker, cutoff, *, csv_path=CSV, provenance_path=PROVENANCE):
    ticker = str(ticker).strip().upper()
    if not ticker:
        raise ValueError('TICKER_REQUIRED')
    day = _time(cutoff).astimezone(ZoneInfo('Europe/Istanbul')).date()
    raw, provenance_raw = Path(csv_path).read_bytes(), Path(provenance_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != CSV_SHA:
        raise ValueError('TICKER_LINEAGE_CSV_HASH_MISMATCH')
    if hashlib.sha256(provenance_raw).hexdigest() != PROVENANCE_SHA:
        raise ValueError('TICKER_LINEAGE_PROVENANCE_HASH_MISMATCH')
    provenance = json.loads(provenance_raw)
    if provenance['sheet'] != 'KOD_DEGISIKLIGI' or provenance['publisher'] != 'Borsa Istanbul A.S.':
        raise ValueError('OFFICIAL_CODE_CHANGE_PROVENANCE_REQUIRED')
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    if len(rows) != provenance['event_count'] or any(
        r['source_workbook_sha256'] != provenance['workbook_sha256'] for r in rows):
        raise ValueError('CODE_CHANGE_EVENT_PROVENANCE_MISMATCH')
    # Follow edges against identity history, with effective dates decreasing
    # along predecessors. Never traverse an old->future-new edge.
    found = [ticker]; edges = []; pending = [(ticker, day)]
    while pending:
        current, upper = pending.pop(0)
        predecessors = [r for r in rows if r['new_ticker'] == current
                        and date.fromisoformat(r['effective_date']) <= upper]
        if len(predecessors) > 1:
            raise ValueError('AMBIGUOUS_CODE_IDENTITY_PREDECESSOR')
        for row in predecessors:
            old = row['old_ticker']
            if old in found:
                raise ValueError('CYCLIC_CODE_IDENTITY_LINEAGE')
            found.append(old); edges.append(row)
            pending.append((old, date.fromisoformat(row['effective_date'])))
    return {'contract':'EXPERIMENTAL_FORWARD_FINANCIAL_IDENTITY_CANDIDATES_V1',
        'ticker':ticker,'cutoff':_time(cutoff).isoformat(),'candidate_source_tickers':found,
        'events':edges,'csv_sha256':CSV_SHA,'provenance_sha256':PROVENANCE_SHA,
        'current_or_future_successor_fallback':False,'semantic_retag_performed':False,
        'raw_report_identity_must_be_preserved':True,
        'risk_ids':['HISTORICAL_CODE_CHANGE_ANNOUNCEMENT_TIMES_NOT_ENUMERATED'],
        'authoritative_pit_claim_allowed':False}
