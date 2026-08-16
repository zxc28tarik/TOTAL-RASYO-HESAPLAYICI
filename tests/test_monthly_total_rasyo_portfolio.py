import pandas as pd
import pytest

from src.analytics.monthly_total_rasyo_portfolio import (
    MonthlyPortfolioError,
    MonthlyTotalRasyoSimulator,
    PortfolioConfig,
    benchmark_dca,
)


def _frames(signals, prices, contributions):
    signal_cols = ["signal_date", "ticker", "final_score", "decision"]
    price_cols = ["trade_date", "ticker", "open", "close"]
    contribution_cols = ["signal_date", "contribution"]
    return (
        pd.DataFrame(signals, columns=signal_cols),
        pd.DataFrame(prices, columns=price_cols),
        pd.DataFrame(contributions, columns=contribution_cols),
    )


def _run(signals, prices, contributions, max_positions=2):
    sim = MonthlyTotalRasyoSimulator(PortfolioConfig(max_positions=max_positions))
    s, p, c = _frames(signals, prices, contributions)
    trades, monthly = sim.run(s, p, c)
    return sim, trades, monthly


def test_no_forced_position_cash_stays_when_no_al():
    sim, trades, monthly = _run(
        [{"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .6, "decision": "IZLE"}],
        [],
        [{"signal_date": "2022-01-03", "contribution": 100}],
    )
    assert sim.positions == {}
    assert sim.cash == 100
    assert trades.empty
    assert monthly.iloc[0].nav == 100


def test_contribution_month_with_zero_signal_rows_is_not_dropped():
    signals = pd.DataFrame(columns=["signal_date", "ticker", "final_score", "decision"])
    prices = pd.DataFrame(columns=["trade_date", "ticker", "open", "close"])
    contributions = pd.DataFrame([
        {"signal_date": "2022-01-03", "contribution": 100},
        {"signal_date": "2022-02-01", "contribution": 150},
    ])
    sim = MonthlyTotalRasyoSimulator()
    trades, monthly = sim.run(signals, prices, contributions)
    assert trades.empty
    assert list(monthly["contribution"]) == [100, 150]
    assert list(monthly["cumulative_contribution"]) == [100, 250]
    assert sim.cash == 250


def test_buy_only_al_and_never_exceed_max_positions():
    signals = [
        {"signal_date": "2022-01-03", "ticker": t, "final_score": score, "decision": decision}
        for t, score, decision in [
            ("AAA", .90, "AL"), ("BBB", .80, "AL"), ("CCC", .70, "AL"), ("DDD", .99, "IZLE")
        ]
    ]
    prices = [
        {"trade_date": "2022-01-03", "ticker": t, "open": 10, "close": 10}
        for t in ["AAA", "BBB", "CCC", "DDD"]
    ]
    sim, trades, _ = _run(signals, prices, [{"signal_date": "2022-01-03", "contribution": 200}], 2)
    assert set(sim.positions) == {"AAA", "BBB"}
    assert len(sim.positions) == 2
    assert set(trades["decision"]) == {"AL"}


def test_izle_is_held_but_receives_no_new_cash():
    sim, trades, _ = _run(
        [
            {"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .9, "decision": "AL"},
            {"signal_date": "2022-02-01", "ticker": "AAA", "final_score": .6, "decision": "IZLE"},
        ],
        [
            {"trade_date": "2022-01-03", "ticker": "AAA", "open": 10, "close": 10},
            {"trade_date": "2022-02-01", "ticker": "AAA", "open": 10, "close": 10},
        ],
        [
            {"signal_date": "2022-01-03", "contribution": 100},
            {"signal_date": "2022-02-01", "contribution": 100},
        ],
        1,
    )
    assert sim.positions["AAA"].shares == 10
    assert sim.cash == 100
    assert (trades["side"] == "BUY").sum() == 1


def test_uzak_sells_and_sale_proceeds_join_same_month_cash_pool():
    sim, trades, _ = _run(
        [
            {"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .9, "decision": "AL"},
            {"signal_date": "2022-02-01", "ticker": "AAA", "final_score": .4, "decision": "UZAK"},
            {"signal_date": "2022-02-01", "ticker": "BBB", "final_score": .8, "decision": "AL"},
        ],
        [
            {"trade_date": "2022-01-03", "ticker": "AAA", "open": 10, "close": 10},
            {"trade_date": "2022-02-01", "ticker": "AAA", "open": 12, "close": 12},
            {"trade_date": "2022-02-01", "ticker": "BBB", "open": 20, "close": 20},
        ],
        [
            {"signal_date": "2022-01-03", "contribution": 100},
            {"signal_date": "2022-02-01", "contribution": 20},
        ],
        1,
    )
    # 10 AAA * 12 + 20 contribution = 140 -> 7 BBB at 20.
    assert "AAA" not in sim.positions
    assert sim.positions["BBB"].shares == 7
    assert sim.cash == 0
    assert list(trades["reason"])[-2:] == ["UZAK", "NEW_AL"]


def test_stronger_new_al_rotates_weakest_holding():
    sim, trades, _ = _run(
        [
            {"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .75, "decision": "AL"},
            {"signal_date": "2022-02-01", "ticker": "AAA", "final_score": .70, "decision": "AL"},
            {"signal_date": "2022-02-01", "ticker": "BBB", "final_score": .90, "decision": "AL"},
        ],
        [
            {"trade_date": "2022-01-03", "ticker": "AAA", "open": 10, "close": 10},
            {"trade_date": "2022-02-01", "ticker": "AAA", "open": 11, "close": 11},
            {"trade_date": "2022-02-01", "ticker": "BBB", "open": 10, "close": 10},
        ],
        [
            {"signal_date": "2022-01-03", "contribution": 100},
            {"signal_date": "2022-02-01", "contribution": 0},
        ],
        1,
    )
    assert set(sim.positions) == {"BBB"}
    assert "ROTATE_TO_STRONGER_AL" in set(trades["reason"])


def test_equal_score_ticker_tiebreak_can_rotate_incumbent():
    # score DESC then ticker ASC means AAA outranks BBB at equal score.
    sim, trades, _ = _run(
        [
            {"signal_date": "2022-01-03", "ticker": "BBB", "final_score": .80, "decision": "AL"},
            {"signal_date": "2022-02-01", "ticker": "BBB", "final_score": .80, "decision": "AL"},
            {"signal_date": "2022-02-01", "ticker": "AAA", "final_score": .80, "decision": "AL"},
        ],
        [
            {"trade_date": "2022-01-03", "ticker": "BBB", "open": 10, "close": 10},
            {"trade_date": "2022-02-01", "ticker": "BBB", "open": 10, "close": 10},
            {"trade_date": "2022-02-01", "ticker": "AAA", "open": 10, "close": 10},
        ],
        [
            {"signal_date": "2022-01-03", "contribution": 100},
            {"signal_date": "2022-02-01", "contribution": 0},
        ],
        1,
    )
    assert set(sim.positions) == {"AAA"}
    assert trades.iloc[-2]["reason"] == "ROTATE_TO_STRONGER_AL"


def test_integer_lots_leave_cash():
    sim, _, _ = _run(
        [{"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .9, "decision": "AL"}],
        [{"trade_date": "2022-01-03", "ticker": "AAA", "open": 30, "close": 30}],
        [{"signal_date": "2022-01-03", "contribution": 100}],
        1,
    )
    assert sim.positions["AAA"].shares == 3
    assert sim.cash == 10


def test_equal_cash_slice_does_not_recycle_expensive_name_residue():
    sim, _, _ = _run(
        [
            {"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .9, "decision": "AL"},
            {"signal_date": "2022-01-03", "ticker": "BBB", "final_score": .8, "decision": "AL"},
        ],
        [
            {"trade_date": "2022-01-03", "ticker": "AAA", "open": 60, "close": 60},
            {"trade_date": "2022-01-03", "ticker": "BBB", "open": 10, "close": 10},
        ],
        [{"signal_date": "2022-01-03", "contribution": 100}],
        2,
    )
    # Fixed 50/50 slices: AAA cannot buy one share; BBB gets exactly 5 shares.
    assert "AAA" not in sim.positions
    assert sim.positions["BBB"].shares == 5
    assert sim.cash == 50


def test_missing_signal_does_not_silently_sell_held_name():
    sim, _, _ = _run(
        [{"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .9, "decision": "AL"}],
        [
            {"trade_date": "2022-01-03", "ticker": "AAA", "open": 10, "close": 10},
            {"trade_date": "2022-02-01", "ticker": "AAA", "open": 11, "close": 11},
        ],
        [
            {"signal_date": "2022-01-03", "contribution": 100},
            {"signal_date": "2022-02-01", "contribution": 0},
        ],
        2,
    )
    assert "AAA" in sim.positions


def test_execution_uses_open_and_mark_uses_close():
    sim, trades, monthly = _run(
        [{"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .9, "decision": "AL"}],
        [{"trade_date": "2022-01-03", "ticker": "AAA", "open": 20, "close": 100}],
        [{"signal_date": "2022-01-03", "contribution": 100}],
        1,
    )
    assert sim.positions["AAA"].shares == 5
    assert trades.iloc[0]["price"] == 20
    assert monthly.iloc[0]["holdings_value"] == 500


def test_al_with_missing_score_is_rejected():
    with pytest.raises(MonthlyPortfolioError, match="AL satirinda final_score"):
        _run(
            [{"signal_date": "2022-01-03", "ticker": "AAA", "final_score": None, "decision": "AL"}],
            [{"trade_date": "2022-01-03", "ticker": "AAA", "open": 10, "close": 10}],
            [{"signal_date": "2022-01-03", "contribution": 100}],
        )


def test_negative_contribution_is_rejected():
    signals = pd.DataFrame(columns=["signal_date", "ticker", "final_score", "decision"])
    prices = pd.DataFrame(columns=["trade_date", "ticker", "open", "close"])
    contributions = pd.DataFrame([{"signal_date": "2022-01-03", "contribution": -1}])
    with pytest.raises(MonthlyPortfolioError, match="contribution"):
        MonthlyTotalRasyoSimulator().run(signals, prices, contributions)



def test_zero_cash_new_al_does_not_require_price_or_force_position():
    sim, trades, monthly = _run(
        [{"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .9, "decision": "AL"}],
        [],
        [{"signal_date": "2022-01-03", "contribution": 0}],
        1,
    )
    assert sim.positions == {}
    assert trades.empty
    assert monthly.iloc[0]["nav"] == 0


def test_max_positions_above_six_is_rejected():
    with pytest.raises(MonthlyPortfolioError, match="1 ile 6"):
        PortfolioConfig(max_positions=7)


def test_benchmark_uses_same_cashflows_and_open_execution():
    out = benchmark_dca(
        pd.DataFrame([
            {"signal_date": "2022-01-03", "contribution": 100},
            {"signal_date": "2022-02-01", "contribution": 120},
        ]),
        pd.DataFrame([
            {"trade_date": "2022-01-03", "open": 10, "close": 11},
            {"trade_date": "2022-02-01", "open": 20, "close": 22},
        ]),
    )
    # 10 units first month, +6 second month = 16 units, marked at 22.
    assert out.iloc[-1]["cumulative_contribution"] == 220
    assert out.iloc[-1]["units"] == 16
    assert out.iloc[-1]["benchmark_value"] == 352
