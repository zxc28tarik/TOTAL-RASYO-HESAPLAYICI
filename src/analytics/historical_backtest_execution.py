from __future__ import annotations

"""Event-aware execution bridge for a validated historical backtest bundle.

V24-C/V24-E are responsible for constructing PIT-safe signals, the historical
universe, exact monthly execution prices, benchmark prices and contributions.
This module is the only bridge that should execute that bundle when verified
corporate actions and Borsa ticker-code changes are available.

It intentionally does not discover or infer events.  Event frames are optional;
when supplied they are validated by ``MonthlyTotalRasyoSimulator`` and applied
chronologically.  The returned audit frames make every applied non-trade event
observable to the caller and to the final backtest evidence pack.
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from src.analytics.historical_backtest_inputs import BacktestInputBundle
from src.analytics.monthly_total_rasyo_portfolio import (
    MonthlyTotalRasyoSimulator,
    PortfolioConfig,
    benchmark_dca,
)


@dataclass(frozen=True)
class EventAwareHistoricalBacktestRun:
    inputs: BacktestInputBundle
    trades: pd.DataFrame
    monthly: pd.DataFrame
    benchmark: pd.DataFrame
    corporate_action_audit: pd.DataFrame
    ticker_change_audit: pd.DataFrame


def _event_frame(events: list[object], columns: list[str]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([event.__dict__ for event in events], columns=columns)


def execute_backtest_bundle(
    bundle: BacktestInputBundle,
    *,
    corporate_actions: Optional[pd.DataFrame] = None,
    ticker_changes: Optional[pd.DataFrame] = None,
    config: PortfolioConfig = PortfolioConfig(),
) -> EventAwareHistoricalBacktestRun:
    """Execute one already-validated PIT-safe bundle with optional verified events."""
    if not isinstance(bundle, BacktestInputBundle):
        raise TypeError("bundle BacktestInputBundle olmali")

    simulator = MonthlyTotalRasyoSimulator(config)
    trades, monthly = simulator.run(
        bundle.signals,
        bundle.prices,
        bundle.contributions,
        corporate_actions=corporate_actions,
        ticker_changes=ticker_changes,
    )
    benchmark = benchmark_dca(bundle.contributions, bundle.benchmark_prices)

    corporate_action_audit = _event_frame(
        simulator.corporate_action_events,
        [
            "date", "ticker", "shares_before", "shares_after", "split_factor",
            "cash_dividend_per_share", "cash_dividend",
        ],
    )
    ticker_change_audit = _event_frame(
        simulator.ticker_change_events,
        ["date", "old_ticker", "new_ticker", "shares", "cost_basis"],
    )

    return EventAwareHistoricalBacktestRun(
        inputs=bundle,
        trades=trades,
        monthly=monthly,
        benchmark=benchmark,
        corporate_action_audit=corporate_action_audit,
        ticker_change_audit=ticker_change_audit,
    )
