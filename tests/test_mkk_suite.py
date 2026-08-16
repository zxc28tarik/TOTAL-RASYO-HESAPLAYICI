from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.ingest.api.mkk_contract import (
    validate_mkk_contract_sample,
    write_mkk_contract_lock,
)
from src.ingest.api.mkk_kap import KapApiConfigError, MkkKapApiConfig
from src.ingest.api.mkk_suite import (
    MkkProductSuite,
    plan_mkk_suite_backfill,
    validate_mkk_product_suite,
)


def _config(source: str, path: str) -> dict:
    return {
        "base_url": "https://apiportal.mkk.com.tr",
        "api_key_header": "X-API-Key",
        "path": path,
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
            "ticker": "ticker",
        },
        "source_name": source,
    }


def _sample(disclosure_id: str) -> dict:
    return {
        "data": {
            "items": [{
                "id": disclosure_id,
                "publishedAt": "2026-08-05T01:00:00+03:00",
                "ticker": "GARAN",
            }],
            "nextCursor": None,
        }
    }


def _product_files(tmp_path: Path, name: str, source: str, path: str):
    checked_at = datetime(2026, 8, 5, 2, 0, tzinfo=timezone(timedelta(hours=3)))
    config_path = tmp_path / f"{name}.config.json"
    sample_path = tmp_path / f"{name}.sample.json"
    lock_path = tmp_path / f"{name}.lock.json"
    raw_config = _config(source, path)
    sample = _sample(name.upper())
    config_path.write_text(json.dumps(raw_config), encoding="utf-8")
    sample_path.write_text(json.dumps(sample), encoding="utf-8")
    config = MkkKapApiConfig.from_dict(raw_config)
    report = validate_mkk_contract_sample(config, sample, checked_at=checked_at)
    write_mkk_contract_lock(lock_path, config, report)
    return config_path, sample_path, lock_path


def _manifest(tmp_path: Path, products: list[dict]) -> Path:
    path = tmp_path / "suite.json"
    path.write_text(json.dumps({
        "suite_name": "KAP_PRODUCTS",
        "suite_version": 1,
        "products": products,
    }), encoding="utf-8")
    return path


def test_suite_validates_multiple_products_and_plans_product_overrides(tmp_path):
    c1, s1, l1 = _product_files(tmp_path, "financials", "MKK_FIN", "/products/financials")
    c2, s2, l2 = _product_files(tmp_path, "corporate", "MKK_CORP", "/products/corporate")
    manifest = _manifest(tmp_path, [{
        "product_name": "financials",
        "config": c1.name,
        "sample": s1.name,
        "contract_lock": l1.name,
        "api_key_env": "MKK_FIN_KEY",
        "stream_name": "disclosures",
        "max_window_hours": 12,
        "overlap_seconds": 60,
    }, {
        "product_name": "corporate",
        "config": c2.name,
        "sample": s2.name,
        "contract_lock": l2.name,
        "api_key_env": "MKK_CORP_KEY",
        "stream_name": "events",
    }])
    suite = MkkProductSuite.from_json_file(manifest)
    checked_at = datetime(2026, 8, 5, 2, 0, tzinfo=timezone(timedelta(hours=3)))
    report = validate_mkk_product_suite(
        suite,
        checked_at=checked_at,
        environment={"MKK_FIN_KEY": "secret-1", "MKK_CORP_KEY": "secret-2"},
        require_api_keys=True,
        require_live_ready=True,
    )
    assert report.live_ready is True
    assert [item.product_name for item in report.products] == ["financials", "corporate"]
    plans = plan_mkk_suite_backfill(
        suite,
        report,
        start_at=checked_at,
        end_at=checked_at + timedelta(hours=25),
        max_window_hours=24,
        overlap_seconds=300,
    )
    assert len(plans[0].windows) == 3
    assert plans[0].windows[1].overlap_seconds == 60
    assert len(plans[1].windows) == 2
    assert plans[1].windows[1].overlap_seconds == 300


def test_suite_rejects_sample_lock_drift(tmp_path):
    c1, s1, l1 = _product_files(tmp_path, "financials", "MKK_FIN", "/products/financials")
    sample = json.loads(s1.read_text(encoding="utf-8"))
    sample["data"]["items"][0]["ticker"] = "AKBNK"
    s1.write_text(json.dumps(sample), encoding="utf-8")
    suite = MkkProductSuite.from_json_file(_manifest(tmp_path, [{
        "product_name": "financials", "config": c1.name, "sample": s1.name,
        "contract_lock": l1.name, "api_key_env": "MKK_FIN_KEY", "stream_name": "disclosures",
    }]))
    with pytest.raises(KapApiConfigError, match="sample contract lock ile uyusmuyor"):
        validate_mkk_product_suite(
            suite,
            checked_at=datetime(2026, 8, 5, 2, tzinfo=timezone.utc),
        )


def test_suite_rejects_duplicate_source_stream_pair(tmp_path):
    c1, s1, l1 = _product_files(tmp_path, "one", "MKK_SAME", "/products/one")
    c2, s2, l2 = _product_files(tmp_path, "two", "MKK_SAME", "/products/two")
    suite = MkkProductSuite.from_json_file(_manifest(tmp_path, [{
        "product_name": "one", "config": c1.name, "sample": s1.name,
        "contract_lock": l1.name, "api_key_env": "KEY_ONE", "stream_name": "same",
    }, {
        "product_name": "two", "config": c2.name, "sample": s2.name,
        "contract_lock": l2.name, "api_key_env": "KEY_TWO", "stream_name": "same",
    }]))
    with pytest.raises(KapApiConfigError, match="source_name/stream_name"):
        validate_mkk_product_suite(
            suite,
            checked_at=datetime(2026, 8, 5, 2, tzinfo=timezone.utc),
        )


def test_suite_disabled_products_are_not_required(tmp_path):
    c1, s1, l1 = _product_files(tmp_path, "financials", "MKK_FIN", "/products/financials")
    suite = MkkProductSuite.from_json_file(_manifest(tmp_path, [{
        "product_name": "financials", "config": c1.name, "sample": s1.name,
        "contract_lock": l1.name, "api_key_env": "MKK_FIN_KEY", "stream_name": "disclosures",
    }, {
        "product_name": "disabled", "config": "missing.json", "sample": "missing.sample.json",
        "contract_lock": "missing.lock.json", "api_key_env": "MISSING_KEY",
        "stream_name": "disabled", "enabled": False,
    }]))
    report = validate_mkk_product_suite(
        suite,
        checked_at=datetime(2026, 8, 5, 2, tzinfo=timezone.utc),
        environment={"MKK_FIN_KEY": "secret"},
    )
    assert len(report.products) == 1


def test_suite_require_api_keys_fails_without_secret(tmp_path):
    c1, s1, l1 = _product_files(tmp_path, "financials", "MKK_FIN", "/products/financials")
    suite = MkkProductSuite.from_json_file(_manifest(tmp_path, [{
        "product_name": "financials", "config": c1.name, "sample": s1.name,
        "contract_lock": l1.name, "api_key_env": "MKK_FIN_KEY", "stream_name": "disclosures",
    }]))
    with pytest.raises(KapApiConfigError, match="ortam degiskeni eksik"):
        validate_mkk_product_suite(
            suite,
            checked_at=datetime(2026, 8, 5, 2, tzinfo=timezone.utc),
            environment={},
            require_api_keys=True,
        )


def test_suite_manifest_rejects_non_mapping_product(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({
        "suite_name": "BAD", "suite_version": 1, "products": ["bad"]
    }), encoding="utf-8")
    with pytest.raises(KapApiConfigError, match="nesne olmali"):
        MkkProductSuite.from_json_file(path)


def test_suite_can_share_one_application_api_key_across_products(tmp_path):
    c1, s1, l1 = _product_files(tmp_path, "one", "MKK_ONE", "/products/one")
    c2, s2, l2 = _product_files(tmp_path, "two", "MKK_TWO", "/products/two")
    suite = MkkProductSuite.from_json_file(_manifest(tmp_path, [{
        "product_name": "one", "config": c1.name, "sample": s1.name,
        "contract_lock": l1.name, "api_key_env": "MKK_SHARED_KEY", "stream_name": "one",
    }, {
        "product_name": "two", "config": c2.name, "sample": s2.name,
        "contract_lock": l2.name, "api_key_env": "MKK_SHARED_KEY", "stream_name": "two",
    }]))
    report = validate_mkk_product_suite(
        suite,
        checked_at=datetime(2026, 8, 5, 2, tzinfo=timezone.utc),
        environment={"MKK_SHARED_KEY": "shared-secret"},
        require_api_keys=True,
    )
    assert len(report.products) == 2


def test_suite_rejects_nonfinite_product_window_override(tmp_path):
    path = _manifest(tmp_path, [{
        "product_name": "bad", "config": "c.json", "sample": "s.json",
        "contract_lock": "l.json", "api_key_env": "KEY", "stream_name": "bad",
        "max_window_hours": float("inf"),
    }])
    with pytest.raises(KapApiConfigError, match="sonlu"):
        MkkProductSuite.from_json_file(path)


def test_suite_rejects_excessive_total_window_count(monkeypatch, tmp_path):
    c1, s1, l1 = _product_files(tmp_path, "one", "MKK_ONE", "/products/one")
    c2, s2, l2 = _product_files(tmp_path, "two", "MKK_TWO", "/products/two")
    suite = MkkProductSuite.from_json_file(_manifest(tmp_path, [{
        "product_name": "one", "config": c1.name, "sample": s1.name,
        "contract_lock": l1.name, "api_key_env": "KEY", "stream_name": "one",
    }, {
        "product_name": "two", "config": c2.name, "sample": s2.name,
        "contract_lock": l2.name, "api_key_env": "KEY", "stream_name": "two",
    }]))
    checked = datetime(2026, 8, 5, 2, tzinfo=timezone.utc)
    report = validate_mkk_product_suite(
        suite, checked_at=checked, environment={"KEY": "secret"}
    )
    monkeypatch.setattr("src.ingest.api.mkk_suite.MAX_MKK_SUITE_TOTAL_WINDOWS", 2)
    with pytest.raises(ValueError, match="toplam backfill"):
        plan_mkk_suite_backfill(
            suite, report,
            start_at=checked,
            end_at=checked + timedelta(hours=25),
            max_window_hours=24,
            overlap_seconds=300,
        )


def test_suite_rejects_unknown_manifest_and_product_fields(tmp_path):
    top = tmp_path / "top.json"
    top.write_text(json.dumps({
        "suite_name": "BAD", "suite_version": 1, "products": [], "typo": 1,
    }), encoding="utf-8")
    with pytest.raises(KapApiConfigError, match="desteklenmeyen alanlar"):
        MkkProductSuite.from_json_file(top)

    product = tmp_path / "product.json"
    product.write_text(json.dumps({
        "suite_name": "BAD", "suite_version": 1, "products": [{
            "product_name": "x", "config": "c", "sample": "s", "contract_lock": "l",
            "api_key_env": "KEY", "stream_name": "x", "max_windw_hours": 2,
        }],
    }), encoding="utf-8")
    with pytest.raises(KapApiConfigError, match="desteklenmeyen alanlar"):
        MkkProductSuite.from_json_file(product)


def test_suite_rejects_invalid_env_name_and_reused_paths(tmp_path):
    invalid_env = _manifest(tmp_path, [{
        "product_name": "x", "config": "c", "sample": "s", "contract_lock": "l",
        "api_key_env": "BAD KEY", "stream_name": "x",
    }])
    with pytest.raises(KapApiConfigError, match="ortam degiskeni"):
        MkkProductSuite.from_json_file(invalid_env)

    reused = _manifest(tmp_path, [{
        "product_name": "x", "config": "same", "sample": "same", "contract_lock": "lock",
        "api_key_env": "KEY", "stream_name": "x",
    }])
    with pytest.raises(KapApiConfigError, match="yollari farkli"):
        MkkProductSuite.from_json_file(reused)
