import shutil

import pytest

from scripts.audit_p7_version_pair import OUT, audit, encode, select_within_recovered_pair


def test_actual_original_and_corrected_financial_bytes_remain_distinct():
    result = audit()
    assert encode(result) == encode(audit())
    assert result['numeric_slots_compared'] == 437
    assert len(result['changed_numeric_slots']) == 4
    assert result['versions'][0]['source_sha256'] != result['versions'][1]['source_sha256']
    assert result['demonstrated_cutoffs'][0]['selected_disclosure_id'] == '1122417'
    assert result['demonstrated_cutoffs'][1]['selected_disclosure_id'] == '1126845'
    assert not result['historical_global_version_enumeration_proven']
    assert not result['authoritative_performance_allowed']


def test_future_revision_is_excluded_and_prepublication_cutoff_rejects():
    versions = audit()['versions']
    assert select_within_recovered_pair(list(reversed(versions)), '2023-03-10T18:10:00+03:00')['disclosure_id'] == '1122417'
    with pytest.raises(ValueError, match='NO_PUBLISHED_VERSION'):
        select_within_recovered_pair(versions, '2023-03-08T18:10:00+03:00')


def test_changed_raw_capture_fails_before_version_selection(tmp_path):
    shutil.copyfile(OUT/'raw_archive.json', tmp_path/'raw_archive.json')
    (tmp_path/'raw_details.zip').write_bytes((OUT/'raw_details.zip').read_bytes()+b'changed')
    with pytest.raises(ValueError, match='DETAIL_ARCHIVE_HASH_MISMATCH'):
        audit(tmp_path)
