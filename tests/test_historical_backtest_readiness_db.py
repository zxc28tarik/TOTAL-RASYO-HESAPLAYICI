from __future__ import annotations

import pandas as pd
import pytest

from src.analytics.historical_backtest_db import HistoricalBacktestDatabaseError
import src.analytics.historical_backtest_readiness_db as readiness_db


def _sixty_month_frames():
    periods = pd.period_range("2021-08", "2026-07", freq="M")
    dates = [p.start_time.normalize() for p in periods]

    index_prices = pd.DataFrame([
        {"index_code": "XU100", "trade_date": d, "open": 100.0 + i, "close": 101.0 + i}
        for i, d in enumerate(dates)
    ])
    membership = pd.DataFrame([
        {
            "ticker": "AAA",
            "valid_from": pd.Timestamp("2020-01-01"),
            "valid_to": pd.NaT,
            "is_tradable": True,
            "company_name": "AAA TEST",
            "sector_index_code": "XTEST",
            "sector_code": "TEST",
            "source": "UNIT",
            "source_ref": "u",
            "source_sha256": "a" * 64,
            "row_sha256": "b" * 64,
        }
    ])
    prices = pd.DataFrame([
        {"ticker": "AAA", "trade_date": d, "open": 10.0 + i, "close": 10.5 + i}
        for i, d in enumerate(dates)
    ])
    wages = pd.DataFrame([
        {
            "schedule_key": "WAGE60",
            "valid_from": pd.Timestamp("2021-01-01"),
            "valid_to": pd.NaT,
            "net_min_wage": 5000.0,
            "source": "UNIT",
            "source_ref": "w",
            "source_sha256": "c" * 64,
            "row_sha256": "d" * 64,
        }
    ])

    cutoff_rows = []
    registry_rows = []
    result_rows = []
    for i, d in enumerate(dates):
        local_midnight = d.tz_localize("Europe/Istanbul")
        execution = local_midnight + pd.Timedelta(hours=10)
        cutoff = local_midnight - pd.Timedelta(hours=4)
        analysis = cutoff - pd.Timedelta(hours=1)
        run_id = f"RUN-{i:02d}"
        cutoff_rows.append({
            "profile_key": "CUT60",
            "signal_date": d,
            "cutoff_at": cutoff,
            "execution_at": execution,
            "source": "UNIT",
            "source_ref": f"c{i}",
            "source_sha256": "e" * 64,
            "row_sha256": f"{i:064x}"[-64:],
        })
        registry_rows.append({
            "run_id": run_id,
            "analysis_at": analysis,
            "overall_status": "COMPLETE",
            "persistence_status": "OK",
            "run_scope": "FULL_UNIVERSE",
            "company_count": 1,
            "universe_company_count": 1,
        })
        result_rows.append({
            "run_id": run_id,
            "analysis_at": analysis,
            "ticker": "AAA",
            "final_score": 0.75,
            "decision": "AL",
            "total_rasyo_status": "OK",
        })

    return {
        "index": index_prices,
        "membership": membership,
        "prices": prices,
        "wages": wages,
        "cutoffs": pd.DataFrame(cutoff_rows),
        "registry": pd.DataFrame(registry_rows),
        "results": pd.DataFrame(result_rows),
    }


def _install_fake_reads(monkeypatch, frames):
    calls = []

    def fake_read_sql(conn, query, params):
        calls.append((query, params))
        if "FROM core.index_prices_daily" in query:
            return frames["index"].copy()
        if "FROM core.universe_membership_history" in query:
            return frames["membership"].copy()
        if "FROM core.backtest_minimum_wage_schedule" in query:
            return frames["wages"].copy()
        if "FROM analytics.backtest_signal_cutoff_schedule" in query:
            return frames["cutoffs"].copy()
        if "FROM core.prices_daily" in query:
            return frames["prices"].copy()
        if "FROM analytics.total_rasyo_run" in query:
            return frames["registry"].copy()
        if "FROM analytics.company_total_rasyo_result" in query:
            return frames["results"].copy()
        raise AssertionError(query)

    monkeypatch.setattr(readiness_db, "_read_sql", fake_read_sql)
    return calls


def test_database_readiness_one_call_audits_full_locked_60_month_window(monkeypatch):
    frames = _sixty_month_frames()
    calls = _install_fake_reads(monkeypatch, frames)

    snapshot = readiness_db.audit_backtest_readiness_from_database(
        object(),
        wage_schedule_key="WAGE60",
        cutoff_profile_key="CUT60",
    )

    assert snapshot.report.ready is True
    assert snapshot.report.checked_months == 60
    assert snapshot.report.expected_months == 60
    assert snapshot.report.findings.empty
    assert len(snapshot.index_prices) == 60
    assert len(snapshot.prices_daily) == 60
    assert len(snapshot.cutoffs) == 60
    assert len(snapshot.run_registry) == 60
    assert len(snapshot.total_rasyo_results) == 60
    assert len(calls) == 7

    price_call = next((params for query, params in calls if "FROM core.prices_daily" in query), None)
    assert price_call is not None
    signal_dates, tickers = price_call
    assert len(signal_dates) == 60
    assert tickers == ["AAA"]


def test_missing_registered_schedules_are_findings_not_early_exceptions(monkeypatch):
    frames = _sixty_month_frames()
    frames["index"] = frames["index"].iloc[:2].copy()
    dates = list(frames["index"]["trade_date"])
    frames["prices"] = frames["prices"][frames["prices"]["trade_date"].isin(dates)].copy()
    frames["cutoffs"] = pd.DataFrame(columns=[
        "profile_key", "signal_date", "cutoff_at", "execution_at",
        "source", "source_ref", "source_sha256", "row_sha256",
    ])
    frames["wages"] = pd.DataFrame(columns=[
        "schedule_key", "valid_from", "valid_to", "net_min_wage",
        "source", "source_ref", "source_sha256", "row_sha256",
    ])
    frames["registry"] = frames["registry"].iloc[:2].copy()
    frames["results"] = frames["results"].iloc[:2].copy()
    _install_fake_reads(monkeypatch, frames)

    snapshot = readiness_db.audit_backtest_readiness_from_database(
        object(),
        wage_schedule_key="MISSING_WAGE",
        cutoff_profile_key="MISSING_CUT",
        start_month="2021-08",
        end_month="2021-09",
        expected_months=2,
    )

    assert snapshot.report.ready is False
    assert snapshot.report.checked_months == 2
    assert set(snapshot.report.findings["category"]) == {"WAGE", "CUTOFF"}
    assert set(snapshot.report.findings["month"]) == {"2021-08", "2021-09"}


def test_database_transport_failure_is_still_technical_error(monkeypatch):
    def boom(conn, query, params):
        raise RuntimeError("db down")

    monkeypatch.setattr(pd, "read_sql_query", boom)
    with pytest.raises(HistoricalBacktestDatabaseError, match="readiness database read failed"):
        readiness_db.audit_backtest_readiness_from_database(
            object(),
            wage_schedule_key="WAGE",
            cutoff_profile_key="CUT",
            start_month="2022-01",
            end_month="2022-01",
            expected_months=1,
        )
