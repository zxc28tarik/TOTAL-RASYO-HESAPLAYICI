"""Catalog integrity tests; malformed mini archives are test inputs, never evidence."""
import gzip
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from scripts.reconstruct_experimental_kap_catalog import canonical, inspect_archive, classifications

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data/backtest_sources/reconstructed_experimental_kap_v1'

def test_drift_rejected_before_any_metadata_parse(tmp_path):
    path = tmp_path/'drift.zip'; path.write_bytes(b'not an accepted archive')
    inventory, rows, errors = inspect_archive((tmp_path, {'filename': path.name, 'sha256': '0'*64, 'member_count': 1}))
    assert inventory['status'] == 'IMMUTABLE_ARCHIVE_HASH_MISMATCH'
    assert rows == errors == []

def test_verified_member_parse_failure_keeps_exact_source_identity(tmp_path):
    path = tmp_path/'bad.zip'; raw = b'not financial HTML'
    with ZipFile(path,'w') as bundle:
        bundle.writestr('TEST_123_2021_1.xls',raw)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    inventory,rows,errors = inspect_archive((tmp_path,{'filename':path.name,'sha256':digest,'member_count':1,'uncompressed_bytes':len(raw)}))
    assert inventory['status'] == 'IMMUTABLE_ARCHIVE_VERIFIED'
    assert rows == [] and len(errors) == 1
    assert errors[0]['archive_sha256'] == digest
    assert errors[0]['member_sha256'] == hashlib.sha256(raw).hexdigest()

def test_reconstructed_catalog_all_rows_bound_to_verified_archives():
    receipt = json.loads((DATA/'receipt.json').read_text(encoding='utf-8'))
    inventories = json.loads((DATA/'archive_inventory.json').read_text(encoding='utf-8'))
    accepted = {r['archive_name']:r['expected_sha256'] for r in inventories if r['status']=='IMMUTABLE_ARCHIVE_VERIFIED'}
    assert len(accepted) == 26
    assert receipt['original_catalog_identity_claimed'] is False
    assert receipt['authoritative_pit_allowed'] is False
    for name, expected in receipt['outputs'].items():
        assert hashlib.sha256((DATA/name).read_bytes()).hexdigest() == expected
    rows = [json.loads(line) for line in gzip.decompress((DATA/'reports.jsonl.gz').read_bytes()).splitlines()]
    assert len(rows) == receipt['report_count'] > 14000
    assert rows == sorted(rows,key=lambda r:(r['source_entity_code'],r['published_at'],r['notification_id'],r['archive_name'],r['member_name']))
    assert len({(r['archive_name'],r['member_name']) for r in rows}) == len(rows)
    for row in rows:
        assert row['archive_sha256'] == accepted[row['archive_name']]
        assert row['member_name'].split('_')[0] == row['source_entity_code']
        assert row['historical_version_enumeration_complete'] is False
        assert row['profile'] == 'EXPERIMENTAL_RISK_ACCEPTED_5Y'
        assert 'ORIGINAL_CATALOG_BYTES_UNAVAILABLE' in row['risks']

def test_catalog_classification_does_not_relax_authoritative_gate():
    result=classifications()
    assert len(result['catalogs']) == 5
    assert result['original_byte_identity_recovered'] is result['authoritative_gate_relaxed'] is False
    assert canonical({'b':2,'a':1}) == b'{"a":1,"b":2}\n'


def test_drift_rows_never_promoted_to_verified_source():
    rows=[json.loads(line) for line in gzip.decompress((DATA/'drift_dependency_metadata.jsonl.gz').read_bytes()).splitlines()]
    assert len(rows) == 1522
    assert {row['archive_name'] for row in rows} == {'KAP_2025_Y.zip','KAP_2026_6A.zip'}
    assert all(row['source_accepted'] is False and row['original_publication_unknown'] is True for row in rows)
    assert all(row['observed_archive_sha256'] != row['expected_original_archive_sha256'] for row in rows)
