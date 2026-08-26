from __future__ import annotations

import gzip
from pathlib import Path

import pandas as pd
import pytest

from src.analytics.historical_cutoff_execution_policy import (
    HistoricalCutoffExecutionPolicyError,
    TOTAL_RASYO_MONTHLY_OPEN_V1,
    build_authorized_cutoff_execution_schedule,
    cutoff_execution_policy_evidence,
    validate_authorized_cutoff_execution_schedule,
)
from src.ingest.historical_backtest_schedules import cutoff_records_from_frame


ROOT = Path(__file__).resolve().parents[1]
SIGNAL_DATES = ROOT / "data/backtest_sources/xu100_signal_dates_yahoo_2021-08_2026-07.csv"
M3_INDEX_CLOSES = ROOT / "data/backtest_sources/m3_source_package/index_closes.csv.gz"


def _real_signal_dates() -> pd.DataFrame:
    return pd.read_csv(SIGNAL_DATES, dtype=str, keep_default_na=False)


def _real_xu100_calendar() -> pd.DataFrame:
    with gzip.open(M3_INDEX_CLOSES, "rt", encoding="utf-8", newline="") as handle:
        closes = pd.read_csv(handle, dtype=str, keep_default_na=False)
    return closes.loc[closes["index_code"] == "XU100", ["index_code", "trade_date"]].copy()


def _real_schedule() -> pd.DataFrame:
    return build_authorized_cutoff_execution_schedule(
        _real_signal_dates(),
        _real_xu100_calendar(),
    )


def test_authorized_policy_is_explicit_and_conservative():
    policy = TOTAL_RASYO_MONTHLY_OPEN_V1
    assert policy.profile_key == "TOTAL_RASYO_MONTHLY_OPEN_V1"
    assert policy.timezone == "Europe/Istanbul"
    assert policy.cutoff_anchor == "PREVIOUS_XU100_TRADING_DAY_SESSION_END"
    assert policy.cutoff_time.strftime("%H:%M:%S") == "18:10:00"
    assert policy.execution_anchor == "SIGNAL_DAY_OPENING_PRICE_ACCOUNTING_BOUNDARY"
    assert policy.execution_time.strftime("%H:%M:%S") == "10:00:00"
    assert policy.execution_price_basis == "DAILY_OPEN"
    assert policy.same_day_information_allowed is False
    assert policy.overnight_information_after_cutoff_allowed is False
    assert policy.authorized is True
    assert len(policy.descriptor_sha256) == 64


def test_real_60_month_schedule_is_derived_from_closed_sources():
    schedule = _real_schedule()
    assert len(schedule) == 60
    assert schedule["month"].tolist() == [
        str(x) for x in pd.period_range("2021-08", "2026-07", freq="M")
    ]
    assert schedule["profile_key"].eq("TOTAL_RASYO_MONTHLY_OPEN_V1").all()
    assert schedule["policy_status"].eq("AUTHORIZED").all()
    assert schedule["execution_price_basis"].eq("DAILY_OPEN").all()
    assert schedule["source_sha256"].eq(TOTAL_RASYO_MONTHLY_OPEN_V1.descriptor_sha256).all()

    first = schedule.iloc[0]
    assert first["signal_date"] == pd.Timestamp("2021-08-02")
    assert first["previous_trading_date"] == pd.Timestamp("2021-07-30")
    assert first["cutoff_at"] == pd.Timestamp("2021-07-30T18:10:00+03:00")
    assert first["execution_at"] == pd.Timestamp("2021-08-02T10:00:00+03:00")

    may_2022 = schedule.loc[schedule["month"] == "2022-05"].iloc[0]
    assert may_2022["signal_date"] == pd.Timestamp("2022-05-05")
    assert may_2022["previous_trading_date"] == pd.Timestamp("2022-04-29")
    assert may_2022["cutoff_at"] == pd.Timestamp("2022-04-29T18:10:00+03:00")
    assert may_2022["execution_at"] == pd.Timestamp("2022-05-05T10:00:00+03:00")


def test_original_signal_date_source_remains_unresolved_and_unmodified():
    source = _real_signal_dates()
    assert source["cutoff_at"].eq("").all()
    assert source["execution_at"].eq("").all()
    assert source["cutoff_policy_status"].eq("UNRESOLVED").all()


def test_authorized_schedule_is_compatible_with_append_only_registry_records():
    schedule = _real_schedule()
    records = cutoff_records_from_frame(schedule)
    assert len(records) == 60
    assert {row.profile_key for row in records} == {"TOTAL_RASYO_MONTHLY_OPEN_V1"}
    assert {row.source for row in records} == {"TOTAL_RASYO_CUTOFF_EXECUTION_POLICY_V1"}
    assert {row.source_sha256 for row in records} == {
        TOTAL_RASYO_MONTHLY_OPEN_V1.descriptor_sha256
    }


def test_mutated_cutoff_clock_is_rejected():
    expected = _real_schedule()
    mutated = expected.copy()
    mutated.loc[0, "cutoff_at"] = pd.Timestamp("2021-07-30T20:00:00+03:00")
    with pytest.raises(HistoricalCutoffExecutionPolicyError, match="exactly match"):
        validate_authorized_cutoff_execution_schedule(
            _real_signal_dates(), _real_xu100_calendar(), mutated
        )


def test_mutated_execution_clock_is_rejected():
    expected = _real_schedule()
    mutated = expected.copy()
    mutated.loc[0, "execution_at"] = pd.Timestamp("2021-08-02T09:55:00+03:00")
    with pytest.raises(HistoricalCutoffExecutionPolicyError, match="exactly match"):
        validate_authorized_cutoff_execution_schedule(
            _real_signal_dates(), _real_xu100_calendar(), mutated
        )


def test_same_day_information_cutoff_is_rejected_even_if_before_execution():
    expected = _real_schedule()
    mutated = expected.copy()
    mutated.loc[0, "previous_trading_date"] = pd.Timestamp("2021-08-02")
    mutated.loc[0, "cutoff_at"] = pd.Timestamp("2021-08-02T09:30:00+03:00")
    with pytest.raises(HistoricalCutoffExecutionPolicyError, match="exactly match"):
        validate_authorized_cutoff_execution_schedule(
            _real_signal_dates(), _real_xu100_calendar(), mutated
        )


def test_non_first_trading_day_signal_is_rejected():
    signals = _real_signal_dates()
    signals.loc[0, "signal_date"] = "2021-08-03"
    with pytest.raises(HistoricalCutoffExecutionPolicyError, match="not the first observed"):
        build_authorized_cutoff_execution_schedule(signals, _real_xu100_calendar())


def test_missing_previous_trading_day_fails_closed():
    calendar = _real_xu100_calendar()
    calendar = calendar.loc[calendar["trade_date"] >= "2021-08-02"].copy()
    with pytest.raises(HistoricalCutoffExecutionPolicyError, match="previous XU100 trading day missing"):
        build_authorized_cutoff_execution_schedule(_real_signal_dates(), calendar)


def test_machine_readable_policy_evidence_matches_contract():
    evidence = cutoff_execution_policy_evidence()
    assert evidence["authorized"] is True
    assert evidence["cutoff_time"] == "18:10:00"
    assert evidence["execution_time"] == "10:00:00"
    assert evidence["execution_price_basis"] == "DAILY_OPEN"
    assert evidence["expected_months"] == 60
    assert evidence["start_month"] == "2021-08"
    assert evidence["end_month"] == "2026-07"
    assert evidence["result"] == "PASS"
