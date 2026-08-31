from __future__ import annotations

from datetime import timedelta
import gzip
from pathlib import Path

import pandas as pd

from src.analytics.historical_cutoff_execution_policy import (
    build_authorized_cutoff_execution_schedule,
)
from src.ingest.public_kap_pit import PublicKapFinancialReport
from src.ingest.public_kap_source_inventory import (
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    AuthorizedEnumerationReceipt,
    PublicKapParsingRejection,
    build_public_kap_source_inventory,
)


ROOT = Path(__file__).resolve().parents[1]
SIGNALS = ROOT / "data/backtest_sources/xu100_signal_dates_yahoo_2021-08_2026-07.csv"
INDEX_CLOSES = ROOT / "data/backtest_sources/m3_source_package/index_closes.csv.gz"


def _policy_sources() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    signals = pd.read_csv(SIGNALS, dtype=str, keep_default_na=False)
    with gzip.open(INDEX_CLOSES, "rt", encoding="utf-8", newline="") as handle:
        closes = pd.read_csv(handle, dtype=str, keep_default_na=False)
    calendar = closes.loc[closes["index_code"] == "XU100", ["index_code", "trade_date"]].copy()
    schedule = build_authorized_cutoff_execution_schedule(signals, calendar)
    return signals, calendar, schedule


def _matrix(signals: pd.DataFrame) -> pd.DataFrame:
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


def _receipt(ticker: str, coverage_end_at: pd.Timestamp) -> AuthorizedEnumerationReceipt:
    return AuthorizedEnumerationReceipt(
        ticker=ticker,
        coverage_end_at=coverage_end_at.to_pydatetime(),
        source_kind="IMMUTABLE_KAP_EXPORT",
        source_identity="cutoff-boundary-fixture",
        source_sha256="a" * 64,
        enumeration_complete=True,
    )


def _report(notification_id: int, ticker: str, published_at: pd.Timestamp) -> PublicKapFinancialReport:
    return PublicKapFinancialReport(
        notification_id=notification_id,
        ticker=ticker,
        published_at=published_at.to_pydatetime(),
        report_year=2021,
        report_period="3 Aylık",
        disclosure_type="FR",
        source_url=f"https://kap.org.tr/tr/Bildirim/{notification_id}",
        raw_sha256="b" * 64,
        is_correction=False,
        previous_notification_date=None,
    )


def test_report_30_seconds_after_cutoff_stays_invisible_for_that_month() -> None:
    signals, calendar, schedule = _policy_sources()
    universe = _matrix(signals)
    cutoff = pd.Timestamp(schedule.loc[schedule["month"] == "2021-09", "cutoff_at"].iloc[0])
    report = _report(910001, "T000", cutoff + timedelta(seconds=30))
    receipt = _receipt("T000", pd.Timestamp(schedule["cutoff_at"].max()))

    result = build_public_kap_source_inventory(
        universe,
        signal_dates=signals,
        trading_calendar=calendar,
        cutoff_schedule=schedule,
        reports=[report],
        receipts=[receipt],
    )

    september = result.inventory.query("month == '2021-09' and ticker == 'T000'").iloc[0]
    assert september["status"] == STATUS_NOT_FOUND
    assert september["selected_report_count"] == 0
    assert september["selected_notification_ids"] == ""


def test_rejection_30_seconds_after_cutoff_cannot_poison_visible_report() -> None:
    signals, calendar, schedule = _policy_sources()
    universe = _matrix(signals)
    cutoff = pd.Timestamp(schedule.loc[schedule["month"] == "2021-09", "cutoff_at"].iloc[0])
    report = _report(920001, "T000", cutoff - timedelta(seconds=30))
    rejection = PublicKapParsingRejection(
        notification_id=920002,
        ticker="T000",
        published_at=(cutoff + timedelta(seconds=30)).to_pydatetime(),
        raw_sha256="c" * 64,
        reason="future-at-cutoff-boundary parser rejection",
    )
    receipt = _receipt("T000", pd.Timestamp(schedule["cutoff_at"].max()))

    result = build_public_kap_source_inventory(
        universe,
        signal_dates=signals,
        trading_calendar=calendar,
        cutoff_schedule=schedule,
        reports=[report],
        rejections=[rejection],
        receipts=[receipt],
    )

    september = result.inventory.query("month == '2021-09' and ticker == 'T000'").iloc[0]
    assert september["status"] == STATUS_FOUND
    assert september["selected_notification_ids"] == "920001"
    assert september["visible_parse_rejection_count"] == 0
    assert september["visible_parse_rejection_ids"] == ""
