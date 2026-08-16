from __future__ import annotations

import itertools
import math

import pytest

from src.analytics.total_rasyo_score import (
    DEFAULT_WEIGHTS,
    TotalRasyoScoreError,
    compute_total_rasyo,
    normalize_weights,
    total_rasyo_decision,
)


def scores(**changes):
    data = {"M2": 0.5, "M1": 0.5, "M3": 0.5, "Ek4": 0.5, "Ek1": 0.5, "Ek9": 0.5}
    data.update(changes)
    return data


def test_default_total_rasyo_matches_daily_pipeline_formula_and_decisions():
    result = compute_total_rasyo(scores(M2=0.8, M1=0.7, M3=0.6, Ek4=0.55, Ek1=0.9, Ek9=0.4), good_count_ge8=8)
    expected = 0.4*0.8 + 0.18*0.7 + 0.12*0.6 + 0.16*0.55 + 0.08*0.9 + 0.06*0.4
    assert result["base_score"] == pytest.approx(expected)
    assert result["final_score"] == pytest.approx(expected)
    assert result["total_rasyo_100"] == pytest.approx(expected * 100)
    assert result["decision"] == total_rasyo_decision(expected)
    assert math.fsum(result["contributions"].values()) == pytest.approx(expected)


def test_veto_contract_matches_existing_60_percent_penalty():
    no_veto = compute_total_rasyo(scores(M2=1, M1=1, M3=1, Ek4=1, Ek1=1, Ek9=1), good_count_ge8=5)
    veto = compute_total_rasyo(scores(M2=1, M1=1, M3=1, Ek4=1, Ek1=1, Ek9=1), good_count_ge8=4)
    assert no_veto["final_score"] == pytest.approx(1.0)
    assert no_veto["decision"] == "AL"
    assert veto["base_score"] == pytest.approx(1.0)
    assert veto["final_score"] == pytest.approx(0.6)
    assert veto["decision"] == "IZLE"


@pytest.mark.parametrize(
    "value, expected",
    [(0.70, "AL"), (0.699999, "IZLE"), (0.55, "IZLE"), (0.549999, "UZAK")],
)
def test_decision_boundaries_are_explicit(value, expected):
    assert total_rasyo_decision(value) == expected


@pytest.mark.parametrize(
    "bad",
    [None, [], True, float("nan"), float("inf"), -0.1, 1.1, "x"],
)
def test_module_score_contract_rejects_invalid_values(bad):
    raw = scores()
    raw["M2"] = bad
    with pytest.raises(TotalRasyoScoreError):
        compute_total_rasyo(raw, good_count_ge8=8)


def test_weight_contract_is_exact_and_not_silently_renormalized():
    assert normalize_weights() == DEFAULT_WEIGHTS
    with pytest.raises(TotalRasyoScoreError, match="toplami 1"):
        normalize_weights({**DEFAULT_WEIGHTS, "M2": 0.39})
    with pytest.raises(TotalRasyoScoreError, match="anahtarlari tam"):
        normalize_weights({"M2": 1.0})


def test_small_grid_never_leaves_score_contract():
    for values in itertools.product((0.0, 0.5, 1.0), repeat=6):
        result = compute_total_rasyo(dict(zip(("M2", "M1", "M3", "Ek4", "Ek1", "Ek9"), values)), good_count_ge8=8)
        assert 0.0 <= result["base_score"] <= 1.0
        assert 0.0 <= result["final_score"] <= 1.0
        assert result["decision"] in {"AL", "IZLE", "UZAK"}


def test_numpy_bool_like_values_are_not_numeric_scores_or_counts():
    np = pytest.importorskip("numpy")
    raw = scores(M2=np.bool_(False))
    with pytest.raises(TotalRasyoScoreError, match="bool"):
        compute_total_rasyo(raw, good_count_ge8=8)
    with pytest.raises(TotalRasyoScoreError, match="bool"):
        compute_total_rasyo(scores(), good_count_ge8=np.bool_(False))


def test_mixed_type_unknown_keys_are_reported_without_sort_crash():
    bad_weights = dict(DEFAULT_WEIGHTS)
    bad_weights[1] = 0.0
    bad_weights[(1,)] = 0.0
    with pytest.raises(TotalRasyoScoreError, match="anahtarlari tam"):
        normalize_weights(bad_weights)

    bad_scores = scores()
    bad_scores[1] = 0.0
    bad_scores[(1,)] = 0.0
    with pytest.raises(TotalRasyoScoreError, match="anahtarlari tam"):
        compute_total_rasyo(bad_scores, good_count_ge8=8)
