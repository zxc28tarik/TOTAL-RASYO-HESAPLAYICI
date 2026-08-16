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
    path = tmp_path / "holding_valuation.json"
    path.write_text(json.dumps({
        "valuation_profile": "HOLDING_CLI",
        "valuation_version": 1,
        "source_nav_profile": "HOLDING_ADJUSTED_NAV",
        "source_nav_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "minimum_peer_count": 2,
        "full_confidence_peer_count": 3
    }), encoding="utf-8")
    return path


def nav_file(tmp_path: Path) -> Path:
    path = tmp_path / "nav.jsonl"
    path.write_text(json.dumps({
        "ticker": "KCHOL",
        "nav_asof_date": "2026-06-30",
        "published_at": "2026-07-25T10:00:00+03:00",
        "version_tag": "ORIGINAL",
        "version_sequence": 1,
        "nav_total": "1000",
        "shares_out": "100",
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "currency": "TRY",
        "source_confidence": "0.9",
        "source_type": "COMPANY_REPORTED_ADJUSTED_NAV",
        "source_document_id": "DOC-1",
        "source_sha256": "a" * 64,
        "nav_profile": "HOLDING_ADJUSTED_NAV",
        "nav_version": 1,
        "lineage": {}
    }) + "\n", encoding="utf-8")
    return path


def test_ingest_holding_nav_no_persist_loads_before_database(monkeypatch, tmp_path, capsys):
    path = nav_file(tmp_path)
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "ingest-holding-nav", "--file", str(path), "--no-persist"
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "OK", "row_count": 1, "persisted_count": 0, "persisted": False
    }


def test_ingest_holding_nav_bad_file_rejected_before_database(monkeypatch, tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text("{bad}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", ["cli", "ingest-holding-nav", "--file", str(path)])
    with pytest.raises(SystemExit, match="HOLDING NAV dosyasi gecersiz"):
        cli.main()


def test_run_holding_batch_cli_calls_pipeline(monkeypatch, tmp_path, capsys):
    valuation = valuation_file(tmp_path)
    conn = FakeConn()
    captured = {}

    def fake_run(conn_arg, **kwargs):
        captured.update(kwargs)
        captured["conn"] = conn_arg
        return {
            "result_count": 1,
            "rejections": [],
            "results": [{
                "ticker": "KCHOL",
                "valuation": {"status": "OK", "v_conf": 0.70, "V_mid": 250.0, "target_discount": 0.35},
                "m2": {"m2": 0.74},
            }],
        }

    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr("src.analytics.holding_batch_pipeline.run_holding_batch", fake_run)
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-holding-batch",
        "--analysis-at", "2026-08-05T20:00:00+03:00",
        "--tickers", "kchol,sahol",
        "--valuation-config", str(valuation),
        "--no-persist",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "OK"
    assert payload["ranking"] == [{
        "ticker": "KCHOL", "m2": 0.74, "valuation_status": "OK",
        "v_conf": 0.70, "v_mid": 250.0, "target_discount": 0.35,
    }]
    assert captured["tickers"] == ["kchol", "sahol"]
    assert captured["persist"] is False
    assert conn.closed is True


def test_run_holding_batch_rejects_naive_time_before_database(monkeypatch, tmp_path):
    valuation = valuation_file(tmp_path)
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-holding-batch",
        "--analysis-at", "2026-08-05T20:00:00",
        "--valuation-config", str(valuation),
    ])
    with pytest.raises(SystemExit, match="timezone offset"):
        cli.main()


def test_run_holding_batch_invalid_config_is_clean_and_pre_db(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"valuation_profile": "X", "valuation_version": 0}), encoding="utf-8")
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-holding-batch",
        "--analysis-at", "2026-08-05T20:00:00+03:00",
        "--valuation-config", str(bad),
    ])
    with pytest.raises(SystemExit, match="HOLDING config gecersiz"):
        cli.main()


def test_holding_nav_example_is_parseable():
    from src.ingest.holding_nav import load_holding_nav_jsonl
    path = Path(__file__).resolve().parents[1] / "data" / "holding_nav.example.jsonl"
    rows = load_holding_nav_jsonl(path)
    assert len(rows) == 1
    assert rows[0].ticker == "KCHOL"
