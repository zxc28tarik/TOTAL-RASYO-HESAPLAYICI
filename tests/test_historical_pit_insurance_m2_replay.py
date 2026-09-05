from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pandas as pd
import pytest
from price_level_fixtures import certify_frames
from src.analytics.price_level_adapter import SOURCE_SHARE_BASIS, SHARE_BASIS

from src.analytics.historical_pit_insurance_m2_replay import (
    HistoricalPitInsuranceM2ReplayError,
    run_historical_pit_insurance_m2_replay,
)
from src.analytics.insurance_valuation import InsuranceValuationConfig

ANALYSIS = datetime(2025, 1, 2, 17, 0, tzinfo=timezone.utc)


def _config() -> InsuranceValuationConfig:
    return InsuranceValuationConfig.from_dict({
        "valuation_profile": "INSURANCE_PB_PE", "valuation_version": 1,
        "source_metrics_profile": "KAP_INSURANCE_TTM", "source_metrics_version": 1,
        "accounting_profile": "TFRS17_LOCAL_STATUTORY", "accounting_version": 1,
        "share_basis": SHARE_BASIS, "currency": "TRY",
        "lower_quantile": 0.25, "upper_quantile": 0.75,
        "minimum_peer_count": 2, "full_confidence_peer_count": 6, "minimum_method_count": 1,
        "minimum_pb": 0.05, "maximum_pb": 8.0, "minimum_pe": 1.0, "maximum_pe": 80.0,
        "pb_weight": 0.65, "pe_weight": 0.35,
        "max_statement_age_days": 220, "full_freshness_days": 100, "max_price_age_days": 7,
        "minimum_source_confidence": 0.4, "max_halfwidth": 1.25,
        "band_width_shadow_mode": True, "valuation_axis_weight": 0.65, "follow_axis_weight": 0.35,
    })


def _universe() -> pd.DataFrame:
    return pd.DataFrame([
        {"ticker": "AAA", "sector_family": "INSURANCE"},
        {"ticker": "BBB", "sector_family": "INSURANCE"},
    ])


def _metrics() -> pd.DataFrame:
    rows = []
    for i, ticker in enumerate(("AAA", "BBB"), start=1):
        rows.append({
            "ticker": ticker, "period_end": "2024-12-31", "published_at": "2025-01-02T12:00:00+00:00",
            "business_type": "NON_LIFE", "accounting_profile": "TFRS17_LOCAL_STATUTORY", "accounting_version": 1,
            "currency": "TRY", "shares_out": 100.0, "share_basis": SOURCE_SHARE_BASIS,
            "total_equity": 1000.0 + 100.0 * i, "net_income_ttm": 100.0 + 10.0 * i,
            "written_premiums_ttm": 900.0 + 50.0 * i, "technical_result_ttm": 120.0,
            "investment_income_ttm": 80.0, "earned_premiums_ttm": 850.0,
            "net_claims_ttm": 500.0, "operating_expenses_ttm": 180.0, "solvency_ratio": 1.8,
            "source_confidence": 0.9, "source_document_id": f"DOC-{i}", "source_sha256": f"{i:x}" * 64,
            "metrics_profile": "KAP_INSURANCE_TTM", "metrics_version": 1,
        })
    return pd.DataFrame(rows)


def _prices() -> pd.DataFrame:
    prices = pd.DataFrame([
        {"ticker": "AAA", "price_trade_date": "2025-01-02", "current_price": 5.0},
        {"ticker": "BBB", "price_trade_date": "2025-01-02", "current_price": 6.0},
    ])

    certify_frames(_metrics(), prices, ANALYSIS, "period_end")
    return prices

def test_insurance_replay_has_no_database_connection_parameter():
    assert "conn" not in inspect.signature(run_historical_pit_insurance_m2_replay).parameters


def test_insurance_replay_reuses_production_two_axis_engine():
    result = run_historical_pit_insurance_m2_replay(
        analysis_at=ANALYSIS, universe=_universe(), metrics=_metrics(), prices=_prices(), config=_config()
    )
    assert result.tickers == ("AAA", "BBB")
    assert result.rejections.empty
    assert len(result.m2_scores) == 2
    assert set(result.m2_scores["m2_source"]) == {"INSURANCE_PB_PE_TWO_AXIS_V1"}
    assert result.m2_scores["m2"].between(0.0, 1.0).all()


def test_insurance_replay_rejects_future_publication():
    metrics = _metrics()
    metrics.loc[0, "published_at"] = "2025-01-03T00:00:00+00:00"
    with pytest.raises(HistoricalPitInsuranceM2ReplayError, match="published_at"):
        run_historical_pit_insurance_m2_replay(
            analysis_at=ANALYSIS, universe=_universe(), metrics=metrics, prices=_prices(), config=_config()
        )


def test_insurance_replay_rejects_future_price():
    prices = _prices()
    prices.loc[0, "price_trade_date"] = "2025-01-03"
    with pytest.raises(HistoricalPitInsuranceM2ReplayError, match="sonrasi fiyat"):
        run_historical_pit_insurance_m2_replay(
            analysis_at=ANALYSIS, universe=_universe(), metrics=_metrics(), prices=prices, config=_config()
        )


def test_insurance_replay_rejects_wrong_metrics_profile():
    metrics = _metrics()
    metrics.loc[0, "metrics_profile"] = "CURRENT_STATE"
    with pytest.raises(HistoricalPitInsuranceM2ReplayError, match="metrics_profile"):
        run_historical_pit_insurance_m2_replay(
            analysis_at=ANALYSIS, universe=_universe(), metrics=metrics, prices=_prices(), config=_config()
        )


def test_insurance_replay_rejects_foreign_ticker():
    metrics = pd.concat([_metrics(), _metrics().iloc[[0]].assign(ticker="ZZZ")], ignore_index=True)
    with pytest.raises(HistoricalPitInsuranceM2ReplayError, match="universe disi"):
        run_historical_pit_insurance_m2_replay(
            analysis_at=ANALYSIS, universe=_universe(), metrics=metrics, prices=_prices(), config=_config()
        )
