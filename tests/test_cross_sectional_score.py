from __future__ import annotations

"""The percentile mapping must have no constants, no clipping, and no invention."""

import pandas as pd
import pytest

from src.analytics.cross_sectional_score import (
    CrossSectionalScoreError, clipping_share, percentile_score,
    percentile_score_by_cohort,
)


def test_a_cohort_spreads_across_the_whole_range_without_clipping():
    scores = percentile_score(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert list(scores) == [0.2, 0.4, 0.6, 0.8, 1.0]
    assert clipping_share(scores)["at_low"] == 0.0


def test_the_mapping_is_invariant_to_any_monotone_rescaling_of_its_input():
    raw = pd.Series([-0.9, -0.2, 0.0, 0.35, 4.0])
    base = percentile_score(raw)
    for transform in (lambda s: s * 1000, lambda s: s + 12.5, lambda s: s ** 3):
        assert list(percentile_score(transform(raw))) == list(base)


def test_missing_values_stay_missing_rather_than_being_ranked():
    scores = percentile_score(pd.Series([1.0, None, 3.0, 4.0, 5.0, 6.0]))
    assert pd.isna(scores.iloc[1])
    assert scores.dropna().max() == 1.0


def test_a_cohort_too_small_to_rank_returns_nothing_instead_of_a_confident_spread():
    for values in ([1.0, 2.0], [1.0, 2.0, 3.0, 4.0]):
        assert percentile_score(pd.Series(values)).isna().all()


def test_a_cohort_with_one_distinct_value_is_refused_not_split():
    assert percentile_score(pd.Series([2.0] * 8)).isna().all()


def test_genuine_ties_share_a_rank():
    scores = percentile_score(pd.Series([1.0, 1.0, 1.0, 5.0, 9.0]))
    assert scores.iloc[0] == scores.iloc[1] == scores.iloc[2]
    assert scores.iloc[4] == 1.0


def test_cohorts_are_ranked_independently_of_one_another():
    frame = pd.DataFrame({
        "month": ["2024-01"] * 5 + ["2024-02"] * 5,
        # February's values are an order of magnitude larger, which must not
        # leak into January's ranks or vice versa.
        "raw": [1, 2, 3, 4, 5, 100, 200, 300, 400, 500],
    })
    scores = percentile_score_by_cohort(frame, value_column="raw", cohort_columns=["month"])
    assert list(scores[:5]) == list(scores[5:]) == [0.2, 0.4, 0.6, 0.8, 1.0]


def test_a_cohort_may_be_narrowed_to_a_peer_group():
    frame = pd.DataFrame({
        "month": ["2024-01"] * 10,
        "sector": ["A"] * 5 + ["B"] * 5,
        "raw": [1, 2, 3, 4, 5, 50, 60, 70, 80, 90],
    })
    scores = percentile_score_by_cohort(
        frame, value_column="raw", cohort_columns=["month", "sector"])
    assert list(scores[:5]) == list(scores[5:]) == [0.2, 0.4, 0.6, 0.8, 1.0]


def test_missing_columns_and_empty_cohorts_are_refused_loudly():
    frame = pd.DataFrame({"month": ["2024-01"] * 5, "raw": [1, 2, 3, 4, 5]})
    with pytest.raises(CrossSectionalScoreError, match="eksik kolonlar"):
        percentile_score_by_cohort(frame, value_column="absent", cohort_columns=["month"])
    with pytest.raises(CrossSectionalScoreError, match="bos olamaz"):
        percentile_score_by_cohort(frame, value_column="raw", cohort_columns=[])


def test_clipping_share_measures_what_a_band_pins_and_ranking_undoes():
    banded = pd.Series([0.0, 0.0, 0.0, 0.4, 0.7, 1.0])
    measured = clipping_share(banded)
    assert measured["at_low"] == pytest.approx(0.5)
    assert measured["at_high"] == pytest.approx(1 / 6)
    ranked = percentile_score(pd.Series([-0.9, -0.5, -0.3, 0.4, 0.7, 2.0]))
    assert clipping_share(ranked)["at_low"] == 0.0
