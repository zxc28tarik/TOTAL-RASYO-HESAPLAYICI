from __future__ import annotations

import pandas as pd

from src.analytics.historical_backtest_readiness import audit_backtest_readiness_frames


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
        {
            "signal_date": dates[0],
            "cutoff_at": pd.Timestamp("2022-01-02T19:00:00Z"),
            "execution_at": pd.Timestamp("2022-01-03T07:00:00Z"),
        },
        {
            "signal_date": dates[1],
            "cutoff_at": pd.Timestamp("2022-01-31T19:00:00Z"),
            "execution_at": pd.Timestamp("2022-02-01T07:00:00Z"),
        },
    ])
    registry = pd.DataFrame([
        {
            "run_id": "R1",
            "analysis_at": pd.Timestamp("2022-01-02T18:00:00Z"),
            "overall_status": "COMPLETE",
            "persistence_status": "OK",
            "run_scope": "FULL_UNIVERSE",
            "company_count": 1,
            "universe_company_count": 1,
        },
        {
            "run_id": "R2",
            "analysis_at": pd.Timestamp("2022-01-31T18:00:00Z"),
            "overall_status": "COMPLETE",
            "persistence_status": "OK",
            "run_scope": "FULL_UNIVERSE",
            "company_count": 1,
            "universe_company_count": 1,
        },
    ])
    results = pd.DataFrame([
        {
            "run_id": "R1",
            "analysis_at": pd.Timestamp("2022-01-02T18:00:00Z"),
            "ticker": "AAA",
            "final_score": 0.8,
            "decision": "AL",
            "total_rasyo_status": "OK",
        },
        {
            "run_id": "R2",
            "analysis_at": pd.Timestamp("2022-01-31T18:00:00Z"),
            "ticker": "AAA",
            "final_score": 0.7,
            "decision": "AL",
            "total_rasyo_status": "OK",
        },
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


def test_extra_authoritative_run_ticker_does_not_block_execution_universe():
    """V24-C requires coverage of the execution universe; unrelated extra run rows are allowed."""
    f = list(_frames())
    f[6] = f[6].copy()
    f[6].loc[f[6]["run_id"] == "R2", ["company_count", "universe_company_count"]] = 2
    f[5] = pd.concat([
        f[5],
        pd.DataFrame([{
            "run_id": "R2",
            "analysis_at": pd.Timestamp("2022-01-31T18:00:00Z"),
            "ticker": "BBB",
            "final_score": 0.4,
            "decision": "UZAK",
            "total_rasyo_status": "OK",
        }]),
    ], ignore_index=True)
    report = _audit(tuple(f))
    assert report.ready is True
    assert not ((report.findings["category"] == "TOTAL_RASYO") if not report.findings.empty else pd.Series(dtype=bool)).any()


def test_malformed_ok_total_rasyo_row_must_block_ready():
    """READY must imply V24-C signal selection will not later reject an OK row as malformed."""
    f = list(_frames())
    f[5] = f[5].copy()
    f[5].loc[f[5]["run_id"] == "R2", "final_score"] = float("nan")
    report = _audit(tuple(f))
    hit = report.findings[
        (report.findings["month"] == "2022-02")
        & (report.findings["category"] == "TOTAL_RASYO")
    ]
    assert report.ready is False
    assert "MALFORMED_OK_ROW" in set(hit["code"])


def test_non_ok_total_rasyo_row_is_valid_no_action_not_readiness_failure():
    """Non-OK rows remain auditable but are intentionally non-actionable in V24-C."""
    f = list(_frames())
    f[5] = f[5].copy()
    mask = f[5]["run_id"] == "R2"
    f[5].loc[mask, "total_rasyo_status"] = "YETERSIZ_VERI"
    f[5].loc[mask, "decision"] = None
    f[5].loc[mask, "final_score"] = None
    report = _audit(tuple(f))
    assert report.ready is True


def test_wage_gap_between_execution_dates_is_still_a_structural_failure():
    """V24-C rejects schedule gaps globally, not only gaps landing on a signal_date."""
    f = list(_frames())
    f[3] = pd.DataFrame([
        {"valid_from": "2021-01-01", "valid_to": "2022-01-15", "net_min_wage": 5000.0},
        {"valid_from": "2022-02-01", "valid_to": None, "net_min_wage": 6000.0},
    ])
    report = _audit(tuple(f))
    hit = report.findings[report.findings["category"] == "WAGE"]
    assert report.ready is False
    assert "STRUCTURE_GAP" in set(hit["code"])


def test_membership_overlap_between_execution_dates_is_structural_failure():
    """V24-C rejects overlapping membership intervals even when no signal day lands in the overlap."""
    f = list(_frames())
    f[1] = pd.DataFrame([
        {"ticker": "AAA", "valid_from": "2020-01-01", "valid_to": "2022-01-15", "is_tradable": True},
        {"ticker": "AAA", "valid_from": "2022-01-10", "valid_to": None, "is_tradable": True},
    ])
    report = _audit(tuple(f))
    hit = report.findings[report.findings["category"] == "UNIVERSE"]
    assert report.ready is False
    assert "STRUCTURE_OVERLAP" in set(hit["code"])


def test_naive_execution_at_is_not_silently_accepted():
    """V24-F registry semantics require timezone-aware execution_at when the field is supplied."""
    f = list(_frames())
    f[4] = f[4].copy()
    f[4]["execution_at"] = f[4]["execution_at"].astype(object)
    f[4].loc[0, "execution_at"] = pd.Timestamp("2022-01-03 10:00:00")
    report = _audit(tuple(f))
    hit = report.findings[
        (report.findings["month"] == "2022-01")
        & (report.findings["category"] == "CUTOFF")
    ]
    assert report.ready is False
    assert "EXECUTION_NAIVE_OR_INVALID" in set(hit["code"])
