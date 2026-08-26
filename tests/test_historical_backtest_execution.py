from __future__ import annotations

import pandas as pd

from src.analytics.historical_backtest_execution import execute_backtest_bundle
from src.analytics.historical_backtest_inputs import BacktestInputBundle
from src.analytics.monthly_total_rasyo_portfolio import PortfolioConfig


def _bundle() -> BacktestInputBundle:
    calendar = pd.DataFrame([
        {"month": "2022-01", "signal_date": "2022-01-03", "index_code": "XU100", "benchmark_open": 100, "benchmark_close": 100},
        {"month": "2022-02", "signal_date": "2022-02-01", "index_code": "XU100", "benchmark_open": 100, "benchmark_close": 100},
    ])
    contributions = pd.DataFrame([
        {"signal_date": "2022-01-03", "contribution": 100.0},
        {"signal_date": "2022-02-01", "contribution": 0.0},
    ])
    monthly_universe = pd.DataFrame([
        {"signal_date": "2022-01-03", "ticker": "OLD"},
        {"signal_date": "2022-02-01", "ticker": "NEW"},
    ])
    signals = pd.DataFrame([
        {"signal_date": "2022-01-03", "ticker": "OLD", "final_score": .9, "decision": "AL"},
        {"signal_date": "2022-02-01", "ticker": "NEW", "final_score": .4, "decision": "UZAK"},
    ])
    signal_audit = pd.DataFrame()
    prices = pd.DataFrame([
        {"trade_date": "2022-01-03", "ticker": "OLD", "open": 10.0, "close": 10.0},
        {"trade_date": "2022-02-01", "ticker": "NEW", "open": 5.0, "close": 5.0},
    ])
    benchmark_prices = pd.DataFrame([
        {"trade_date": "2022-01-03", "open": 100.0, "close": 100.0},
        {"trade_date": "2022-02-01", "open": 100.0, "close": 100.0},
    ])
    return BacktestInputBundle(
        calendar=calendar,
        contributions=contributions,
        monthly_universe=monthly_universe,
        signals=signals,
        signal_audit=signal_audit,
        prices=prices,
        benchmark_prices=benchmark_prices,
    )


def test_bridge_passes_ticker_changes_and_corporate_actions_and_returns_audits():
    run = execute_backtest_bundle(
        _bundle(),
        corporate_actions=pd.DataFrame([
            {"action_date": "2022-02-01", "ticker": "NEW", "split_factor": 2.0, "cash_dividend_per_share": 1.0},
        ]),
        ticker_changes=pd.DataFrame([
            {"effective_date": "2022-02-01", "old_ticker": "OLD", "new_ticker": "NEW"},
        ]),
        config=PortfolioConfig(max_positions=1),
    )

    assert len(run.trades) == 2
    assert list(run.trades["side"]) == ["BUY", "SELL"]
    assert run.trades.iloc[-1].ticker == "NEW"
    assert run.trades.iloc[-1].shares == 20
    assert run.monthly.iloc[-1].nav == 120.0
    assert run.corporate_action_audit.iloc[0].cash_dividend == 20.0
    assert run.ticker_change_audit.iloc[0].old_ticker == "OLD"
    assert run.ticker_change_audit.iloc[0].new_ticker == "NEW"
    assert run.benchmark.iloc[-1].benchmark_value == 100.0


def test_bridge_without_events_preserves_legacy_monthly_execution_semantics():
    bundle = _bundle()
    # Without the OLD->NEW migration, the old position remains an incumbent and
    # February must fail on missing held OLD price.  This protects against the
    # bridge silently fabricating ticker continuity.
    import pytest
    from src.analytics.monthly_total_rasyo_portfolio import MonthlyPortfolioError

    with pytest.raises(MonthlyPortfolioError, match="missing close price for held OLD"):
        execute_backtest_bundle(bundle, config=PortfolioConfig(max_positions=1))
