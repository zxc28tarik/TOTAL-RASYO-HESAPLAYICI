from copy import deepcopy
import shutil

import pytest

from scripts.audit_p2_share_continuity_v1 import OUT, audit, eligible_action, encode, reconcile


def test_real_capital_reconciliation_never_promotes_completeness():
    receipt = audit()
    assert encode(receipt) == encode(audit())
    assert len(receipt['independently_captured_next_capital_points']) == 5
    assert len(receipt['balance_links']) == 8
    assert all(r['capital_reconciles'] for r in receipt['balance_links'])
    assert len(receipt['cells']) == 12
    assert all(c['status'] == 'EXPLICIT_REJECTION' and c['next_balance_is_not_pit_input'] for c in receipt['cells'])
    assert all(not c['proven_effective_actions_by_cutoff'] for c in receipt['cells'])
    assert receipt['runtime_action_bundles_created'] == 0


def test_equal_endpoints_do_not_prove_no_intermediate_share_changes():
    result = reconcile(100, 100, [])
    assert result['capital_reconciles']
    assert not result['action_completeness_proven']
    assert not result['runtime_materialization_allowed']


def test_action_decision_and_future_disclosures_cannot_be_applied_early():
    events = audit()['actions']
    decision = next(e for e in events if e['disclosure_id'] == '1139424')
    effective = next(e for e in events if e['disclosure_id'] == '1156058')
    assert not eligible_action(decision, '2023-03-31', '2023-05-30', '2023-05-31T18:10:00+03:00')
    assert not eligible_action(effective, '2023-03-31', '2023-05-30', '2023-05-31T18:10:00+03:00')
    assert not eligible_action(effective, '2023-03-31', '2023-06-06', '2023-06-02T18:10:00+03:00')
    assert eligible_action(effective, '2023-03-31', '2023-06-06', '2023-06-06T18:10:00+03:00')
    with pytest.raises(ValueError, match='DUPLICATE_ECONOMIC_ACTION'):
        reconcile(650000000, 1625000000, [effective, deepcopy(effective)])


def test_corrupt_capture_rejected_before_reconciliation(tmp_path):
    for name in ['raw_archive.json', 'raw_sources.zip']:
        shutil.copyfile(OUT / name, tmp_path / name)
    with (tmp_path / 'raw_sources.zip').open('ab') as stream:
        stream.write(b'corruption')
    with pytest.raises(ValueError, match='RAW_ARCHIVE_HASH_MISMATCH'):
        audit(tmp_path)
