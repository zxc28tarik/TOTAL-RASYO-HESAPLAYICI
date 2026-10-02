from __future__ import annotations

"""R2 must keep reporting every horizon, and keep refusing to fit anything."""

import json
import math
from pathlib import Path

import pytest

from scripts.audit_r2_forward_skill import HORIZONS, SCORES, check, summarise


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "data/audit/r2_forward_skill_v1/receipt.json"


def _receipt() -> dict:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


def test_summarise_matches_a_hand_computed_t():
    values = [0.1, 0.2, 0.3, 0.4]
    result = summarise(values)
    mean = 0.25
    stdev = math.sqrt(sum((v - mean) ** 2 for v in values) / 3)
    assert result["mean_rho"] == pytest.approx(mean)
    assert result["t_stat"] == pytest.approx(mean / (stdev / 2.0))
    assert result["positive_share"] == 1.0


def test_summarise_refuses_to_speak_on_fewer_than_three_points():
    for values in ([], [0.1], [0.1, 0.2]):
        assert summarise(values)["insufficient"] is True


def test_no_parameter_was_fitted_and_the_holdout_stayed_shut():
    receipt = _receipt()
    assert receipt["purpose"] == "MEASUREMENT_ONLY_NO_PARAMETER_WAS_FITTED"
    assert receipt["holdout_read_here"] is False
    assert receipt["evaluation_split_used"] == "IN_SAMPLE"
    assert receipt["conclusions"]["weights_cannot_be_fitted_on_this_sample"] is True
    assert receipt["conclusions"]["no_module_has_defensible_forward_skill"] is True


def test_every_score_is_reported_at_every_horizon_not_just_the_flattering_ones():
    grid = _receipt()["grid"]
    assert set(grid) == set(SCORES)
    for column, horizons in grid.items():
        assert set(horizons) == {f"{h}m" for h in HORIZONS}, column
        for cell in horizons.values():
            assert {"overlapping", "non_overlapping"} <= set(cell)


def test_the_overlap_and_multiple_testing_caveats_are_on_the_record():
    receipt = _receipt()
    assert "non_overlapping" in receipt["overlap_caveat"]
    testing = receipt["multiple_testing"]
    assert testing["cells_tested"] == len(SCORES) * len(HORIZONS)
    assert testing["expected_false_positives_at_5pct"] == pytest.approx(
        testing["cells_tested"] * 0.05)


def test_overlapping_samples_never_outnumber_independent_ones():
    grid = _receipt()["grid"]
    for column, horizons in grid.items():
        for label, cell in horizons.items():
            overlapping = cell["overlapping"].get("observations", 0)
            independent = cell["non_overlapping"].get("observations", 0)
            assert independent <= overlapping, f"{column} {label}"


def test_the_structural_findings_name_each_module_including_the_untested_one():
    findings = _receipt()["conclusions"]["structural_findings"]
    assert {"M1", "M3", "EK4", "EK1", "EK9", "M2"} == set(findings)
    assert "UNTESTED" in findings["M2"]


def test_ek1_and_the_veto_are_recorded_as_pointing_the_wrong_way():
    receipt = _receipt()
    assert receipt["sign_consistency_across_horizons"]["Ek1"] == "ALL_NEGATIVE"
    assert "went on to do better" in receipt["conclusions"]["the_veto_is_backwards"]


def test_r2_sources_still_match():
    assert check()["status"] == "R2_CHECK_PASS"
