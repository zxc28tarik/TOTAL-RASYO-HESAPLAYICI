import json

from scripts.build_m2_priority_matrix import DEFAULT_INPUT, build


def test_real_five_module_priority_matrix_is_exact_and_raw_close_is_closed(tmp_path):
    summary = build(DEFAULT_INPUT, tmp_path)
    assert summary['target_cells'] == 3017
    assert summary['target_tickers'] == 143
    assert summary['target_months'] == 60
    assert summary['raw_close_verified_cells_after_wiring'] == 3017
    assert summary['raw_close_evidence_routes'] == {
        'VERIFIED_YAHOO_RAW_CLOSE_V1': 3017,
    }
    assert summary['issued_capital_observation_cells'] == 2854
    assert summary['issued_capital_without_nominal_share_proof_cells'] == 2854
    assert summary['composite_financial_entity_cells'] == 32
    assert summary['minimum_complete_year_set'] == [2021, 2022, 2023, 2024, 2025, 2026]
    persisted = json.loads((tmp_path / 'summary.json').read_bytes())
    assert persisted['outputs'] == summary['outputs']
