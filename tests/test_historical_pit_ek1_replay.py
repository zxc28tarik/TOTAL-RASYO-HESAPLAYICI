from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import inspect

import pandas as pd
import pytest

from src.analytics.historical_pit_ek1_replay import (
    HistoricalPitEk1ReplayError,
    run_historical_pit_ek1_replay,
)
from src.analytics.historical_pit_m1_replay import (
    HistoricalPitM1ReplayResult,
    run_historical_pit_m1_replay,
)
from src.analytics.historical_pit_rsc_replay import HistoricalPitRscReplayResult
from src.analytics.total_rasyo_score import compute_total_rasyo


ANALYSIS = datetime(2025, 1, 2, 7, 0, tzinfo=timezone.utc)


def _rsc(*, include_missing_ticker: bool = False) -> HistoricalPitRscReplayResult:
    dates = pd.date_range("2023-03-31", periods=8, freq="QE")
    rows = [
        {
            "ticker": "AAA",
            "period_end": pe,
            "version_tag": "v1",
            "rsc_core_norm": 0.50,
            "rsc_val_norm": 0.40,
            "good_count_ge8": i,
            "score_mean": 5.0,
            "score_std": 1.0,
        }
        for i, pe in enumerate(dates, start=1)
    ]
    tickers = ("AAA", "BBB") if include_missing_ticker else ("AAA",)
    return HistoricalPitRscReplayResult(
        analysis_at=ANALYSIS,
        tickers=tickers,
        ratio_scores=pd.DataFrame(),
        rsc_summary=pd.DataFrame(rows),
    )


def _m1(*, include_missing_ticker: bool = False) -> HistoricalPitM1ReplayResult:
    return run_historical_pit_m1_replay(
        _rsc(include_missing_ticker=include_missing_ticker),
        asof_date="2025-01-02",
    )


def test_historical_ek1_has_no_database_connection_parameter():
    assert "conn" not in inspect.signature(run_historical_pit_ek1_replay).parameters


def test_historical_ek1_uses_same_latest_rsc_period_and_good_count_as_m1():
    m1 = _m1()
    result = run_historical_pit_ek1_replay(m1)

    source = m1.period_comparison.iloc[0]
    row = result.ek1_scores.iloc[0]
    assert row["ticker"] == "AAA"
    assert row["period_end"] == source["latest_period_end"]
    assert int(row["good_count_ge8"]) == int(source["good_count_latest"]) == 8
    assert float(row["ek1"]) == pytest.approx(8.0 / 18.0)
    assert result.rejections.empty


def test_historical_ek1_clips_good_count_above_18_without_changing_veto_input():
    m1 = _m1()
    scores = m1.m1_scores.copy()
    period = m1.period_comparison.copy()
    scores.loc[0, "good_count_ge8"] = 25
    period.loc[0, "good_count_latest"] = 25
    result = run_historical_pit_ek1_replay(
        replace(m1, m1_scores=scores, period_comparison=period)
    )
    row = result.ek1_scores.iloc[0]
    assert float(row["ek1"]) == 1.0
    assert int(row["good_count_ge8"]) == 25


@pytest.mark.parametrize("good_count", [4, 5])
def test_historical_ek1_good_count_enters_locked_total_rasyo_veto(good_count):
    m1 = _m1()
    scores = m1.m1_scores.copy()
    period = m1.period_comparison.copy()
    scores.loc[0, "good_count_ge8"] = good_count
    period.loc[0, "good_count_latest"] = good_count
    ek1 = run_historical_pit_ek1_replay(
        replace(m1, m1_scores=scores, period_comparison=period)
    ).ek1_scores.iloc[0]

    total = compute_total_rasyo(
        {"M2": 1.0, "M1": 1.0, "M3": 1.0, "Ek4": 1.0, "Ek1": ek1["ek1"], "Ek9": 1.0},
        good_count_ge8=ek1["good_count_ge8"],
    )
    assert total["veto_flag"] is (good_count < 5)
    if good_count < 5:
        assert total["final_score"] == pytest.approx(total["base_score"] * 0.60)
    else:
        assert total["final_score"] == pytest.approx(total["base_score"])


def test_historical_ek1_missing_rsc_period_is_rejection_not_zero_veto_input():
    result = run_historical_pit_ek1_replay(_m1(include_missing_ticker=True))
    assert set(result.ek1_scores["ticker"]) == {"AAA"}
    assert result.rejections.to_dict("records") == [
        {"ticker": "BBB", "reason": "PIT_RSC_PERIOD_UNAVAILABLE", "period_end": None}
    ]


def test_historical_ek1_rejects_m1_period_good_count_lineage_mismatch():
    m1 = _m1()
    period = m1.period_comparison.copy()
    period.loc[0, "good_count_latest"] = 7
    with pytest.raises(HistoricalPitEk1ReplayError, match="lineage eslesmiyor"):
        run_historical_pit_ek1_replay(replace(m1, period_comparison=period))


def test_historical_ek1_rejects_m1_period_end_lineage_mismatch():
    m1 = _m1()
    scores = m1.m1_scores.copy()
    scores.loc[0, "period_end"] = "2024-09-30"
    with pytest.raises(HistoricalPitEk1ReplayError, match="lineage eslesmiyor"):
        run_historical_pit_ek1_replay(replace(m1, m1_scores=scores))


@pytest.mark.parametrize("bad", [True, -1, 1.5, float("nan"), float("inf")])
def test_historical_ek1_rejects_invalid_good_count(bad):
    m1 = _m1()
    scores = m1.m1_scores.copy()
    period = m1.period_comparison.copy()
    scores["good_count_ge8"] = pd.Series([bad], dtype=object)
    period["good_count_latest"] = pd.Series([bad], dtype=object)
    with pytest.raises(HistoricalPitEk1ReplayError, match="good_count|bool|tam sayi|negatif"):
        run_historical_pit_ek1_replay(
            replace(m1, m1_scores=scores, period_comparison=period)
        )


def test_historical_ek1_rejects_current_universe_contamination():
    m1 = _m1()
    scores = m1.m1_scores.copy()
    period = m1.period_comparison.copy()
    scores.loc[0, "ticker"] = "TODAYONLY"
    period.loc[0, "ticker"] = "TODAYONLY"
    with pytest.raises(HistoricalPitEk1ReplayError, match="historical universe disi"):
        run_historical_pit_ek1_replay(
            replace(m1, m1_scores=scores, period_comparison=period)
        )


def test_historical_ek1_rejects_future_period_even_if_m1_result_is_mutated():
    m1 = _m1()
    scores = m1.m1_scores.copy()
    period = m1.period_comparison.copy()
    scores.loc[0, "period_end"] = "2026-12-31"
    period.loc[0, "latest_period_end"] = "2026-12-31"
    with pytest.raises(HistoricalPitEk1ReplayError, match="analysis_at sonrasi"):
        run_historical_pit_ek1_replay(
            replace(m1, m1_scores=scores, period_comparison=period)
        )


def test_historical_ek1_rejects_future_asof_even_if_m1_result_is_mutated():
    m1 = _m1()
    with pytest.raises(HistoricalPitEk1ReplayError, match="analysis_at sonrasi asof_date"):
        run_historical_pit_ek1_replay(replace(m1, asof_date=pd.Timestamp("2025-01-03").date()))


def test_historical_ek1_rejects_invalid_asof_even_if_m1_result_is_mutated():
    m1 = _m1()
    with pytest.raises(HistoricalPitEk1ReplayError, match="asof_date gecersiz"):
        run_historical_pit_ek1_replay(replace(m1, asof_date="not-a-date"))
