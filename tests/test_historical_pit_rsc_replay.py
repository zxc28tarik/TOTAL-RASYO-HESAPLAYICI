from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pandas as pd
import pytest

from src.analytics.historical_pit_ratio_replay import HistoricalPitRatioReplayResult
from src.analytics.historical_pit_rsc_replay import (
    HistoricalPitRscReplayError,
    run_historical_pit_rsc_replay,
)


ANALYSIS = datetime(2023, 1, 2, 19, 0, tzinfo=timezone.utc)
RATIOS = "config/ratios.json"
SECTORS = "config/sectors.json"


def _ratio_frame(rows):
    return pd.DataFrame(
        rows,
        columns=["ticker", "period_end", "version_tag", "ratio_name", "ratio_value", "is_na"],
    )


def _foundation(*, period_end: str = "2022-12-31") -> HistoricalPitRatioReplayResult:
    core = _ratio_frame(
        [
            ["AAA", period_end, "v1", "ROE", 0.20, False],
            ["BBB", period_end, "v1", "ROE", 0.10, False],
        ]
    )
    val = _ratio_frame(
        [
            ["AAA", period_end, "v1", "PB", 1.0, False],
            ["BBB", period_end, "v1", "PB", 2.0, False],
        ]
    )
    return HistoricalPitRatioReplayResult(
        analysis_at=ANALYSIS,
        tickers=("AAA", "BBB"),
        core_ratios=core,
        val_ratios=val,
    )


def test_historical_rsc_replay_has_no_database_connection_parameter():
    assert "conn" not in inspect.signature(run_historical_pit_rsc_replay).parameters


def test_historical_rsc_scores_directly_from_pit_core_plus_val_foundation():
    result = run_historical_pit_rsc_replay(
        _foundation(),
        routing={"AAA": "NONFIN", "BBB": "NONFIN"},
        ratios_json_path=RATIOS,
        sectors_json_path=SECTORS,
        min_group_size=1,
    )

    assert result.analysis_at is ANALYSIS
    assert result.tickers == ("AAA", "BBB")
    assert set(result.ratio_scores["ratio_name"]) == {"ROE", "PB"}
    assert len(result.rsc_summary) == 2

    summary = result.rsc_summary.set_index("ticker")
    # ROE is higher-better and PB is lower-better.  Both axes therefore rank
    # AAA above BBB in this fixture.
    assert float(summary.loc["AAA", "rsc_core_norm"]) > float(summary.loc["BBB", "rsc_core_norm"])
    assert float(summary.loc["AAA", "rsc_val_norm"]) > float(summary.loc["BBB", "rsc_val_norm"])


def test_historical_rsc_requires_exact_same_routing_ticker_set_as_pit_foundation():
    with pytest.raises(HistoricalPitRscReplayError, match="birebir ayni"):
        run_historical_pit_rsc_replay(
            _foundation(),
            routing={"AAA": "NONFIN"},
            ratios_json_path=RATIOS,
            sectors_json_path=SECTORS,
        )


def test_historical_rsc_rejects_unsupported_sector_family():
    with pytest.raises(HistoricalPitRscReplayError, match="desteklenmeyen sektor ailesi"):
        run_historical_pit_rsc_replay(
            _foundation(),
            routing={"AAA": "CRYPTO", "BBB": "NONFIN"},
            ratios_json_path=RATIOS,
            sectors_json_path=SECTORS,
        )


def test_historical_rsc_fails_closed_on_future_period_even_if_upstream_is_mutated():
    with pytest.raises(HistoricalPitRscReplayError, match="analysis_at sonrasi period_end"):
        run_historical_pit_rsc_replay(
            _foundation(period_end="2023-03-31"),
            routing={"AAA": "NONFIN", "BBB": "NONFIN"},
            ratios_json_path=RATIOS,
            sectors_json_path=SECTORS,
        )


def test_historical_rsc_fails_closed_on_unknown_ratio_name():
    foundation = _foundation()
    bad = foundation.core_ratios.copy()
    bad.loc[0, "ratio_name"] = "MAGIC_FUTURE_RATIO"
    mutated = HistoricalPitRatioReplayResult(
        analysis_at=foundation.analysis_at,
        tickers=foundation.tickers,
        core_ratios=bad,
        val_ratios=foundation.val_ratios,
    )
    with pytest.raises(HistoricalPitRscReplayError, match="bilinmeyen oran"):
        run_historical_pit_rsc_replay(
            mutated,
            routing={"AAA": "NONFIN", "BBB": "NONFIN"},
            ratios_json_path=RATIOS,
            sectors_json_path=SECTORS,
        )


def test_historical_rsc_rejects_text_false_is_na_instead_of_bool_false():
    foundation = _foundation()
    bad = foundation.core_ratios.copy()
    # Pandas 3 refuses assigning text directly into a native bool block. Cast
    # the mutation target to object first so the malformed upstream value
    # reaches our replay boundary and tests _strict_bool itself.
    bad["is_na"] = bad["is_na"].astype(object)
    bad.loc[0, "is_na"] = "False"
    mutated = HistoricalPitRatioReplayResult(
        analysis_at=foundation.analysis_at,
        tickers=foundation.tickers,
        core_ratios=bad,
        val_ratios=foundation.val_ratios,
    )
    with pytest.raises(HistoricalPitRscReplayError, match="is_na Python/numpy bool"):
        run_historical_pit_rsc_replay(
            mutated,
            routing={"AAA": "NONFIN", "BBB": "NONFIN"},
            ratios_json_path=RATIOS,
            sectors_json_path=SECTORS,
        )


def test_historical_rsc_rejects_scorer_output_for_period_not_in_pit_input():
    def malicious_scorer(frame, **kwargs):
        scores = pd.DataFrame(
            [["AAA", "2024-12-31", "v1", "ROE", "PROFIT", 5.0, 0.5, 0.0, False]],
            columns=[
                "ticker", "period_end", "version_tag", "ratio_name", "pillar",
                "score_1_10", "level_percentile", "trend_bonus", "is_na",
            ],
        )
        summary = pd.DataFrame(
            [["AAA", "2024-12-31", "v1", 0.5, 0.5, 0, 5.0, 0.0]],
            columns=[
                "ticker", "period_end", "version_tag", "rsc_core_norm", "rsc_val_norm",
                "good_count_ge8", "score_mean", "score_std",
            ],
        )
        return scores, summary

    with pytest.raises(HistoricalPitRscReplayError, match="PIT inputunda olmayan period/version"):
        run_historical_pit_rsc_replay(
            _foundation(),
            routing={"AAA": "NONFIN", "BBB": "NONFIN"},
            ratios_json_path=RATIOS,
            sectors_json_path=SECTORS,
            scorer=malicious_scorer,
        )
