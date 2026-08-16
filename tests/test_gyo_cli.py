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
    path = tmp_path / "gyo_valuation.json"
    path.write_text(json.dumps({
        "valuation_profile": "GYO_CLI", "valuation_version": 1,
        "source_nav_profile": "GYO_REPORTED_NAV", "source_nav_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "minimum_peer_count": 2, "full_confidence_peer_count": 3,
    }), encoding="utf-8")
    return path


def nav_file(tmp_path: Path) -> Path:
    path = tmp_path / "nav.jsonl"
    path.write_text(json.dumps({
        "ticker": "AGYO", "nav_asof_date": "2026-06-30",
        "published_at": "2026-07-25T10:00:00+03:00",
        "version_tag": "ORIGINAL", "version_sequence": 1,
        "nav_total": "1000", "shares_out": "100",
        "share_basis": "ADJUSTED_PRICE_SERIES_V1", "currency": "TRY",
        "property_portfolio_value": "1300", "source_confidence": "0.9",
        "source_type": "KAP_GYO_PORTFOLIO_REPORT", "source_document_id": "DOC-1",
        "source_sha256": "a" * 64, "nav_profile": "GYO_REPORTED_NAV",
        "nav_version": 1, "lineage": {},
    }) + "\n", encoding="utf-8")
    return path


def test_ingest_gyo_nav_no_persist_loads_before_db(monkeypatch, tmp_path, capsys):
    path = nav_file(tmp_path)
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", ["cli", "ingest-gyo-nav", "--file", str(path), "--no-persist"])
    cli.main()
    assert json.loads(capsys.readouterr().out) == {
        "status": "OK", "row_count": 1, "persisted_count": 0, "persisted": False,
    }


def test_run_gyo_batch_cli_calls_pipeline(monkeypatch, tmp_path, capsys):
    valuation = valuation_file(tmp_path)
    conn = FakeConn()
    captured = {}
    def fake_run(conn_arg, **kwargs):
        captured.update(kwargs); captured["conn"] = conn_arg
        return {"result_count": 1, "rejections": [], "results": [{
            "ticker": "AGYO",
            "valuation": {"status": "OK", "v_conf": .7, "V_mid": 12.5, "target_pd_nav": .65},
            "m2": {"m2": .74},
        }]}
    monkeypatch.setattr(cli, "get_conn", lambda: conn)
    monkeypatch.setattr("src.analytics.gyo_batch_pipeline.run_gyo_batch", fake_run)
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-gyo-batch", "--analysis-at", "2026-08-05T20:00:00+03:00",
        "--tickers", "agyo,ekgyo", "--valuation-config", str(valuation), "--no-persist",
    ])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["ranking"] == [{
        "ticker": "AGYO", "m2": .74, "valuation_status": "OK",
        "v_conf": .7, "v_mid": 12.5, "target_pd_nav": .65,
    }]
    assert captured["tickers"] == ["agyo", "ekgyo"]
    assert captured["persist"] is False
    assert conn.closed is True


def test_run_gyo_batch_rejects_naive_time_before_db(monkeypatch, tmp_path):
    valuation = valuation_file(tmp_path)
    monkeypatch.setattr(cli, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("DB touched")))
    monkeypatch.setattr(sys, "argv", [
        "cli", "run-gyo-batch", "--analysis-at", "2026-08-05T20:00:00",
        "--valuation-config", str(valuation),
    ])
    with pytest.raises(SystemExit, match="timezone offset"):
        cli.main()


def test_gyo_example_parseable():
    from src.ingest.gyo_nav import load_gyo_nav_jsonl
    path = Path(__file__).resolve().parents[1] / "data" / "gyo_nav.example.jsonl"
    rows = load_gyo_nav_jsonl(path)
    assert len(rows) == 1
    assert rows[0].ticker == "AGYO"
