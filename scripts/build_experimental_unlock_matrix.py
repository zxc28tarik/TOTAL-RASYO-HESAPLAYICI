"""Build machine-readable yield and blocker scope for a 6000-cell replay."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path

from scripts.materialize_experimental_financial_facts import encoded, sha


BLOCKER_MODULES = {
    'HISTORICAL_SECTOR_FAMILY_EVIDENCE_MISSING': ('M2', 'M1', 'Ek1'),
    'BANK_PIT_ASSUMPTIONS_EVIDENCE_MISSING': ('M2',),
    'DATED_NOMINAL_SHARE_EVIDENCE_MISSING': ('M2',),
    'SOURCE_SHARE_BASIS_MISMATCH: dated unadjusted shares required': ('M2',),
    'RAW_CLOSE_BASIS_EVIDENCE_MISSING': ('M2',),
    'PRICE_BASIS_MISMATCH: raw market close required': ('M2',),
    'ACTION_COMPLETENESS_EVIDENCE_MISSING': ('M2',),
    'HISTORICAL_SECTOR_INDEX_EVIDENCE_MISSING': ('M3', 'Ek4'),
    'STOCK_WINDOW_PRICE_MISSING': ('M3', 'Ek4', 'Ek9'),
    'FINANCIAL_SOURCE_PRIMARY_BYTES_MISSING': ('M1', 'Ek1', 'M2'),
    'SEMANTIC_MAPPING_UNRESOLVED': ('M1', 'Ek1', 'M2'),
    'PIT_PUBLICATION_AFTER_CUTOFF': ('M1', 'Ek1', 'M2'),
}

CLASSIFICATION = {
    # Remaining instances are XUMAL routes, which do not distinguish banks,
    # finance, holdings and GYOs.  The positive NONFIN wiring is already live;
    # these cells need narrower dated evidence rather than another join.
    'HISTORICAL_SECTOR_FAMILY_EVIDENCE_MISSING': ('PARTIAL', False, False, False, True),
    'BANK_PIT_ASSUMPTIONS_EVIDENCE_MISSING': ('NO', False, False, True, True),
    'DATED_NOMINAL_SHARE_EVIDENCE_MISSING': ('PARTIAL', False, False, True, True),
    'SOURCE_SHARE_BASIS_MISMATCH: dated unadjusted shares required': ('PARTIAL', False, False, True, True),
    'RAW_CLOSE_BASIS_EVIDENCE_MISSING': ('YES_SECONDARY', False, False, True, True),
    'PRICE_BASIS_MISMATCH: raw market close required': ('PARTIAL', False, False, True, True),
    'ACTION_COMPLETENESS_EVIDENCE_MISSING': ('PARTIAL', False, False, False, True),
    'HISTORICAL_SECTOR_INDEX_EVIDENCE_MISSING': ('YES', True, False, True, False),
    'STOCK_WINDOW_PRICE_MISSING': ('PARTIAL', False, False, True, True),
    'FINANCIAL_SOURCE_PRIMARY_BYTES_MISSING': ('PARTIAL', False, False, False, True),
    'SEMANTIC_MAPPING_UNRESOLVED': ('YES', False, False, True, False),
    'PIT_PUBLICATION_AFTER_CUTOFF': ('NO_AT_CUTOFF', False, False, False, True),
}


def _rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as source:
        for line in source:
            yield json.loads(line)


def summarize_cells(cells):
    cells = list(cells)
    module_counts = Counter()
    reason_scope = defaultdict(lambda: {'cells': 0, 'months': set(), 'tickers': set()})
    months_with_scores = set()
    months_with_six = Counter()
    valid = 0
    for cell in cells:
        values = cell['module_values']
        count = sum(values.get(key) is not None for key in ('M2','M1','M3','Ek4','Ek1','Ek9'))
        module_counts[count] += 1
        if cell.get('final_score') is not None:
            valid += 1
            months_with_scores.add(cell['month'])
            months_with_six[cell['month']] += 1
        for reason in cell.get('reasons', []):
            scope = reason_scope[reason]
            scope['cells'] += 1; scope['months'].add(cell['month']); scope['tickers'].add(cell['ticker'])
    metrics = {
        'M1_valid': sum(c['module_values'].get('M1') is not None for c in cells),
        'M2_valid': sum(c['module_values'].get('M2') is not None for c in cells),
        'M3_valid': sum(c['module_values'].get('M3') is not None for c in cells),
        'Ek1_valid': sum(c['module_values'].get('Ek1') is not None for c in cells),
        'Ek4_valid': sum(c['module_values'].get('Ek4') is not None for c in cells),
        'Ek9_valid': sum(c['module_values'].get('Ek9') is not None for c in cells),
        'cells_with_ge1_module': sum(v for k,v in module_counts.items() if k >= 1),
        'cells_with_ge4_modules': sum(v for k,v in module_counts.items() if k >= 4),
        'cells_with_ge5_modules': sum(v for k,v in module_counts.items() if k >= 5),
        'valid_total_scores': valid,
        'months_with_ge1_total_score': len(months_with_scores),
        'months_with_ge6_valid_candidates': sum(v >= 6 for v in months_with_six.values()),
    }
    matrix=[]
    for reason,scope in sorted(reason_scope.items(), key=lambda item:(-item[1]['cells'],item[0])):
        modules=BLOCKER_MODULES.get(reason, ())
        source,wiring,strict,reconstructible,external=CLASSIFICATION.get(
            reason, ('UNKNOWN',False,False,False,True))
        optimistic_ge5=optimistic_total=0
        if modules:
            for cell in cells:
                if reason not in cell.get('reasons', []): continue
                values=cell['module_values']; present={k for k,v in values.items() if v is not None}
                after=present | set(modules)
                optimistic_ge5 += len(after) >= 5 and len(present) < 5
                optimistic_total += len(after) == 6 and cell.get('good_count') is not None
        matrix.append({
            'blocker':reason, 'affected_cell_count':scope['cells'],
            'affected_month_count':len(scope['months']), 'affected_ticker_count':len(scope['tickers']),
            'affected_modules':list(modules), 'source_already_exists_in_repo':source,
            'wiring_missing':wiring, 'gate_too_strict':strict,
            'deterministically_reconstructible':reconstructible,
            'real_external_evidence_required':external,
            'if_fixed_new_ge5_cells_upper_bound':optimistic_ge5,
            'if_fixed_total_scores_possible_upper_bound':optimistic_total,
        })
    return metrics,matrix


def build(artifact_dir, output, stage):
    p4=artifact_dir/'p4_cells.jsonl.gz'; receipt=json.loads((artifact_dir/'receipt.json').read_bytes())
    if sha(p4) != receipt['outputs']['p4_cells.jsonl.gz']:
        raise ValueError('P4_UNLOCK_SOURCE_HASH_MISMATCH')
    metrics,matrix=summarize_cells(_rows(p4))
    result={'contract':'EXPERIMENTAL_UNLOCK_MATRIX_V1','stage':stage,
            'source_p4_sha256':sha(p4),'metrics':metrics,'blockers':matrix,
            'counterfactual_note':'Upper bounds fill only modules directly associated with one blocker; they are not score claims.',
            'authoritative_claim_allowed':False}
    output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(encoded(result))
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--artifact-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True); parser.add_argument('--stage',required=True)
    args=parser.parse_args(); print(json.dumps(build(args.artifact_dir,args.output,args.stage),indent=2))
