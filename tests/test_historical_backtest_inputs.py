import pandas as pd
import pytest

from src.analytics.historical_backtest_inputs import (
    HistoricalBacktestInputError,
    benchmark_prices_for_portfolio,
    build_backtest_input_bundle,
    build_contributions,
    build_execution_calendar,
    build_signal_cutoffs,
    expand_historical_universe,
    select_execution_prices,
    select_pit_total_rasyo_signals,
)


def _calendar_prices():
    return pd.DataFrame([
        {"index_code": "XU100", "trade_date": "2022-01-03", "open": 100, "close": 101},
        {"index_code": "XU100", "trade_date": "2022-01-04", "open": 102, "close": 103},
        {"index_code": "XU100", "trade_date": "2022-02-02", "open": 110, "close": 111},
        {"index_code": "XU100", "trade_date": "2022-02-03", "open": 112, "close": 113},
    ])


def _calendar():
    return build_execution_calendar(
        _calendar_prices(), start_month="2022-01", end_month="2022-02", expected_months=2
    )


def test_calendar_uses_first_observed_trading_day_each_month():
    out = _calendar()
    assert list(out["signal_date"].dt.strftime("%Y-%m-%d")) == ["2022-01-03", "2022-02-02"]
    assert list(out["benchmark_open"]) == [100.0, 110.0]


def test_calendar_missing_month_fails_closed():
    prices = _calendar_prices().query("trade_date != '2022-02-02' and trade_date != '2022-02-03'")
    with pytest.raises(HistoricalBacktestInputError, match="missing months"):
        build_execution_calendar(prices, start_month="2022-01", end_month="2022-02", expected_months=2)


def test_default_contract_is_exactly_60_months():
    with pytest.raises(HistoricalBacktestInputError, match="hedef ay sayisi"):
        build_execution_calendar(_calendar_prices(), start_month="2022-01", end_month="2022-02")


def test_contribution_uses_half_open_wage_intervals_and_two_x():
    wages = pd.DataFrame([
        {"valid_from": "2021-01-01", "valid_to": "2022-02-02", "net_min_wage": 1000},
        {"valid_from": "2022-02-02", "valid_to": None, "net_min_wage": 1500},
    ])
    out = build_contributions(_calendar(), wages)
    assert list(out["contribution"]) == [2000.0, 3000.0]


def test_wage_gap_or_overlap_fails_closed():
    gap = pd.DataFrame([
        {"valid_from": "2021-01-01", "valid_to": "2022-01-15", "net_min_wage": 1000},
        {"valid_from": "2022-01-20", "valid_to": None, "net_min_wage": 1500},
    ])
    with pytest.raises(HistoricalBacktestInputError, match="coverage"):
        build_contributions(_calendar(), gap)

    overlap = pd.DataFrame([
        {"valid_from": "2021-01-01", "valid_to": "2022-02-15", "net_min_wage": 1000},
        {"valid_from": "2022-02-01", "valid_to": None, "net_min_wage": 1500},
    ])
    with pytest.raises(HistoricalBacktestInputError, match="overlapping"):
        build_contributions(_calendar(), overlap)


def test_historical_universe_respects_listing_and_delisting_intervals():
    membership = pd.DataFrame([
        {"ticker": "AAA", "valid_from": "2020-01-01", "valid_to": None, "is_tradable": True},
        {"ticker": "BBB", "valid_from": "2022-02-01", "valid_to": None, "is_tradable": True},
        {"ticker": "OLD", "valid_from": "2020-01-01", "valid_to": "2022-02-01", "is_tradable": True},
    ])
    out = expand_historical_universe(_calendar(), membership)
    jan = set(out.loc[out["signal_date"] == pd.Timestamp("2022-01-03"), "ticker"])
    feb = set(out.loc[out["signal_date"] == pd.Timestamp("2022-02-02"), "ticker"])
    assert jan == {"AAA", "OLD"}
    assert feb == {"AAA", "BBB"}


def test_nontradable_membership_is_not_backtest_eligible():
    membership = pd.DataFrame([
        {"ticker": "AAA", "valid_from": "2020-01-01", "valid_to": None, "is_tradable": True},
        {"ticker": "HALT", "valid_from": "2020-01-01", "valid_to": None, "is_tradable": False},
    ])
    out = expand_historical_universe(_calendar(), membership)
    assert set(out["ticker"]) == {"AAA"}


def test_overlapping_membership_intervals_are_rejected():
    membership = pd.DataFrame([
        {"ticker": "AAA", "valid_from": "2020-01-01", "valid_to": "2022-02-10", "is_tradable": True},
        {"ticker": "AAA", "valid_from": "2022-02-01", "valid_to": None, "is_tradable": True},
    ])
    with pytest.raises(HistoricalBacktestInputError, match="overlapping membership"):
        expand_historical_universe(_calendar(), membership)


def test_cutoffs_must_be_timezone_aware_and_cover_every_month():
    naive = pd.DataFrame([
        {"signal_date": "2022-01-03", "cutoff_at": "2022-01-02 23:00:00"},
        {"signal_date": "2022-02-02", "cutoff_at": "2022-02-01 23:00:00"},
    ])
    with pytest.raises(HistoricalBacktestInputError, match="timezone-aware"):
        build_signal_cutoffs(_calendar(), naive)

    missing = pd.DataFrame([
        {"signal_date": "2022-01-03", "cutoff_at": "2022-01-02T20:00:00Z"},
    ])
    with pytest.raises(HistoricalBacktestInputError, match="missing signal cutoffs"):
        build_signal_cutoffs(_calendar(), missing)


def _universe():
    return pd.DataFrame([
        {"signal_date": "2022-01-03", "ticker": "AAA"},
        {"signal_date": "2022-01-03", "ticker": "BBB"},
        {"signal_date": "2022-02-02", "ticker": "AAA"},
        {"signal_date": "2022-02-02", "ticker": "BBB"},
    ])


def _cutoffs():
    return pd.DataFrame([
        {"signal_date": "2022-01-03", "cutoff_at": "2022-01-02T20:00:00Z"},
        {"signal_date": "2022-02-02", "cutoff_at": "2022-02-01T20:00:00Z"},
    ])


def _results():
    rows = []
    for analysis_at, payloads in [
        ("2022-01-02T19:00:00Z", [("AAA", .9, "AL", "OK"), ("BBB", .4, "UZAK", "OK")]),
        ("2022-01-02T21:00:00Z", [("AAA", .1, "UZAK", "OK"), ("BBB", .9, "AL", "OK")]),
        ("2022-02-01T19:00:00Z", [("AAA", .7, "IZLE", "OK"), ("BBB", None, None, "YETERSIZ_VERI")]),
    ]:
        for ticker, score, decision, status in payloads:
            rows.append({
                "analysis_at": analysis_at, "ticker": ticker, "final_score": score,
                "decision": decision, "total_rasyo_status": status,
            })
    return pd.DataFrame(rows)


def test_pit_signal_selection_uses_latest_whole_run_before_cutoff():
    signals, audit = select_pit_total_rasyo_signals(_calendar(), _universe(), _cutoffs(), _results())
    jan = signals[signals["signal_date"] == pd.Timestamp("2022-01-03")]
    assert set(jan["analysis_at"].astype(str)) == {"2022-01-02 19:00:00+00:00"}
    assert dict(zip(jan["ticker"], jan["decision"])) == {"AAA": "AL", "BBB": "UZAK"}
    # The 21:00 restatement is after the 20:00 cutoff and must not leak backward.
    assert "2022-01-02 21:00:00+00:00" not in set(audit["analysis_at"].astype(str))


def test_non_ok_total_rasyo_row_is_audited_but_not_actionable():
    signals, audit = select_pit_total_rasyo_signals(_calendar(), _universe(), _cutoffs(), _results())
    feb_bbb = audit[(audit["signal_date"] == pd.Timestamp("2022-02-02")) & (audit["ticker"] == "BBB")].iloc[0]
    assert feb_bbb["total_rasyo_status"] == "YETERSIZ_VERI"
    assert not bool(feb_bbb["actionable"])
    assert not ((signals["signal_date"] == pd.Timestamp("2022-02-02")) & (signals["ticker"] == "BBB")).any()


def test_selected_total_rasyo_run_must_cover_whole_historical_universe():
    incomplete = _results()[~((_results()["analysis_at"] == "2022-01-02T19:00:00Z") & (_results()["ticker"] == "BBB"))]
    with pytest.raises(HistoricalBacktestInputError, match="missing 1 universe tickers"):
        select_pit_total_rasyo_signals(_calendar(), _universe(), _cutoffs(), incomplete)


def test_total_rasyo_run_after_cutoff_cannot_be_used_when_no_prior_run():
    only_future = _results()[_results()["analysis_at"] == "2022-01-02T21:00:00Z"]
    with pytest.raises(HistoricalBacktestInputError, match="no Total Rasyo run before cutoff"):
        select_pit_total_rasyo_signals(_calendar(), _universe(), _cutoffs(), only_future)


def test_execution_prices_require_exact_open_close_for_every_universe_row():
    prices = pd.DataFrame([
        {"ticker": "AAA", "trade_date": "2022-01-03", "open": 10, "close": 11},
        {"ticker": "BBB", "trade_date": "2022-01-03", "open": 20, "close": 21},
        {"ticker": "AAA", "trade_date": "2022-02-02", "open": 12, "close": 13},
        {"ticker": "BBB", "trade_date": "2022-02-02", "open": 22, "close": 23},
    ])
    out = select_execution_prices(_universe(), prices)
    assert len(out) == 4
    assert set(out.columns) == {"trade_date", "ticker", "open", "close"}

    with pytest.raises(HistoricalBacktestInputError, match="missing exact execution prices"):
        select_execution_prices(_universe(), prices.iloc[:-1])


def test_benchmark_adapter_uses_same_execution_dates_and_open_close():
    out = benchmark_prices_for_portfolio(_calendar())
    assert list(out["trade_date"].dt.strftime("%Y-%m-%d")) == ["2022-01-03", "2022-02-02"]
    assert list(out["open"]) == [100.0, 110.0]
    assert list(out["close"]) == [101.0, 111.0]


def test_bundle_is_directly_compatible_with_v24b_shapes():
    membership = pd.DataFrame([
        {"ticker": "AAA", "valid_from": "2020-01-01", "valid_to": None, "is_tradable": True},
        {"ticker": "BBB", "valid_from": "2020-01-01", "valid_to": None, "is_tradable": True},
    ])
    wages = pd.DataFrame([
        {"valid_from": "2020-01-01", "valid_to": None, "net_min_wage": 1000},
    ])
    prices = pd.DataFrame([
        {"ticker": "AAA", "trade_date": "2022-01-03", "open": 10, "close": 11},
        {"ticker": "BBB", "trade_date": "2022-01-03", "open": 20, "close": 21},
        {"ticker": "AAA", "trade_date": "2022-02-02", "open": 12, "close": 13},
        {"ticker": "BBB", "trade_date": "2022-02-02", "open": 22, "close": 23},
    ])
    bundle = build_backtest_input_bundle(
        index_prices=_calendar_prices(), prices_daily=prices, membership=membership,
        minimum_wage_schedule=wages, cutoffs=_cutoffs(), total_rasyo_results=_results(),
        start_month="2022-01", end_month="2022-02", expected_months=2,
    )
    assert list(bundle.contributions.columns) == ["signal_date", "contribution"]
    assert {"signal_date", "ticker", "final_score", "decision"}.issubset(bundle.signals.columns)
    assert list(bundle.prices.columns) == ["trade_date", "ticker", "open", "close"]
    assert list(bundle.benchmark_prices.columns) == ["trade_date", "open", "close"]
    assert len(bundle.signal_audit) == 4
