from __future__ import annotations

"""Offline, fail-closed source inventory for the real 60-cutoff KAP package.

This module does not fetch KAP.  It consumes only already-captured/authorized
source evidence and the closed historical BIST100 month+ticker matrix.  Every
one of the 60 x 100 cells must end in exactly one explicit source-coverage
state; missing evidence is never reinterpreted as NOT_FOUND.

FOUND is deliberately a source-presence state, not a downstream scoring-ready
claim.  Period-depth, taxonomy/schema-family coverage and module-specific
financial sufficiency remain later gates.
"""

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Iterable, Mapping

import pandas as pd

from src.analytics.historical_cutoff_execution_policy import (
    EXPECTED_END_MONTH,
    EXPECTED_MONTHS,
    EXPECTED_START_MONTH,
    validate_authorized_cutoff_execution_schedule,
)
from src.ingest.public_kap_pit import (
    PublicKapFinancialReport,
    PublicKapPitError,
    select_visible_financial_report_versions,
    validate_financial_report_set,
)


EXPECTED_MEMBERS_PER_MONTH = 100
EXPECTED_CELLS = EXPECTED_MONTHS * EXPECTED_MEMBERS_PER_MONTH

STATUS_FOUND = "FOUND"
STATUS_PARSING_REJECTED = "PARSING_REJECTED"
STATUS_NOT_FOUND = "NOT_FOUND"
STATUS_AWAITING_AUTHORIZED_SOURCE = "AWAITING_AUTHORIZED_SOURCE"
VALID_STATUSES = frozenset(
    {
        STATUS_FOUND,
        STATUS_PARSING_REJECTED,
        STATUS_NOT_FOUND,
        STATUS_AWAITING_AUTHORIZED_SOURCE,
    }
)

AUTHORIZED_SOURCE_KINDS = frozenset(
    {
        "KAP_DATA_DISTRIBUTION_REST",
        "IMMUTABLE_KAP_EXPORT",
    }
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class PublicKapSourceInventoryError(ValueError):
    pass


@dataclass(frozen=True)
class AuthorizedEnumerationReceipt:
    """Proof that one historical ticker was enumerated through a time boundary.

    `enumeration_complete=False` is allowed as an explicit partial receipt, but
    it can only yield AWAITING_AUTHORIZED_SOURCE.  A missing receipt has the
    same fail-closed result.
    """

    ticker: str
    coverage_end_at: datetime
    source_kind: str
    source_identity: str
    source_sha256: str
    enumeration_complete: bool


@dataclass(frozen=True)
class PublicKapParsingRejection:
    """A source notification known to exist but not safely parsed."""

    notification_id: int
    ticker: str
    published_at: datetime
    raw_sha256: str
    reason: str


@dataclass(frozen=True)
class PublicKapSourceInventoryResult:
    inventory: pd.DataFrame
    summary: Mapping[str, object]


def _ticker(value: object, field: str = "ticker") -> str:
    if not isinstance(value, str) or not value.strip():
        raise PublicKapSourceInventoryError(f"{field} dolu metin olmali")
    text = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,12}", text):
        raise PublicKapSourceInventoryError(f"{field} kanonik BIST kodu olmali")
    return text


def _aware(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PublicKapSourceInventoryError(f"{field} timezone-aware datetime olmali")
    return value


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise PublicKapSourceInventoryError(f"{field} 64 karakter kucuk harf hex olmali")
    return value


def _normalize_universe(universe: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(universe, pd.DataFrame):
        raise PublicKapSourceInventoryError("universe DataFrame olmali")
    required = {"month", "signal_date", "index_code", "ticker"}
    missing = required - set(universe.columns)
    if missing:
        raise PublicKapSourceInventoryError(f"universe missing columns: {sorted(missing)}")

    frame = universe[["month", "signal_date", "index_code", "ticker"]].copy()
    frame["month"] = frame["month"].astype(str).str.strip()
    frame["index_code"] = frame["index_code"].astype(str).str.strip().str.upper()
    frame["ticker"] = frame["ticker"].map(_ticker)
    try:
        frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="raise").dt.normalize()
    except Exception as exc:
        raise PublicKapSourceInventoryError("universe.signal_date gecersiz") from exc

    expected_months = [
        str(value)
        for value in pd.period_range(EXPECTED_START_MONTH, EXPECTED_END_MONTH, freq="M")
    ]
    observed_months = frame["month"].drop_duplicates().tolist()
    if observed_months != expected_months:
        raise PublicKapSourceInventoryError(
            "universe exact ordered 2021-08..2026-07 month sequence olmali"
        )
    if len(frame) != EXPECTED_CELLS:
        raise PublicKapSourceInventoryError(
            f"universe tam {EXPECTED_CELLS} month+ticker satiri icermeli"
        )
    if not frame["index_code"].eq("XU100").all():
        raise PublicKapSourceInventoryError("universe yalniz XU100 icermeli")
    if frame.duplicated(["month", "ticker"]).any():
        raise PublicKapSourceInventoryError("universe duplicate month+ticker iceriyor")

    counts = frame.groupby("month", sort=False)["ticker"].size()
    if not counts.eq(EXPECTED_MEMBERS_PER_MONTH).all():
        raise PublicKapSourceInventoryError("her ay tam 100 tarihsel BIST100 uyesi olmali")
    signal_counts = frame.groupby("month", sort=False)["signal_date"].nunique()
    if not signal_counts.eq(1).all():
        raise PublicKapSourceInventoryError("her ay tek signal_date icermeli")

    return frame.sort_values(["month", "ticker"], kind="stable").reset_index(drop=True)


def _normalize_receipts(
    receipts: Iterable[AuthorizedEnumerationReceipt],
    *,
    allowed_tickers: set[str],
) -> dict[str, AuthorizedEnumerationReceipt]:
    by_ticker: dict[str, AuthorizedEnumerationReceipt] = {}
    for receipt in receipts:
        if not isinstance(receipt, AuthorizedEnumerationReceipt):
            raise TypeError("receipts yalniz AuthorizedEnumerationReceipt icermeli")
        ticker = _ticker(receipt.ticker)
        if ticker != receipt.ticker:
            raise PublicKapSourceInventoryError("receipt.ticker kanonik buyuk harf olmali")
        if ticker not in allowed_tickers:
            raise PublicKapSourceInventoryError(f"receipt historical universe disi ticker: {ticker}")
        _aware(receipt.coverage_end_at, "receipt.coverage_end_at")
        if receipt.source_kind not in AUTHORIZED_SOURCE_KINDS:
            raise PublicKapSourceInventoryError("receipt.source_kind yetkili kaynak turu degil")
        if not isinstance(receipt.source_identity, str) or not receipt.source_identity.strip():
            raise PublicKapSourceInventoryError("receipt.source_identity dolu olmali")
        _sha256(receipt.source_sha256, "receipt.source_sha256")
        if type(receipt.enumeration_complete) is not bool:
            raise PublicKapSourceInventoryError("receipt.enumeration_complete bool olmali")
        if ticker in by_ticker:
            raise PublicKapSourceInventoryError(f"duplicate enumeration receipt: {ticker}")
        by_ticker[ticker] = receipt
    return by_ticker


def _normalize_rejections(
    rejections: Iterable[PublicKapParsingRejection],
    *,
    allowed_tickers: set[str],
    report_ids: set[int],
) -> tuple[PublicKapParsingRejection, ...]:
    rows: list[PublicKapParsingRejection] = []
    seen_ids: set[int] = set()
    for row in rejections:
        if not isinstance(row, PublicKapParsingRejection):
            raise TypeError("rejections yalniz PublicKapParsingRejection icermeli")
        if isinstance(row.notification_id, bool) or not isinstance(row.notification_id, int) or row.notification_id <= 0:
            raise PublicKapSourceInventoryError("rejection.notification_id pozitif int olmali")
        ticker = _ticker(row.ticker)
        if ticker != row.ticker:
            raise PublicKapSourceInventoryError("rejection.ticker kanonik buyuk harf olmali")
        if ticker not in allowed_tickers:
            raise PublicKapSourceInventoryError(f"rejection historical universe disi ticker: {ticker}")
        _aware(row.published_at, "rejection.published_at")
        _sha256(row.raw_sha256, "rejection.raw_sha256")
        if not isinstance(row.reason, str) or not row.reason.strip():
            raise PublicKapSourceInventoryError("rejection.reason dolu olmali")
        if row.notification_id in seen_ids:
            raise PublicKapSourceInventoryError("duplicate rejection notification_id")
        if row.notification_id in report_ids:
            raise PublicKapSourceInventoryError(
                "ayni notification_id hem parsed report hem rejection olamaz"
            )
        seen_ids.add(row.notification_id)
        rows.append(row)
    return tuple(sorted(rows, key=lambda item: (item.published_at, item.notification_id)))


def _schedule_by_month(
    *,
    signal_dates: pd.DataFrame,
    trading_calendar: pd.DataFrame,
    cutoff_schedule: pd.DataFrame,
) -> dict[str, pd.Timestamp]:
    try:
        schedule = validate_authorized_cutoff_execution_schedule(
            signal_dates,
            trading_calendar,
            cutoff_schedule,
        )
    except Exception as exc:
        raise PublicKapSourceInventoryError("cutoff_schedule authorized policy ile eslesmiyor") from exc
    if len(schedule) != EXPECTED_MONTHS:
        raise PublicKapSourceInventoryError("authorized cutoff schedule 60 satir olmali")
    return {
        str(row.month): pd.Timestamp(row.cutoff_at)
        for row in schedule.itertuples(index=False)
    }


def build_public_kap_source_inventory(
    universe: pd.DataFrame,
    *,
    signal_dates: pd.DataFrame,
    trading_calendar: pd.DataFrame,
    cutoff_schedule: pd.DataFrame,
    reports: Iterable[PublicKapFinancialReport] = (),
    rejections: Iterable[PublicKapParsingRejection] = (),
    receipts: Iterable[AuthorizedEnumerationReceipt] = (),
) -> PublicKapSourceInventoryResult:
    """Classify every closed month+ticker cell without silent omission.

    NOT_FOUND is possible only when a receipt proves authorized enumeration was
    complete through that cutoff.  Missing/partial receipts remain
    AWAITING_AUTHORIZED_SOURCE.  Any visible parse rejection dominates FOUND,
    because a rejected notification could be the PIT-valid latest version.
    """

    matrix = _normalize_universe(universe)
    schedule = _schedule_by_month(
        signal_dates=signal_dates,
        trading_calendar=trading_calendar,
        cutoff_schedule=cutoff_schedule,
    )
    allowed_tickers = set(matrix["ticker"])

    try:
        parsed_reports = validate_financial_report_set(reports)
    except (PublicKapPitError, TypeError) as exc:
        raise PublicKapSourceInventoryError("reports PR #25 contract'ini gecemedi") from exc
    foreign_reports = sorted({row.ticker for row in parsed_reports} - allowed_tickers)
    if foreign_reports:
        raise PublicKapSourceInventoryError(
            f"reports historical universe disi ticker iceriyor: {foreign_reports}"
        )
    report_ids = {row.notification_id for row in parsed_reports}
    normalized_rejections = _normalize_rejections(
        rejections,
        allowed_tickers=allowed_tickers,
        report_ids=report_ids,
    )
    receipts_by_ticker = _normalize_receipts(receipts, allowed_tickers=allowed_tickers)

    reports_by_ticker: dict[str, list[PublicKapFinancialReport]] = {}
    for row in parsed_reports:
        reports_by_ticker.setdefault(row.ticker, []).append(row)
    rejections_by_ticker: dict[str, list[PublicKapParsingRejection]] = {}
    for row in normalized_rejections:
        rejections_by_ticker.setdefault(row.ticker, []).append(row)

    output: list[dict[str, object]] = []
    for cell in matrix.itertuples(index=False):
        month = str(cell.month)
        ticker = str(cell.ticker)
        cutoff = schedule.get(month)
        if cutoff is None:
            raise PublicKapSourceInventoryError(f"cutoff missing for month {month}")
        cutoff_dt = cutoff.to_pydatetime()
        receipt = receipts_by_ticker.get(ticker)

        status: str
        selected: tuple[PublicKapFinancialReport, ...] = ()
        visible_rejections: tuple[PublicKapParsingRejection, ...] = ()
        reason: str

        if (
            receipt is None
            or not receipt.enumeration_complete
            or pd.Timestamp(receipt.coverage_end_at).tz_convert(cutoff.tz) < cutoff
        ):
            status = STATUS_AWAITING_AUTHORIZED_SOURCE
            reason = "authorized enumeration missing/incomplete through cutoff"
        else:
            visible_rejections = tuple(
                row
                for row in rejections_by_ticker.get(ticker, ())
                if row.published_at <= cutoff_dt
            )
            if visible_rejections:
                status = STATUS_PARSING_REJECTED
                reason = "one or more visible source notifications could not be safely parsed"
            else:
                # This is the exact PR #25 selector.  Do not reimplement latest-
                # visible semantics here: future restatements must stay invisible.
                selected = select_visible_financial_report_versions(
                    reports_by_ticker.get(ticker, ()),
                    cutoff_at=cutoff_dt,
                )
                if selected:
                    status = STATUS_FOUND
                    reason = "authorized enumeration complete and visible FR version(s) found"
                else:
                    status = STATUS_NOT_FOUND
                    reason = "authorized enumeration complete; no visible FR found by cutoff"

        if status not in VALID_STATUSES:
            raise AssertionError("unreachable inventory status")
        output.append(
            {
                "month": month,
                "signal_date": pd.Timestamp(cell.signal_date).date().isoformat(),
                "ticker": ticker,
                "cutoff_at": cutoff.isoformat(),
                "status": status,
                "selected_report_count": len(selected),
                "selected_notification_ids": ";".join(str(row.notification_id) for row in selected),
                "visible_parse_rejection_count": len(visible_rejections),
                "visible_parse_rejection_ids": ";".join(
                    str(row.notification_id) for row in visible_rejections
                ),
                "enumeration_complete": bool(receipt.enumeration_complete) if receipt else False,
                "enumeration_coverage_end_at": (
                    receipt.coverage_end_at.isoformat() if receipt else ""
                ),
                "source_kind": receipt.source_kind if receipt else "",
                "source_identity": receipt.source_identity if receipt else "",
                "source_sha256": receipt.source_sha256 if receipt else "",
                "reason": reason,
            }
        )

    inventory = pd.DataFrame(output)
    expected_keys = list(zip(matrix["month"], matrix["ticker"]))
    actual_keys = list(zip(inventory["month"], inventory["ticker"]))
    if len(inventory) != EXPECTED_CELLS or actual_keys != expected_keys:
        raise PublicKapSourceInventoryError("inventory 6000 closed cell'i birebir korumadi")
    if inventory["status"].isna().any() or not set(inventory["status"]).issubset(VALID_STATUSES):
        raise PublicKapSourceInventoryError("inventory classification incomplete")

    counts = inventory["status"].value_counts().to_dict()
    status_counts = {status: int(counts.get(status, 0)) for status in sorted(VALID_STATUSES)}
    if sum(status_counts.values()) != EXPECTED_CELLS:
        raise PublicKapSourceInventoryError("status counts do not consume all 6000 cells")

    summary: dict[str, object] = {
        "contract": "PUBLIC_KAP_SOURCE_INVENTORY_V1",
        "months": EXPECTED_MONTHS,
        "members_per_month": EXPECTED_MEMBERS_PER_MONTH,
        "total_cells": EXPECTED_CELLS,
        "status_counts": status_counts,
        "all_cells_classified": True,
        "uses_pr25_visible_version_selector": True,
        "source_presence_only": True,
        "downstream_period_depth_verified": False,
        "schema_family_coverage_verified": False,
        "real_total_rasyo_scoring_authorized": False,
        "result": "PASS",
    }
    return PublicKapSourceInventoryResult(inventory=inventory, summary=summary)
