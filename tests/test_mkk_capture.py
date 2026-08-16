from __future__ import annotations

import json
import stat
from datetime import datetime, timezone

import pytest

from src.ingest.api.mkk_contract import write_mkk_contract_capture
from src.ingest.api.mkk_kap import (
    KapApiConfigError,
    KapApiProtocolError,
    MkkKapApiClient,
    MkkKapApiConfig,
)


class Response:
    status_code = 200
    headers = {}
    content = b""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return Response(self.payload)


def config() -> MkkKapApiConfig:
    return MkkKapApiConfig.from_dict({
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
        "source_name": "MKK_CAPTURE_TEST",
    })


def payload():
    return {"data": {"items": [{
        "id": "D1", "publishedAt": "2026-08-05T01:00:00+03:00"
    }], "next": "CUR2"}}


def test_capture_writes_private_raw_sample_and_secret_free_metadata(tmp_path):
    session = Session(payload())
    client = MkkKapApiClient(
        config(), "super-secret", session=session,
        clock=lambda: datetime(2026, 8, 5, 0, 30, tzinfo=timezone.utc),
    )
    capture = client.capture_contract_sample(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    sample_path, meta_path = write_mkk_contract_capture(
        sample_path=tmp_path / "sample.json",
        metadata_path=tmp_path / "sample.meta.json",
        config=config(),
        capture=capture,
    )
    assert json.loads(sample_path.read_text(encoding="utf-8")) == payload()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["authentication_material_persisted"] is False
    assert meta["sample_sha256"] == capture.payload_sha256
    assert "super-secret" not in sample_path.read_text(encoding="utf-8")
    assert "super-secret" not in meta_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(sample_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(meta_path.stat().st_mode) == 0o600
    assert session.calls[0]["headers"]["X-API-Key"] == "super-secret"


def test_capture_rejects_empty_page():
    client = MkkKapApiClient(
        config(), "secret", session=Session({"data": {"items": [], "next": None}}),
        clock=lambda: datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    with pytest.raises(KapApiProtocolError, match="sayfasi bos"):
        client.capture_contract_sample(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )


def test_capture_refuses_overwrite_without_force(tmp_path):
    client = MkkKapApiClient(
        config(), "secret", session=Session(payload()),
        clock=lambda: datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    capture = client.capture_contract_sample(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    sample = tmp_path / "sample.json"
    meta = tmp_path / "sample.meta.json"
    sample.write_text("old", encoding="utf-8")
    with pytest.raises(KapApiConfigError, match="--force"):
        write_mkk_contract_capture(
            sample_path=sample, metadata_path=meta, config=config(), capture=capture,
        )


def test_capture_rejects_placeholder_endpoint_before_request():
    raw = config().__dict__.copy()
    raw["base_url"] = "https://provider.example.invalid"
    session = Session(payload())
    client = MkkKapApiClient(MkkKapApiConfig.from_dict(raw), "secret", session=session)
    with pytest.raises(KapApiConfigError, match="placeholder"):
        client.capture_contract_sample(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
    assert session.calls == []


def test_capture_pair_rolls_back_existing_files_if_second_install_fails(monkeypatch, tmp_path):
    client = MkkKapApiClient(
        config(), "secret", session=Session(payload()),
        clock=lambda: datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    capture = client.capture_contract_sample(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    sample = tmp_path / "sample.json"
    meta = tmp_path / "sample.meta.json"
    sample.write_text("old-sample", encoding="utf-8")
    meta.write_text("old-meta", encoding="utf-8")

    import os
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 4:
            raise OSError("simulated second install failure")
        return real_replace(src, dst)

    monkeypatch.setattr("src.ingest.api.mkk_contract.os.replace", flaky_replace)
    with pytest.raises(OSError, match="simulated"):
        write_mkk_contract_capture(
            sample_path=sample, metadata_path=meta, config=config(), capture=capture,
            overwrite=True,
        )
    assert sample.read_text(encoding="utf-8") == "old-sample"
    assert meta.read_text(encoding="utf-8") == "old-meta"


def test_capture_pair_cleans_first_temp_if_second_temp_creation_fails(monkeypatch, tmp_path):
    client = MkkKapApiClient(
        config(), "secret", session=Session(payload()),
        clock=lambda: datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    capture = client.capture_contract_sample(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    from src.ingest.api import mkk_contract
    real = mkk_contract._write_private_temp
    calls = {"n": 0}

    def flaky(path, text):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("second temp failed")
        return real(path, text)

    monkeypatch.setattr(mkk_contract, "_write_private_temp", flaky)
    with pytest.raises(OSError, match="second temp failed"):
        write_mkk_contract_capture(
            sample_path=tmp_path / "sample.json",
            metadata_path=tmp_path / "meta.json",
            config=config(), capture=capture,
        )
    assert not (tmp_path / "sample.json").exists()
    assert not (tmp_path / "meta.json").exists()
    assert not list(tmp_path.glob(".sample.json.*"))
