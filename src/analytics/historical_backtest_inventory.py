from __future__ import annotations

"""Read-only discovery for the real historical backtest input set.

This module does not make a readiness decision and never mutates historical
state.  Its job is to answer the operator question that V24-G intentionally
does not answer: which audited schedule/profile keys exist, and which broad
input families already have enough coverage to justify running the keyed
V24-G readiness gate.
"""

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.analytics.historical_backtest_db import HistoricalBacktestDatabaseError


@dataclass(frozen=True)
class HistoricalBacktestInventory:
    start_month: str
    end_month: str
    expected_months: int
    index_code: str
    benchmark: dict[str, object]
    universe: dict[str, object]
    prices: dict[str, object]
    wage_schedules: tuple[dict[str, object], ...]
    cutoff_profiles: tuple[dict[str, object], ...]
    total_rasyo: dict[str, object]
    hard_blockers: tuple[str, ...]
    candidate_wage_schedule_keys: tuple[str, ...]
    candidate_cutoff_profile_keys: tuple[str, ...]

    @property
    def status(self) -> str:
        if self.hard_blockers:
            return "BLOCKED"
        if len(self.candidate_wage_schedule_keys) != 1 or len(self.candidate_cutoff_profile_keys) != 1:
            return "KEY_SELECTION_REQUIRED"
        return "CANDIDATE_READY_FOR_V24G"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "start_month": self.start_month,
            "end_month": self.end_month,
            "expected_months": self.expected_months,
            "index_code": self.index_code,
            "benchmark": self.benchmark,
            "universe": self.universe,
            "prices": self.prices,
            "wage_schedules": list(self.wage_schedules),
            "cutoff_profiles": list(self.cutoff_profiles),
            "total_rasyo": self.total_rasyo,
            "hard_blockers": list(self.hard_blockers),
            "candidate_wage_schedule_keys": list(self.candidate_wage_schedule_keys),
            "candidate_cutoff_profile_keys": list(self.candidate_cutoff_profile_keys),
            "requires_v24g_readiness_audit": True,
        }


def _window(start_month: str, end_month: str, expected_months: Optional[int]) -> tuple[pd.Timestamp, pd.Timestamp, int]:
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
    return start.start_time.normalize(), (end + 1).start_time.normalize(), count


def _read_sql(conn: Any, query: str, params: tuple[object, ...]) -> pd.DataFrame:
    try:
        return pd.read_sql_query(query, conn, params=params)
    except Exception as exc:
        raise HistoricalBacktestDatabaseError(f"inventory database read failed: {exc}") from exc


def _iso_date(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def _iso_timestamp(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).isoformat()


def _signal_dates(index_prices: pd.DataFrame, index_code: str) -> list[pd.Timestamp]:
    if index_prices.empty:
        return []
    frame = index_prices.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame = frame[frame["trade_date"].notna()]
    if "index_code" in frame.columns:
        frame = frame[
            frame["index_code"].astype(str).str.strip().str.upper().eq(index_code)
        ]
    if frame.empty:
        return []
    frame = frame.assign(_month=frame["trade_date"].dt.to_period("M"))
    return [pd.Timestamp(v).normalize() for v in frame.groupby("_month", sort=True)["trade_date"].min().tolist()]


def _active_tickers(membership: pd.DataFrame, signal_date: pd.Timestamp) -> set[str]:
    if membership.empty:
        return set()
    frame = membership.copy()
    frame["valid_from"] = pd.to_datetime(frame["valid_from"], errors="coerce").dt.normalize()
    frame["valid_to"] = pd.to_datetime(frame["valid_to"], errors="coerce").dt.normalize()
    tradable = frame["is_tradable"].fillna(False).astype(bool)
    mask = (
        tradable
        & frame["valid_from"].notna()
        & (frame["valid_from"] <= signal_date)
        & (frame["valid_to"].isna() | (signal_date < frame["valid_to"]))
    )
    return {
        str(v).strip().upper()
        for v in frame.loc[mask, "ticker"].dropna()
        if str(v).strip()
    }


def _covers_window(group: pd.DataFrame, start_date: pd.Timestamp, end_exclusive: pd.Timestamp) -> bool:
    if group.empty:
        return False
    rows = group.copy()
    rows["valid_from"] = pd.to_datetime(rows["valid_from"], errors="coerce").dt.normalize()
    rows["valid_to"] = pd.to_datetime(rows["valid_to"], errors="coerce").dt.normalize()
    rows = rows[rows["valid_from"].notna()].sort_values("valid_from")
    cursor = start_date
    for row in rows.itertuples(index=False):
        left = pd.Timestamp(row.valid_from).normalize()
        right = end_exclusive if pd.isna(row.valid_to) else pd.Timestamp(row.valid_to).normalize()
        if right <= cursor:
            continue
        if left > cursor:
            return False
        if right > cursor:
            cursor = right
        if cursor >= end_exclusive:
            return True
    return cursor >= end_exclusive


def _valid_price_pairs(prices: pd.DataFrame) -> tuple[set[tuple[pd.Timestamp, str]], int]:
    if prices.empty:
        return set(), 0
    frame = prices.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
    frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    duplicate_count = int(frame.duplicated(["trade_date", "ticker"], keep=False).sum())
    valid = (
        frame["trade_date"].notna()
        & frame["ticker"].ne("")
        & np.isfinite(frame["open"])
        & np.isfinite(frame["close"])
        & frame["open"].gt(0)
        & frame["close"].gt(0)
    )
    pairs = {
        (pd.Timestamp(row.trade_date).normalize(), str(row.ticker))
        for row in frame.loc[valid, ["trade_date", "ticker"]].itertuples(index=False)
    }
    return pairs, duplicate_count


def inventory_backtest_database(
    conn: Any,
    *,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
) -> HistoricalBacktestInventory:
    start_date, end_exclusive, month_count = _window(start_month, end_month, expected_months)
    index_code_norm = str(index_code).strip().upper()
    if not index_code_norm:
        raise HistoricalBacktestDatabaseError("index_code dolu metin olmali")

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
    signals = _signal_dates(index_prices, index_code_norm)

    membership = _read_sql(
        conn,
        """
        SELECT ticker, valid_from, valid_to, is_tradable
          FROM core.universe_membership_history
         WHERE valid_from < %s
           AND (valid_to IS NULL OR valid_to > %s)
         ORDER BY ticker, valid_from
        """,
        (end_exclusive.date(), start_date.date()),
    )

    candidate_tickers = sorted({
        ticker
        for signal in signals
        for ticker in _active_tickers(membership, signal)
    })
    if signals and candidate_tickers:
        prices = _read_sql(
            conn,
            """
            SELECT ticker, trade_date, open, close
              FROM core.prices_daily
             WHERE trade_date = ANY(%s)
               AND ticker = ANY(%s)
             ORDER BY trade_date, ticker
            """,
            ([d.date() for d in signals], candidate_tickers),
        )
    else:
        prices = pd.DataFrame(columns=["ticker", "trade_date", "open", "close"])

    wages = _read_sql(
        conn,
        """
        SELECT schedule_key, valid_from, valid_to, net_min_wage,
               source, source_ref, source_sha256, row_sha256
          FROM core.backtest_minimum_wage_schedule
         WHERE valid_from < %s
           AND (valid_to IS NULL OR valid_to > %s)
         ORDER BY schedule_key, valid_from
        """,
        (end_exclusive.date(), start_date.date()),
    )
    cutoffs = _read_sql(
        conn,
        """
        SELECT profile_key, signal_date, cutoff_at, execution_at,
               source, source_ref, source_sha256, row_sha256
          FROM analytics.backtest_signal_cutoff_schedule
         WHERE signal_date >= %s
           AND signal_date < %s
         ORDER BY profile_key, signal_date
        """,
        (start_date.date(), end_exclusive.date()),
    )

    end_utc = end_exclusive.tz_localize("Europe/Istanbul").tz_convert("UTC").to_pydatetime()
    total_rasyo = _read_sql(
        conn,
        """
        SELECT COUNT(*) AS authority_run_count,
               MIN(analysis_at) AS first_analysis_at,
               MAX(analysis_at) AS last_analysis_at
          FROM analytics.total_rasyo_run
         WHERE analysis_at < %s
           AND run_scope = 'FULL_UNIVERSE'
           AND overall_status IN ('COMPLETE','COMPLETE_NO_RESULTS')
           AND persistence_status = 'OK'
        """,
        (end_utc,),
    )

    signal_set = {d.date() for d in signals}
    execution_ticker_pairs = {
        (signal, ticker)
        for signal in signals
        for ticker in _active_tickers(membership, signal)
    }
    valid_pairs, duplicate_price_rows = _valid_price_pairs(prices)
    valid_execution_pairs = {
        (signal, ticker)
        for signal, ticker in execution_ticker_pairs
        if (signal, ticker) in valid_pairs
    }

    wage_rows: list[dict[str, object]] = []
    candidate_wages: list[str] = []
    if not wages.empty:
        for key, group in wages.groupby("schedule_key", sort=True):
            key_text = str(key).strip()
            covers = _covers_window(group, start_date, end_exclusive)
            if covers:
                candidate_wages.append(key_text)
            wage_rows.append({
                "schedule_key": key_text,
                "row_count": int(len(group)),
                "first_valid_from": _iso_date(group["valid_from"].min()),
                "last_valid_to": None if group["valid_to"].isna().any() else _iso_date(group["valid_to"].max()),
                "covers_full_window": bool(covers),
            })

    cutoff_rows: list[dict[str, object]] = []
    candidate_cutoffs: list[str] = []
    if not cutoffs.empty:
        work = cutoffs.copy()
        work["signal_date"] = pd.to_datetime(work["signal_date"], errors="coerce").dt.date
        for key, group in work.groupby("profile_key", sort=True):
            key_text = str(key).strip()
            dates = {d for d in group["signal_date"].dropna()}
            missing = sorted(signal_set - dates)
            extra = sorted(dates - signal_set)
            if not missing and len(signal_set) == month_count:
                candidate_cutoffs.append(key_text)
            cutoff_rows.append({
                "profile_key": key_text,
                "row_count": int(len(group)),
                "signal_date_count": int(len(dates)),
                "matched_signal_date_count": int(len(signal_set & dates)),
                "missing_signal_date_count": int(len(missing)),
                "extra_signal_date_count": int(len(extra)),
                "first_signal_date": None if not dates else min(dates).isoformat(),
                "last_signal_date": None if not dates else max(dates).isoformat(),
            })

    active_months = sum(1 for d in signals if _active_tickers(membership, d))
    authority_row = total_rasyo.iloc[0] if not total_rasyo.empty else None
    authority_count = 0 if authority_row is None or pd.isna(authority_row.get("authority_run_count")) else int(authority_row["authority_run_count"])

    blockers: list[str] = []
    if len(signals) != month_count:
        blockers.append("BENCHMARK_MONTH_COVERAGE")
    if active_months != month_count:
        blockers.append("UNIVERSE_MONTH_COVERAGE")
    if len(valid_execution_pairs) != len(execution_ticker_pairs) or duplicate_price_rows:
        blockers.append("PRICE_EXECUTION_COVERAGE")
    if not candidate_wages:
        blockers.append("WAGE_SCHEDULE_COVERAGE")
    if not candidate_cutoffs:
        blockers.append("CUTOFF_PROFILE_COVERAGE")
    if authority_count == 0:
        blockers.append("TOTAL_RASYO_AUTHORITY_ABSENT")

    index_dates = pd.to_datetime(index_prices["trade_date"], errors="coerce") if not index_prices.empty else pd.Series(dtype="datetime64[ns]")
    universe_tickers = {
        str(v).strip().upper() for v in membership.get("ticker", pd.Series(dtype=object)).dropna() if str(v).strip()
    }

    return HistoricalBacktestInventory(
        start_month=start_month,
        end_month=end_month,
        expected_months=month_count,
        index_code=index_code_norm,
        benchmark={
            "row_count": int(len(index_prices)),
            "covered_months": int(len(signals)),
            "first_trade_date": None if index_dates.empty else _iso_date(index_dates.min()),
            "last_trade_date": None if index_dates.empty else _iso_date(index_dates.max()),
        },
        universe={
            "interval_count": int(len(membership)),
            "ticker_count": int(len(universe_tickers)),
            "months_with_nonempty_universe": int(active_months),
            "execution_ticker_pairs": int(len(execution_ticker_pairs)),
        },
        prices={
            "rows_loaded_for_execution_dates": int(len(prices)),
            "expected_execution_ticker_pairs": int(len(execution_ticker_pairs)),
            "valid_execution_ticker_pairs": int(len(valid_execution_pairs)),
            "missing_or_invalid_execution_ticker_pairs": int(len(execution_ticker_pairs) - len(valid_execution_pairs)),
            "duplicate_price_rows": int(duplicate_price_rows),
        },
        wage_schedules=tuple(wage_rows),
        cutoff_profiles=tuple(cutoff_rows),
        total_rasyo={
            "authority_run_count_before_window_end": authority_count,
            "first_analysis_at": None if authority_row is None else _iso_timestamp(authority_row.get("first_analysis_at")),
            "last_analysis_at": None if authority_row is None else _iso_timestamp(authority_row.get("last_analysis_at")),
            "note": "Authority count is discovery only; month-by-month PIT coverage is decided by V24-G after schedule/profile selection.",
        },
        hard_blockers=tuple(blockers),
        candidate_wage_schedule_keys=tuple(candidate_wages),
        candidate_cutoff_profile_keys=tuple(candidate_cutoffs),
    )
