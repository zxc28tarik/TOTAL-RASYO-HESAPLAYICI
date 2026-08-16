from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.analytics.bank_shadow_report import (
    build_bank_shadow_distribution,
    canonical_thresholds,
)
from src.analytics.bank_valuation_pipeline import CanonicalizationError


def sample_df():
    return pd.DataFrame([
        {
            "ticker": "AAA", "sector_code": "BANK", "anchor_period_end": date(2025, 12, 31),
            "valuation_status": "OK", "lower_halfwidth": 0.70, "upper_halfwidth": 0.75,
            "floor_source": "ABSOLUTE_FLOOR", "sd_roe_floor": 0.01,
            "sd_roe_effective": 0.01, "justified_pb": 0.8, "roe_sus": 0.22,
            "outlier_conf_penalty": 1.0, "v_mid": 10.0, "current_price": 8.0,
            "z_val": 0.9, "s_valuation": 0.72, "v_conf": 0.8,
            "coe": 0.37, "macro_cap": 0.14, "risk_free_rate": 0.30,
        },
        {
            "ticker": "BBB", "sector_code": "BANK", "anchor_period_end": date(2025, 12, 31),
            "valuation_status": "OK", "lower_halfwidth": 0.85, "upper_halfwidth": 0.95,
            "floor_source": "SECTOR_QUANTILE", "sd_roe_floor": 0.01,
            "sd_roe_effective": 0.03, "justified_pb": 1.2, "roe_sus": 0.28,
            "outlier_conf_penalty": 0.85, "v_mid": 20.0, "current_price": 22.0,
            "z_val": -0.3, "s_valuation": 0.0, "v_conf": 0.68,
            "coe": 0.39, "macro_cap": 0.15, "risk_free_rate": 0.32,
        },
        {
            "ticker": "CCC", "sector_code": "BANK", "anchor_period_end": date(2025, 12, 31),
            "valuation_status": "YETERSIZ_VERI", "lower_halfwidth": None, "upper_halfwidth": None,
            "floor_source": "INSUFFICIENT_DATA", "sd_roe_floor": 0.01,
            "sd_roe_effective": 0.01, "justified_pb": None, "roe_sus": None,
            "outlier_conf_penalty": None, "v_mid": None, "current_price": 5.0,
            "z_val": None, "s_valuation": None, "v_conf": None,
            "coe": 0.38, "macro_cap": 0.14, "risk_free_rate": None,
        },
    ])


def test_shadow_distribution_reports_three_thresholds_without_hard_gate():
    out = build_bank_shadow_distribution(sample_df()).iloc[0]
    assert out["total_count"] == 3
    assert out["valuation_usable_count"] == 2
    assert out["floor_binding_count"] == 1
    assert out["outlier_count"] == 1
    assert out["saturation_zero_count"] == 1
    assert out["reject_0_80_count"] == 1
    assert out["reject_0_90_count"] == 1
    assert out["reject_1_00_count"] == 0
    assert out["reject_0_80_rate"] == pytest.approx(0.5)
    assert out["price_to_v_mid_p50"] == pytest.approx(0.95)
    assert out["coe_p50"] == pytest.approx(0.38)
    assert out["macro_cap_p50"] == pytest.approx(0.145)
    assert out["risk_free_rate_p50"] == pytest.approx(0.31)


def test_shadow_report_rejects_duplicate_ticker_period_rows():
    df = pd.concat([sample_df(), sample_df().iloc[[0]]], ignore_index=True)
    with pytest.raises(CanonicalizationError, match="birden fazla"):
        build_bank_shadow_distribution(df)


def test_shadow_report_rejects_missing_contract_column():
    with pytest.raises(CanonicalizationError, match="eksik kolonlar"):
        build_bank_shadow_distribution(sample_df().drop(columns=["z_val"]))


@pytest.mark.parametrize("values", [[], [0], [-1], [True], ["oops"]])
def test_shadow_thresholds_are_strict(values):
    with pytest.raises(CanonicalizationError):
        canonical_thresholds(values)


def test_shadow_thresholds_are_sorted_and_deduplicated():
    assert canonical_thresholds([1.0, "0.8", 0.9, 0.8]) == (0.8, 0.9, 1.0)


def test_shadow_threshold_labels_must_not_collide_after_two_decimal_formatting():
    with pytest.raises(CanonicalizationError, match="cakismamali"):
        canonical_thresholds([0.801, 0.804])


@pytest.mark.parametrize("value", [True, "bozuk", float("inf")])
def test_shadow_report_rejects_invalid_numeric_cells_instead_of_hiding_them(value):
    df = sample_df()
    df["coe"] = df["coe"].astype(object)
    df.loc[0, "coe"] = value
    with pytest.raises(CanonicalizationError, match="coe"):
        build_bank_shadow_distribution(df)
