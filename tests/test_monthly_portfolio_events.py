from __future__ import annotations

import pandas as pd
import pytest

from src.analytics.monthly_total_rasyo_portfolio import (
    MonthlyPortfolioError,
    MonthlyTotalRasyoSimulator,
    PortfolioConfig,
)


def _signals(rows):
    return pd.DataFrame(rows, columns=["signal_date", "ticker", "final_score", "decision"])


def _prices(rows):
    return pd.DataFrame(rows, columns=["trade_date", "ticker", "open", "close"])


def _contributions(rows):
    return pd.DataFrame(rows, columns=["signal_date", "contribution"])


def _actions(rows):
    return pd.DataFrame(rows, columns=[
        "action_date", "ticker", "split_factor", "cash_dividend_per_share"
    ])


def _changes(rows):
    return pd.DataFrame(rows, columns=["effective_date", "old_ticker", "new_ticker"])


def test_split_between_months_changes_shares_not_cost_basis():
    sim = MonthlyTotalRasyoSimulator(PortfolioConfig(max_positions=1))
    trades, monthly = sim.run(
        _signals([
            {"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .9, "decision": "AL"},
            {"signal_date": "2022-02-01", "ticker": "AAA", "final_score": .6, "decision": "IZLE"},
        ]),
        _prices([
            {"trade_date": "2022-01-03", "ticker": "AAA", "open": 10, "close": 10},
            {"trade_date": "2022-02-01", "ticker": "AAA", "open": 5, "close": 5},
        ]),
        _contributions([
            {"signal_date": "2022-01-03", "contribution": 100},
            {"signal_date": "2022-02-01", "contribution": 0},
        ]),
        corporate_actions=_actions([
            {"action_date": "2022-01-15", "ticker": "AAA", "split_factor": 2.0, "cash_dividend_per_share": 0.0},
        ]),
    )

    assert sim.positions["AAA"].shares == 20
    assert sim.positions["AAA"].cost_basis == 100
    assert sim.positions["AAA"].avg_cost == 5
    assert monthly.iloc[-1].holdings_value == 100
    assert len(sim.corporate_action_events) == 1
    event = sim.corporate_action_events[0]
    assert event.shares_before == 10
    assert event.shares_after == 20
    assert trades.shape[0] == 1


def test_cash_dividend_between_months_enters_cash_before_next_rebalance():
    sim = MonthlyTotalRasyoSimulator(PortfolioConfig(max_positions=1))
    _, monthly = sim.run(
        _signals([
            {"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .9, "decision": "AL"},
            {"signal_date": "2022-02-01", "ticker": "AAA", "final_score": .6, "decision": "IZLE"},
        ]),
        _prices([
            {"trade_date": "2022-01-03", "ticker": "AAA", "open": 10, "close": 10},
            {"trade_date": "2022-02-01", "ticker": "AAA", "open": 10, "close": 10},
        ]),
        _contributions([
            {"signal_date": "2022-01-03", "contribution": 100},
            {"signal_date": "2022-02-01", "contribution": 0},
        ]),
        corporate_actions=_actions([
            {"action_date": "2022-01-15", "ticker": "AAA", "split_factor": 0.0, "cash_dividend_per_share": 1.5},
        ]),
    )

    assert sim.cash == 15
    assert monthly.iloc[-1].cash == 15
    assert sim.corporate_action_events[0].cash_dividend == 15


def test_same_day_ex_dividend_does_not_pay_new_open_purchase():
    sim = MonthlyTotalRasyoSimulator(PortfolioConfig(max_positions=1))
    sim.run(
        _signals([
            {"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .9, "decision": "AL"},
        ]),
        _prices([
            {"trade_date": "2022-01-03", "ticker": "AAA", "open": 10, "close": 10},
        ]),
        _contributions([
            {"signal_date": "2022-01-03", "contribution": 100},
        ]),
        corporate_actions=_actions([
            {"action_date": "2022-01-03", "ticker": "AAA", "split_factor": 0.0, "cash_dividend_per_share": 1.0},
        ]),
    )

    assert sim.positions["AAA"].shares == 10
    assert sim.cash == 0
    # No entering position existed when ex-date event was applied.
    assert sim.corporate_action_events == []


def test_fractional_split_requires_explicit_cash_in_lieu_contract():
    sim = MonthlyTotalRasyoSimulator(PortfolioConfig(max_positions=1))
    with pytest.raises(MonthlyPortfolioError, match="cash-in-lieu"):
        sim.run(
            _signals([
                {"signal_date": "2022-01-03", "ticker": "AAA", "final_score": .9, "decision": "AL"},
                {"signal_date": "2022-02-01", "ticker": "AAA", "final_score": .6, "decision": "IZLE"},
            ]),
            _prices([
                {"trade_date": "2022-01-03", "ticker": "AAA", "open": 30, "close": 30},
                {"trade_date": "2022-02-01", "ticker": "AAA", "open": 60, "close": 60},
            ]),
            _contributions([
                {"signal_date": "2022-01-03", "contribution": 100},
                {"signal_date": "2022-02-01", "contribution": 0},
            ]),
            corporate_actions=_actions([
                {"action_date": "2022-01-15", "ticker": "AAA", "split_factor": 0.5, "cash_dividend_per_share": 0.0},
            ]),
        )


def test_ticker_change_migrates_held_position_without_trade():
    sim = MonthlyTotalRasyoSimulator(PortfolioConfig(max_positions=1))
    trades, monthly = sim.run(
        _signals([
            {"signal_date": "2022-01-03", "ticker": "OLD", "final_score": .9, "decision": "AL"},
            {"signal_date": "2022-02-01", "ticker": "NEW", "final_score": .6, "decision": "IZLE"},
        ]),
        _prices([
            {"trade_date": "2022-01-03", "ticker": "OLD", "open": 10, "close": 10},
            {"trade_date": "2022-02-01", "ticker": "NEW", "open": 11, "close": 11},
        ]),
        _contributions([
            {"signal_date": "2022-01-03", "contribution": 100},
            {"signal_date": "2022-02-01", "contribution": 0},
        ]),
        ticker_changes=_changes([
            {"effective_date": "2022-01-15", "old_ticker": "OLD", "new_ticker": "NEW"},
        ]),
    )

    assert set(sim.positions) == {"NEW"}
    assert sim.positions["NEW"].shares == 10
    assert sim.positions["NEW"].cost_basis == 100
    assert len(trades) == 1
    assert trades.iloc[0].side == "BUY"
    assert len(sim.ticker_change_events) == 1
    assert sim.ticker_change_events[0].old_ticker == "OLD"
    assert sim.ticker_change_events[0].new_ticker == "NEW"
    assert monthly.iloc[-1].holdings == "NEW"


def test_same_day_ticker_change_precedes_split_dividend_and_sale():
    sim = MonthlyTotalRasyoSimulator(PortfolioConfig(max_positions=1))
    trades, monthly = sim.run(
        _signals([
            {"signal_date": "2022-01-03", "ticker": "OLD", "final_score": .9, "decision": "AL"},
            {"signal_date": "2022-02-01", "ticker": "NEW", "final_score": .4, "decision": "UZAK"},
        ]),
        _prices([
            {"trade_date": "2022-01-03", "ticker": "OLD", "open": 10, "close": 10},
            {"trade_date": "2022-02-01", "ticker": "NEW", "open": 5, "close": 5},
        ]),
        _contributions([
            {"signal_date": "2022-01-03", "contribution": 100},
            {"signal_date": "2022-02-01", "contribution": 0},
        ]),
        corporate_actions=_actions([
            {"action_date": "2022-02-01", "ticker": "NEW", "split_factor": 2.0, "cash_dividend_per_share": 1.0},
        ]),
        ticker_changes=_changes([
            {"effective_date": "2022-02-01", "old_ticker": "OLD", "new_ticker": "NEW"},
        ]),
    )

    # 10 OLD -> 10 NEW -> split to 20 NEW -> 20 dividend cash -> sell 20 @ 5.
    assert sim.positions == {}
    assert sim.cash == 120
    assert monthly.iloc[-1].nav == 120
    assert sim.corporate_action_events[0].shares_after == 20
    assert sim.corporate_action_events[0].cash_dividend == 20
    assert trades.iloc[-1].ticker == "NEW"
    assert trades.iloc[-1].shares == 20
    assert trades.iloc[-1].reason == "UZAK"


def test_ticker_change_destination_collision_fails_closed():
    sim = MonthlyTotalRasyoSimulator(PortfolioConfig(max_positions=2))
    with pytest.raises(MonthlyPortfolioError, match="destination already held"):
        sim.run(
            _signals([
                {"signal_date": "2022-01-03", "ticker": "OLD", "final_score": .9, "decision": "AL"},
                {"signal_date": "2022-01-03", "ticker": "NEW", "final_score": .8, "decision": "AL"},
                {"signal_date": "2022-02-01", "ticker": "NEW", "final_score": .7, "decision": "IZLE"},
            ]),
            _prices([
                {"trade_date": "2022-01-03", "ticker": "OLD", "open": 10, "close": 10},
                {"trade_date": "2022-01-03", "ticker": "NEW", "open": 10, "close": 10},
                {"trade_date": "2022-02-01", "ticker": "NEW", "open": 10, "close": 10},
            ]),
            _contributions([
                {"signal_date": "2022-01-03", "contribution": 200},
                {"signal_date": "2022-02-01", "contribution": 0},
            ]),
            ticker_changes=_changes([
                {"effective_date": "2022-01-15", "old_ticker": "OLD", "new_ticker": "NEW"},
            ]),
        )


def test_duplicate_and_noop_corporate_actions_are_rejected():
    empty_signals = _signals([])
    empty_prices = _prices([])
    contrib = _contributions([{"signal_date": "2022-01-03", "contribution": 0}])

    with pytest.raises(MonthlyPortfolioError, match="duplicate corporate action"):
        MonthlyTotalRasyoSimulator().run(
            empty_signals,
            empty_prices,
            contrib,
            corporate_actions=_actions([
                {"action_date": "2022-01-03", "ticker": "AAA", "split_factor": 2, "cash_dividend_per_share": 0},
                {"action_date": "2022-01-03", "ticker": "AAA", "split_factor": 0, "cash_dividend_per_share": 1},
            ]),
        )

    with pytest.raises(MonthlyPortfolioError, match="no-op"):
        MonthlyTotalRasyoSimulator().run(
            empty_signals,
            empty_prices,
            contrib,
            corporate_actions=_actions([
                {"action_date": "2022-01-03", "ticker": "AAA", "split_factor": 1, "cash_dividend_per_share": 0},
            ]),
        )
