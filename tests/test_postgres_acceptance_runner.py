from __future__ import annotations

import subprocess

import pytest

import scripts.run_postgres_bank_acceptance as runner


def test_server_version_parser_and_postgres_16_gate(monkeypatch):
    assert runner.parse_server_version_num("160014\n") == 160014
    assert runner.parse_server_version_num("\n 160999 \n") == 160999
    with pytest.raises(RuntimeError, match="ayrıştırılamadı"):
        runner.parse_server_version_num("PostgreSQL 16.14")

    monkeypatch.setattr(runner, "psql", lambda *args: "160014\n")
    assert runner.assert_postgres_16() == 160014
    monkeypatch.setattr(runner, "psql", lambda *args: "170002\n")
    with pytest.raises(RuntimeError, match="PostgreSQL 16 gerekli"):
        runner.assert_postgres_16()


def test_acceptance_always_cleans_fixture_after_success(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "assert_postgres_16", lambda: 160014)
    monkeypatch.setattr(runner, "apply_fixture", lambda: calls.append("apply"))
    monkeypatch.setattr(runner, "assert_daily_reference", lambda: {"daily": "ok"})
    monkeypatch.setattr(runner, "assert_intraday_reference", lambda: {"intraday": "ok"})
    monkeypatch.setattr(runner, "cleanup_fixture", lambda: calls.append("cleanup"))
    result = runner.run_acceptance()
    assert result["status"] == "PASS"
    assert result["postgres_server_version_num"] == 160014
    assert calls == ["apply", "cleanup"]


def test_acceptance_cleans_after_assertion_failure_and_can_keep_fixture(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "assert_postgres_16", lambda: 160014)
    monkeypatch.setattr(runner, "apply_fixture", lambda: calls.append("apply"))
    monkeypatch.setattr(runner, "assert_daily_reference", lambda: (_ for _ in ()).throw(AssertionError("boom")))
    monkeypatch.setattr(runner, "cleanup_fixture", lambda: calls.append("cleanup"))
    with pytest.raises(AssertionError, match="boom"):
        runner.run_acceptance()
    assert calls == ["apply", "cleanup"]

    calls.clear()
    monkeypatch.setattr(runner, "assert_daily_reference", lambda: {})
    monkeypatch.setattr(runner, "assert_intraday_reference", lambda: {})
    runner.run_acceptance(keep_fixture=True)
    assert calls == ["apply"]


def test_psql_timeout_is_controlled(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="psql", timeout=runner.PSQL_TIMEOUT_SECONDS)

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="tamamlanmadi"):
        runner.psql("-c", "SELECT 1")


def test_partial_fixture_apply_still_attempts_cleanup_without_masking_root_error(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "assert_postgres_16", lambda: 160014)
    monkeypatch.setattr(runner, "apply_fixture", lambda: (_ for _ in ()).throw(RuntimeError("migration boom")))
    monkeypatch.setattr(runner, "cleanup_fixture", lambda: calls.append("cleanup"))
    with pytest.raises(RuntimeError, match="migration boom"):
        runner.run_acceptance()
    assert calls == ["cleanup"]

    monkeypatch.setattr(runner, "cleanup_fixture", lambda: (_ for _ in ()).throw(RuntimeError("cleanup boom")))
    with pytest.raises(RuntimeError, match="migration boom"):
        runner.run_acceptance()
