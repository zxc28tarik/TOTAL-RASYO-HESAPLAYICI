from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pandas as pd
import pytest
from price_level_fixtures import certify_frames
from src.analytics.price_level_adapter import SOURCE_SHARE_BASIS, SHARE_BASIS

from src.analytics.gyo_valuation import GyoValuationConfig
from src.analytics.historical_pit_gyo_m2_replay import (
    HistoricalPitGyoM2ReplayError,
    run_historical_pit_gyo_m2_replay,
)


ANALYSIS = datetime(2025, 1, 2, 17, 0, tzinfo=timezone.utc)


def _config() -> GyoValuationConfig:
    return GyoValuationConfig.from_dict({
        "valuation_profile": "GYO_PD_NAV",
        "valuation_version": 1,
        "source_nav_profile": "GYO_REPORTED_NAV",
        "source_nav_version": 1,
        "share_basis": SHARE_BASIS,
        "currency": "TRY",
        "lower_quantile": 0.25,
        "upper_quantile": 0.75,
        "minimum_peer_count": 3,
        "full_confidence_peer_count": 8,
        "minimum_pd_nav": 0.05,
        "maximum_pd_nav": 3.0,
        "max_nav_age_days": 200,
        "full_freshness_days": 95,
        "max_price_age_days": 7,
        "minimum_source_confidence": 0.4,
        "derived_nav_confidence_factor": 0.85,
        "max_halfwidth": 1.25,
        "band_width_shadow_mode": True,
        "valuation_axis_weight": 0.65,
        "follow_axis_weight": 0.35,
    })


def _universe() -> pd.DataFrame:
    return pd.DataFrame([
        {"ticker": "AAA", "peer_group": "XGMYO", "sector_family": "GYO"},
        {"ticker": "BBB", "peer_group": "XGMYO", "sector_family": "GYO"},
        {"ticker": "CCC", "peer_group": "XGMYO", "sector_family": "GYO"},
    ])


def _navs() -> pd.DataFrame:
    rows = []
    for i, ticker in enumerate(("AAA", "BBB", "CCC"), start=1):
        rows.append({
            "ticker": ticker,
            "nav_asof_date": "2024-12-31",
            "nav_published_at": "2024-12-31T15:00:00+00:00",
            "nav_total": 1000.0,
            "shares_out": 100.0,
            "share_basis": SOURCE_SHARE_BASIS,
            "currency": "TRY",
            "property_portfolio_value": 1200.0,
            "nav_source_method": "DIRECT",
            "source_confidence": 0.9,
            "source_document_id": f"DOC-{i}",
            "source_sha256": f"{i:x}" * 64,
            "nav_profile": "GYO_REPORTED_NAV",
            "nav_version": 1,
        })
    return pd.DataFrame(rows)


def _prices() -> pd.DataFrame:
    prices = pd.DataFrame([
        {"ticker": "AAA", "price_trade_date": "2025-01-02", "current_price": 5.0},
        {"ticker": "BBB", "price_trade_date": "2025-01-02", "current_price": 6.0},
        {"ticker": "CCC", "price_trade_date": "2025-01-02", "current_price": 7.0},
    ])

    certify_frames(_navs(), prices, ANALYSIS, "nav_asof_date")
    return prices

def test_historical_gyo_m2_has_no_database_connection_parameter():
    assert "conn" not in inspect.signature(run_historical_pit_gyo_m2_replay).parameters


def test_historical_gyo_m2_reuses_production_two_axis_engine():
    result = run_historical_pit_gyo_m2_replay(
        analysis_at=ANALYSIS, universe=_universe(), navs=_navs(), prices=_prices(), config=_config()
    )
    assert result.tickers == ("AAA", "BBB", "CCC")
    assert len(result.m2_scores) == 3
    assert result.rejections.empty
    assert set(result.m2_scores["m2_source"]) == {"GYO_PD_NAV_TWO_AXIS_V1"}
    assert result.m2_scores["m2"].between(0.0, 1.0).all()


def test_historical_gyo_m2_rejects_future_nav_publication():
    navs = _navs()
    navs.loc[0, "nav_published_at"] = "2025-01-03T00:00:00+00:00"
    with pytest.raises(HistoricalPitGyoM2ReplayError, match="nav_published_at"):
        run_historical_pit_gyo_m2_replay(
            analysis_at=ANALYSIS, universe=_universe(), navs=navs, prices=_prices(), config=_config()
        )


def test_historical_gyo_m2_rejects_future_price():
    prices = _prices()
    prices.loc[0, "price_trade_date"] = "2025-01-03"
    with pytest.raises(HistoricalPitGyoM2ReplayError, match="sonrasi fiyat"):
        run_historical_pit_gyo_m2_replay(
            analysis_at=ANALYSIS, universe=_universe(), navs=_navs(), prices=prices, config=_config()
        )


def test_historical_gyo_m2_rejects_wrong_nav_profile():
    navs = _navs()
    navs.loc[0, "nav_profile"] = "CURRENT_STATE_PROFILE"
    with pytest.raises(HistoricalPitGyoM2ReplayError, match="nav_profile"):
        run_historical_pit_gyo_m2_replay(
            analysis_at=ANALYSIS, universe=_universe(), navs=navs, prices=_prices(), config=_config()
        )


def test_historical_gyo_m2_rejects_foreign_data():
    navs = pd.concat([_navs(), _navs().iloc[[0]].assign(ticker="ZZZ")], ignore_index=True)
    with pytest.raises(HistoricalPitGyoM2ReplayError, match="universe disi"):
        run_historical_pit_gyo_m2_replay(
            analysis_at=ANALYSIS, universe=_universe(), navs=navs, prices=_prices(), config=_config()
        )
