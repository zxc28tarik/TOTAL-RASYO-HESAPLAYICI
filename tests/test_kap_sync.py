from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import subprocess
import sys

import pytest

from src.ingest.kap_sync import (
    KapSyncCheckpoint,
    acquire_kap_sync_lock,
    load_kap_sync_checkpoint,
    plan_kap_backfill_windows,
    plan_kap_sync_window,
    release_kap_sync_lock,
)


class Cursor:
    def __init__(self, row=None):
        self.row = row
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Conn:
    def __init__(self, row=None):
        self.cur = Cursor(row)

    def cursor(self):
        return self.cur


def aware(day: int, hour: int = 0, minute: int = 0):
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc)


def checkpoint():
    return KapSyncCheckpoint(
        source="MKK_KAP_API",
        stream_name="disclosures",
        cursor_value=None,
        window_start=aware(3),
        window_end=aware(4),
        last_success_at=aware(4, 1),
        rows_seen=10,
        pages_fetched=2,
    )


def test_load_checkpoint_reads_strict_eight_column_contract():
    cp = checkpoint()
    row = (
        cp.source, cp.stream_name, cp.cursor_value, cp.window_start,
        cp.window_end, cp.last_success_at, cp.rows_seen, cp.pages_fetched,
    )
    conn = Conn(row)
    loaded = load_kap_sync_checkpoint(conn)
    assert loaded == cp
    sql, params = conn.cur.executed[0]
    assert "FROM raw.kap_sync_state" in sql
    assert params == ("MKK_KAP_API", "disclosures")


def test_resume_plan_overlaps_checkpoint_and_caps_window():
    plan = plan_kap_sync_window(
        requested_start=None,
        requested_end=aware(7),
        checkpoint=checkpoint(),
        resume=True,
        overlap_seconds=300,
        max_window_hours=24,
    )
    assert plan.start_at == aware(4) - timedelta(minutes=5)
    assert plan.end_at == plan.start_at + timedelta(hours=24)
    assert plan.truncated is True
    assert plan.checkpoint_window_end == aware(4)


def test_resume_start_is_a_floor_and_no_checkpoint_requires_start():
    plan = plan_kap_sync_window(
        requested_start=aware(4, 12),
        requested_end=aware(5),
        checkpoint=checkpoint(),
        resume=True,
        overlap_seconds=300,
        max_window_hours=24,
    )
    assert plan.start_at == aware(4, 12)
    with pytest.raises(ValueError, match="checkpoint yokken"):
        plan_kap_sync_window(
            requested_start=None,
            requested_end=aware(5),
            checkpoint=None,
            resume=True,
        )


def test_completed_checkpoint_with_cursor_is_rejected_fail_closed():
    bad = KapSyncCheckpoint(
        source="MKK_KAP_API",
        stream_name="disclosures",
        cursor_value="UNFINISHED",
        window_start=aware(3),
        window_end=aware(4),
        last_success_at=aware(4, 1),
        rows_seen=10,
        pages_fetched=2,
    )
    with pytest.raises(ValueError, match="cursor_value"):
        plan_kap_sync_window(
            requested_start=None,
            requested_end=aware(5),
            checkpoint=bad,
            resume=True,
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"overlap_seconds": True}, "overlap_seconds"),
        ({"overlap_seconds": 86401}, "overlap_seconds"),
        ({"max_window_hours": float("inf")}, "max_window_hours"),
        ({"max_window_hours": 0}, "max_window_hours"),
    ],
)
def test_sync_plan_rejects_unsafe_controls(kwargs, message):
    with pytest.raises(ValueError, match=message):
        plan_kap_sync_window(
            requested_start=aware(4),
            requested_end=aware(5),
            **kwargs,
        )


def test_runtime_safety_migration_has_quarantine_and_run_guards():
    sql = (
        Path(__file__).resolve().parents[1]
        / "sql"
        / "019_mkk_kap_runtime_safety.sql"
    ).read_text(encoding="utf-8").lower()
    for token in (
        "create table if not exists raw.kap_sync_runs",
        "create table if not exists raw.kap_api_quarantine",
        "ck_kap_sync_run_status_count",
        "ck_kap_quarantine_sha",
        "idx_kap_quarantine_stream_seen",
    ):
        assert token in sql


def test_mkk_runtime_self_audit_runs_directly_without_pythonpath():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts/self_audit_mkk_runtime.py"), "--smoke"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "PASS"
    assert report["counts"]["uncontrolled"] == 0
    assert report["counts"]["silent_accept"] == 0


def test_backfill_windows_cover_range_with_controlled_overlap():
    windows = plan_kap_backfill_windows(
        start_at=aware(1),
        end_at=aware(3, 6),
        max_window_hours=24,
        overlap_seconds=600,
    )
    assert len(windows) == 3
    assert windows[0].start_at == aware(1)
    assert windows[0].end_at == aware(2)
    assert windows[0].overlap_seconds == 0
    assert windows[1].start_at == aware(1, 23, 50)
    assert windows[1].end_at == aware(2, 23, 50)
    assert windows[1].overlap_seconds == 600
    assert windows[-1].end_at == aware(3, 6)


def test_backfill_rejects_overlap_that_prevents_progress():
    with pytest.raises(ValueError, match="pencere suresinden kucuk"):
        plan_kap_backfill_windows(
            start_at=aware(1), end_at=aware(2),
            max_window_hours=1, overlap_seconds=3600,
        )


class LockCursor(Cursor):
    def __init__(self, answers):
        super().__init__()
        self.answers = list(answers)

    def fetchone(self):
        return self.answers.pop(0)


class LockConn:
    def __init__(self, answers):
        self.cur = LockCursor(answers)

    def cursor(self):
        return self.cur


def test_sync_advisory_lock_acquire_and_release_use_same_key():
    conn = LockConn([(True,), (True,)])
    key = acquire_kap_sync_lock(conn, source="MKK_KAP_FINANCIALS", stream_name="disclosures")
    release_kap_sync_lock(conn, key)
    assert key == "total_rasyo:kap_sync:MKK_KAP_FINANCIALS:disclosures"
    assert "pg_try_advisory_lock" in conn.cur.executed[0][0]
    assert "pg_advisory_unlock" in conn.cur.executed[1][0]
    assert conn.cur.executed[0][1] == (key,)
    assert conn.cur.executed[1][1] == (key,)


def test_sync_advisory_lock_rejects_concurrent_worker():
    conn = LockConn([(False,)])
    with pytest.raises(RuntimeError, match="zaten calisiyor"):
        acquire_kap_sync_lock(conn, source="MKK_KAP_FINANCIALS", stream_name="disclosures")


def test_mkk_onboarding_self_audit_runs_directly_without_pythonpath():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts/self_audit_mkk_onboarding.py"), "--smoke"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "PASS"
    assert report["counts"]["uncontrolled"] == 0
    assert report["counts"]["silent_accept"] == 0
