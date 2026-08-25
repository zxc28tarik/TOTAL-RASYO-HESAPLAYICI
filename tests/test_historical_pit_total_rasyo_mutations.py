from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.analytics.historical_pit_holding_m2_replay import HistoricalPitHoldingM2ReplayResult
from src.analytics.historical_pit_nonfin_m2_replay import HistoricalPitNonfinM2ReplayResult
from src.analytics.historical_pit_total_rasyo_replay import (
    HistoricalPitTotalRasyoReplayError,
    _m1_ek1_lineage,
    _normalize_m2_replays,
)

ISTANBUL = ZoneInfo("Europe/Istanbul")


def _analysis() -> datetime:
    return datetime(2025, 3, 3, 10, 0, tzinfo=ISTANBUL)


def _score_frame(ticker: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": ticker,
                "m2": 0.75,
                "m2_source": "TEST_M2_SOURCE",
                "valuation_usable": True,
                "valuation_status": "OK",
                "valuation_confidence": 1.0,
            }
        ]
    )


def test_mutation_same_ticker_cannot_be_owned_by_two_m2_families():
    analysis = _analysis()
    nonfin = HistoricalPitNonfinM2ReplayResult(
        analysis_at=analysis,
        tickers=("AAA",),
        valuation_profile="TEST",
        valuation_version=1,
        report={},
        m2_scores=_score_frame("AAA"),
        rejections=pd.DataFrame(columns=["ticker", "reason"]),
    )
    holding = HistoricalPitHoldingM2ReplayResult(
        analysis_at=analysis,
        tickers=("AAA",),
        valuation_profile="TEST",
        valuation_version=1,
        report={},
        m2_scores=_score_frame("AAA"),
        rejections=pd.DataFrame(columns=["ticker", "reason"]),
    )

    with pytest.raises(HistoricalPitTotalRasyoReplayError, match="tek motor sahipligi"):
        _normalize_m2_replays(
            (nonfin, holding),
            analysis_at=analysis,
            tickers=("AAA",),
        )


def test_mutation_m1_ek1_period_end_drift_is_rejected():
    m1 = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "m1": 0.8,
                "period_end": date(2024, 12, 31),
                "good_count_ge8": 5,
            }
        ]
    )
    ek1 = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "ek1": 5 / 18,
                "period_end": date(2024, 9, 30),
                "good_count_ge8": 5,
            }
        ]
    )

    with pytest.raises(HistoricalPitTotalRasyoReplayError, match="lineage eslesmiyor"):
        _m1_ek1_lineage(m1, ek1)
