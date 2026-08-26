from __future__ import annotations

"""Build the monthly signal-date skeleton without inventing cutoff policy.

Signal dates are the first observed index trading dates in the locked month
window.  The output deliberately contains no cutoff/execution timestamps: V24-F
requires those to come from an explicit, provenance-preserving policy source.
"""

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from src.analytics.historical_backtest_db import HistoricalBacktestDatabaseError
from src.analytics.historical_backtest_inventory import _read_sql, _signal_dates, _window


@dataclass(frozen=True)
class HistoricalSignalDateManifest:
    start_month: str
    end_month: str
    expected_months: int
    index_code: str
    rows: tuple[dict[str, object], ...]
    missing_months: tuple[str, ...]

    @property
    def status(self) -> str:
        return "COMPLETE_SIGNAL_DATES_POLICY_UNRESOLVED" if not self.missing_months else "BLOCKED_MISSING_SIGNAL_DATES"

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            self.rows,
            columns=[
                "month",
                "signal_date",
                "index_code",
                "cutoff_at",
                "execution_at",
                "cutoff_policy_status",
            ],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "start_month": self.start_month,
            "end_month": self.end_month,
            "expected_months": self.expected_months,
            "index_code": self.index_code,
            "signal_date_count": len(self.rows),
            "missing_months": list(self.missing_months),
            "cutoff_policy_status": "UNRESOLVED",
        }


def build_historical_signal_date_manifest(
    conn: Any,
    *,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
) -> HistoricalSignalDateManifest:
    start_date, end_exclusive, month_count = _window(start_month, end_month, expected_months)
    code = str(index_code).strip().upper()
    if not code:
        raise HistoricalBacktestDatabaseError("index_code dolu metin olmali")

    frame = _read_sql(
        conn,
        """
        SELECT index_code, trade_date, open, close
          FROM core.index_prices_daily
         WHERE index_code = %s
           AND trade_date >= %s
           AND trade_date < %s
         ORDER BY trade_date
        """,
        (code, start_date.date(), end_exclusive.date()),
    )
    signal_dates = _signal_dates(frame, code)
    by_month = {pd.Timestamp(day).to_period("M"): pd.Timestamp(day).date() for day in signal_dates}

    months = list(pd.period_range(start=start_month, end=end_month, freq="M"))
    missing = tuple(str(month) for month in months if month not in by_month)
    rows = tuple(
        {
            "month": str(month),
            "signal_date": by_month[month].isoformat(),
            "index_code": code,
            "cutoff_at": None,
            "execution_at": None,
            "cutoff_policy_status": "UNRESOLVED",
        }
        for month in months
        if month in by_month
    )
    if len(months) != month_count:
        raise HistoricalBacktestDatabaseError("internal month count mismatch")

    return HistoricalSignalDateManifest(
        start_month=start_month,
        end_month=end_month,
        expected_months=month_count,
        index_code=code,
        rows=rows,
        missing_months=missing,
    )
