from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pandas as pd
import pytest

from src.analytics.historical_pit_nonfin_m2_replay import (
    HistoricalPitNonfinM2ReplayError,
    run_historical_pit_nonfin_m2_replay,
)
from src.analytics.nonfin_batch_pipeline import build_nonfin_snapshots_from_frames
from src.analytics.nonfin_valuation import NonfinValuationConfig, evaluate_nonfin_batch


ANALYSIS = datetime(2023, 1, 3, 12, 0, tzinfo=timezone.utc)
CONFIG_PATH = "config/nonfin_valuation.relative_v1.json"
TICKERS = tuple(f"N{i}" for i in range(1, 7))


def _config() -> NonfinValuationConfig:
    return NonfinValuationConfig.from_json_file(CONFIG_PATH)


def _universe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ticker": ticker, "peer_group": "XTEST", "sector_family": "NONFIN"}
            for ticker in TICKERS
        ]
    )


def _financials() -> pd.DataFrame:
    config = _config()
    periods = [
        ("2022-03-31", "2022-05-10T09:00:00Z"),
        ("2022-06-30", "2022-08-10T09:00:00Z"),
        ("2022-09-30", "2022-11-10T09:00:00Z"),
        ("2022-12-31", "2023-01-02T09:00:00Z"),
    ]
    rows = []
    for idx, ticker in enumerate(TICKERS, start=1):
        scale = 1.0 + idx * 0.03
        for period_end, published_at in periods:
            rows.append(
                {
                    "ticker": ticker,
                    "period_end": period_end,
                    "published_at": published_at,
                    "derivation_profile": config.source_derivation_profile,
                    "derivation_version": config.source_derivation_version,
                    "revenue": 100.0 * scale,
                    "ebit": 20.0 * scale,
                    "net_income": 10.0 * scale,
                    "total_equity": 200.0 * scale,
                    "debt_st": 20.0,
                    "debt_lt": 30.0,
                    "cash_and_eq": 10.0,
                    "st_investments": 5.0,
                    "shares_out": 100.0,
                }
            )
    return pd.DataFrame(rows)


def _prices() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": ticker,
                "price_trade_date": "2023-01-02",
                "current_price": 9.0 + idx,
            }
            for idx, ticker in enumerate(TICKERS, start=1)
        ]
    )


def _follow() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": ticker,
                "follow_score": 0.30 + idx * 0.05,
                "follow_active": True,
                "asof_date": "2023-01-03",
            }
            for idx, ticker in enumerate(TICKERS, start=1)
        ]
    )


def test_historical_nonfin_m2_replay_has_no_database_connection_parameter():
    assert "conn" not in inspect.signature(run_historical_pit_nonfin_m2_replay).parameters


def test_historical_nonfin_m2_matches_existing_pure_production_engine_from_same_frames():
    config = _config()
    universe = _universe()
    financials = _financials()
    prices = _prices()
    follow = _follow()

    replay = run_historical_pit_nonfin_m2_replay(
        analysis_at=ANALYSIS,
        universe=universe,
        financials=financials,
        prices=prices,
        config=config,
        follow_contexts=follow,
    )

    prepared_fin = financials.copy()
    prepared_fin["period_end"] = pd.to_datetime(prepared_fin["period_end"]).dt.date
    prepared_prices = prices.copy()
    prepared_prices["price_trade_date"] = pd.to_datetime(prepared_prices["price_trade_date"]).dt.date
    snapshots, rejections = build_nonfin_snapshots_from_frames(
        universe=universe,
        financials=prepared_fin,
        prices=prepared_prices,
        analysis_at=ANALYSIS,
        anchor_period_end=None,
    )
    direct = evaluate_nonfin_batch(
        snapshots,
        config=config,
        follow_contexts={
            row.ticker: {"follow_score": row.follow_score, "follow_active": True}
            for row in follow.itertuples(index=False)
        },
    )

    assert rejections == []
    assert replay.rejections.empty
    assert replay.tickers == tuple(sorted(TICKERS))
    assert replay.report["config_sha256"] == direct["config_sha256"]
    direct_scores = {
        row["ticker"]: row["m2"]["m2"]
        for row in direct["results"]
    }
    replay_scores = replay.m2_scores.set_index("ticker")["m2"].to_dict()
    assert replay_scores == pytest.approx(direct_scores)
    assert set(replay.m2_scores["m2_source"]) == {"NONFIN_RELATIVE_TWO_AXIS_V1"}
    assert replay.m2_scores["m2"].between(0.0, 1.0).all()


def test_historical_nonfin_m2_is_deterministic_for_identical_pit_inputs():
    kwargs = dict(
        analysis_at=ANALYSIS,
        universe=_universe(),
        financials=_financials(),
        prices=_prices(),
        config=_config(),
        follow_contexts=_follow(),
    )
    first = run_historical_pit_nonfin_m2_replay(**kwargs)
    second = run_historical_pit_nonfin_m2_replay(**kwargs)
    pd.testing.assert_frame_equal(first.m2_scores, second.m2_scores)
    assert first.report["config_sha256"] == second.report["config_sha256"]


def test_historical_nonfin_m2_defaults_follow_axis_to_production_neutral_when_context_absent():
    replay = run_historical_pit_nonfin_m2_replay(
        analysis_at=ANALYSIS,
        universe=_universe(),
        financials=_financials(),
        prices=_prices(),
        config=_config(),
        follow_contexts=None,
    )
    assert len(replay.m2_scores) == 6
    for inputs in replay.m2_scores["score_inputs"]:
        assert inputs["follow_active"] is False
        assert inputs["follow_score_effective"] == pytest.approx(0.5)


def test_historical_nonfin_m2_rejects_financial_published_after_analysis_at():
    financials = _financials()
    financials.loc[0, "published_at"] = "2023-01-04T09:00:00Z"
    with pytest.raises(HistoricalPitNonfinM2ReplayError, match="published_at"):
        run_historical_pit_nonfin_m2_replay(
            analysis_at=ANALYSIS,
            universe=_universe(),
            financials=financials,
            prices=_prices(),
            config=_config(),
        )


def test_historical_nonfin_m2_rejects_future_financial_period():
    financials = _financials()
    financials.loc[0, "period_end"] = "2023-03-31"
    with pytest.raises(HistoricalPitNonfinM2ReplayError, match="period_end"):
        run_historical_pit_nonfin_m2_replay(
            analysis_at=ANALYSIS,
            universe=_universe(),
            financials=financials,
            prices=_prices(),
            config=_config(),
        )


def test_historical_nonfin_m2_rejects_future_price():
    prices = _prices()
    prices.loc[0, "price_trade_date"] = "2023-01-04"
    with pytest.raises(HistoricalPitNonfinM2ReplayError, match="sonrasi fiyat"):
        run_historical_pit_nonfin_m2_replay(
            analysis_at=ANALYSIS,
            universe=_universe(),
            financials=_financials(),
            prices=prices,
            config=_config(),
        )


def test_historical_nonfin_m2_rejects_current_universe_contamination():
    financials = pd.concat(
        [
            _financials(),
            pd.DataFrame(
                [
                    {
                        "ticker": "TODAYONLY",
                        "period_end": "2022-12-31",
                        "published_at": "2023-01-02T09:00:00Z",
                        "derivation_profile": _config().source_derivation_profile,
                        "derivation_version": _config().source_derivation_version,
                        "revenue": 1.0,
                        "ebit": 1.0,
                        "net_income": 1.0,
                        "total_equity": 1.0,
                        "debt_st": 0.0,
                        "debt_lt": 0.0,
                        "cash_and_eq": 0.0,
                        "st_investments": 0.0,
                        "shares_out": 1.0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    with pytest.raises(HistoricalPitNonfinM2ReplayError, match="universe disi"):
        run_historical_pit_nonfin_m2_replay(
            analysis_at=ANALYSIS,
            universe=_universe(),
            financials=financials,
            prices=_prices(),
            config=_config(),
        )


def test_historical_nonfin_m2_rejects_wrong_derivation_profile_instead_of_filtering_it_away():
    financials = _financials()
    financials.loc[0, "derivation_profile"] = "CURRENT_OR_WRONG_PROFILE"
    with pytest.raises(HistoricalPitNonfinM2ReplayError, match="derivation profile/version"):
        run_historical_pit_nonfin_m2_replay(
            analysis_at=ANALYSIS,
            universe=_universe(),
            financials=financials,
            prices=_prices(),
            config=_config(),
        )


def test_historical_nonfin_m2_rejects_two_pit_versions_for_same_financial_period():
    financials = _financials()
    duplicate = financials.iloc[[0]].copy()
    duplicate["published_at"] = "2022-05-11T09:00:00Z"
    financials = pd.concat([financials, duplicate], ignore_index=True)
    with pytest.raises(HistoricalPitNonfinM2ReplayError, match="birden fazla PIT version"):
        run_historical_pit_nonfin_m2_replay(
            analysis_at=ANALYSIS,
            universe=_universe(),
            financials=financials,
            prices=_prices(),
            config=_config(),
        )


def test_historical_nonfin_m2_rejects_future_follow_context():
    follow = _follow()
    follow.loc[0, "asof_date"] = "2023-01-04"
    with pytest.raises(HistoricalPitNonfinM2ReplayError, match="follow context"):
        run_historical_pit_nonfin_m2_replay(
            analysis_at=ANALYSIS,
            universe=_universe(),
            financials=_financials(),
            prices=_prices(),
            config=_config(),
            follow_contexts=follow,
        )
