from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from src.app import cli
from src.ingest.company_fact_materializer import CompanyMaterializationBatchReport


class FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def derivation_file(tmp_path: Path) -> Path:
    path = tmp_path / "company_derivation.json"
    path.write_text(json.dumps({
        "derivation_profile": "COMPANY_CLI",
        "derivation_version": 1,
        "semantic_profile": "SEM_CLI",
        "semantic_version": 1,
        "sector_families": ["NONFIN"],
        "target_periods": 2,
        "history_periods": 3,
        "field_map": {"revenue": "REVENUE", "total_assets": "TOTAL_ASSETS"},
        "shares_out_field": "SHARES_OUT",
        "issued_capital_field": "ISSUED_CAPITAL",
        "share_nominal_value": 1,
        "required_fields": ["revenue"],
        "minimum_present_fields": ["revenue", "total_assets"],
        "minimum_present_count": 1,
        "derive_gross_profit": True
    }), encoding="utf-8")
    return path


def test_materialize_company_cli_uses_explicit_tickers_and_no_persist(monkeypatch, tmp_path, capsys):
    config = derivation_file(tmp_path)
    conn = FakeConn()
    captured = {}

    def fake_batch(conn_arg, **kwargs):
        captured.update(kwargs)
        captured["conn"] = conn_arg
        return CompanyMaterializationBatchReport(2, 1, 1, 2, {"BAD": "NO_DATA"})

    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr(
        "src.ingest.company_fact_materializer.materialize_company_metrics_batch",
        fake_batch,
    )
    monkeypatch.setattr(sys, "argv", [
        "cli", "materialize-company-facts",
        "--derivation-config", str(config),
        "--analysis-at", "2026-08-05T16:00:00+03:00",
        "--anchor", "2026-06-30",
        "--tickers", "thyao, BAD",
        "--no-persist",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PARTIAL"
    assert payload["metrics_written"] == 2
    assert captured["tickers"] == ["thyao", "BAD"]
    assert captured["persist"] is False
    assert conn.closed is True


def test_materialize_company_cli_rejects_naive_time_before_database(monkeypatch, tmp_path):
    config = derivation_file(tmp_path)
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "materialize-company-facts",
        "--derivation-config", str(config),
        "--analysis-at", "2026-08-05T16:00:00",
        "--anchor", "2026-06-30",
    ])
    with pytest.raises(SystemExit, match="timezone offset"):
        cli.main()


def test_calc_company_ratios_cli_calls_core_only_pipeline(monkeypatch, capsys):
    conn = FakeConn()
    captured = {}

    def fake_run(conn_arg, **kwargs):
        captured.update(kwargs)
        captured["conn"] = conn_arg
        return pd.DataFrame([
            {"ticker": "AAA", "period_end": "2026-03-31", "version_tag": "D", "ratio_name": "ROE", "ratio_value": 0.2, "is_na": False},
            {"ticker": "BBB", "period_end": "2026-03-31", "version_tag": "D", "ratio_name": "ROE", "ratio_value": 0.1, "is_na": False},
        ])

    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr(
        "src.analytics.company_ratio_pipeline.run_company_core_ratios_asof", fake_run
    )
    monkeypatch.setattr(sys, "argv", [
        "cli", "calc-company-ratios",
        "--analysis-at", "2026-08-05T16:00:00+03:00",
        "--tickers", "aaa,bbb",
        "--since-period-end", "2025-01-01",
        "--no-persist",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "OK",
        "analysis_at": "2026-08-05T16:00:00+03:00",
        "ticker_count": 2,
        "ratio_row_count": 2,
        "persisted": False,
        "core_only": True,
    }
    assert captured["tickers"] == ["aaa", "bbb"]
    assert captured["since_period_end"].isoformat() == "2025-01-01"
    assert captured["persist"] is False
    assert conn.closed is True


def test_calc_company_ratios_rejects_bad_since_before_database(monkeypatch):
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "calc-company-ratios",
        "--analysis-at", "2026-08-05T16:00:00+03:00",
        "--since-period-end", "bad",
    ])
    with pytest.raises(SystemExit, match="ISO date"):
        cli.main()


def _valuation_file(tmp_path: Path) -> Path:
    path = tmp_path / "nonfin_valuation.json"
    path.write_text(json.dumps({
        "valuation_profile": "NONFIN_CLI",
        "valuation_version": 1,
        "source_derivation_profile": "DERIVE_CLI",
        "source_derivation_version": 1,
        "multiple_weights": {"PE": 0.3, "EV_EBIT": 0.3, "PS": 0.2, "PB": 0.2},
        "minimum_peer_count": 2,
        "full_confidence_peer_count": 3
    }), encoding="utf-8")
    return path


def test_run_nonfin_batch_cli_calls_pipeline(monkeypatch, tmp_path, capsys):
    valuation = _valuation_file(tmp_path)
    conn = FakeConn()
    captured = {}

    def fake_run(conn_arg, **kwargs):
        captured.update(kwargs)
        captured["conn"] = conn_arg
        return {
            "analysis_at": kwargs["analysis_at"],
            "anchor_period_end": kwargs["anchor_period_end"],
            "result_count": 1,
            "rejections": [],
            "results": [{
                "ticker": "THYAO",
                "valuation": {"status": "OK", "v_conf": 0.75, "V_mid": 400.0},
                "m2": {"m2": 0.72},
            }],
        }

    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr("src.analytics.nonfin_batch_pipeline.run_nonfin_batch", fake_run)
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-nonfin-batch",
        "--analysis-at", "2026-08-05T20:00:00+03:00",
        "--anchor", "2026-06-30",
        "--tickers", "thyao,asels",
        "--valuation-config", str(valuation),
        "--no-persist",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "OK"
    assert payload["ranking"] == [{
        "ticker": "THYAO", "m2": 0.72,
        "valuation_status": "OK", "v_conf": 0.75, "v_mid": 400.0,
    }]
    assert captured["tickers"] == ["thyao", "asels"]
    assert captured["persist"] is False
    assert conn.closed is True


def test_run_nonfin_batch_cli_rejects_naive_time_before_database(monkeypatch, tmp_path):
    valuation = _valuation_file(tmp_path)
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-nonfin-batch",
        "--analysis-at", "2026-08-05T20:00:00",
        "--valuation-config", str(valuation),
    ])
    with pytest.raises(SystemExit, match="timezone offset"):
        cli.main()
