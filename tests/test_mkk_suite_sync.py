from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.ingest.api.mkk_kap import (
    KapApiTransportError,
    KapFetchResult,
    KapQuarantinedItem,
    MkkKapApiConfig,
)
from src.ingest.api.mkk_suite import (
    MkkProductDefinition,
    MkkProductSuite,
    MkkProductValidation,
    MkkSuiteValidationReport,
)
from src.ingest.kap_sync import KapSyncCheckpoint
from src.ingest.mkk_suite_sync import (
    MkkSuiteDatabaseReadiness,
    MkkSuiteSyncReport,
    check_mkk_suite_database_readiness,
    persist_mkk_suite_sync_report,
    run_mkk_product_suite_sync,
)

UTC = timezone.utc


def aware(hour: int) -> datetime:
    return datetime(2026, 8, 5, hour, tzinfo=UTC)


class SequenceCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class ReadinessConn:
    def __init__(self, rows):
        self.cursor_obj = SequenceCursor(rows)

    def cursor(self):
        return self.cursor_obj


def ready_db() -> MkkSuiteDatabaseReadiness:
    names = (
        "raw.kap_disclosures", "raw.kap_sync_state", "raw.kap_sync_runs",
        "raw.kap_api_quarantine", "raw.mkk_suite_sync_runs",
        "raw.mkk_suite_product_runs",
    )
    return MkkSuiteDatabaseReadiness(160014, names, names)


def test_database_readiness_requires_postgresql_16_and_all_relations():
    names = ready_db().required_relations
    conn = ReadinessConn([("160014",), tuple(names)])
    report = check_mkk_suite_database_readiness(conn)
    assert report.ready is True
    assert report.postgres_16 is True
    assert report.missing_relations == ()

    missing = ReadinessConn([("150009",), tuple([names[0], None, *names[2:]])])
    report2 = check_mkk_suite_database_readiness(missing)
    assert report2.ready is False
    assert report2.postgres_16 is False
    assert report2.missing_relations == (names[1],)


def product(name: str, source: str) -> MkkProductDefinition:
    return MkkProductDefinition(
        product_name=name,
        config_path=Path(f"/{name}.config.json"),
        contract_lock_path=Path(f"/{name}.lock.json"),
        sample_path=Path(f"/{name}.sample.json"),
        api_key_env=f"{name.upper()}_KEY",
        stream_name=f"{name}_stream",
        max_window_hours=1,
        overlap_seconds=0,
    )


def suite_and_validation():
    products = (product("financials", "SRC_FIN"), product("corporate", "SRC_CORP"))
    suite = MkkProductSuite("LIVE", 1, Path("/suite.json"), products)
    rows = tuple(
        MkkProductValidation(
            product_name=p.product_name,
            source_name=source,
            stream_name=p.stream_name,
            config_path=str(p.config_path),
            contract_lock_path=str(p.contract_lock_path),
            sample_path=str(p.sample_path),
            config_sha256=("a" if p.product_name == "financials" else "b") * 64,
            sample_sha256=("c" if p.product_name == "financials" else "d") * 64,
            items_validated=1,
            live_ready=True,
            api_key_env=p.api_key_env,
            api_key_present=True,
        )
        for p, source in zip(products, ("SRC_FIN", "SRC_CORP"))
    )
    validation = MkkSuiteValidationReport("LIVE", 1, aware(0), rows)
    return suite, validation


def configs():
    def make(source: str):
        return MkkKapApiConfig.from_dict({
            "base_url": "https://apiportal.mkk.com.tr",
            "api_key_header": "X-API-Key",
            "path": "/registered/product",
            "method": "GET",
            "items_path": "data.items",
            "fields": {"disclosure_id": "id", "published_at": "publishedAt"},
            "start_param": "startAt",
            "end_param": "endAt",
            "source_name": source,
        })
    return {"financials": make("SRC_FIN"), "corporate": make("SRC_CORP")}


def complete_result(source: str, start: datetime, end: datetime, *, pages=1):
    return KapFetchResult(
        disclosures=(), next_cursor=None, pages_fetched=pages,
        start_at=start, end_at=end, completed_at=end,
        quarantined_items=(), complete=True, source=source,
    )


def quarantine_result(source: str, start: datetime, end: datetime):
    item = KapQuarantinedItem(
        page_number=1, item_index=0, cursor_value=None, reason="bad",
        payload={"bad": True}, payload_sha256="e" * 64,
        fetched_at=end, source=source,
    )
    return KapFetchResult(
        disclosures=(), next_cursor=None, pages_fetched=1,
        start_at=start, end_at=end, completed_at=end,
        quarantined_items=(item,), complete=False, source=source,
    )


class DummyConn:
    pass


def patch_runtime(monkeypatch, result_factory):
    suite, validation = suite_and_validation()
    config_by_path = {
        str(p.config_path): cfg
        for p, cfg in zip(suite.products, configs().values())
    }
    state = {}
    locks = []
    releases = []

    monkeypatch.setattr(
        "src.ingest.mkk_suite_sync.check_mkk_suite_database_readiness",
        lambda conn: ready_db(),
    )
    monkeypatch.setattr(
        "src.ingest.mkk_suite_sync.MkkKapApiConfig.from_json_file",
        lambda path: config_by_path[str(path)],
    )
    def verify_lock(path, config):
        return {
            "config_sha256": "a" * 64 if config.source_name == "SRC_FIN" else "b" * 64
        }
    monkeypatch.setattr("src.ingest.mkk_suite_sync.verify_mkk_contract_lock", verify_lock)

    def acquire(conn, *, source, stream_name):
        key = f"{source}:{stream_name}"
        locks.append(key)
        return key

    def release(conn, key):
        releases.append(key)

    def load(conn, *, source, stream_name):
        return state.get((source, stream_name))

    def persist(conn, result, *, stream_name):
        if result.complete:
            state[(result.source, stream_name)] = KapSyncCheckpoint(
                source=result.source,
                stream_name=stream_name,
                cursor_value=None,
                window_start=result.start_at,
                window_end=result.end_at,
                last_success_at=result.completed_at,
                rows_seen=len(result.disclosures),
                pages_fetched=result.pages_fetched,
            )
        return len(result.disclosures)

    monkeypatch.setattr("src.ingest.mkk_suite_sync.acquire_kap_sync_lock", acquire)
    monkeypatch.setattr("src.ingest.mkk_suite_sync.release_kap_sync_lock", release)
    monkeypatch.setattr("src.ingest.mkk_suite_sync.load_kap_sync_checkpoint", load)
    monkeypatch.setattr("src.ingest.mkk_suite_sync.persist_kap_disclosures", persist)

    class Client:
        def __init__(self, config, api_key):
            self.config = config
            self.api_key = api_key

        def fetch_disclosures(self, **kwargs):
            return result_factory(self.config.source_name, kwargs)

    return suite, validation, state, locks, releases, Client


def test_suite_sync_completes_two_products_over_two_windows(monkeypatch):
    def factory(source, kwargs):
        return complete_result(source, kwargs["start_at"], kwargs["end_at"])

    suite, validation, state, locks, releases, client = patch_runtime(monkeypatch, factory)
    clock_values = iter([aware(0), aware(3)])
    report, readiness = run_mkk_product_suite_sync(
        DummyConn(), suite, validation,
        requested_start=aware(0), requested_end=aware(2),
        max_windows_per_product=2,
        environment={"FINANCIALS_KEY": "x", "CORPORATE_KEY": "y"},
        client_factory=client,
        clock=lambda: next(clock_values),
    )
    assert readiness.ready is True
    assert report.status == "COMPLETE"
    assert [row.status for row in report.products] == ["COMPLETE", "COMPLETE"]
    assert [row.windows_completed for row in report.products] == [2, 2]
    assert locks[0] == "MKK_SUITE:LIVE"
    assert releases[-1] == "MKK_SUITE:LIVE"
    assert set(state) == {("SRC_FIN", "financials_stream"), ("SRC_CORP", "corporate_stream")}


def test_suite_sync_fail_fast_marks_remaining_product_not_run(monkeypatch):
    def factory(source, kwargs):
        if source == "SRC_FIN":
            raise ValueError("broken product")
        return complete_result(source, kwargs["start_at"], kwargs["end_at"])

    suite, validation, _, _, _, client = patch_runtime(monkeypatch, factory)
    times = iter([aware(0), aware(1)])
    report, _ = run_mkk_product_suite_sync(
        DummyConn(), suite, validation,
        requested_start=aware(0), requested_end=aware(1),
        environment={"FINANCIALS_KEY": "x", "CORPORATE_KEY": "y"},
        client_factory=client, clock=lambda: next(times),
    )
    assert report.status == "FAILED"
    assert report.products[0].status == "FAILED"
    assert report.products[1].status == "NOT_RUN"


def test_suite_sync_continue_on_error_runs_later_products(monkeypatch):
    def factory(source, kwargs):
        if source == "SRC_FIN":
            raise ValueError("broken product")
        return complete_result(source, kwargs["start_at"], kwargs["end_at"])

    suite, validation, _, _, _, client = patch_runtime(monkeypatch, factory)
    times = iter([aware(0), aware(1)])
    report, _ = run_mkk_product_suite_sync(
        DummyConn(), suite, validation,
        requested_start=aware(0), requested_end=aware(1),
        continue_on_error=True,
        environment={"FINANCIALS_KEY": "x", "CORPORATE_KEY": "y"},
        client_factory=client, clock=lambda: next(times),
    )
    assert report.status == "PARTIAL"
    assert [row.status for row in report.products] == ["FAILED", "COMPLETE"]


def test_suite_sync_quarantine_stops_checkpoint_and_can_continue(monkeypatch):
    def factory(source, kwargs):
        if source == "SRC_FIN":
            return quarantine_result(source, kwargs["start_at"], kwargs["end_at"])
        return complete_result(source, kwargs["start_at"], kwargs["end_at"])

    suite, validation, state, _, _, client = patch_runtime(monkeypatch, factory)
    times = iter([aware(0), aware(1)])
    report, _ = run_mkk_product_suite_sync(
        DummyConn(), suite, validation,
        requested_start=aware(0), requested_end=aware(1),
        continue_on_error=True, quarantine_invalid_items=True,
        environment={"FINANCIALS_KEY": "x", "CORPORATE_KEY": "y"},
        client_factory=client, clock=lambda: next(times),
    )
    assert [row.status for row in report.products] == ["QUARANTINED", "COMPLETE"]
    assert ("SRC_FIN", "financials_stream") not in state
    assert report.total_quarantined == 1


def test_suite_sync_retries_transport_and_preserves_attempt_count(monkeypatch):
    calls = {"SRC_FIN": 0, "SRC_CORP": 0}

    def factory(source, kwargs):
        calls[source] += 1
        if source == "SRC_FIN" and calls[source] == 1:
            raise KapApiTransportError("temporary")
        return complete_result(source, kwargs["start_at"], kwargs["end_at"])

    suite, validation, _, _, _, client = patch_runtime(monkeypatch, factory)
    times = iter([aware(0), aware(1)])
    report, _ = run_mkk_product_suite_sync(
        DummyConn(), suite, validation,
        requested_start=aware(0), requested_end=aware(1),
        max_product_attempts=2,
        environment={"FINANCIALS_KEY": "x", "CORPORATE_KEY": "y"},
        client_factory=client, clock=lambda: next(times),
    )
    assert report.products[0].status == "COMPLETE"
    assert report.products[0].attempts == 2


def test_suite_sync_resume_up_to_date_does_not_call_client(monkeypatch):
    def factory(source, kwargs):
        raise AssertionError("client must not be called")

    suite, validation, state, _, _, client = patch_runtime(monkeypatch, factory)
    for product_row, source in zip(suite.products, ("SRC_FIN", "SRC_CORP")):
        state[(source, product_row.stream_name)] = KapSyncCheckpoint(
            source=source, stream_name=product_row.stream_name,
            cursor_value=None, window_start=aware(0), window_end=aware(2),
            last_success_at=aware(2), rows_seen=1, pages_fetched=1,
        )
    times = iter([aware(2), aware(3)])
    report, _ = run_mkk_product_suite_sync(
        DummyConn(), suite, validation,
        requested_start=None, requested_end=aware(1), resume=True,
        environment={"FINANCIALS_KEY": "x", "CORPORATE_KEY": "y"},
        client_factory=client, clock=lambda: next(times),
    )
    assert [row.status for row in report.products] == ["UP_TO_DATE", "UP_TO_DATE"]


class PersistCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class PersistConn:
    def __init__(self):
        self.cur = PersistCursor()
        self.entered = 0

    def cursor(self):
        return self.cur

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *_):
        return False


def test_persist_suite_report_writes_parent_and_product_rows():
    product_result = __import__("src.ingest.mkk_suite_sync", fromlist=["MkkProductSyncResult"]).MkkProductSyncResult(
        product_name="financials", source_name="SRC", stream_name="disclosures",
        config_sha256="a" * 64, status="COMPLETE", windows_completed=1,
        attempts=1, rows_persisted=2, pages_fetched=1, quarantined_count=0,
        requested_end=aware(1), last_window_start=aware(0), last_window_end=aware(1),
        checkpoint_window_end=aware(1),
    )
    report = MkkSuiteSyncReport(
        run_key="f" * 64, suite_name="LIVE", suite_version=1,
        started_at=aware(0), completed_at=aware(1), requested_start=aware(0),
        requested_end=aware(1), resume=False, continue_on_error=False,
        max_windows_per_product=1, max_product_attempts=1,
        database_ready=True, products=(product_result,),
    )
    conn = PersistConn()
    persist_mkk_suite_sync_report(conn, report)
    assert conn.entered == 1
    assert len(conn.cur.executed) == 2
    assert "INSERT INTO raw.mkk_suite_sync_runs" in conn.cur.executed[0][0]
    assert "INSERT INTO raw.mkk_suite_product_runs" in conn.cur.executed[1][0]


def test_suite_migration_contains_run_and_product_guards():
    sql = (Path(__file__).resolve().parents[1] / "sql" / "020_mkk_suite_sync.sql").read_text(encoding="utf-8").lower()
    for token in (
        "create table if not exists raw.mkk_suite_sync_runs",
        "create table if not exists raw.mkk_suite_product_runs",
        "ck_mkk_suite_product_error",
        "ck_mkk_suite_product_quarantine",
        "idx_mkk_suite_product_status",
    ):
        assert token in sql

def test_suite_sync_failure_after_first_window_preserves_progress(monkeypatch):
    calls = {"SRC_FIN": 0, "SRC_CORP": 0}

    def factory(source, kwargs):
        calls[source] += 1
        if source == "SRC_FIN" and calls[source] == 2:
            raise ValueError("second window broke")
        return complete_result(source, kwargs["start_at"], kwargs["end_at"], pages=2)

    suite, validation, _, _, _, client = patch_runtime(monkeypatch, factory)
    times = iter([aware(0), aware(3)])
    report, _ = run_mkk_product_suite_sync(
        DummyConn(), suite, validation,
        requested_start=aware(0), requested_end=aware(2),
        max_windows_per_product=2, continue_on_error=True,
        environment={"FINANCIALS_KEY": "x", "CORPORATE_KEY": "y"},
        client_factory=client, clock=lambda: next(times),
    )
    failed = report.products[0]
    assert failed.status == "FAILED"
    assert failed.windows_completed == 1
    assert failed.attempts == 2
    assert failed.pages_fetched == 2
    assert failed.checkpoint_window_end == aware(1)


def test_suite_run_key_changes_when_execution_policy_changes():
    from src.ingest.mkk_suite_sync import _make_run_key
    suite, validation = suite_and_validation()
    base = dict(
        started_at=aware(0), requested_start=aware(0), requested_end=aware(1),
        resume=False, continue_on_error=False, overlap_seconds=0,
        max_window_hours=1, max_windows_per_product=1,
        max_product_attempts=1, max_pages=10, quarantine_invalid_items=True,
    )
    key1 = _make_run_key(suite, validation, **base)
    key2 = _make_run_key(suite, validation, **{**base, "continue_on_error": True})
    key3 = _make_run_key(suite, validation, **{**base, "max_product_attempts": 2})
    assert len({key1, key2, key3}) == 3


def test_suite_sync_rejects_config_lock_drift_before_network(monkeypatch):
    def factory(source, kwargs):
        raise AssertionError("network must not be called")

    suite, validation, _, _, _, client = patch_runtime(monkeypatch, factory)
    monkeypatch.setattr(
        "src.ingest.mkk_suite_sync.verify_mkk_contract_lock",
        lambda path, config: {"config_sha256": "0" * 64},
    )
    times = iter([aware(0), aware(1)])
    report, _ = run_mkk_product_suite_sync(
        DummyConn(), suite, validation,
        requested_start=aware(0), requested_end=aware(1),
        continue_on_error=True,
        environment={"FINANCIALS_KEY": "x", "CORPORATE_KEY": "y"},
        client_factory=client, clock=lambda: next(times),
    )
    assert [row.status for row in report.products] == ["FAILED", "FAILED"]
    assert all("config/lock degisti" in row.error for row in report.products)


@pytest.mark.parametrize("mutation", ["source", "window", "cursor"])
def test_suite_sync_rejects_api_result_identity_drift(monkeypatch, mutation):
    def factory(source, kwargs):
        result = complete_result(source, kwargs["start_at"], kwargs["end_at"])
        if source != "SRC_FIN":
            return result
        if mutation == "source":
            return KapFetchResult(
                disclosures=(), next_cursor=None, pages_fetched=1,
                start_at=result.start_at, end_at=result.end_at,
                completed_at=result.completed_at, complete=True, source="OTHER",
            )
        if mutation == "window":
            return KapFetchResult(
                disclosures=(), next_cursor=None, pages_fetched=1,
                start_at=result.start_at, end_at=result.end_at + timedelta(minutes=1),
                completed_at=result.completed_at, complete=True, source=source,
            )
        return KapFetchResult(
            disclosures=(), next_cursor="NEXT", pages_fetched=1,
            start_at=result.start_at, end_at=result.end_at,
            completed_at=result.completed_at, complete=True, source=source,
        )

    suite, validation, state, _, _, client = patch_runtime(monkeypatch, factory)
    times = iter([aware(0), aware(1)])
    report, _ = run_mkk_product_suite_sync(
        DummyConn(), suite, validation,
        requested_start=aware(0), requested_end=aware(1),
        continue_on_error=True,
        environment={"FINANCIALS_KEY": "x", "CORPORATE_KEY": "y"},
        client_factory=client, clock=lambda: next(times),
    )
    assert report.products[0].status == "FAILED"
    assert ("SRC_FIN", "financials_stream") not in state


def test_suite_sync_self_audit_runs_directly_without_pythonpath():
    import json
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts/self_audit_mkk_suite_sync.py"), "--smoke"],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "PASS"
    assert payload["counts"]["uncontrolled"] == 0
    assert payload["counts"]["silent_accept"] == 0


def test_suite_report_rejects_all_not_run_products():
    from src.ingest.mkk_suite_sync import MkkProductSyncResult
    row = MkkProductSyncResult(
        product_name="p", source_name="s", stream_name="x",
        config_sha256="a" * 64, status="NOT_RUN", windows_completed=0,
        attempts=0, rows_persisted=0, pages_fetched=0, quarantined_count=0,
        requested_end=aware(1),
    )
    with pytest.raises(ValueError, match="denenmis urun"):
        MkkSuiteSyncReport(
            run_key="f" * 64, suite_name="LIVE", suite_version=1,
            started_at=aware(0), completed_at=aware(1), requested_start=aware(0),
            requested_end=aware(1), resume=False, continue_on_error=False,
            max_windows_per_product=1, max_product_attempts=1,
            database_ready=True, products=(row,),
        )
