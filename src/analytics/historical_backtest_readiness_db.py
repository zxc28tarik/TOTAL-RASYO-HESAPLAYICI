from __future__ import annotations

"""V24-G Part 3 — raw PostgreSQL snapshot feeding the report-only readiness audit.

Unlike the executable V24-E/V24-F bridges, this module must not fail early on
ordinary historical data gaps.  It reads the requested window, preserves the
raw registry/schedule rows, and lets ``audit_backtest_readiness_frames`` report
all month/category findings in one pass.

Only technical database failures and invalid caller configuration raise.  A
missing wage schedule, missing cutoff profile, missing benchmark month, empty
historical universe, missing exact-day price, or absent Total Rasyo authority
remains audit data and therefore becomes a readiness finding.
"""

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from src.analytics.historical_backtest_db import HistoricalBacktestDatabaseError
from src.analytics.historical_backtest_readiness import (
    BacktestReadinessReport,
    audit_backtest_readiness_frames,
)


_RESULT_COLUMNS = [
    "run_id",
    "analysis_at",
    "ticker",
    "final_score",
    "decision",
    "total_rasyo_status",
]
_PRICE_COLUMNS = ["ticker", "trade_date", "open", "close"]


@dataclass(frozen=True)
class DatabaseBacktestReadinessSnapshot:
    """Exact frames inspected by one database-backed readiness audit."""

    report: BacktestReadinessReport
    index_prices: pd.DataFrame
    membership: pd.DataFrame
    prices_daily: pd.DataFrame
    wages: pd.DataFrame
    cutoffs: pd.DataFrame
    total_rasyo_results: pd.DataFrame
    run_registry: pd.DataFrame


def _key(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalBacktestDatabaseError(f"{name} dolu metin olmali")
    return value.strip()


def _window(
    start_month: str,
    end_month: str,
    expected_months: Optional[int],
) -> tuple[pd.Timestamp, pd.Timestamp]:
    try:
        start = pd.Period(start_month, freq="M")
        end = pd.Period(end_month, freq="M")
    except Exception as exc:
        raise HistoricalBacktestDatabaseError("start_month/end_month YYYY-MM olmali") from exc
    if end < start:
        raise HistoricalBacktestDatabaseError("end_month start_month'tan once olamaz")
    count = int(end.ordinal - start.ordinal + 1)
    if expected_months is not None and count != int(expected_months):
        raise HistoricalBacktestDatabaseError(
            f"month window count mismatch: got={count} expected={int(expected_months)}"
        )
    return start.start_time.normalize(), (end + 1).start_time.normalize()


def _read_sql(conn: Any, query: str, params: tuple[object, ...]) -> pd.DataFrame:
    try:
        return pd.read_sql_query(query, conn, params=params)
    except Exception as exc:
        raise HistoricalBacktestDatabaseError(
            f"readiness database read failed: {exc}"
        ) from exc


def _signal_dates_from_index(
    index_prices: pd.DataFrame,
    *,
    start_date: pd.Timestamp,
    end_exclusive: pd.Timestamp,
    index_code: str,
) -> list[object]:
    if index_prices.empty or "trade_date" not in index_prices.columns:
        return []
    frame = index_prices.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame = frame[frame["trade_date"].notna()]
    if "index_code" in frame.columns:
        frame = frame[
            frame["index_code"].astype(str).str.strip().str.upper()
            == index_code
        ]
    frame = frame[
        (frame["trade_date"] >= start_date)
        & (frame["trade_date"] < end_exclusive)
    ]
    if frame.empty:
        return []
    frame = frame.assign(_month=frame["trade_date"].dt.to_period("M"))
    dates = frame.groupby("_month", sort=True)["trade_date"].min().tolist()
    return [pd.Timestamp(value).date() for value in dates]


def _candidate_tickers(membership: pd.DataFrame) -> list[str]:
    if membership.empty or "ticker" not in membership.columns:
        return []
    values = {
        str(value).strip().upper()
        for value in membership["ticker"].dropna()
        if str(value).strip()
    }
    return sorted(values)


def _authority_run_ids(run_registry: pd.DataFrame) -> list[str]:
    required = {"run_id", "overall_status", "persistence_status", "run_scope"}
    if run_registry.empty or not required.issubset(run_registry.columns):
        return []
    frame = run_registry.copy()
    scope = frame["run_scope"].astype(str).str.strip().str.upper()
    status = frame["overall_status"].astype(str).str.strip().str.upper()
    persistence = frame["persistence_status"].astype(str).str.strip().str.upper()
    mask = (
        scope.eq("FULL_UNIVERSE")
        & status.isin({"COMPLETE", "COMPLETE_NO_RESULTS"})
        & persistence.eq("OK")
    )
    values = {
        str(value).strip()
        for value in frame.loc[mask, "run_id"].dropna()
        if str(value).strip()
    }
    return sorted(values)


def audit_backtest_readiness_from_database(
    conn: Any,
    *,
    wage_schedule_key: str,
    cutoff_profile_key: str,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
) -> DatabaseBacktestReadinessSnapshot:
    """Read one raw historical snapshot and run the full readiness audit.

    This function deliberately avoids ``build_execution_calendar``,
    ``fetch_registered_backtest_schedules`` and executable backtest builders:
    those are fail-closed execution gates and would stop before later months can
    be audited.  Here ordinary data defects are preserved for the report.
    """
    wage_key = _key(wage_schedule_key, "wage_schedule_key")
    cutoff_key = _key(cutoff_profile_key, "cutoff_profile_key")
    index_code_norm = _key(index_code, "index_code").upper()
    start_date, end_exclusive = _window(start_month, end_month, expected_months)

    index_prices = _read_sql(
        conn,
        """
        SELECT index_code, trade_date, open, close
          FROM core.index_prices_daily
         WHERE index_code = %s
           AND trade_date >= %s
           AND trade_date < %s
         ORDER BY trade_date
        """,
        (index_code_norm, start_date.date(), end_exclusive.date()),
    )

    membership = _read_sql(
        conn,
        """
        SELECT ticker, valid_from, valid_to, is_tradable,
               company_name, sector_index_code, sector_code,
               source, source_ref, source_sha256, row_sha256
          FROM core.universe_membership_history
         WHERE valid_from < %s
           AND (valid_to IS NULL OR valid_to > %s)
         ORDER BY ticker, valid_from
        """,
        (end_exclusive.date(), start_date.date()),
    )

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

    cutoffs = _read_sql(
        conn,
        """
        SELECT profile_key, signal_date, cutoff_at, execution_at,
               source, source_ref, source_sha256, row_sha256
          FROM analytics.backtest_signal_cutoff_schedule
         WHERE profile_key = %s
           AND signal_date >= %s
           AND signal_date < %s
         ORDER BY signal_date
        """,
        (cutoff_key, start_date.date(), end_exclusive.date()),
    )

    signal_dates = _signal_dates_from_index(
        index_prices,
        start_date=start_date,
        end_exclusive=end_exclusive,
        index_code=index_code_norm,
    )
    tickers = _candidate_tickers(membership)
    if signal_dates and tickers:
        prices_daily = _read_sql(
            conn,
            """
            SELECT ticker, trade_date, open, close
              FROM core.prices_daily
             WHERE trade_date = ANY(%s)
               AND ticker = ANY(%s)
             ORDER BY trade_date, ticker
            """,
            (signal_dates, tickers),
        )
    else:
        prices_daily = pd.DataFrame(columns=_PRICE_COLUMNS)

    end_exclusive_utc = (
        end_exclusive.tz_localize("Europe/Istanbul").tz_convert("UTC").to_pydatetime()
    )
    run_registry = _read_sql(
        conn,
        """
        SELECT run_id, analysis_at, overall_status, persistence_status,
               run_scope, company_count, universe_company_count
          FROM analytics.total_rasyo_run
         WHERE analysis_at < %s
         ORDER BY analysis_at, run_id
        """,
        (end_exclusive_utc,),
    )

    run_ids = _authority_run_ids(run_registry)
    if run_ids:
        total_rasyo_results = _read_sql(
            conn,
            """
            SELECT run_id, analysis_at, ticker, final_score,
                   decision, total_rasyo_status
              FROM analytics.company_total_rasyo_result
             WHERE run_id = ANY(%s)
             ORDER BY analysis_at, ticker
            """,
            (run_ids,),
        )
    else:
        total_rasyo_results = pd.DataFrame(columns=_RESULT_COLUMNS)

    report = audit_backtest_readiness_frames(
        index_prices=index_prices,
        membership=membership,
        prices_daily=prices_daily,
        wages=wages,
        cutoffs=cutoffs,
        total_rasyo_results=total_rasyo_results,
        run_registry=run_registry,
        start_month=start_month,
        end_month=end_month,
        index_code=index_code_norm,
        expected_months=expected_months,
    )

    return DatabaseBacktestReadinessSnapshot(
        report=report,
        index_prices=index_prices.reset_index(drop=True),
        membership=membership.reset_index(drop=True),
        prices_daily=prices_daily.reset_index(drop=True),
        wages=wages.reset_index(drop=True),
        cutoffs=cutoffs.reset_index(drop=True),
        total_rasyo_results=total_rasyo_results.reset_index(drop=True),
        run_registry=run_registry.reset_index(drop=True),
    )
