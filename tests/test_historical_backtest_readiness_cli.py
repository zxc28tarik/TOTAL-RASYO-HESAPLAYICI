from __future__ import annotations

import json

import pandas as pd

from src.analytics.historical_backtest_readiness import BacktestReadinessReport
from src.analytics.historical_backtest_readiness_cli import (
    NOT_READY_EXIT_CODE,
    READY_EXIT_CODE,
    build_readiness_payload,
    render_readiness_json,
    run_readiness_command,
)
from src.analytics.historical_backtest_readiness_db import (
    DatabaseBacktestReadinessSnapshot,
)


def _snapshot(findings: pd.DataFrame, *, checked: int = 2, expected: int = 2):
    report = BacktestReadinessReport(
        start_month="2022-01",
        end_month="2022-02",
        expected_months=expected,
        checked_months=checked,
        findings=findings,
    )
    empty = pd.DataFrame()
    return DatabaseBacktestReadinessSnapshot(
        report=report,
        index_prices=empty,
        membership=empty,
        prices_daily=empty,
        wages=empty,
        cutoffs=empty,
        total_rasyo_results=empty,
        run_registry=empty,
    )


def _finding_frame():
    return pd.DataFrame(
        [
            {
                "month": "2022-02",
                "signal_date": pd.Timestamp("2022-02-01"),
                "category": "PRICE",
                "code": "EXACT_DAY_COVERAGE",
                "detail": "missing=['AAA'] duplicate=[] invalid=[]",
            }
        ]
    )


def test_ready_payload_is_machine_readable_and_zero_exit():
    snapshot = _snapshot(
        pd.DataFrame(columns=["month", "signal_date", "category", "code", "detail"])
    )
    payload = build_readiness_payload(snapshot)
    assert payload["status"] == "READY"
    assert payload["finding_count"] == 0
    assert payload["checked_months"] == 2
    assert payload["expected_months"] == 2
    assert all(value == 0 for value in payload["category_counts"].values())

    calls = []

    def audit_fn(conn, **kwargs):
        calls.append((conn, kwargs))
        return snapshot

    result = run_readiness_command(
        object(),
        wage_schedule_key="WAGE_AUDITED",
        cutoff_profile_key="CUTOFF_AUDITED",
        start_month="2022-01",
        end_month="2022-02",
        expected_months=2,
        audit_fn=audit_fn,
    )
    assert result.exit_code == READY_EXIT_CODE
    assert len(calls) == 1
    assert result.payload["status"] == "READY"


def test_not_ready_returns_three_and_preserves_full_finding():
    snapshot = _snapshot(_finding_frame())

    def audit_fn(conn, **kwargs):
        return snapshot

    result = run_readiness_command(
        object(),
        wage_schedule_key="WAGE_AUDITED",
        cutoff_profile_key="CUTOFF_AUDITED",
        start_month="2022-01",
        end_month="2022-02",
        expected_months=2,
        audit_fn=audit_fn,
    )
    assert result.exit_code == NOT_READY_EXIT_CODE
    assert result.payload["status"] == "NOT_READY"
    assert result.payload["finding_count"] == 1
    assert result.payload["findings"] == [
        {
            "month": "2022-02",
            "signal_date": "2022-02-01T00:00:00",
            "category": "PRICE",
            "code": "EXACT_DAY_COVERAGE",
            "detail": "missing=['AAA'] duplicate=[] invalid=[]",
        }
    ]


def test_checked_month_mismatch_cannot_render_ready_even_without_findings():
    snapshot = _snapshot(
        pd.DataFrame(columns=["month", "signal_date", "category", "code", "detail"]),
        checked=1,
        expected=2,
    )
    assert build_readiness_payload(snapshot)["status"] == "NOT_READY"


def test_json_and_csv_artifacts_are_report_only_outputs(tmp_path):
    snapshot = _snapshot(_finding_frame())
    json_out = tmp_path / "readiness.json"
    csv_out = tmp_path / "findings.csv"

    def audit_fn(conn, **kwargs):
        return snapshot

    result = run_readiness_command(
        object(),
        wage_schedule_key="WAGE_AUDITED",
        cutoff_profile_key="CUTOFF_AUDITED",
        start_month="2022-01",
        end_month="2022-02",
        expected_months=2,
        json_out=str(json_out),
        findings_csv=str(csv_out),
        audit_fn=audit_fn,
    )
    assert result.exit_code == NOT_READY_EXIT_CODE
    assert json.loads(json_out.read_text(encoding="utf-8"))["status"] == "NOT_READY"
    csv_frame = pd.read_csv(csv_out)
    assert list(csv_frame["code"]) == ["EXACT_DAY_COVERAGE"]


def test_rendered_json_is_deterministic_compact_json():
    snapshot = _snapshot(_finding_frame())
    first = render_readiness_json(snapshot)
    second = render_readiness_json(snapshot)
    assert first == second
    assert "\n" not in first
    assert json.loads(first)["category_counts"]["PRICE"] == 1
