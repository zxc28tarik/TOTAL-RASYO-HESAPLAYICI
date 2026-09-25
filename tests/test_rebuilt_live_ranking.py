from __future__ import annotations

"""The published rebuilt ranking must say what it is and what it is not."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "data/live/rebuilt_total_scores_v1"


def _receipt() -> dict:
    return json.loads((DIRECTORY / "receipt.json").read_text(encoding="utf-8"))


def _ranking() -> list[dict]:
    return [json.loads(line) for line in
            (DIRECTORY / "ranking.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_rebuilt_ranking_carries_no_veto_and_equal_weights():
    receipt = _receipt()
    assert receipt["veto"] is None
    assert set(receipt["weights"].values()) == {0.2}
    assert receipt["core_modules"] == ["M1", "M3", "Ek4", "Ek1", "Ek9"]


def test_m2_is_published_as_an_overlay_but_kept_out_of_the_score():
    receipt = _receipt()
    assert "M2" not in receipt["core_modules"]
    assert receipt["m2_role"].startswith("OVERLAY_PUBLISHED_NOT_SCORED")
    assert any(row["m2_overlay"] is not None for row in _ranking())


def test_it_makes_no_predictive_claim():
    assert _receipt()["predictive_claim"] == "NONE_R2_FOUND_NO_DEFENSIBLE_FORWARD_SKILL"


def test_the_design_direction_of_ek1_is_kept_rather_than_flipped_to_suit_the_sample():
    assert _receipt()["ek1_direction"] == "AS_DESIGNED_NOT_FLIPPED_DESPITE_NEGATIVE_R2_SIGN"


def test_clipped_modules_are_ranked_on_raw_production_inputs():
    receipt = _receipt()
    recovered = receipt["raw_inputs_recovered_from_production_replays"]
    assert any("alpha_trailing" in item for item in recovered)
    assert any("excess_return_20d" in item for item in recovered)
    assert any("volatility" in item for item in recovered)
    # The reason it matters: the old M3 pinned a large share to exactly zero.
    assert receipt["old_m3_share_pinned_at_zero"] > 0.3


def test_the_ranking_inhabits_the_whole_scale_and_is_ordered():
    ranking = _ranking()
    scores = [row["total_rasyo_100"] for row in ranking]
    assert max(scores) == 100.0
    assert min(scores) < 5.0
    assert scores == sorted(scores, reverse=True)
    assert [row["rank"] for row in ranking] == list(range(1, len(ranking) + 1))


def test_every_old_company_is_still_scored_and_more_are_added():
    receipt = _receipt()
    assert receipt["common_ticker_count"] == receipt["old_scored_count"]
    assert receipt["scored_count"] > receipt["old_scored_count"]


def test_al_is_roughly_the_share_the_band_policy_promises():
    receipt = _receipt()
    share = receipt["decision_counts"]["AL"] / receipt["scored_count"]
    assert abs(share - receipt["bands"]["al_share"]) < 0.02
