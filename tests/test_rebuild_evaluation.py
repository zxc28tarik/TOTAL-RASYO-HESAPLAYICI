from __future__ import annotations

"""The holdout must stay shut, and the baseline must stay a measurement."""

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.rebuild_evaluation import (
    HOLDOUT, HOLDOUT_UNLOCK_TOKEN, IN_SAMPLE, EvaluationSplitError,
    discrimination, forward_returns, months_of, scale_usage, select_split,
)
from scripts.audit_r0_model_baseline import PRODUCTION_WEIGHTS, check


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "data/audit/r0_model_baseline_v1/receipt.json"


def _rows():
    return [{"month": m, "ticker": "X"} for m in
            ("2021-08", "2023-01", "2024-07", "2024-08", "2025-06", "2026-07")]


def test_the_two_splits_do_not_overlap_and_meet_end_to_end():
    assert IN_SAMPLE.last_month < HOLDOUT.first_month
    for month in ("2021-08", "2024-07"):
        assert IN_SAMPLE.contains(month) and not HOLDOUT.contains(month)
    for month in ("2024-08", "2026-07"):
        assert HOLDOUT.contains(month) and not IN_SAMPLE.contains(month)


def test_reading_the_holdout_without_the_token_is_refused():
    with pytest.raises(EvaluationSplitError, match="HOLDOUT_LOCKED"):
        select_split(_rows(), split=HOLDOUT)


def test_a_wrong_token_is_also_refused():
    for token in ("", "please", HOLDOUT_UNLOCK_TOKEN.lower(), HOLDOUT_UNLOCK_TOKEN + "!"):
        with pytest.raises(EvaluationSplitError):
            select_split(_rows(), split=HOLDOUT, unlock=token)


def test_the_explicit_token_opens_the_holdout_and_only_the_holdout():
    opened = select_split(_rows(), split=HOLDOUT, unlock=HOLDOUT_UNLOCK_TOKEN)
    assert [row["month"] for row in opened] == ["2024-08", "2025-06", "2026-07"]


def test_in_sample_needs_no_token():
    assert [row["month"] for row in select_split(_rows(), split=IN_SAMPLE)] == [
        "2021-08", "2023-01", "2024-07"]
    assert months_of(IN_SAMPLE, [row["month"] for row in _rows()]) == [
        "2021-08", "2023-01", "2024-07"]


def test_forward_return_looks_forward_and_drops_the_final_month():
    prices = pd.DataFrame([
        {"trade_date": "2024-01-02", "ticker": "A", "open": 10.0},
        {"trade_date": "2024-02-01", "ticker": "A", "open": 12.0},
        {"trade_date": "2024-03-01", "ticker": "A", "open": 9.0},
    ])
    frame = forward_returns(prices, ["2024-01-02", "2024-02-01", "2024-03-01"])
    got = {row.month: round(row.forward_return, 6) for row in frame.itertuples()}
    # January earns February's move, February earns March's, March has no successor.
    assert got == {"2024-01": 0.2, "2024-02": -0.25}


def test_discrimination_reports_a_negative_gap_when_the_score_is_inverted():
    frame = pd.DataFrame({
        "score": [10, 20, 30, 40, 50, 60],
        "ret": [0.9, 0.7, 0.5, -0.1, -0.3, -0.9],
    })
    result = discrimination(frame, score_column="score", return_column="ret", extreme_n=2)
    assert result["top_minus_bottom_score_gap"] < 0
    assert result["spearman_pooled"] < 0


def test_scale_usage_detects_a_score_crammed_into_a_band():
    crammed = scale_usage([48.0, 49.0, 50.0, 51.0, 52.0])
    assert crammed["observed_span_share_of_scale"] < 0.05
    wide = scale_usage([2.0, 25.0, 50.0, 75.0, 98.0])
    assert wide["observed_span_share_of_scale"] > 0.9


def test_the_baseline_records_all_four_defects_without_reading_the_holdout():
    receipt = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert receipt["purpose"] == "IMMUTABLE_PRE_REBUILD_MEASUREMENT_NOT_A_MODEL_CHANGE"
    assert receipt["evaluation_split"]["holdout_read_here"] is False
    assert receipt["pre_rebuild_parameters"]["weights"] == PRODUCTION_WEIGHTS

    live = receipt["six_module_live_snapshot"]
    # 1: the valuation axis inverts the score.
    decomposition = live["weighted_contribution_decomposition"]
    assert decomposition["verdict"] == "VALUATION_AXIS_INVERTS_THE_SCORE"
    assert decomposition["net_weighted_gap"] < 0
    assert decomposition["valuation_cancels_momentum_by"] > 1.0
    # 3: the veto is not selective.
    assert live["veto"]["verdict"] == "THRESHOLD_AT_MEDIAN_PENALTY_IS_NOT_SELECTIVE"
    assert live["veto"]["vetoed_share"] > 0.4
    # 4: the scale is not inhabited and AL is nearly unreachable.
    assert live["scale_usage"]["observed_span_share_of_scale"] < 0.7
    assert live["decision_counts"]["AL"] <= 5

    # 2: no forward predictive power.
    forward = receipt["five_module_in_sample_forward"]["forward_discrimination"]
    assert forward["month_count"] == 36
    assert abs(forward["spearman_monthly_mean"]) < 0.10
    assert forward["spearman_monthly_positive_share"] < 0.65


def test_baseline_sources_still_match_their_recorded_hashes():
    assert check()["status"] == "R0_CHECK_PASS"
