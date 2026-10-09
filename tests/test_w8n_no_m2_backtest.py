from __future__ import annotations

"""The no-M2 diagnostic variant must exclude M2, not neutral-fill it."""

import json
from pathlib import Path

import pytest

from scripts.audit_w8n_no_m2_backtest import (
    EXCLUDED_MODULE, PRODUCTION_WEIGHTS, SCORED_MODULES, VARIANT_WEIGHTS,
    check, score_cell,
)
from src.analytics.total_rasyo_score import compute_total_rasyo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/audit/w8n_no_m2_backtest_v1"


def _cell(**overrides):
    cell = {
        "month": "2024-06", "ticker": "TEST", "signal_date": "2024-06-03",
        "knowledge_cutoff_at": "2024-05-31T18:10:00+03:00",
        "good_count": 7, "p3_cell_sha256": "0" * 64,
        "module_values": {"M1": 0.6, "M3": 0.5, "Ek4": 0.4, "Ek1": 0.3, "Ek9": 0.2, "M2": None},
    }
    cell.update(overrides)
    return cell


def test_excluded_module_carries_zero_weight_and_cannot_move_the_score():
    assert VARIANT_WEIGHTS[EXCLUDED_MODULE] == 0.0
    low, _ = score_cell(_cell())
    # Feed the same cell an M2 value at the opposite extreme; the variant must
    # be blind to it, which is what distinguishes exclusion from neutral fill.
    values = dict(_cell()["module_values"])
    for probe in (0.0, 0.5, 1.0):
        scored = compute_total_rasyo(
            {**{key: values[key] for key in SCORED_MODULES}, EXCLUDED_MODULE: probe},
            good_count_ge8=7, weights=VARIANT_WEIGHTS,
        )
        assert scored["final_score"] == low["final_score"]
        assert scored["contributions"][EXCLUDED_MODULE] == 0.0


def test_retained_weights_keep_their_production_ratios_and_sum_to_one():
    assert sum(VARIANT_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-12)
    retained = sum(PRODUCTION_WEIGHTS[key] for key in SCORED_MODULES)
    for key in SCORED_MODULES:
        assert VARIANT_WEIGHTS[key] == pytest.approx(PRODUCTION_WEIGHTS[key] / retained, abs=1e-12)
    # Ratios between retained modules are exactly the production ratios.
    assert (VARIANT_WEIGHTS["M1"] / VARIANT_WEIGHTS["Ek9"]
            == pytest.approx(PRODUCTION_WEIGHTS["M1"] / PRODUCTION_WEIGHTS["Ek9"], abs=1e-12))


def test_production_weights_are_not_mutated_by_the_variant():
    assert PRODUCTION_WEIGHTS == {
        "M2": 0.40, "M1": 0.18, "M3": 0.12, "Ek4": 0.16, "Ek1": 0.08, "Ek9": 0.06
    }
    assert sum(PRODUCTION_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("module", SCORED_MODULES)
def test_a_missing_retained_module_fails_closed(module):
    values = dict(_cell()["module_values"])
    values[module] = None
    scored, rejected = score_cell(_cell(module_values=values))
    assert scored is None
    assert rejected["reason"] == "MODULE_MISSING"
    assert module in rejected["missing_modules"]


def test_a_missing_good_count_fails_closed():
    scored, rejected = score_cell(_cell(good_count=None))
    assert scored is None
    assert rejected["reason"] == "GOOD_COUNT_MISSING"


def test_veto_still_applies_at_the_production_threshold():
    vetoed, _ = score_cell(_cell(good_count=4))
    clear, _ = score_cell(_cell(good_count=5))
    assert vetoed["veto_flag"] is True and clear["veto_flag"] is False
    assert vetoed["final_score"] == pytest.approx(clear["final_score"] * 0.60, abs=1e-12)


def test_published_series_reproduces_and_declares_itself_diagnostic():
    receipt = json.loads((OUTPUT / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["purpose"] == "DIAGNOSTIC_ONLY_NOT_THE_PRODUCTION_MODEL"
    assert receipt["neutral_fill"] is False
    assert receipt["excluded_module_is_neutral_fill"] is False
    assert receipt["excluded_module_weight"] == 0.0
    assert receipt["month_count"] == 60
    assert receipt["scored_count"] == 3017
    assert receipt["months_with_at_least_six_scored"] == 60
    assert receipt["production_engine"] == "src.analytics.total_rasyo_score.compute_total_rasyo"
    assert check()["status"] == "W8N_CHECK_PASS"
