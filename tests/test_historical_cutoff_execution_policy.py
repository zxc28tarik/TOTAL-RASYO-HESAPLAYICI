from __future__ import annotations

from dataclasses import replace
from datetime import time
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


def test_authorized_policy_is_explicit_conservative_and_half_day_aware():
    policy = TOTAL_RASYO_MONTHLY_OPEN_V1
    assert policy.profile_key == "TOTAL_RASYO_MONTHLY_OPEN_V1"
    assert policy.timezone == "Europe/Istanbul"
    assert policy.cutoff_anchor == "PREVIOUS_XU100_TRADING_SESSION_END"
    assert policy.full_day_session_end.strftime("%H:%M:%S") == "18:10:00"
    assert policy.half_day_session_end.strftime("%H:%M:%S") == "12:40:00"
    assert policy.half_day_cutoff_dates == (
        "2021-10-28",
        "2023-06-27",
        "2026-05-26",
    )
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
    assert schedule["session_type"].value_counts().to_dict() == {
        "FULL_DAY": 57,
        "HALF_DAY": 3,
    }

    first = schedule.iloc[0]
    assert first["signal_date"] == pd.Timestamp("2021-08-02")
    assert first["previous_trading_date"] == pd.Timestamp("2021-07-30")
    assert first["session_type"] == "FULL_DAY"
    assert first["cutoff_at"] == pd.Timestamp("2021-07-30T18:10:00+03:00")
    assert first["execution_at"] == pd.Timestamp("2021-08-02T10:00:00+03:00")

    may_2022 = schedule.loc[schedule["month"] == "2022-05"].iloc[0]
    assert may_2022["signal_date"] == pd.Timestamp("2022-05-05")
    assert may_2022["previous_trading_date"] == pd.Timestamp("2022-04-29")
    assert may_2022["session_type"] == "FULL_DAY"
    assert may_2022["cutoff_at"] == pd.Timestamp("2022-04-29T18:10:00+03:00")
    assert may_2022["execution_at"] == pd.Timestamp("2022-05-05T10:00:00+03:00")


def test_three_relevant_half_day_predecessors_use_1240_session_end():
    schedule = _real_schedule().set_index("month")
    expected = {
        "2021-11": ("2021-10-28", "2021-10-28T12:40:00+03:00"),
        "2023-07": ("2023-06-27", "2023-06-27T12:40:00+03:00"),
        "2026-06": ("2026-05-26", "2026-05-26T12:40:00+03:00"),
    }
    for month, (previous_day, cutoff_at) in expected.items():
        row = schedule.loc[month]
        assert row["previous_trading_date"] == pd.Timestamp(previous_day)
        assert row["session_type"] == "HALF_DAY"
        assert row["cutoff_at"] == pd.Timestamp(cutoff_at)
        assert row["execution_at"].hour == 10
        assert row["execution_at"].minute == 0


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


def test_mutated_full_day_cutoff_clock_is_rejected():
    expected = _real_schedule()
    mutated = expected.copy()
    mutated.loc[0, "cutoff_at"] = pd.Timestamp("2021-07-30T20:00:00+03:00")
    with pytest.raises(HistoricalCutoffExecutionPolicyError, match="exactly match"):
        validate_authorized_cutoff_execution_schedule(
            _real_signal_dates(), _real_xu100_calendar(), mutated
        )


def test_mutated_half_day_cutoff_to_full_day_clock_is_rejected():
    expected = _real_schedule()
    index = expected.index[expected["month"] == "2021-11"][0]
    mutated = expected.copy()
    mutated.loc[index, "cutoff_at"] = pd.Timestamp("2021-10-28T18:10:00+03:00")
    mutated.loc[index, "session_type"] = "FULL_DAY"
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


@pytest.mark.parametrize("mutation", ["remove", "reorder"])
def test_signal_month_sequence_mutations_fail_closed(mutation: str):
    signals = _real_signal_dates()
    if mutation == "remove":
        signals = signals.drop(index=17).reset_index(drop=True)
    else:
        order = list(range(len(signals)))
        order[17], order[18] = order[18], order[17]
        signals = signals.iloc[order].reset_index(drop=True)

    with pytest.raises(
        HistoricalCutoffExecutionPolicyError,
        match="exact ordered 60-month",
    ):
        build_authorized_cutoff_execution_schedule(signals, _real_xu100_calendar())


@pytest.mark.parametrize(
    "candidate",
    [
        replace(TOTAL_RASYO_MONTHLY_OPEN_V1),
        replace(TOTAL_RASYO_MONTHLY_OPEN_V1, full_day_session_end=time(20, 0)),
        replace(TOTAL_RASYO_MONTHLY_OPEN_V1, profile_key="TOTAL_RASYO_MONTHLY_OPEN_V1_COPY"),
    ],
)
def test_only_canonical_policy_object_identity_is_authorized(candidate):
    assert candidate is not TOTAL_RASYO_MONTHLY_OPEN_V1
    with pytest.raises(HistoricalCutoffExecutionPolicyError, match="unauthorized timing policy"):
        build_authorized_cutoff_execution_schedule(
            _real_signal_dates(),
            _real_xu100_calendar(),
            policy=candidate,
        )


def test_machine_readable_policy_evidence_matches_contract():
    evidence = cutoff_execution_policy_evidence()
    assert evidence["authorized"] is True
    assert evidence["full_day_session_end"] == "18:10:00"
    assert evidence["half_day_session_end"] == "12:40:00"
    assert evidence["half_day_cutoff_dates"] == [
        "2021-10-28",
        "2023-06-27",
        "2026-05-26",
    ]
    assert evidence["execution_time"] == "10:00:00"
    assert evidence["execution_price_basis"] == "DAILY_OPEN"
    assert evidence["expected_months"] == 60
    assert evidence["start_month"] == "2021-08"
    assert evidence["end_month"] == "2026-07"
    assert evidence["result"] == "PASS"
