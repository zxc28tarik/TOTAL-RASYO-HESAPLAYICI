from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.ingest.api.mkk_contract import (
    load_contract_sample,
    validate_mkk_contract_sample,
    write_mkk_contract_lock,
    verify_mkk_contract_lock,
)
from src.ingest.api.mkk_kap import KapApiProtocolError, MkkKapApiConfig


def config() -> MkkKapApiConfig:
    return MkkKapApiConfig.from_dict({
        "base_url": "https://apiportal.mkk.com.tr",
        "api_key_header": "X-API-Key",
        "path": "/registered/product/path",
        "method": "GET",
        "items_path": "data.items",
        "next_cursor_path": "data.nextCursor",
        "cursor_param": "cursor",
        "start_param": "startAt",
        "end_param": "endAt",
        "page_size_param": "pageSize",
        "page_size": 100,
        "fields": {
            "disclosure_id": "id",
            "published_at": "publishedAt",
            "ticker": "company.ticker",
            "company_id": "company.id",
            "notification_type": "type",
            "subject": "subject",
            "source_url": "url",
        },
        "source_name": "MKK_KAP_FINANCIALS",
    })


def sample():
    first = {
        "id": "D1",
        "publishedAt": "2026-08-04T10:00:00+03:00",
        "company": {"ticker": "GARAN", "id": "101"},
        "type": "FINANCIAL_STATEMENT",
        "subject": "Finansal rapor",
        "url": "https://kap.org.tr/tr/Bildirim/D1",
    }
    return {"data": {"items": [first, dict(first), {
        "id": "D2",
        "publishedAt": "2026-08-04T11:00:00+03:00",
        "company": {"ticker": "AKBNK", "id": "102"},
        "type": "FINANCIAL_STATEMENT",
        "subject": "Finansal rapor 2",
        "url": "https://kap.org.tr/tr/Bildirim/D2",
    }], "nextCursor": "NEXT"}}


def test_contract_sample_report_and_lock_are_deterministic(tmp_path):
    checked_at = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    report = validate_mkk_contract_sample(config(), sample(), checked_at=checked_at)
    assert report.items_seen == 3
    assert report.items_validated == 3
    assert report.duplicate_ids == 1
    assert report.first_disclosure_id == "D1"
    assert report.last_disclosure_id == "D2"
    assert report.optional_field_coverage["ticker"] == 3
    assert report.next_cursor_present is True
    assert report.live_ready is True
    assert report.live_ready_error is None
    assert len(report.config_sha256) == 64
    assert len(report.sample_sha256) == 64

    target = write_mkk_contract_lock(tmp_path / "contract.lock.json", config(), report)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["config_sha256"] == report.config_sha256
    assert payload["sample_sha256"] == report.sample_sha256
    assert payload["endpoint"]["host"] == "apiportal.mkk.com.tr"
    assert payload["request_contract"]["static_param_keys"] == []
    lock_text = target.read_text(encoding="utf-8")
    assert "CONTRACT-SAMPLE-ONLY" not in lock_text
    assert "secret" not in lock_text.lower()


def test_contract_sample_rejects_mutated_duplicate():
    payload = sample()
    payload["data"]["items"][1] = dict(payload["data"]["items"][1], subject="degisti")
    with pytest.raises(KapApiProtocolError, match="farkli payload"):
        validate_mkk_contract_sample(
            config(), payload,
            checked_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        )


def test_contract_sample_file_is_strict_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    with pytest.raises(KapApiProtocolError, match="gecersiz JSON"):
        load_contract_sample(bad)


def test_contract_lock_rejects_config_drift(tmp_path):
    checked_at = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    original = config()
    report = validate_mkk_contract_sample(original, sample(), checked_at=checked_at)
    target = write_mkk_contract_lock(tmp_path / "contract.lock.json", original, report)
    assert verify_mkk_contract_lock(target, original)["config_sha256"] == report.config_sha256

    raw = original.__dict__.copy()
    raw["page_size"] = 50
    changed = MkkKapApiConfig.from_dict(raw)
    with pytest.raises(Exception, match="contract lock ile uyusmuyor"):
        verify_mkk_contract_lock(target, changed)


def test_placeholder_contract_can_validate_but_is_not_live_ready():
    raw = config().__dict__.copy()
    raw["base_url"] = "https://provider.example.invalid"
    placeholder = MkkKapApiConfig.from_dict(raw)
    report = validate_mkk_contract_sample(
        placeholder, sample(),
        checked_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    assert report.items_validated == 3
    assert report.live_ready is False
    assert "placeholder" in report.live_ready_error
