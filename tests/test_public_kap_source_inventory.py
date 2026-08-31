from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import gzip
from pathlib import Path

import pandas as pd
import pytest

from src.analytics.historical_cutoff_execution_policy import (
    build_authorized_cutoff_execution_schedule,
)
from src.ingest.public_kap_pit import PublicKapFinancialReport
from src.ingest.public_kap_source_inventory import (
    EXPECTED_CELLS,
    STATUS_AWAITING_AUTHORIZED_SOURCE,
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_PARSING_REJECTED,
    AuthorizedEnumerationReceipt,
    PublicKapParsingRejection,
    PublicKapSourceInventoryError,
    build_public_kap_source_inventory,
)


ROOT = Path(__file__).resolve().parents[1]
SIGNALS = ROOT / "data/backtest_sources/xu100_signal_dates_yahoo_2021-08_2026-07.csv"
INDEX_CLOSES = ROOT / "data/backtest_sources/m3_source_package/index_closes.csv.gz"
REAL_MATRIX = ROOT / "data/backtest_sources/yahoo_resolved/monthly_member_signal_price_coverage.csv"


def _real_policy_sources() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    signals = pd.read_csv(SIGNALS, dtype=str, keep_default_na=False)
    with gzip.open(INDEX_CLOSES, "rt", encoding="utf-8", newline="") as handle:
        closes = pd.read_csv(handle, dtype=str, keep_default_na=False)
    calendar = closes.loc[closes["index_code"] == "XU100", ["index_code", "trade_date"]].copy()
    schedule = build_authorized_cutoff_execution_schedule(signals, calendar)
    return signals, calendar, schedule


def _synthetic_matrix(signals: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for signal in signals.itertuples(index=False):
        for index in range(100):
            rows.append(
                {
                    "month": signal.month,
                    "signal_date": signal.signal_date,
                    "index_code": "XU100",
                    "ticker": f"T{index:03d}",
                }
            )
    return pd.DataFrame(rows)


def _receipt(ticker: str, coverage_end_at, *, complete: bool = True) -> AuthorizedEnumerationReceipt:
    return AuthorizedEnumerationReceipt(
        ticker=ticker,
        coverage_end_at=coverage_end_at.to_pydatetime()
        if isinstance(coverage_end_at, pd.Timestamp)
        else coverage_end_at,
        source_kind="IMMUTABLE_KAP_EXPORT",
        source_identity="fixture-export-v1",
        source_sha256="a" * 64,
        enumeration_complete=complete,
    )


def _report(notification_id: int, ticker: str, published_at, *, period: str = "3 Aylık") -> PublicKapFinancialReport:
    if isinstance(published_at, pd.Timestamp):
        published_at = published_at.to_pydatetime()
    return PublicKapFinancialReport(
        notification_id=notification_id,
        ticker=ticker,
        published_at=published_at,
        report_year=2021,
        report_period=period,
        disclosure_type="FR",
        source_url=f"https://kap.org.tr/tr/Bildirim/{notification_id}",
        raw_sha256=("b" if notification_id % 2 else "c") * 64,
        is_correction=False,
        previous_notification_date=None,
    )


def _build(universe: pd.DataFrame, *, reports=(), rejections=(), receipts=()):
    signals, calendar, schedule = _real_policy_sources()
    return build_public_kap_source_inventory(
        universe,
        signal_dates=signals,
        trading_calendar=calendar,
        cutoff_schedule=schedule,
        reports=reports,
        rejections=rejections,
        receipts=receipts,
    )


def test_real_closed_month_ticker_matrix_is_exactly_6000_and_all_awaiting_without_source() -> None:
    real = pd.read_csv(REAL_MATRIX, dtype=str, keep_default_na=False)
    result = _build(real)
    assert len(result.inventory) == EXPECTED_CELLS == 6000
    assert result.inventory[["month", "ticker"]].duplicated().sum() == 0
    assert set(result.inventory["status"]) == {STATUS_AWAITING_AUTHORIZED_SOURCE}
    assert result.summary["status_counts"][STATUS_AWAITING_AUTHORIZED_SOURCE] == 6000
    assert result.summary["real_total_rasyo_scoring_authorized"] is False


def test_missing_one_closed_cell_fails_instead_of_silent_omission() -> None:
    signals, _, _ = _real_policy_sources()
    universe = _synthetic_matrix(signals).iloc[:-1].copy()
    with pytest.raises(PublicKapSourceInventoryError, match="6000"):
        _build(universe)


def test_reordered_month_blocks_fail_exact_order_contract() -> None:
    signals, _, _ = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    first = universe.iloc[:100].copy()
    second = universe.iloc[100:200].copy()
    mutated = pd.concat([second, first, universe.iloc[200:]], ignore_index=True)
    with pytest.raises(PublicKapSourceInventoryError, match="exact ordered"):
        _build(mutated)


def test_99_members_in_one_month_fails() -> None:
    signals, _, _ = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    mutated = universe.drop(index=50).copy()
    duplicate = mutated.iloc[[100]].copy()
    duplicate["month"] = signals.iloc[0]["month"]
    duplicate["signal_date"] = signals.iloc[0]["signal_date"]
    mutated = pd.concat([mutated, duplicate], ignore_index=True)
    with pytest.raises(PublicKapSourceInventoryError):
        _build(mutated)


def test_missing_receipt_is_awaiting_not_not_found() -> None:
    signals, _, _ = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    result = _build(universe)
    row = result.inventory.query("month == '2021-08' and ticker == 'T000'").iloc[0]
    assert row["status"] == STATUS_AWAITING_AUTHORIZED_SOURCE


def test_partial_receipt_is_awaiting_not_not_found() -> None:
    signals, _, schedule = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    receipt = _receipt("T000", schedule["cutoff_at"].max(), complete=False)
    result = _build(universe, receipts=[receipt])
    rows = result.inventory.loc[result.inventory["ticker"] == "T000"]
    assert set(rows["status"]) == {STATUS_AWAITING_AUTHORIZED_SOURCE}


def test_complete_receipt_without_visible_report_is_not_found() -> None:
    signals, _, schedule = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    receipt = _receipt("T000", schedule["cutoff_at"].max())
    result = _build(universe, receipts=[receipt])
    rows = result.inventory.loc[result.inventory["ticker"] == "T000"]
    assert set(rows["status"]) == {STATUS_NOT_FOUND}


def test_found_uses_pr25_latest_visible_selector_not_eventual_latest() -> None:
    signals, _, schedule = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    early_cutoff = pd.Timestamp(schedule.loc[schedule["month"] == "2021-09", "cutoff_at"].iloc[0])
    later_cutoff = pd.Timestamp(schedule.loc[schedule["month"] == "2021-11", "cutoff_at"].iloc[0])
    original = _report(1001, "T000", early_cutoff - timedelta(days=10))
    correction = replace(
        _report(1002, "T000", later_cutoff - timedelta(days=5)),
        is_correction=True,
        previous_notification_date="01.09.2021",
    )
    receipt = _receipt("T000", schedule["cutoff_at"].max())
    result = _build(universe, reports=[original, correction], receipts=[receipt])

    september = result.inventory.query("month == '2021-09' and ticker == 'T000'").iloc[0]
    november = result.inventory.query("month == '2021-11' and ticker == 'T000'").iloc[0]
    assert september["status"] == STATUS_FOUND
    assert september["selected_notification_ids"] == "1001"
    assert november["status"] == STATUS_FOUND
    assert november["selected_notification_ids"] == "1002"


def test_visible_parse_rejection_dominates_found_but_future_rejection_does_not_leak_backward() -> None:
    signals, _, schedule = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    september_cutoff = pd.Timestamp(schedule.loc[schedule["month"] == "2021-09", "cutoff_at"].iloc[0])
    november_cutoff = pd.Timestamp(schedule.loc[schedule["month"] == "2021-11", "cutoff_at"].iloc[0])
    report = _report(2001, "T000", september_cutoff - timedelta(days=10))
    rejection = PublicKapParsingRejection(
        notification_id=2002,
        ticker="T000",
        published_at=(november_cutoff - timedelta(days=5)).to_pydatetime(),
        raw_sha256="d" * 64,
        reason="statement taxonomy could not be safely parsed",
    )
    receipt = _receipt("T000", schedule["cutoff_at"].max())
    result = _build(universe, reports=[report], rejections=[rejection], receipts=[receipt])

    september = result.inventory.query("month == '2021-09' and ticker == 'T000'").iloc[0]
    november = result.inventory.query("month == '2021-11' and ticker == 'T000'").iloc[0]
    assert september["status"] == STATUS_FOUND
    assert september["visible_parse_rejection_count"] == 0
    assert november["status"] == STATUS_PARSING_REJECTED
    assert november["visible_parse_rejection_ids"] == "2002"


def test_receipt_ending_before_cutoff_reverts_later_cells_to_awaiting() -> None:
    signals, _, schedule = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    september_cutoff = pd.Timestamp(schedule.loc[schedule["month"] == "2021-09", "cutoff_at"].iloc[0])
    receipt = _receipt("T000", september_cutoff)
    result = _build(universe, receipts=[receipt])
    september = result.inventory.query("month == '2021-09' and ticker == 'T000'").iloc[0]
    october = result.inventory.query("month == '2021-10' and ticker == 'T000'").iloc[0]
    assert september["status"] == STATUS_NOT_FOUND
    assert october["status"] == STATUS_AWAITING_AUTHORIZED_SOURCE


def test_same_notification_id_cannot_be_both_parsed_and_rejected() -> None:
    signals, _, schedule = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    cutoff = pd.Timestamp(schedule.loc[schedule["month"] == "2021-09", "cutoff_at"].iloc[0])
    report = _report(3001, "T000", cutoff - timedelta(days=2))
    rejection = PublicKapParsingRejection(
        notification_id=3001,
        ticker="T000",
        published_at=(cutoff - timedelta(days=1)).to_pydatetime(),
        raw_sha256="e" * 64,
        reason="ambiguous parser result",
    )
    receipt = _receipt("T000", schedule["cutoff_at"].max())
    with pytest.raises(PublicKapSourceInventoryError, match="hem parsed report hem rejection"):
        _build(universe, reports=[report], rejections=[rejection], receipts=[receipt])


def test_foreign_ticker_receipt_fails() -> None:
    signals, _, schedule = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    receipt = _receipt("ZZZZ", schedule["cutoff_at"].max())
    with pytest.raises(PublicKapSourceInventoryError, match="historical universe disi"):
        _build(universe, receipts=[receipt])


def test_unauthorized_source_kind_fails() -> None:
    signals, _, schedule = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    receipt = replace(
        _receipt("T000", schedule["cutoff_at"].max()),
        source_kind="PUBLIC_WEB_SCRAPE",
    )
    with pytest.raises(PublicKapSourceInventoryError, match="yetkili kaynak"):
        _build(universe, receipts=[receipt])


def test_cutoff_schedule_drift_is_rejected_by_closed_policy_validator() -> None:
    signals, calendar, schedule = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    mutated = schedule.copy(deep=True)
    mutated.loc[0, "cutoff_at"] = pd.Timestamp(mutated.loc[0, "cutoff_at"]) + timedelta(minutes=1)
    with pytest.raises(PublicKapSourceInventoryError, match="authorized policy"):
        build_public_kap_source_inventory(
            universe,
            signal_dates=signals,
            trading_calendar=calendar,
            cutoff_schedule=mutated,
        )


def test_summary_is_structural_only_and_cannot_authorize_real_scoring() -> None:
    signals, _, _ = _real_policy_sources()
    universe = _synthetic_matrix(signals)
    result = _build(universe)
    assert result.summary["result"] == "PASS"
    assert result.summary["all_cells_classified"] is True
    assert result.summary["uses_pr25_visible_version_selector"] is True
    assert result.summary["source_presence_only"] is True
    assert result.summary["downstream_period_depth_verified"] is False
    assert result.summary["schema_family_coverage_verified"] is False
    assert result.summary["real_total_rasyo_scoring_authorized"] is False
