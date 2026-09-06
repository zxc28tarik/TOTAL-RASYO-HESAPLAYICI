"""Compare two independent complete cell materializations at byte level."""
import argparse
import json
from pathlib import Path
from scripts.materialize_experimental_financial_facts import encoded, sha


def audit(first, second, semantic_audit):
    a = json.loads((first / 'receipt.json').read_bytes())
    b = json.loads((second / 'receipt.json').read_bytes())
    if a != b or sha(first / 'receipt.json') != sha(second / 'receipt.json'):
        raise ValueError('CELL_REBUILD_RECEIPTS_DIFFER')
    if a['total_cells'] != 6000 or len(a['p4_months']) != 60:
        raise ValueError('CELL_REBUILD_NOT_COMPLETE')
    verified = {}
    for name, expected in sorted(a['outputs'].items()):
        left, right = sha(first / name), sha(second / name)
        if left != expected or right != expected:
            raise ValueError('CELL_REBUILD_BYTES_DIFFER:' + name)
        verified[name] = expected
    semantic = json.loads(semantic_audit.read_bytes())
    if semantic['result'] != 'PASS' or semantic['independent_primary_byte_rebuilds'] != 2:
        raise ValueError('TWO_PRIMARY_READS_NOT_VERIFIED')
    for key, filename in [('semantic','semantic_reports.jsonl.gz'),
                          ('semantic_alias','semantic_alias_reports.jsonl.gz'),
                          ('semantic_entity','semantic_entity_reports.jsonl.gz')]:
        if a['source_hashes'][key] != semantic['byte_identical_outputs'][filename]:
            raise ValueError('CELL_SEMANTIC_REBUILD_LINEAGE_MISMATCH:' + key)
    return dict(contract='EXPERIMENTAL_P3_P4_TWO_COMPLETE_REBUILDS_V1', result='PASS',
        rebuild_mode='TWO_SEPARATE_PROCESSES_SAME_VERIFIED_SEMANTIC_INPUT',
        primary_semantic_rebuild_audit_sha256=sha(semantic_audit),
        independent_primary_byte_rebuilds=2, independent_cell_materializations=2,
        receipt_sha256=sha(first / 'receipt.json'), byte_identical_outputs=verified,
        total_cells=6000, months=60, valid_total_scores=a['p4_valid_total_scores'],
        authoritative_claim_allowed=False, producer_sha256=sha(Path(__file__)))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--first', type=Path, required=True)
    p.add_argument('--second', type=Path, required=True)
    p.add_argument('--semantic-audit', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = audit(args.first, args.second, args.semantic_audit)
    args.output.write_bytes(encoded(result))
    print(json.dumps({k:v for k,v in result.items() if k!='byte_identical_outputs'}, indent=2))
