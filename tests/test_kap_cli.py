from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from src.app import cli
from src.ingest.kap_raw import KapFactExtractionReport


class FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _mapping_file(tmp_path: Path) -> Path:
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({
        "mapping_profile": "CLI_TEST",
        "mapping_version": 2,
        "facts_path": "facts",
        "fields": {
            "fact_code": "code",
            "value": "value",
            "period_end": "periodEnd",
        },
    }), encoding="utf-8")
    return path


def test_extract_kap_facts_cli_calls_strict_pipeline(monkeypatch, tmp_path, capsys):
    mapping = _mapping_file(tmp_path)
    conn = FakeConn()
    captured = {}

    def fake_extract(conn_arg, config, **kwargs):
        captured.update(kwargs)
        captured["conn"] = conn_arg
        captured["profile"] = config.mapping_profile
        return KapFactExtractionReport(3, 2, 1, 8, ("D_BAD",))

    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr("src.ingest.kap_raw.extract_pending_kap_financial_facts", fake_extract)
    monkeypatch.setattr(sys, "argv", [
        "cli", "extract-kap-facts",
        "--mapping-config", str(mapping),
        "--analysis-at", "2026-08-04T19:00:00+03:00",
        "--source", "MKK_KAP_API",
        "--notification-type", "FINANCIAL_STATEMENT",
        "--limit", "25",
        "--retry-rejections",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["facts_written"] == 8
    assert payload["rejected_ids"] == ["D_BAD"]
    assert payload["mapping_profile"] == "CLI_TEST"
    assert captured["conn"] is conn
    assert captured["profile"] == "CLI_TEST"
    assert captured["limit"] == 25
    assert captured["retry_rejections"] is True
    assert captured["extracted_at"].utcoffset().total_seconds() == 3 * 3600
    assert conn.closed is True


def test_extract_kap_facts_cli_rejects_naive_time_before_pipeline(monkeypatch, tmp_path):
    mapping = _mapping_file(tmp_path)
    conn = FakeConn()
    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr(sys, "argv", [
        "cli", "extract-kap-facts",
        "--mapping-config", str(mapping),
        "--analysis-at", "2026-08-04T19:00:00",
    ])
    with pytest.raises(SystemExit, match="timezone offset"):
        cli.main()
    assert conn.closed is True


def test_sync_mkk_dry_run_rejects_naive_windows_without_database(monkeypatch, tmp_path):
    config = tmp_path / "api.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "cli", "sync-mkk-kap", "--no-persist",
        "--api-config", str(config),
        "--start", "2026-08-04T00:00:00",
        "--end", "2026-08-04T23:59:59+03:00",
    ])
    with pytest.raises(SystemExit, match="timezone offset"):
        cli.main()


def _semantic_file(tmp_path: Path) -> Path:
    path = tmp_path / "semantic.json"
    path.write_text(json.dumps({
        "mapping_profile": "SEM_CLI",
        "mapping_version": 1,
        "sector_family": "BANK",
        "fields": {
            "TOTAL_EQUITY": {"source_codes": ["EQ"], "nature": "INSTANT"},
        },
    }), encoding="utf-8")
    return path


def _derivation_file(tmp_path: Path) -> Path:
    path = tmp_path / "derivation.json"
    path.write_text(json.dumps({
        "derivation_profile": "DERIVE_CLI",
        "derivation_version": 1,
        "semantic_profile": "SEM_CLI",
        "semantic_version": 1,
        "total_equity_field": "EQ",
        "shares_out_field": "SH",
        "net_income_field": "NI",
        "target_periods": 8,
        "history_periods": 12,
    }), encoding="utf-8")
    return path


def test_map_semantic_cli_calls_versioned_pipeline(monkeypatch, tmp_path, capsys):
    from src.ingest.semantic_materialization import SemanticMappingReport
    semantic = _semantic_file(tmp_path)
    conn = FakeConn()
    captured = {}

    def fake_map(conn_arg, config, **kwargs):
        captured.update(kwargs)
        captured["conn"] = conn_arg
        captured["profile"] = config.mapping_profile
        return SemanticMappingReport(4, 3, 1, 12, ("BAD",))

    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr("src.ingest.semantic_materialization.map_pending_semantic_facts", fake_map)
    monkeypatch.setattr(sys, "argv", [
        "cli", "map-kap-semantic-facts",
        "--semantic-config", str(semantic),
        "--source-mapping-profile", "PORTAL_FACTS",
        "--source-mapping-version", "3",
        "--analysis-at", "2026-08-04T20:00:00+03:00",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["semantic_facts_written"] == 12
    assert payload["rejected_ids"] == ["BAD"]
    assert captured["profile"] == "SEM_CLI"
    assert captured["source_mapping_profile"] == "PORTAL_FACTS"
    assert captured["source_mapping_version"] == 3
    assert conn.closed is True


def test_map_semantic_cli_rejects_naive_time_before_database(monkeypatch, tmp_path):
    semantic = _semantic_file(tmp_path)
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "map-kap-semantic-facts",
        "--semantic-config", str(semantic),
        "--source-mapping-profile", "PORTAL_FACTS",
        "--source-mapping-version", "1",
        "--analysis-at", "2026-08-04T20:00:00",
    ])
    with pytest.raises(SystemExit, match="timezone offset"):
        cli.main()


def test_materialize_bank_facts_cli_uses_explicit_tickers_and_no_persist(monkeypatch, tmp_path, capsys):
    from src.ingest.bank_fact_materializer import BankMaterializationBatchReport
    derivation = _derivation_file(tmp_path)
    conn = FakeConn()
    captured = {}

    def fake_batch(conn_arg, **kwargs):
        captured.update(kwargs)
        captured["conn"] = conn_arg
        return BankMaterializationBatchReport(2, 2, 0, 16, {})

    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr("src.ingest.bank_fact_materializer.materialize_bank_metrics_batch", fake_batch)
    monkeypatch.setattr(sys, "argv", [
        "cli", "materialize-bank-facts",
        "--derivation-config", str(derivation),
        "--analysis-at", "2026-08-04T20:00:00+03:00",
        "--anchor", "2026-06-30",
        "--tickers", "garan, AKBNK",
        "--no-persist",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["metrics_written"] == 16
    assert payload["persisted"] is False
    assert captured["tickers"] == ["garan", "AKBNK"]
    assert captured["persist"] is False
    assert conn.closed is True


def test_materialize_bank_facts_cli_rejects_bad_anchor_before_database(monkeypatch, tmp_path):
    derivation = _derivation_file(tmp_path)
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "materialize-bank-facts",
        "--derivation-config", str(derivation),
        "--analysis-at", "2026-08-04T20:00:00+03:00",
        "--anchor", "bad-date",
    ])
    with pytest.raises(SystemExit, match="ISO date"):
        cli.main()


def _fake_batch_report():
    from datetime import date, datetime, timedelta, timezone
    return {
        "status": "PARTIAL",
        "analysis_at": datetime(2026, 5, 15, 20, 0, tzinfo=timezone(timedelta(hours=3))),
        "anchor_period_end": date(2026, 3, 31),
        "requested_count": 2,
        "result_count": 1,
        "rejected_count": 1,
        "ranking": [{"rank": 1, "ticker": "AKBNK", "total_rasyo_100": 66.7}],
        "rejections": [{"ticker": "BAD", "reason": "BOZUK"}],
    }


def test_run_kap_bank_batch_cli_persists_and_prints_summary(monkeypatch, capsys):
    from src.analytics.kap_bank_batch_persistence import PersistedKapBankBatch

    conn = FakeConn()
    captured = {}
    report = _fake_batch_report()

    def fake_preview(**kwargs):
        captured["preview"] = kwargs
        return report

    def fake_persist(conn_arg, report_arg, **kwargs):
        captured["persist"] = (conn_arg, report_arg, kwargs)
        return PersistedKapBankBatch("a" * 64, "PARTIAL", 1, 1, 1, 1)

    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr("src.analytics.kap_bank_batch_io.run_batch_preview_from_files", fake_preview)
    monkeypatch.setattr("src.analytics.kap_bank_batch_persistence.persist_kap_bank_batch_report", fake_persist)
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-kap-bank-batch",
        "--file", "disclosures.jsonl",
        "--contexts-config", "contexts.json",
        "--mapping-config", "facts.json",
        "--semantic-config", "semantic.json",
        "--derivation-config", "derive.json",
        "--analysis-at", "2026-05-15T20:00:00+03:00",
        "--anchor", "2026-03-31",
        "--horizon-days", "42",
        "--pipeline-version", "TEST_V7",
        "--batch-source", "TEST_SOURCE",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["persistence"]["run_key"] == "a" * 64
    assert payload["ranking"][0]["ticker"] == "AKBNK"
    assert captured["persist"][0] is conn
    assert captured["persist"][1] is report
    assert captured["persist"][2] == {
        "horizon_days": 42,
        "pipeline_version": "TEST_V7",
        "source": "TEST_SOURCE",
    }
    assert conn.closed is True


def test_run_kap_bank_batch_no_persist_does_not_touch_database(monkeypatch, capsys):
    report = _fake_batch_report()
    monkeypatch.setattr(
        "src.analytics.kap_bank_batch_io.run_batch_preview_from_files",
        lambda **kwargs: report,
    )
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-kap-bank-batch", "--no-persist",
        "--file", "disclosures.jsonl",
        "--contexts-config", "contexts.json",
        "--mapping-config", "facts.json",
        "--semantic-config", "semantic.json",
        "--derivation-config", "derive.json",
        "--analysis-at", "2026-05-15T20:00:00+03:00",
        "--anchor", "2026-03-31",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PARTIAL"


def test_show_bank_ranking_cli_reads_latest_snapshot(monkeypatch, capsys):
    conn = FakeConn()
    captured = {}

    def fake_fetch(conn_arg, **kwargs):
        captured["conn"] = conn_arg
        captured.update(kwargs)
        return [{"rank": 1, "ticker": "YKBNK", "total_rasyo_100": 70.66}]

    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr(
        "src.analytics.kap_bank_batch_persistence.fetch_latest_kap_bank_ranking",
        fake_fetch,
    )
    monkeypatch.setattr(sys, "argv", [
        "cli", "show-bank-ranking", "--asof", "2026-05-15",
        "--horizon-days", "42", "--limit", "10",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["ticker"] == "YKBNK"
    assert captured["conn"] is conn
    assert captured["asof_date"].isoformat() == "2026-05-15"
    assert captured["horizon_days"] == 42
    assert captured["limit"] == 10
    assert conn.closed is True


def test_show_bank_ranking_rejects_bad_date_before_database(monkeypatch):
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "show-bank-ranking", "--asof", "bad-date",
    ])
    with pytest.raises(SystemExit, match="ISO date"):
        cli.main()


def test_run_kap_bank_db_cli_reads_database_and_prints_summary(monkeypatch, capsys):
    from datetime import date, datetime, timedelta, timezone
    from src.analytics.kap_bank_batch_persistence import PersistedKapBankBatch
    from src.analytics.kap_bank_db_workflow import KapBankDatabaseBatchResult

    conn = FakeConn()
    captured = {}
    report = _fake_batch_report()

    def fake_run(conn_arg, **kwargs):
        captured["conn"] = conn_arg
        captured.update(kwargs)
        return KapBankDatabaseBatchResult(
            report=report,
            persistence=PersistedKapBankBatch("b" * 64, "PARTIAL", 1, 1, 1, 1),
            tickers=("AKBNK", "BAD"),
            disclosures_loaded=12,
            context_ready_count=1,
            context_rejections={"BAD": "MISSING"},
        )

    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr(
        "src.analytics.kap_bank_db_workflow.run_kap_bank_database_batch", fake_run,
    )
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-kap-bank-db",
        "--mapping-config", "config/mkk_kap_financial_facts_mapping.example.json",
        "--semantic-config", "config/kap_bank_semantic_mapping.official_v1.json",
        "--derivation-config", "config/bank_fact_derivation.official_v1.json",
        "--weights", "config/weights.json",
        "--analysis-at", "2026-05-15T20:00:00+03:00",
        "--anchor", "2026-03-31",
        "--tickers", "akbnk,BAD",
        "--horizon-days", "42",
        "--max-context-age-days", "5",
        "--pipeline-version", "DB_V8",
        "--batch-source", "RAW_TEST",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["persistence"]["run_key"] == "b" * 64
    assert payload["disclosures_loaded"] == 12
    assert payload["context_ready_count"] == 1
    assert captured["conn"] is conn
    assert captured["anchor_period_end"] == date(2026, 3, 31)
    assert captured["tickers"] == ["akbnk", "BAD"]
    assert captured["horizon_days"] == 42
    assert captured["max_context_age_days"] == 5
    assert captured["pipeline_version"] == "DB_V8"
    assert captured["total_weights"]["M2"] == pytest.approx(0.40)
    assert conn.closed is True


def test_run_kap_bank_db_rejects_naive_time_before_database(monkeypatch):
    monkeypatch.setattr(
        cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched"))
    )
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-kap-bank-db",
        "--mapping-config", "config/mkk_kap_financial_facts_mapping.example.json",
        "--semantic-config", "config/kap_bank_semantic_mapping.official_v1.json",
        "--derivation-config", "config/bank_fact_derivation.official_v1.json",
        "--analysis-at", "2026-05-15T20:00:00",
    ])
    with pytest.raises(SystemExit, match="timezone offset"):
        cli.main()


def _live_api_file(tmp_path: Path) -> Path:
    path = tmp_path / "live_api.json"
    path.write_text(json.dumps({
        "base_url": "https://api.provider.example.org",
        "api_key_header": "X-Api-Key",
        "path": "/kap/disclosures",
        "method": "GET",
        "items_path": "data.items",
        "next_cursor_path": "data.next",
        "cursor_param": "cursor",
        "start_param": "startAt",
        "end_param": "endAt",
        "fields": {
            "disclosure_id": "id",
            "published_at": "publishedAt",
        },
    }), encoding="utf-8")
    return path


def test_check_mkk_kap_is_database_independent(monkeypatch, tmp_path, capsys):
    from datetime import datetime, timezone
    from src.ingest.api.mkk_kap import KapApiProbeReport

    config = _live_api_file(tmp_path)
    monkeypatch.setenv("MKK_API_KEY", "secret")
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))

    def fake_probe(self, **kwargs):
        assert kwargs["validate_items_limit"] == 3
        return KapApiProbeReport(
            endpoint_url="https://api.provider.example.org/kap/disclosures",
            method="GET",
            items_seen=2,
            items_validated=2,
            next_cursor_present=False,
            checked_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
            first_disclosure_id="D1",
            last_disclosure_id="D2",
        )

    monkeypatch.setattr("src.ingest.api.mkk_kap.MkkKapApiClient.probe", fake_probe)
    monkeypatch.setattr(sys, "argv", [
        "cli", "check-mkk-kap",
        "--api-config", str(config),
        "--start", "2026-08-04T00:00:00+03:00",
        "--end", "2026-08-04T01:00:00+03:00",
        "--validate-items-limit", "3",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "OK"
    assert payload["items_seen"] == 2
    assert payload["last_disclosure_id"] == "D2"


def test_check_mkk_kap_placeholder_exits_cleanly_without_database(monkeypatch, tmp_path):
    config = _live_api_file(tmp_path)
    raw = json.loads(config.read_text(encoding="utf-8"))
    raw["base_url"] = "https://placeholder.example.invalid"
    config.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("MKK_API_KEY", "secret")
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "check-mkk-kap",
        "--api-config", str(config),
        "--start", "2026-08-04T00:00:00+03:00",
        "--end", "2026-08-04T00:05:00+03:00",
    ])
    with pytest.raises(SystemExit, match="health-check basarisiz.*placeholder"):
        cli.main()


def test_sync_mkk_no_persist_rejects_resume_before_database(monkeypatch):
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "sync-mkk-kap", "--no-persist", "--resume",
    ])
    with pytest.raises(SystemExit, match="checkpoint"):
        cli.main()


class SyncCursor:
    def __init__(self, row, lock_answer=True):
        self.row = row
        self.lock_answer = lock_answer
        self.executed = []
        self._last_sql = ""

    def execute(self, sql, params=None):
        self._last_sql = sql
        self.executed.append((sql, params))

    def fetchone(self):
        if "pg_try_advisory_lock" in self._last_sql:
            return (self.lock_answer,)
        if "pg_advisory_unlock" in self._last_sql:
            return (True,)
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class SyncConn(FakeConn):
    def __init__(self, row, lock_answer=True):
        super().__init__()
        self.cur = SyncCursor(row, lock_answer=lock_answer)

    def cursor(self):
        return self.cur


def test_sync_mkk_resume_uses_checkpoint_overlap_and_persists(monkeypatch, tmp_path, capsys):
    from datetime import datetime, timezone
    from src.ingest.api.mkk_kap import KapFetchResult

    config = _live_api_file(tmp_path)
    row = (
        "MKK_KAP_API", "disclosures", None,
        datetime(2026, 8, 3, tzinfo=timezone.utc),
        datetime(2026, 8, 4, tzinfo=timezone.utc),
        datetime(2026, 8, 4, 0, 5, tzinfo=timezone.utc),
        10, 2,
    )
    conn = SyncConn(row)
    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setenv("MKK_API_KEY", "secret")
    captured = {}

    def fake_fetch(self, **kwargs):
        captured.update(kwargs)
        return KapFetchResult(
            disclosures=(),
            next_cursor=None,
            pages_fetched=1,
            start_at=kwargs["start_at"],
            end_at=kwargs["end_at"],
            completed_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        )

    def fake_persist(conn_arg, result, **kwargs):
        assert conn_arg is conn
        captured["stream_name"] = kwargs["stream_name"]
        return 0

    monkeypatch.setattr("src.ingest.api.mkk_kap.MkkKapApiClient.fetch_disclosures", fake_fetch)
    monkeypatch.setattr("src.ingest.kap_raw.persist_kap_disclosures", fake_persist)
    monkeypatch.setattr(sys, "argv", [
        "cli", "sync-mkk-kap", "--resume",
        "--api-config", str(config),
        "--end", "2026-08-05T00:00:00+00:00",
        "--overlap-seconds", "600",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert captured["start_at"] == datetime(2026, 8, 3, 23, 50, tzinfo=timezone.utc)
    assert captured["end_at"] == datetime(2026, 8, 4, 23, 50, tzinfo=timezone.utc)
    assert captured["stream_name"] == "disclosures"
    assert payload["resumed"] is True
    assert payload["window_truncated"] is True
    assert payload["checkpoint_advanced"] is True
    assert any("pg_try_advisory_lock" in sql for sql, _ in conn.cur.executed)
    assert any("pg_advisory_unlock" in sql for sql, _ in conn.cur.executed)
    assert conn.closed is True


def test_validate_mkk_contract_cli_writes_lock_without_database(monkeypatch, tmp_path, capsys):
    config = _live_api_file(tmp_path)
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({"data": {"items": [{
        "id": "D1",
        "publishedAt": "2026-08-04T10:00:00+03:00",
        "company": {"ticker": "GARAN", "id": "101"},
        "type": "FINANCIAL_STATEMENT",
        "subject": "Rapor",
        "url": "https://kap.org.tr/tr/Bildirim/D1",
    }], "next": None}}), encoding="utf-8")
    lock = tmp_path / "contract.lock.json"
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "validate-mkk-contract",
        "--api-config", str(config),
        "--sample", str(sample),
        "--checked-at", "2026-08-04T12:00:00+03:00",
        "--contract-lock-out", str(lock),
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "OK"
    assert payload["items_validated"] == 1
    assert payload["contract_lock"] == str(lock)
    assert lock.exists()


def test_plan_mkk_backfill_cli_is_database_free(monkeypatch, tmp_path, capsys):
    out = tmp_path / "plan.json"
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "plan-mkk-backfill",
        "--start", "2026-08-01T00:00:00+03:00",
        "--end", "2026-08-03T06:00:00+03:00",
        "--max-window-hours", "24",
        "--overlap-seconds", "600",
        "--out", str(out),
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "OK"
    assert payload["window_count"] == 3
    assert json.loads(out.read_text(encoding="utf-8"))["window_count"] == 3


def test_sync_mkk_concurrent_worker_is_rejected_before_fetch(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    config = _live_api_file(tmp_path)
    conn = SyncConn(None, lock_answer=False)
    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setenv("MKK_API_KEY", "secret")
    monkeypatch.setattr(
        "src.ingest.api.mkk_kap.MkkKapApiClient.fetch_disclosures",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fetch touched")),
    )
    monkeypatch.setattr(sys, "argv", [
        "cli", "sync-mkk-kap",
        "--api-config", str(config),
        "--start", "2026-08-04T00:00:00+00:00",
        "--end", "2026-08-04T01:00:00+00:00",
    ])
    with pytest.raises(SystemExit, match="zaten calisiyor"):
        cli.main()
    assert any("pg_try_advisory_lock" in sql for sql, _ in conn.cur.executed)
    assert not any("kap_sync_state" in sql for sql, _ in conn.cur.executed)
    assert conn.closed is True


def test_check_mkk_kap_contract_lock_failure_stops_before_network(monkeypatch, tmp_path):
    from src.ingest.api.mkk_kap import KapApiConfigError

    config = _live_api_file(tmp_path)
    lock = tmp_path / "contract.lock.json"
    lock.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MKK_API_KEY", "secret")
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(
        "src.ingest.api.mkk_contract.verify_mkk_contract_lock",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KapApiConfigError("lock drift")),
    )
    monkeypatch.setattr(
        "src.ingest.api.mkk_kap.MkkKapApiClient.probe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network touched")),
    )
    monkeypatch.setattr(sys, "argv", [
        "cli", "check-mkk-kap",
        "--api-config", str(config),
        "--contract-lock", str(lock),
        "--start", "2026-08-04T00:00:00+03:00",
        "--end", "2026-08-04T00:05:00+03:00",
    ])
    with pytest.raises(SystemExit, match="lock drift"):
        cli.main()
