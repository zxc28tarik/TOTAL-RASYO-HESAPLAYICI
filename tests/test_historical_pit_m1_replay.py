from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pandas as pd
import pytest

from src.analytics.historical_pit_m1_replay import (
    HistoricalPitM1ReplayError,
    run_historical_pit_m1_replay,
)
from src.analytics.historical_pit_rsc_replay import HistoricalPitRscReplayResult


ANALYSIS = datetime(2025, 1, 2, 7, 0, tzinfo=timezone.utc)


def _rsc(periods: int = 8) -> HistoricalPitRscReplayResult:
    dates = pd.date_range("2023-03-31", periods=periods, freq="QE")
    rows = []
    for i, pe in enumerate(dates):
        rows.append(
            {
                "ticker": "AAA",
                "period_end": pe,
                "version_tag": "v1",
                "rsc_core_norm": 0.50 + i * 0.02,
                "rsc_val_norm": 0.40,
                "good_count_ge8": 3 + i,
                "score_mean": 3.0 + i,
                "score_std": 1.0,
            }
        )
    return HistoricalPitRscReplayResult(
        analysis_at=ANALYSIS,
        tickers=("AAA",),
        ratio_scores=pd.DataFrame(),
        rsc_summary=pd.DataFrame(rows),
    )


def test_historical_m1_replay_has_no_database_connection_parameter():
    assert "conn" not in inspect.signature(run_historical_pit_m1_replay).parameters


def test_historical_m1_reproduces_production_8q_quality_trend_math():
    result = run_historical_pit_m1_replay(_rsc(), asof_date="2025-01-02")

    assert result.tickers == ("AAA",)
    assert len(result.period_comparison) == 1
    row = result.period_comparison.iloc[0]
    assert int(row["period_count"]) == 8
    assert float(row["score_latest"]) == pytest.approx(10.0)
    assert float(row["score_prev"]) == pytest.approx(9.0)
    assert float(row["score_avg_8q"]) == pytest.approx(6.5)
    assert float(row["score_change_1q"]) == pytest.approx(1.0)
    assert float(row["score_change_4q"]) == pytest.approx(4.0)
    assert float(row["score_slope_8q"]) == pytest.approx(1.0)
    assert row["quality_trend_label"] == "IMPROVING"
    assert float(row["quality_trend_score"]) == pytest.approx(0.9416666666666667)

    m1 = result.m1_scores.iloc[0]
    assert float(m1["m1"]) == pytest.approx(float(row["quality_trend_score"]))
    assert int(m1["good_count_ge8"]) == 10


def test_historical_m1_uses_only_latest_eight_financial_periods():
    replay = _rsc(periods=10)
    result = run_historical_pit_m1_replay(replay, asof_date="2025-01-02")
    row = result.period_comparison.iloc[0]
    assert int(row["period_count"]) == 8
    assert pd.Timestamp(row["latest_period_end"]) == pd.Timestamp("2025-06-30")


def test_historical_m1_rejects_two_versions_of_same_ticker_period():
    replay = _rsc()
    dup = replay.rsc_summary.iloc[[0]].copy()
    dup["version_tag"] = "v2"
    mutated = HistoricalPitRscReplayResult(
        analysis_at=replay.analysis_at,
        tickers=replay.tickers,
        ratio_scores=replay.ratio_scores,
        rsc_summary=pd.concat([replay.rsc_summary, dup], ignore_index=True),
    )
    with pytest.raises(HistoricalPitM1ReplayError, match="birden fazla PIT version"):
        run_historical_pit_m1_replay(mutated, asof_date="2025-01-02")


def test_historical_m1_rejects_future_period_even_if_upstream_is_mutated():
    replay = _rsc()
    bad = replay.rsc_summary.copy()
    bad.loc[bad.index[-1], "period_end"] = "2026-12-31"
    mutated = HistoricalPitRscReplayResult(
        analysis_at=replay.analysis_at,
        tickers=replay.tickers,
        ratio_scores=replay.ratio_scores,
        rsc_summary=bad,
    )
    with pytest.raises(HistoricalPitM1ReplayError, match="analysis_at sonrasi period_end"):
        run_historical_pit_m1_replay(mutated, asof_date="2025-01-02")


def test_historical_m1_rejects_foreign_ticker():
    replay = _rsc()
    bad = replay.rsc_summary.copy()
    bad.loc[0, "ticker"] = "ZZZ"
    mutated = HistoricalPitRscReplayResult(
        analysis_at=replay.analysis_at,
        tickers=replay.tickers,
        ratio_scores=replay.ratio_scores,
        rsc_summary=bad,
    )
    with pytest.raises(HistoricalPitM1ReplayError, match="routing disi"):
        run_historical_pit_m1_replay(mutated, asof_date="2025-01-02")
