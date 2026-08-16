from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from src.app import cli


class FakeConn:
    def __init__(self): self.closed = False
    def close(self): self.closed = True


def valuation_file(tmp_path: Path) -> Path:
    path = tmp_path / "insurance_valuation.json"
    path.write_text(json.dumps({
        "valuation_profile": "INSURANCE_CLI",
        "valuation_version": 1,
        "source_metrics_profile": "KAP_INSURANCE_TTM",
        "source_metrics_version": 1,
        "accounting_profile": "TFRS17_LOCAL_STATUTORY",
        "accounting_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "minimum_peer_count": 2,
        "full_confidence_peer_count": 3
    }), encoding="utf-8")
    return path


def metrics_file(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "data" / "insurance_metrics.example.jsonl"
    path = tmp_path / "insurance.jsonl"
    path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def test_ingest_insurance_no_persist_loads_before_db(monkeypatch, tmp_path, capsys):
    path = metrics_file(tmp_path)
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", ["cli", "ingest-insurance-metrics", "--file", str(path), "--no-persist"])
    cli.main()
    assert json.loads(capsys.readouterr().out) == {
        "status": "OK", "row_count": 1, "persisted_count": 0, "persisted": False,
    }


def test_run_insurance_batch_cli_calls_pipeline(monkeypatch, tmp_path, capsys):
    valuation = valuation_file(tmp_path)
    conn = FakeConn()
    captured = {}

    def fake_run(conn_arg, **kwargs):
        captured.update(kwargs); captured["conn"] = conn_arg
        return {"result_count": 1, "rejections": [], "results": [{
            "ticker": "ANSGR",
            "valuation": {
                "status": "OK", "v_conf": .7, "V_mid": 120.0,
                "target_pb": 2.0, "target_pe": 9.0,
                "technical_margin": .12, "combined_ratio": .86,
            },
            "m2": {"m2": .74},
        }]}

    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr("src.analytics.insurance_batch_pipeline.run_insurance_batch", fake_run)
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-insurance-batch", "--analysis-at", "2026-08-05T20:00:00+03:00",
        "--tickers", "ansgr,tursg", "--valuation-config", str(valuation), "--no-persist",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["ranking"] == [{
        "ticker": "ANSGR", "m2": .74, "valuation_status": "OK",
        "v_conf": .7, "v_mid": 120.0, "target_pb": 2.0, "target_pe": 9.0,
        "technical_margin": .12, "combined_ratio": .86,
    }]
    assert captured["tickers"] == ["ansgr", "tursg"]
    assert captured["persist"] is False
    assert conn.closed is True


def test_run_insurance_rejects_naive_time_before_db(monkeypatch, tmp_path):
    valuation = valuation_file(tmp_path)
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-insurance-batch", "--analysis-at", "2026-08-05T20:00:00",
        "--valuation-config", str(valuation),
    ])
    with pytest.raises(SystemExit, match="timezone offset"):
        cli.main()
