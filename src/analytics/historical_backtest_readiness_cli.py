from __future__ import annotations

"""V24-G Part 4 — operator-facing, report-only readiness interface.

This module turns the database-backed readiness snapshot into deterministic
machine-readable output.  It does not execute a historical backtest and it does
not repair or persist source data.  Ordinary readiness findings return a
non-zero operational status while technical/configuration errors remain
exceptions for the outer CLI to classify separately.
"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from src.analytics.historical_backtest_readiness_db import (
    DatabaseBacktestReadinessSnapshot,
    audit_backtest_readiness_from_database,
)


READY_EXIT_CODE = 0
NOT_READY_EXIT_CODE = 3
TECHNICAL_ERROR_EXIT_CODE = 2


@dataclass(frozen=True)
class ReadinessCommandResult:
    exit_code: int
    payload: dict[str, Any]
    snapshot: DatabaseBacktestReadinessSnapshot


def _json_value(value: object) -> object:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()  # type: ignore[no-any-return]
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def build_readiness_payload(snapshot: DatabaseBacktestReadinessSnapshot) -> dict[str, Any]:
    """Return one deterministic report payload for automation and operators."""
    report = snapshot.report
    findings: list[dict[str, object]] = []
    if not report.findings.empty:
        for row in report.findings.itertuples(index=False):
            findings.append(
                {
                    "month": _json_value(row.month),
                    "signal_date": _json_value(row.signal_date),
                    "category": _json_value(row.category),
                    "code": _json_value(row.code),
                    "detail": _json_value(row.detail),
                }
            )

    return {
        "status": "READY" if report.ready else "NOT_READY",
        "start_month": report.start_month,
        "end_month": report.end_month,
        "expected_months": int(report.expected_months),
        "checked_months": int(report.checked_months),
        "finding_count": len(findings),
        "category_counts": report.category_counts(),
        "findings": findings,
    }


def render_readiness_json(snapshot: DatabaseBacktestReadinessSnapshot) -> str:
    return json.dumps(
        build_readiness_payload(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def write_readiness_artifacts(
    snapshot: DatabaseBacktestReadinessSnapshot,
    *,
    json_out: Optional[str] = None,
    findings_csv: Optional[str] = None,
) -> None:
    """Write report artifacts only; never mutate PostgreSQL source tables."""
    if json_out:
        Path(json_out).write_text(render_readiness_json(snapshot) + "\n", encoding="utf-8")
    if findings_csv:
        snapshot.report.findings.to_csv(findings_csv, index=False)


def run_readiness_command(
    conn: Any,
    *,
    wage_schedule_key: str,
    cutoff_profile_key: str,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
    json_out: Optional[str] = None,
    findings_csv: Optional[str] = None,
    audit_fn: Callable[..., DatabaseBacktestReadinessSnapshot] = audit_backtest_readiness_from_database,
) -> ReadinessCommandResult:
    """Run only the readiness audit and classify READY vs NOT_READY.

    `NOT_READY` is an expected operational outcome and therefore returns code 3
    with the full report.  The function intentionally has no backtest runner
    dependency, so a readiness command cannot accidentally start execution.
    """
    snapshot = audit_fn(
        conn,
        wage_schedule_key=wage_schedule_key,
        cutoff_profile_key=cutoff_profile_key,
        start_month=start_month,
        end_month=end_month,
        index_code=index_code,
        expected_months=expected_months,
    )
    write_readiness_artifacts(
        snapshot,
        json_out=json_out,
        findings_csv=findings_csv,
    )
    payload = build_readiness_payload(snapshot)
    exit_code = READY_EXIT_CODE if snapshot.report.ready else NOT_READY_EXIT_CODE
    return ReadinessCommandResult(
        exit_code=exit_code,
        payload=payload,
        snapshot=snapshot,
    )
