import pandas as pd
import pytest

from src.analytics.historical_backtest_db import (
    HistoricalBacktestDatabaseError,
    build_verified_backtest_input_bundle,
    validate_total_rasyo_run_registry,
)


def _registry(extra=None):
    rows = [{
        "run_id": "FULL-1",
        "analysis_at": "2022-01-02T19:00:00Z",
        "overall_status": "COMPLETE",
        "persistence_status": "OK",
        "run_scope": "FULL_UNIVERSE",
        "company_count": 2,
        "universe_company_count": 2,
    }]
    if extra:
        rows.extend(extra)
    return pd.DataFrame(rows)


def _results(run_id="FULL-1", analysis_at="2022-01-02T19:00:00Z"):
    return pd.DataFrame([
        {"run_id": run_id, "analysis_at": analysis_at, "ticker": "AAA", "final_score": .9, "decision": "AL", "total_rasyo_status": "OK"},
        {"run_id": run_id, "analysis_at": analysis_at, "ticker": "BBB", "final_score": .4, "decision": "UZAK", "total_rasyo_status": "OK"},
    ])


def test_authoritative_full_universe_run_is_kept():
    out = validate_total_rasyo_run_registry(_results(), _registry())
    assert list(out["run_id"].unique()) == ["FULL-1"]
    assert set(out["ticker"]) == {"AAA", "BBB"}


def test_later_targeted_run_is_filtered_not_selected():
    targeted = {
        "run_id": "TARGET-2", "analysis_at": "2022-01-02T19:30:00Z",
        "overall_status": "COMPLETE", "persistence_status": "OK",
        "run_scope": "TARGETED", "company_count": 1, "universe_company_count": 2,
    }
    results = pd.concat([
        _results(),
        pd.DataFrame([{"run_id": "TARGET-2", "analysis_at": "2022-01-02T19:30:00Z", "ticker": "AAA", "final_score": .1, "decision": "UZAK", "total_rasyo_status": "OK"}]),
    ], ignore_index=True)
    out = validate_total_rasyo_run_registry(results, _registry([targeted]))
    assert set(out["run_id"]) == {"FULL-1"}


def test_partial_or_persistence_error_runs_are_not_backtest_authority():
    extras = [
        {"run_id": "P", "analysis_at": "2022-01-02T19:10:00Z", "overall_status": "PARTIAL", "persistence_status": "OK", "run_scope": "FULL_UNIVERSE", "company_count": 2, "universe_company_count": 2},
        {"run_id": "E", "analysis_at": "2022-01-02T19:20:00Z", "overall_status": "COMPLETE", "persistence_status": "KALICILIK_HATASI", "run_scope": "FULL_UNIVERSE", "company_count": 2, "universe_company_count": 2},
    ]
    extra_rows = pd.concat([_results("P", extras[0]["analysis_at"]), _results("E", extras[1]["analysis_at"])], ignore_index=True)
    out = validate_total_rasyo_run_registry(pd.concat([_results(), extra_rows], ignore_index=True), _registry(extras))
    assert set(out["run_id"]) == {"FULL-1"}



def test_legacy_null_scope_and_counts_do_not_poison_valid_modern_run():
    legacy = {
        "run_id": "LEGACY-0", "analysis_at": "2021-12-31T19:00:00Z",
        "overall_status": "OK", "persistence_status": None,
        "run_scope": None, "company_count": None, "universe_company_count": None,
    }
    out = validate_total_rasyo_run_registry(_results(), _registry([legacy]))
    assert set(out["run_id"]) == {"FULL-1"}


def test_authoritative_null_counts_fail_closed():
    bad = _registry().copy()
    bad.loc[0, "company_count"] = None
    with pytest.raises(HistoricalBacktestDatabaseError, match="company_count"):
        validate_total_rasyo_run_registry(_results(), bad)

def test_authoritative_registry_count_mismatch_fails_closed():
    bad = _registry().copy()
    bad.loc[0, "universe_company_count"] = 3
    with pytest.raises(HistoricalBacktestDatabaseError, match="count mismatch"):
        validate_total_rasyo_run_registry(_results(), bad)


def test_persisted_result_count_must_match_registry():
    with pytest.raises(HistoricalBacktestDatabaseError, match="persisted result count mismatch"):
        validate_total_rasyo_run_registry(_results().iloc[:1], _registry())


def test_result_analysis_at_must_match_registry():
    bad = _results().copy()
    bad.loc[1, "analysis_at"] = "2022-01-02T18:00:00Z"
    with pytest.raises(HistoricalBacktestDatabaseError, match="analysis_at"):
        validate_total_rasyo_run_registry(bad, _registry())


def test_duplicate_authoritative_analysis_at_is_ambiguous():
    extra = {
        "run_id": "FULL-2", "analysis_at": "2022-01-02T19:00:00Z",
        "overall_status": "COMPLETE", "persistence_status": "OK",
        "run_scope": "FULL_UNIVERSE", "company_count": 2, "universe_company_count": 2,
    }
    with pytest.raises(HistoricalBacktestDatabaseError, match="ambiguous authoritative"):
        validate_total_rasyo_run_registry(pd.concat([_results(), _results("FULL-2")], ignore_index=True), _registry([extra]))


def test_no_authoritative_run_fails_closed():
    reg = _registry().copy()
    reg.loc[0, "run_scope"] = "TARGETED"
    with pytest.raises(HistoricalBacktestDatabaseError, match="authoritative FULL_UNIVERSE"):
        validate_total_rasyo_run_registry(_results(), reg)


def test_naive_analysis_timestamp_is_rejected():
    reg = _registry().copy()
    reg.loc[0, "analysis_at"] = "2022-01-02 19:00:00"
    with pytest.raises(HistoricalBacktestDatabaseError, match="timezone-aware"):
        validate_total_rasyo_run_registry(_results(), reg)


def _index_prices():
    return pd.DataFrame([
        {"index_code": "XU100", "trade_date": "2022-01-03", "open": 100, "close": 101},
        {"index_code": "XU100", "trade_date": "2022-02-02", "open": 110, "close": 111},
    ])


def _membership():
    return pd.DataFrame([
        {"ticker": "AAA", "valid_from": "2020-01-01", "valid_to": None, "is_tradable": True},
        {"ticker": "BBB", "valid_from": "2020-01-01", "valid_to": None, "is_tradable": True},
    ])


def _prices():
    return pd.DataFrame([
        {"ticker": "AAA", "trade_date": "2022-01-03", "open": 10, "close": 11},
        {"ticker": "BBB", "trade_date": "2022-01-03", "open": 20, "close": 21},
        {"ticker": "AAA", "trade_date": "2022-02-02", "open": 12, "close": 13},
        {"ticker": "BBB", "trade_date": "2022-02-02", "open": 22, "close": 23},
    ])


def _wages():
    return pd.DataFrame([{"valid_from": "2020-01-01", "valid_to": None, "net_min_wage": 1000}])


def _cutoffs():
    return pd.DataFrame([
        {"signal_date": "2022-01-03", "cutoff_at": "2022-01-02T20:00:00Z"},
        {"signal_date": "2022-02-02", "cutoff_at": "2022-02-01T20:00:00Z"},
    ])


def test_verified_bundle_ignores_later_targeted_run_and_remains_v24b_compatible():
    second_full = {
        "run_id": "FULL-2", "analysis_at": "2022-02-01T19:00:00Z",
        "overall_status": "COMPLETE", "persistence_status": "OK",
        "run_scope": "FULL_UNIVERSE", "company_count": 2, "universe_company_count": 2,
    }
    targeted = {
        "run_id": "TARGET-X", "analysis_at": "2022-02-01T19:30:00Z",
        "overall_status": "COMPLETE", "persistence_status": "OK",
        "run_scope": "TARGETED", "company_count": 1, "universe_company_count": 2,
    }
    results = pd.concat([
        _results(),
        pd.DataFrame([
            {"run_id": "FULL-2", "analysis_at": "2022-02-01T19:00:00Z", "ticker": "AAA", "final_score": .7, "decision": "IZLE", "total_rasyo_status": "OK"},
            {"run_id": "FULL-2", "analysis_at": "2022-02-01T19:00:00Z", "ticker": "BBB", "final_score": .8, "decision": "AL", "total_rasyo_status": "OK"},
            {"run_id": "TARGET-X", "analysis_at": "2022-02-01T19:30:00Z", "ticker": "AAA", "final_score": .1, "decision": "UZAK", "total_rasyo_status": "OK"},
        ]),
    ], ignore_index=True)
    bundle = build_verified_backtest_input_bundle(
        index_prices=_index_prices(), prices_daily=_prices(), membership=_membership(),
        minimum_wage_schedule=_wages(), cutoffs=_cutoffs(), total_rasyo_results=results,
        run_registry=_registry([second_full, targeted]), start_month="2022-01", end_month="2022-02", expected_months=2,
    )
    feb = bundle.signals[bundle.signals["signal_date"] == pd.Timestamp("2022-02-02")]
    assert set(feb["analysis_at"].astype(str)) == {"2022-02-01 19:00:00+00:00"}
    assert dict(zip(feb["ticker"], feb["decision"])) == {"AAA": "IZLE", "BBB": "AL"}
    assert list(bundle.contributions["contribution"]) == [2000.0, 2000.0]
