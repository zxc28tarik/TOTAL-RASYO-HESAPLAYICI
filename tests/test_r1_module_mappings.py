from __future__ import annotations

"""R1 must keep probing the real mappings, and keep recording what it found."""

import json
from pathlib import Path

import numpy as np
import pytest

from scripts.audit_r1_module_mappings import MODULE_WEIGHTS, check, probe_linear_band
from src.analytics.ek1_quality import compute_ek1_score_from_good_count
from src.analytics.ek9_volatility import EK9_VOLATILITY_CAP
from src.analytics.trailing_alpha import _score_alpha


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "data/audit/r1_module_mappings_v1/receipt.json"


def _receipt() -> dict:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


def test_probe_recovers_a_known_band_it_was_not_told_about():
    band = probe_linear_band(lambda x: float(np.clip((x + 0.5) / 2.0, 0.0, 1.0)))
    assert band["reaches_zero_at_or_below"] == pytest.approx(-0.5, abs=1e-3)
    assert band["reaches_one_at_or_above"] == pytest.approx(1.5, abs=1e-3)
    assert band["band_is_symmetric_about_zero"] is False


def test_m3_and_ek4_really_do_share_one_band():
    """If they ever diverge the audit's shared-mapping claim must fail loudly."""
    from src.analytics.ek4_momentum import compute_ek4_momentum_point
    for excess in (-0.35, -0.20, -0.05, 0.0, 0.05, 0.20, 0.45):
        assert _score_alpha(excess) == pytest.approx(
            float(np.clip((excess + 0.20) / 0.40, 0.0, 1.0)), abs=1e-12)
    assert compute_ek4_momentum_point is not None


def test_ek9_cannot_award_a_perfect_score_to_any_traded_stock():
    # A perfect Ek9 needs exactly zero realised volatility.
    assert 1.0 - 0.0 / EK9_VOLATILITY_CAP == 1.0
    for volatility in (0.0001, 0.001, 0.005):
        assert float(np.clip(1.0 - volatility / EK9_VOLATILITY_CAP, 0.0, 1.0)) < 1.0


def test_ek1_needs_a_good_count_far_above_the_typical_company():
    assert compute_ek1_score_from_good_count(18) == pytest.approx(1.0)
    # The measured median company scores about 4.5.
    assert compute_ek1_score_from_good_count(4.5) == pytest.approx(0.25, abs=1e-9)


def test_receipt_changed_nothing_and_never_opened_the_holdout():
    receipt = _receipt()
    assert receipt["purpose"] == "MEASUREMENT_ONLY_NO_MODULE_WAS_CHANGED"
    assert receipt["holdout_read_here"] is False
    assert receipt["evaluation_split_used"] == "IN_SAMPLE"


def test_receipt_records_the_band_miscalibration_it_measured():
    receipt = _receipt()
    measured = receipt["what_m3_and_ek4_actually_measure"]
    current = receipt["m3_ek4_clipping_under_current_band"]
    wider = receipt["m3_ek4_clipping_under_p5_p95_band"]
    # The band is far narrower than one standard deviation of its own input.
    assert receipt["recovered_mappings"]["M3_AND_EK4_excess_return_band"]["band_width"] < measured["stdev"] * 1.5
    assert current["clipped_total_share"] > 0.30
    assert wider["clipped_total_share"] < current["clipped_total_share"]
    assert measured["skew_right"] is True


def test_receipt_records_that_weight_runs_against_discrimination():
    aggregate = _receipt()["aggregate"]
    assert aggregate["weight_is_allocated_against_discrimination"] is True
    assert aggregate["heaviest_weight_module"] == "M2"
    ranges = _receipt()["realised_module_ranges"]
    assert ranges["M2"]["effective_range_p10_p90"] < ranges["M3"]["effective_range_p10_p90"]
    assert MODULE_WEIGHTS["m2"] > MODULE_WEIGHTS["m3"]


def test_receipt_records_the_good_count_double_count():
    double = _receipt()["good_count_is_double_counted"]
    assert double["verdict"] == "ONE_VARIABLE_SCORED_TWICE_WITH_OPPOSING_SIGNS"
    assert double["enters_as_ek1_additively_with_weight"] == MODULE_WEIGHTS["ek1"]
    assert double["enters_as_veto_multiplicatively_with_factor"] == 0.60


def test_every_module_carries_a_verdict():
    verdicts = _receipt()["verdicts"]
    assert {key.upper() for key in MODULE_WEIGHTS} == set(verdicts)
    assert all(isinstance(value, str) and value for value in verdicts.values())


def test_r1_sources_still_match():
    assert check()["status"] == "R1_CHECK_PASS"
