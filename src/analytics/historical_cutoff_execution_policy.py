from __future__ import annotations

"""Authorized PIT cutoff/execution policy for the 60-month Total Rasyo backtest.

This module does not infer market dates from weekdays or calendar days.  The
caller must provide the observed XU100 trading calendar and the already-closed
60 monthly signal dates.  The policy then derives one deterministic schedule:

* information cutoff: 18:10 Europe/Istanbul on the immediately preceding
  observed XU100 trading day;
* portfolio execution accounting timestamp: 10:00 Europe/Istanbul on the
  signal date;
* execution price basis: the already-authorized daily OPEN price.

The 10:00 timestamp is an accounting boundary for the opening price, not a
claim that an order is first submitted at exactly 10:00.  The signal is fixed
before the opening session because the cutoff is the prior trading-day close.
Information published after that cutoff is deliberately excluded even if it
would have been available before the next opening.  This conservative choice
removes unmodelled overnight ingestion/processing latency from the backtest.
"""

from dataclasses import dataclass
from datetime import time
from hashlib import sha256
import json
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd


ISTANBUL = ZoneInfo("Europe/Istanbul")
EXPECTED_START_MONTH = "2021-08"
EXPECTED_END_MONTH = "2026-07"
EXPECTED_MONTHS = 60


class HistoricalCutoffExecutionPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalCutoffExecutionPolicy:
    profile_key: str
    timezone: str
    cutoff_anchor: str
    cutoff_time: time
    execution_anchor: str
    execution_time: time
    execution_price_basis: str
    same_day_information_allowed: bool
    overnight_information_after_cutoff_allowed: bool
    authorized: bool

    def descriptor(self) -> dict[str, object]:
        return {
            "profile_key": self.profile_key,
            "timezone": self.timezone,
            "cutoff_anchor": self.cutoff_anchor,
            "cutoff_time": self.cutoff_time.strftime("%H:%M:%S"),
            "execution_anchor": self.execution_anchor,
            "execution_time": self.execution_time.strftime("%H:%M:%S"),
            "execution_price_basis": self.execution_price_basis,
            "same_day_information_allowed": self.same_day_information_allowed,
            "overnight_information_after_cutoff_allowed": self.overnight_information_after_cutoff_allowed,
            "authorized": self.authorized,
        }

    @property
    def descriptor_sha256(self) -> str:
        raw = json.dumps(
            self.descriptor(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(raw).hexdigest()


TOTAL_RASYO_MONTHLY_OPEN_V1 = HistoricalCutoffExecutionPolicy(
    profile_key="TOTAL_RASYO_MONTHLY_OPEN_V1",
    timezone="Europe/Istanbul",
    cutoff_anchor="PREVIOUS_XU100_TRADING_DAY_SESSION_END",
    cutoff_time=time(18, 10),
    execution_anchor="SIGNAL_DAY_OPENING_PRICE_ACCOUNTING_BOUNDARY",
    execution_time=time(10, 0),
    execution_price_basis="DAILY_OPEN",
    same_day_information_allowed=False,
    overnight_information_after_cutoff_allowed=False,
    authorized=True,
)


def _required(frame: pd.DataFrame, columns: Iterable[str], name: str) -> None:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise HistoricalCutoffExecutionPolicyError(
            f"{name} missing columns: {sorted(missing)}"
        )


def _normalize_trade_calendar(trading_calendar: pd.DataFrame) -> pd.DataFrame:
    _required(trading_calendar, {"trade_date"}, "trading_calendar")
    frame = trading_calendar.copy()
    if "index_code" in frame.columns:
        frame["index_code"] = frame["index_code"].astype(str).str.strip().str.upper()
        frame = frame.loc[frame["index_code"] == "XU100"].copy()
    if frame.empty:
        raise HistoricalCutoffExecutionPolicyError("XU100 trading calendar empty")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    if frame["trade_date"].duplicated().any():
        raise HistoricalCutoffExecutionPolicyError("duplicate XU100 trade_date")
    return frame[["trade_date"]].sort_values("trade_date").reset_index(drop=True)


def _normalize_signal_dates(signal_dates: pd.DataFrame) -> pd.DataFrame:
    _required(signal_dates, {"month", "signal_date", "index_code"}, "signal_dates")
    frame = signal_dates.copy()
    frame["month"] = frame["month"].astype(str).str.strip()
    frame["index_code"] = frame["index_code"].astype(str).str.strip().str.upper()
    if not frame["index_code"].eq("XU100").all():
        raise HistoricalCutoffExecutionPolicyError("signal dates must be XU100")
    expected = [
        str(x)
        for x in pd.period_range(
            EXPECTED_START_MONTH,
            EXPECTED_END_MONTH,
            freq="M",
        )
    ]
    if len(frame) != EXPECTED_MONTHS or frame["month"].tolist() != expected:
        raise HistoricalCutoffExecutionPolicyError(
            "signal dates must be the exact ordered 60-month 2021-08..2026-07 sequence"
        )
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="raise").dt.normalize()
    if frame["signal_date"].duplicated().any():
        raise HistoricalCutoffExecutionPolicyError("duplicate signal_date")
    return frame[["month", "signal_date", "index_code"]].reset_index(drop=True)


def _istanbul_timestamp(day: pd.Timestamp, clock: time) -> pd.Timestamp:
    return pd.Timestamp(
        year=day.year,
        month=day.month,
        day=day.day,
        hour=clock.hour,
        minute=clock.minute,
        second=clock.second,
        tz=ISTANBUL,
    )


def build_authorized_cutoff_execution_schedule(
    signal_dates: pd.DataFrame,
    trading_calendar: pd.DataFrame,
    *,
    policy: HistoricalCutoffExecutionPolicy = TOTAL_RASYO_MONTHLY_OPEN_V1,
) -> pd.DataFrame:
    """Build the exact authorized 60-row PIT cutoff/execution schedule."""
    if policy != TOTAL_RASYO_MONTHLY_OPEN_V1 or not policy.authorized:
        raise HistoricalCutoffExecutionPolicyError("unauthorized timing policy")

    signals = _normalize_signal_dates(signal_dates)
    calendar = _normalize_trade_calendar(trading_calendar)
    dates = calendar["trade_date"]

    rows: list[dict[str, object]] = []
    for row in signals.itertuples(index=False):
        signal_day = pd.Timestamp(row.signal_date).normalize()
        in_month = dates[dates.dt.to_period("M") == signal_day.to_period("M")]
        if in_month.empty or pd.Timestamp(in_month.iloc[0]) != signal_day:
            raise HistoricalCutoffExecutionPolicyError(
                f"{signal_day.date()} is not the first observed XU100 trading day of its month"
            )
        previous = dates[dates < signal_day]
        if previous.empty:
            raise HistoricalCutoffExecutionPolicyError(
                f"previous XU100 trading day missing for {signal_day.date()}"
            )
        previous_day = pd.Timestamp(previous.iloc[-1]).normalize()
        cutoff_at = _istanbul_timestamp(previous_day, policy.cutoff_time)
        execution_at = _istanbul_timestamp(signal_day, policy.execution_time)
        if cutoff_at >= execution_at:
            raise HistoricalCutoffExecutionPolicyError("cutoff_at must precede execution_at")
        rows.append(
            {
                "profile_key": policy.profile_key,
                "month": row.month,
                "signal_date": signal_day,
                "previous_trading_date": previous_day,
                "cutoff_at": cutoff_at,
                "execution_at": execution_at,
                "execution_price_basis": policy.execution_price_basis,
                "policy_status": "AUTHORIZED",
                "source": "TOTAL_RASYO_CUTOFF_EXECUTION_POLICY_V1",
                "source_ref": "docs/HISTORICAL_CUTOFF_EXECUTION_POLICY.md",
                "source_sha256": policy.descriptor_sha256,
            }
        )
    return pd.DataFrame(rows)


def validate_authorized_cutoff_execution_schedule(
    signal_dates: pd.DataFrame,
    trading_calendar: pd.DataFrame,
    schedule: pd.DataFrame,
    *,
    policy: HistoricalCutoffExecutionPolicy = TOTAL_RASYO_MONTHLY_OPEN_V1,
) -> pd.DataFrame:
    """Fail closed unless a supplied schedule is exactly the authorized policy."""
    expected = build_authorized_cutoff_execution_schedule(
        signal_dates,
        trading_calendar,
        policy=policy,
    )
    required = list(expected.columns)
    _required(schedule, required, "schedule")
    actual = schedule[required].copy().reset_index(drop=True)

    for column in ("signal_date", "previous_trading_date"):
        actual[column] = pd.to_datetime(actual[column], errors="raise").dt.normalize()
    for column in ("cutoff_at", "execution_at"):
        parsed: list[pd.Timestamp] = []
        for value in actual[column]:
            ts = pd.Timestamp(value)
            if ts.tzinfo is None or ts.utcoffset() is None:
                raise HistoricalCutoffExecutionPolicyError(
                    f"{column} must be timezone-aware"
                )
            parsed.append(ts.tz_convert(ISTANBUL))
        actual[column] = parsed

    if not actual.equals(expected):
        raise HistoricalCutoffExecutionPolicyError(
            "registered cutoff/execution schedule does not exactly match authorized policy"
        )
    return expected


def cutoff_execution_policy_evidence() -> dict[str, object]:
    policy = TOTAL_RASYO_MONTHLY_OPEN_V1
    return {
        **policy.descriptor(),
        "policy_descriptor_sha256": policy.descriptor_sha256,
        "expected_months": EXPECTED_MONTHS,
        "start_month": EXPECTED_START_MONTH,
        "end_month": EXPECTED_END_MONTH,
        "signal_date_source_status_required": "UNRESOLVED_SOURCE_FACTS_PRESERVED",
        "result": "PASS",
    }
