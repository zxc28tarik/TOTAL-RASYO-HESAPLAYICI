from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.app import cli
from src.ingest.api.mkk_kap import KapContractSampleCapture


def _config_file(tmp_path: Path) -> Path:
    path = tmp_path / "api.json"
    path.write_text(json.dumps({
        "base_url": "https://apiportal.mkk.com.tr",
        "api_key_header": "X-API-Key",
        "path": "/registered/product/path",
        "method": "GET",
        "items_path": "data.items",
        "next_cursor_path": "data.next",
        "cursor_param": "cursor",
        "start_param": "startAt",
        "end_param": "endAt",
        "fields": {"disclosure_id": "id", "published_at": "publishedAt"},
        "source_name": "MKK_TEST",
    }), encoding="utf-8")
    return path


def test_capture_mkk_sample_cli_never_touches_database(monkeypatch, tmp_path, capsys):
    config_path = _config_file(tmp_path)
    sample_path = tmp_path / "sample.json"
    meta_path = tmp_path / "sample.meta.json"
    payload = {"data": {"items": [{
        "id": "D1", "publishedAt": "2026-08-05T01:00:00+03:00"
    }], "next": None}}
    from src.ingest.api.mkk_kap import _canonical_json
    capture = KapContractSampleCapture(
        payload=payload,
        payload_sha256=_canonical_json(payload)[1],
        endpoint_url="https://apiportal.mkk.com.tr/registered/product/path",
        method="GET",
        source_name="MKK_TEST",
        start_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, 1, tzinfo=timezone.utc),
        captured_at=datetime(2026, 8, 5, 1, tzinfo=timezone.utc),
        items_seen=1,
        items_validated=1,
        next_cursor_present=False,
    )

    monkeypatch.setenv("MKK_API_KEY", "top-secret")
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(
        "src.ingest.api.mkk_kap.MkkKapApiClient.capture_contract_sample",
        lambda self, **kwargs: capture,
    )
    monkeypatch.setattr(sys, "argv", [
        "cli", "capture-mkk-sample",
        "--api-config", str(config_path),
        "--start", "2026-08-05T00:00:00+00:00",
        "--end", "2026-08-05T01:00:00+00:00",
        "--out", str(sample_path),
        "--metadata-out", str(meta_path),
    ])
    cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "OK"
    assert output["authentication_material_persisted"] is False
    assert sample_path.exists() and meta_path.exists()
    assert "top-secret" not in sample_path.read_text(encoding="utf-8")
    assert "top-secret" not in meta_path.read_text(encoding="utf-8")


def test_capture_mkk_sample_cli_requires_metadata_output(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [
        "cli", "capture-mkk-sample",
        "--api-config", str(_config_file(tmp_path)),
        "--start", "2026-08-05T00:00:00+00:00",
        "--end", "2026-08-05T01:00:00+00:00",
        "--out", str(tmp_path / "sample.json"),
    ])
    with pytest.raises(SystemExit, match="metadata-out"):
        cli.main()


def test_validate_mkk_suite_cli_prints_report(monkeypatch, tmp_path, capsys):
    class Suite:
        @classmethod
        def from_json_file(cls, path):
            assert path == str(tmp_path / "suite.json")
            return cls()

    class Report:
        def to_dict(self):
            return {"suite_name": "TEST", "live_ready": False, "products": []}

    monkeypatch.setattr("src.ingest.api.mkk_suite.MkkProductSuite", Suite)
    monkeypatch.setattr("src.ingest.api.mkk_suite.validate_mkk_product_suite", lambda *a, **k: Report())
    monkeypatch.setattr(sys, "argv", [
        "cli", "validate-mkk-suite",
        "--suite-config", str(tmp_path / "suite.json"),
        "--checked-at", "2026-08-05T02:00:00+03:00",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "OK"
    assert payload["suite_name"] == "TEST"


def test_plan_mkk_suite_cli_writes_plan_with_force_guard(monkeypatch, tmp_path, capsys):
    from datetime import timedelta
    from src.ingest.api.mkk_suite import MkkProductBackfillPlan
    from src.ingest.kap_sync import KapBackfillWindow

    class Suite:
        suite_name = "TEST"
        suite_version = 1

        @classmethod
        def from_json_file(cls, path):
            return cls()

    class Validation:
        live_ready = False

    start = datetime(2026, 8, 5, tzinfo=timezone.utc)
    plan = MkkProductBackfillPlan(
        product_name="financials", source_name="MKK_FIN", stream_name="disclosures",
        config_sha256="a" * 64, live_ready=False, api_key_present=False,
        windows=(KapBackfillWindow(1, start, start + timedelta(hours=1), 0),),
    )
    monkeypatch.setattr("src.ingest.api.mkk_suite.MkkProductSuite", Suite)
    monkeypatch.setattr("src.ingest.api.mkk_suite.validate_mkk_product_suite", lambda *a, **k: Validation())
    monkeypatch.setattr("src.ingest.api.mkk_suite.plan_mkk_suite_backfill", lambda *a, **k: (plan,))
    target = tmp_path / "plan.json"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "cli", "plan-mkk-suite-backfill",
        "--suite-config", "suite.json",
        "--checked-at", "2026-08-05T02:00:00+03:00",
        "--start", "2026-08-05T00:00:00+00:00",
        "--end", "2026-08-05T01:00:00+00:00",
        "--plan-out", str(target),
    ])
    with pytest.raises(SystemExit, match="--force"):
        cli.main()

    monkeypatch.setattr(sys, "argv", [
        "cli", "plan-mkk-suite-backfill",
        "--suite-config", "suite.json",
        "--checked-at", "2026-08-05T02:00:00+03:00",
        "--start", "2026-08-05T00:00:00+00:00",
        "--end", "2026-08-05T01:00:00+00:00",
        "--plan-out", str(target), "--force",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_window_count"] == 1
    assert json.loads(target.read_text(encoding="utf-8"))["product_count"] == 1


def test_capture_cli_does_not_use_shared_default_out(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [
        "cli", "capture-mkk-sample",
        "--api-config", str(_config_file(tmp_path)),
        "--start", "2026-08-05T00:00:00+00:00",
        "--end", "2026-08-05T01:00:00+00:00",
        "--metadata-out", str(tmp_path / "meta.json"),
    ])
    with pytest.raises(SystemExit, match="--out"):
        cli.main()


def test_check_mkk_suite_readiness_cli_uses_database_without_network(monkeypatch, tmp_path, capsys):
    class Suite:
        suite_name = "LIVE"
        suite_version = 1
        @classmethod
        def from_json_file(cls, path):
            return cls()

    class Validation:
        live_ready = True
        def to_dict(self):
            return {"suite_name": "LIVE", "live_ready": True, "products": []}

    class Database:
        ready = True
        def to_dict(self):
            return {"ready": True, "postgres_16": True, "missing_relations": []}

    class Plan:
        windows = (1, 2)
        def to_dict(self):
            return {"product_name": "financials", "window_count": 2}

    monkeypatch.setattr("src.ingest.api.mkk_suite.MkkProductSuite", Suite)
    monkeypatch.setattr("src.ingest.api.mkk_suite.validate_mkk_product_suite", lambda *a, **k: Validation())
    monkeypatch.setattr("src.ingest.api.mkk_suite.plan_mkk_suite_backfill", lambda *a, **k: (Plan(),))
    monkeypatch.setattr("src.ingest.mkk_suite_sync.check_mkk_suite_database_readiness", lambda conn: Database())
    monkeypatch.setattr(cli, "get_conn", lambda: type("Conn", (), {"close": lambda self: None})())
    monkeypatch.setattr(sys, "argv", [
        "cli", "check-mkk-suite-readiness",
        "--suite-config", str(tmp_path / "suite.json"),
        "--checked-at", "2026-08-05T02:00:00+03:00",
        "--start", "2026-08-01T00:00:00+03:00",
        "--end", "2026-08-05T00:00:00+03:00",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "READY"
    assert payload["total_window_count"] == 2


def test_sync_mkk_suite_cli_persists_report_and_exits_two_on_partial(monkeypatch, tmp_path, capsys):
    from src.ingest.mkk_suite_sync import MkkProductSyncResult, MkkSuiteSyncReport

    class Suite:
        suite_name = "LIVE"
        suite_version = 1
        @classmethod
        def from_json_file(cls, path):
            return cls()

    class Validation:
        live_ready = True

    class Database:
        ready = True
        def to_dict(self):
            return {"ready": True}

    product = MkkProductSyncResult(
        product_name="financials", source_name="SRC", stream_name="disclosures",
        config_sha256="a" * 64, status="QUARANTINED", windows_completed=1,
        attempts=1, rows_persisted=0, pages_fetched=1, quarantined_count=1,
        requested_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
        last_window_start=datetime(2026, 8, 4, tzinfo=timezone.utc),
        last_window_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    report = MkkSuiteSyncReport(
        run_key="f" * 64, suite_name="LIVE", suite_version=1,
        started_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 5, 1, tzinfo=timezone.utc),
        requested_start=datetime(2026, 8, 4, tzinfo=timezone.utc),
        requested_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
        resume=False, continue_on_error=True,
        max_windows_per_product=1, max_product_attempts=1,
        database_ready=True, products=(product,),
    )
    persisted = []
    monkeypatch.setattr("src.ingest.api.mkk_suite.MkkProductSuite", Suite)
    monkeypatch.setattr("src.ingest.api.mkk_suite.validate_mkk_product_suite", lambda *a, **k: Validation())
    monkeypatch.setattr("src.ingest.mkk_suite_sync.run_mkk_product_suite_sync", lambda *a, **k: (report, Database()))
    monkeypatch.setattr("src.ingest.mkk_suite_sync.persist_mkk_suite_sync_report", lambda conn, item: persisted.append(item))
    monkeypatch.setattr(cli, "get_conn", lambda: type("Conn", (), {"close": lambda self: None})())
    monkeypatch.setattr(sys, "argv", [
        "cli", "sync-mkk-suite",
        "--suite-config", str(tmp_path / "suite.json"),
        "--checked-at", "2026-08-05T02:00:00+03:00",
        "--start", "2026-08-04T00:00:00+03:00",
        "--end", "2026-08-05T00:00:00+03:00",
        "--continue-on-error", "--quarantine-invalid-items",
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PARTIAL"
    assert persisted == [report]


def test_sync_mkk_suite_cli_prints_report_when_report_persistence_fails(monkeypatch, tmp_path, capsys):
    from src.ingest.mkk_suite_sync import MkkProductSyncResult, MkkSuiteSyncReport

    class Suite:
        suite_name = "LIVE"
        suite_version = 1
        @classmethod
        def from_json_file(cls, path):
            return cls()

    class Validation:
        live_ready = True

    class Database:
        ready = True
        def to_dict(self):
            return {"ready": True}

    product = MkkProductSyncResult(
        product_name="financials", source_name="SRC", stream_name="disclosures",
        config_sha256="a" * 64, status="COMPLETE", windows_completed=1,
        attempts=1, rows_persisted=2, pages_fetched=1, quarantined_count=0,
        requested_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
        last_window_start=datetime(2026, 8, 4, tzinfo=timezone.utc),
        last_window_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
        checkpoint_window_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    report = MkkSuiteSyncReport(
        run_key="e" * 64, suite_name="LIVE", suite_version=1,
        started_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 5, 1, tzinfo=timezone.utc),
        requested_start=datetime(2026, 8, 4, tzinfo=timezone.utc),
        requested_end=datetime(2026, 8, 5, tzinfo=timezone.utc),
        resume=False, continue_on_error=False,
        max_windows_per_product=1, max_product_attempts=1,
        database_ready=True, products=(product,),
    )
    monkeypatch.setattr("src.ingest.api.mkk_suite.MkkProductSuite", Suite)
    monkeypatch.setattr("src.ingest.api.mkk_suite.validate_mkk_product_suite", lambda *a, **k: Validation())
    monkeypatch.setattr("src.ingest.mkk_suite_sync.run_mkk_product_suite_sync", lambda *a, **k: (report, Database()))
    monkeypatch.setattr(
        "src.ingest.mkk_suite_sync.persist_mkk_suite_sync_report",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db report failed")),
    )
    monkeypatch.setattr(cli, "get_conn", lambda: type("Conn", (), {"close": lambda self: None})())
    target = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "cli", "sync-mkk-suite",
        "--suite-config", str(tmp_path / "suite.json"),
        "--checked-at", "2026-08-05T02:00:00+03:00",
        "--start", "2026-08-04T00:00:00+03:00",
        "--end", "2026-08-05T00:00:00+03:00",
        "--report-out", str(target),
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "COMPLETE"
    assert payload["suite_report_persisted"] is False
    assert "db report failed" in payload["suite_report_persistence_error"]
    assert json.loads(target.read_text(encoding="utf-8"))["run_key"] == report.run_key
