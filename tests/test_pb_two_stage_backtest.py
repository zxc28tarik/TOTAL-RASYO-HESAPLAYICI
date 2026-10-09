from __future__ import annotations

"""The P/B two-stage backtest must report every variant, and its engine must add up."""

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.backtest_pb_two_stage import (
    RULES, Book, _sell, _trade_to, discontinuity_tickers, pb_percentile,
)

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "data/audit/pb_two_stage_backtest_v1/receipt.json"


def _receipt() -> dict:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


def test_the_frozen_rules_are_the_ones_the_run_used():
    receipt = _receipt()
    assert receipt["rules_frozen_before_first_run"] == json.loads(json.dumps(RULES))
    assert RULES["max_positions"] == 6
    assert RULES["cost_per_side"] == 0.002
    assert "1/3" in RULES["pb_very_good"]


def test_every_planned_variant_is_reported_not_just_the_best():
    results = _receipt()["results"]
    for name in ("aylik", "3_aylik", "6_aylik", "serbest"):
        assert name in results
        assert {"total_return", "xu100_return", "max_drawdown"} <= set(results[name])
    for reference in ("ref_sadece_skor_AL_aylik", "ref_sadece_ucuz_PDDD_aylik",
                      "ref_tum_evren_esit_agirlik_aylik"):
        assert reference in results


def test_the_receipt_says_what_it_consumed_and_what_it_left_out():
    receipt = _receipt()
    assert "holdout" in receipt["holdout_consumed"]
    assert receipt["p4_reproduction"].startswith("866/866")
    assert receipt["excluded_discontinuity_tickers"]
    assert any("rights issue" in item for item in receipt["not_modelled"])


def test_the_placebo_brackets_every_fixed_schedule_variant():
    placebo = _receipt()["placebo"]
    assert set(placebo) == {"aylik", "3_aylik", "6_aylik"}
    for result in placebo.values():
        assert 0.0 <= result["strategy_percentile_among_placebo"] <= 1.0
        assert result["placebo_p05"] < result["placebo_median"] < result["placebo_p95"]


def test_the_daily_engine_agrees_with_the_independent_gross_recomputation():
    receipt = _receipt()
    for name in ("aylik", "3_aylik", "6_aylik"):
        net = receipt["results"][name]["total_return"]
        gross = receipt["placebo"][name]["strategy_gross_return"]
        # Net may only trail gross, and only by what twelve rounds of costs can take.
        assert net <= gross + 1e-9
        assert gross - net < 0.05


def test_equal_weight_trade_conserves_value_net_of_the_fee():
    book = Book(cash=1.0, units={})
    prices = pd.Series({"A": 2.0, "B": 4.0})
    event = _trade_to(book, ["A", "B"], prices, cost=0.002)
    assert book.value(prices) + event["fee"] == pytest.approx(1.0)
    assert event["fee"] == pytest.approx(0.002 * event["traded"])
    assert book.units["A"] * 2.0 == pytest.approx(book.units["B"] * 4.0)
    # One fixed-point pass for the fee leaves a residue of order cost^2 in cash;
    # the engine documents it as well below a basis point, and that is the bound.
    assert book.cash == pytest.approx(0.0, abs=1e-4)


def test_moving_to_nothing_leaves_cash_minus_the_selling_fee():
    book = Book(cash=0.0, units={"A": 1.0})
    prices = pd.Series({"A": 10.0})
    _trade_to(book, [], prices, cost=0.002)
    assert book.units == {}
    assert book.cash == pytest.approx(10.0 * 0.998)


def test_a_partial_sale_pays_only_on_what_was_sold():
    book = Book(cash=0.0, units={"A": 1.0, "B": 1.0})
    prices = pd.Series({"A": 10.0, "B": 5.0})
    event = _sell(book, ["A"], prices, cost=0.002)
    assert event["fee"] == pytest.approx(0.02)
    assert book.units == {"B": 1.0}
    assert book.cash == pytest.approx(9.98)


def test_negative_book_value_can_never_count_as_cheap():
    pb = pd.Series([-3.0, 0.5, 0.8, 1.1, 1.6, 2.4, 3.0])
    ranked = pb_percentile(pb)
    assert pd.isna(ranked.iloc[0])
    assert ranked.iloc[1] == ranked.dropna().min()


def test_a_move_beyond_the_price_limit_is_flagged_and_a_normal_one_is_not():
    days = pd.bdate_range("2025-08-01", periods=4).date
    prices = pd.DataFrame({
        "ticker": ["SPLIT"] * 4 + ["NORMAL"] * 4,
        "trade_date": list(days) * 2,
        "close": [10.0, 10.2, 5.1, 5.0, 10.0, 9.1, 9.9, 9.0],
    })
    flagged = discontinuity_tickers(prices, first=days[0], last=days[-1])
    assert set(flagged) == {"SPLIT"}
    assert flagged["SPLIT"][0]["move"] == pytest.approx(-0.5)
