from __future__ import annotations

import pandas as pd
import pytest

from src.analytics.historical_backtest_db import HistoricalBacktestDatabaseError
from src.analytics.historical_backtest_readiness import (
    BacktestReadinessReport,
    audit_backtest_readiness_frames,
)


def _frames():
    dates = [pd.Timestamp("2022-01-03"), pd.Timestamp("2022-02-01")]
    index_prices = pd.DataFrame([
        {"index_code": "XU100", "trade_date": dates[0], "open": 100.0, "close": 101.0},
        {"index_code": "XU100", "trade_date": dates[1], "open": 110.0, "close": 111.0},
    ])
    membership = pd.DataFrame([
        {"ticker": "AAA", "valid_from": "2020-01-01", "valid_to": None, "is_tradable": True},
    ])
    prices = pd.DataFrame([
        {"ticker": "AAA", "trade_date": dates[0], "open": 10.0, "close": 10.5},
        {"ticker": "AAA", "trade_date": dates[1], "open": 11.0, "close": 11.5},
    ])
    wages = pd.DataFrame([
        {"valid_from": "2021-01-01", "valid_to": None, "net_min_wage": 5000.0},
    ])
    cutoffs = pd.DataFrame([
        {"signal_date": dates[0], "cutoff_at": pd.Timestamp("2022-01-02T19:00:00Z"), "execution_at": pd.Timestamp("2022-01-03T07:00:00Z")},
        {"signal_date": dates[1], "cutoff_at": pd.Timestamp("2022-01-31T19:00:00Z"), "execution_at": pd.Timestamp("2022-02-01T07:00:00Z")},
    ])
    registry = pd.DataFrame([
        {"run_id": "R1", "analysis_at": pd.Timestamp("2022-01-02T18:00:00Z"), "overall_status": "COMPLETE", "persistence_status": "OK", "run_scope": "FULL_UNIVERSE", "company_count": 1, "universe_company_count": 1},
        {"run_id": "R2", "analysis_at": pd.Timestamp("2022-01-31T18:00:00Z"), "overall_status": "COMPLETE", "persistence_status": "OK", "run_scope": "FULL_UNIVERSE", "company_count": 1, "universe_company_count": 1},
    ])
    results = pd.DataFrame([
        {"run_id": "R1", "analysis_at": pd.Timestamp("2022-01-02T18:00:00Z"), "ticker": "AAA", "final_score": 0.8, "decision": "AL", "total_rasyo_status": "OK"},
        {"run_id": "R2", "analysis_at": pd.Timestamp("2022-01-31T18:00:00Z"), "ticker": "AAA", "final_score": 0.7, "decision": "AL", "total_rasyo_status": "OK"},
    ])
    return index_prices, membership, prices, wages, cutoffs, results, registry


def _audit(frames):
    index_prices, membership, prices, wages, cutoffs, results, registry = frames
    return audit_backtest_readiness_frames(
        index_prices=index_prices,
        membership=membership,
        prices_daily=prices,
        wages=wages,
        cutoffs=cutoffs,
        total_rasyo_results=results,
        run_registry=registry,
        start_month="2022-01",
        end_month="2022-02",
        expected_months=2,
    )


def test_complete_two_month_fixture_is_ready():
    report = _audit(_frames())
    assert report.ready is True
    assert report.checked_months == 2
    assert report.findings.empty
    assert all(v == 0 for v in report.category_counts().values())


def test_ready_requires_checked_months_to_equal_expected_months():
    """Empty findings alone may never produce READY when a target month was not checked."""
    report = BacktestReadinessReport(
        start_month="2022-01",
        end_month="2022-02",
        expected_months=2,
        checked_months=1,
        findings=pd.DataFrame(columns=["month", "signal_date", "category", "code", "detail"]),
    )
    assert report.ready is False


def test_missing_price_is_reported_without_stopping_later_months():
    f = list(_frames())
    f[2] = f[2][f[2]["trade_date"] != pd.Timestamp("2022-01-03")].copy()
    report = _audit(tuple(f))
    assert report.ready is False
    assert report.checked_months == 2
    assert ((report.findings["month"] == "2022-01") & (report.findings["category"] == "PRICE")).any()


def test_missing_cutoff_and_wage_are_both_reported():
    f = list(_frames())
    f[3] = pd.DataFrame(columns=["valid_from", "valid_to", "net_min_wage"])
    f[4] = f[4].iloc[:1].copy()
    report = _audit(tuple(f))
    cats = set(report.findings["category"])
    assert "WAGE" in cats
    assert "CUTOFF" in cats
    assert report.checked_months == 2


def test_empty_universe_is_reported_for_each_month():
    f = list(_frames())
    f[1] = pd.DataFrame(columns=["ticker", "valid_from", "valid_to", "is_tradable"])
    report = _audit(tuple(f))
    findings = report.findings[report.findings["category"] == "UNIVERSE"]
    assert len(findings) == 2
    assert set(findings["month"]) == {"2022-01", "2022-02"}


def test_latest_authoritative_run_must_cover_that_months_universe():
    f = list(_frames())
    f[1] = pd.concat([f[1], pd.DataFrame([{"ticker": "BBB", "valid_from": "2022-02-01", "valid_to": None, "is_tradable": True}])], ignore_index=True)
    f[2] = pd.concat([f[2], pd.DataFrame([{"ticker": "BBB", "trade_date": "2022-02-01", "open": 20.0, "close": 21.0}])], ignore_index=True)
    report = _audit(tuple(f))
    hit = report.findings[(report.findings["month"] == "2022-02") & (report.findings["category"] == "TOTAL_RASYO")]
    assert len(hit) == 1
    assert hit.iloc[0]["code"] == "UNIVERSE_COVERAGE"
    assert "BBB" in hit.iloc[0]["detail"]


def test_invalid_authority_is_global_finding_not_silent_fallback():
    f = list(_frames())
    f[6] = f[6].copy()
    f[6].loc[0, "company_count"] = 2
    report = _audit(tuple(f))
    hit = report.findings[(report.findings["month"] == "*") & (report.findings["code"] == "AUTHORITY_INVALID")]
    assert len(hit) == 1
    assert report.ready is False


def test_naive_cutoff_is_reported():
    f = list(_frames())
    f[4] = f[4].copy()
    f[4]["cutoff_at"] = f[4]["cutoff_at"].astype(object)
    f[4].loc[0, "cutoff_at"] = pd.Timestamp("2022-01-02 19:00:00")
    report = _audit(tuple(f))
    hit = report.findings[(report.findings["month"] == "2022-01") & (report.findings["category"] == "CUTOFF")]
    assert "NAIVE_OR_INVALID" in set(hit["code"])


def test_expected_month_count_is_fail_closed():
    frames = _frames()
    with pytest.raises(HistoricalBacktestDatabaseError, match="month window count mismatch"):
        index_prices, membership, prices, wages, cutoffs, results, registry = frames
        audit_backtest_readiness_frames(
            index_prices=index_prices,
            membership=membership,
            prices_daily=prices,
            wages=wages,
            cutoffs=cutoffs,
            total_rasyo_results=results,
            run_registry=registry,
            start_month="2022-01",
            end_month="2022-02",
            expected_months=60,
        )
