import pytest
from scripts.experimental_financial_ticker_lineage import candidate_source_tickers, CSV, PROVENANCE


def test_known_rename_only_moves_predecessor_reports_forward_after_effective_date():
    assert candidate_source_tickers('GRTHO','2024-09-30T18:10:00+03:00') == ('GRTHO',)
    assert candidate_source_tickers('GRTHO','2024-10-01T18:10:00+03:00') == ('GRTHO','GRTRK')
    assert candidate_source_tickers('GRTRK','2025-01-01T18:10:00+03:00') == ('GRTRK',)
    assert candidate_source_tickers('UNKNOWN','2025-01-01T18:10:00+03:00') == ('UNKNOWN',)


def test_transitive_identity_chain_does_not_imply_economic_family():
    assert candidate_source_tickers('TEHOL','2025-08-02T18:10:00+03:00') == ('TEHOL','PEHOL','PEGYO')
    assert candidate_source_tickers('TEHOL','2025-07-31T18:10:00+03:00') == ('TEHOL',)


def test_changed_source_or_provenance_rejected(tmp_path):
    source = tmp_path/'csv'; source.write_bytes(CSV.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='CSV_HASH_MISMATCH'):
        candidate_source_tickers('GRTHO','2025-01-01T00:00:00+03:00',csv_path=source)
    proof = tmp_path/'json'; proof.write_bytes(PROVENANCE.read_bytes()+b' ')
    with pytest.raises(ValueError,match='PROVENANCE_HASH_MISMATCH'):
        candidate_source_tickers('GRTHO','2025-01-01T00:00:00+03:00',provenance_path=proof)
