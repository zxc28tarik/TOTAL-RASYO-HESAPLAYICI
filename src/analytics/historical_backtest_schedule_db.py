from __future__ import annotations

"""V24-F — database-selected audited wage and PIT cutoff schedules.

The caller supplies only schedule/profile identities.  Historical values are
read from append-only registries; no fallback constants or inferred cutoffs are
allowed.  The selected provenance rows are returned alongside the V24-E run.
"""

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from src.analytics.historical_backtest_db import (
    HistoricalBacktestDatabaseError,
    HistoricalBacktestRun,
    run_monthly_backtest_from_database,
)
from src.analytics.historical_backtest_inputs import (
    HistoricalBacktestInputError,
    build_contributions,
    build_execution_calendar,
    build_signal_cutoffs,
)
from src.analytics.monthly_total_rasyo_portfolio import PortfolioConfig


@dataclass(frozen=True)
class RegisteredBacktestSchedules:
    minimum_wage: pd.DataFrame
    cutoffs: pd.DataFrame


@dataclass(frozen=True)
class RegisteredHistoricalBacktestRun:
    run: HistoricalBacktestRun
    schedules: RegisteredBacktestSchedules


def _key(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalBacktestDatabaseError(f"{name} dolu metin olmali")
    return value.strip()


def _month_window(start_month: str, end_month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    try:
        start = pd.Period(start_month, freq="M")
        end = pd.Period(end_month, freq="M")
    except Exception as exc:
        raise HistoricalBacktestDatabaseError("start_month/end_month YYYY-MM olmali") from exc
    if end < start:
        raise HistoricalBacktestDatabaseError("end_month start_month'tan once olamaz")
    return start.start_time.normalize(), (end + 1).start_time.normalize()


def _read_sql(conn: Any, query: str, params: tuple[object, ...]) -> pd.DataFrame:
    try:
        return pd.read_sql_query(query, conn, params=params)
    except Exception as exc:
        raise HistoricalBacktestDatabaseError(f"schedule database read failed: {exc}") from exc


def fetch_registered_backtest_schedules(
    conn: Any,
    *,
    wage_schedule_key: str,
    cutoff_profile_key: str,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
) -> RegisteredBacktestSchedules:
    """Fetch and validate the exact schedules for the requested backtest window."""
    wage_key = _key(wage_schedule_key, "wage_schedule_key")
    cutoff_key = _key(cutoff_profile_key, "cutoff_profile_key")
    start_date, end_exclusive = _month_window(start_month, end_month)

    index_prices = _read_sql(
        conn,
        """
        SELECT index_code, trade_date, open, close
          FROM core.index_prices_daily
         WHERE index_code = %s AND trade_date >= %s AND trade_date < %s
         ORDER BY trade_date
        """,
        (str(index_code).strip().upper(), start_date.date(), end_exclusive.date()),
    )
    try:
        calendar = build_execution_calendar(
            index_prices,
            start_month=start_month,
            end_month=end_month,
            index_code=index_code,
            expected_months=expected_months,
        )
    except HistoricalBacktestInputError as exc:
        raise HistoricalBacktestDatabaseError(str(exc)) from exc

    wages = _read_sql(
        conn,
        """
        SELECT schedule_key, valid_from, valid_to, net_min_wage,
               source, source_ref, source_sha256, row_sha256
          FROM core.backtest_minimum_wage_schedule
         WHERE schedule_key = %s
           AND valid_from < %s
           AND (valid_to IS NULL OR valid_to > %s)
         ORDER BY valid_from
        """,
        (wage_key, end_exclusive.date(), start_date.date()),
    )
    if wages.empty:
        raise HistoricalBacktestDatabaseError(
            f"minimum-wage schedule yok: {wage_key}"
        )

    signal_dates = [pd.Timestamp(x).date() for x in calendar["signal_date"]]
    cutoffs = _read_sql(
        conn,
        """
        SELECT profile_key, signal_date, cutoff_at, execution_at,
               source, source_ref, source_sha256, row_sha256
          FROM analytics.backtest_signal_cutoff_schedule
         WHERE profile_key = %s AND signal_date = ANY(%s)
         ORDER BY signal_date
        """,
        (cutoff_key, signal_dates),
    )
    if cutoffs.empty:
        raise HistoricalBacktestDatabaseError(
            f"signal cutoff schedule yok: {cutoff_key}"
        )
    if cutoffs["signal_date"].duplicated().any():
        raise HistoricalBacktestDatabaseError("duplicate registered signal_date")

    parsed_cutoff: list[pd.Timestamp] = []
    parsed_execution: list[pd.Timestamp] = []
    for row in cutoffs.itertuples(index=False):
        cutoff = pd.Timestamp(row.cutoff_at)
        execution = pd.Timestamp(row.execution_at)
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise HistoricalBacktestDatabaseError("registered cutoff_at timezone-aware olmali")
        if execution.tzinfo is None or execution.utcoffset() is None:
            raise HistoricalBacktestDatabaseError("registered execution_at timezone-aware olmali")
        if cutoff >= execution:
            raise HistoricalBacktestDatabaseError("registered cutoff_at execution_at'tan once olmali")
        day = pd.Timestamp(row.signal_date).date()
        if execution.tz_convert("Europe/Istanbul").date() != day:
            raise HistoricalBacktestDatabaseError(
                "registered execution_at Istanbul gunu signal_date ile uyusmuyor"
            )
        parsed_cutoff.append(cutoff)
        parsed_execution.append(execution)
    cutoffs = cutoffs.copy()
    cutoffs["cutoff_at"] = parsed_cutoff
    cutoffs["execution_at"] = parsed_execution

    try:
        # These calls deliberately reuse the V24-C contracts: interval gaps,
        # overlaps and missing monthly cutoffs remain fail-closed in one place.
        build_contributions(
            calendar,
            wages[["valid_from", "valid_to", "net_min_wage"]],
            multiplier=1.0,
        )
        build_signal_cutoffs(
            calendar,
            cutoffs[["signal_date", "cutoff_at"]],
        )
    except HistoricalBacktestInputError as exc:
        raise HistoricalBacktestDatabaseError(str(exc)) from exc

    return RegisteredBacktestSchedules(
        minimum_wage=wages.reset_index(drop=True),
        cutoffs=cutoffs.reset_index(drop=True),
    )


def run_monthly_backtest_with_registered_schedules(
    conn: Any,
    *,
    wage_schedule_key: str,
    cutoff_profile_key: str,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
    contribution_multiplier: float = 2.0,
    config: PortfolioConfig = PortfolioConfig(),
) -> RegisteredHistoricalBacktestRun:
    schedules = fetch_registered_backtest_schedules(
        conn,
        wage_schedule_key=wage_schedule_key,
        cutoff_profile_key=cutoff_profile_key,
        start_month=start_month,
        end_month=end_month,
        index_code=index_code,
        expected_months=expected_months,
    )
    run = run_monthly_backtest_from_database(
        conn,
        minimum_wage_schedule=schedules.minimum_wage[
            ["valid_from", "valid_to", "net_min_wage"]
        ],
        cutoffs=schedules.cutoffs[["signal_date", "cutoff_at"]],
        start_month=start_month,
        end_month=end_month,
        index_code=index_code,
        expected_months=expected_months,
        contribution_multiplier=contribution_multiplier,
        config=config,
    )
    return RegisteredHistoricalBacktestRun(run=run, schedules=schedules)
