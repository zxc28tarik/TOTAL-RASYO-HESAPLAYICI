from scripts.build_m2_procurement_manifest import build


def test_procurement_manifest_is_cell_scoped_and_does_not_promise_unlock(tmp_path):
    manifest = build(tmp_path / 'manifest.json')
    assert manifest['minimum_annual_package_request']['packages'] == [
        'serart2021.zip', 'serart2022.zip', 'serart2023.zip',
        'serart2024.zip', 'serart2025.zip', 'serart2026.zip',
    ]
    assert manifest['raw_price_product_required'] is False
    assert manifest['best_case_target_coverage']['raw_close_ready'] == 3017
    assert manifest['best_case_target_coverage']['guaranteed_unlock_from_purchase_alone'] == 0
    assert manifest['point_in_time_safe_fallback_if_annual_archives_lack_revisions']['package_count'] == 60
    assert manifest['bank_requirement']['affected_cells'] == 509
    assert len(manifest['bank_requirement']['affected_tickers']) == 9
