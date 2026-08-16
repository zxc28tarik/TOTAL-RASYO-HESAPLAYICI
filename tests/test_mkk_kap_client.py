from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from src.ingest.api.mkk_kap import (
    KapApiConfigError,
    KapApiProtocolError,
    MkkKapApiClient,
    MkkKapApiConfig,
)


BASE_CONFIG = {
    "base_url": "https://api.example.test",
    "api_key_header": "X-Test-Key",
    "path": "/kap/disclosures",
    "method": "GET",
    "items_path": "data.items",
    "next_cursor_path": "data.next",
    "cursor_param": "cursor",
    "start_param": "startAt",
    "end_param": "endAt",
    "page_size_param": "limit",
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
    "max_retries": 2,
}


class FakeResponse:
    def __init__(self, payload=None, status=200, json_error=False, headers=None, content=None):
        self.payload = payload
        self.status_code = status
        self.json_error = json_error
        self.headers = {} if headers is None else dict(headers)
        self.content = b"" if content is None else content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        if self.json_error:
            raise ValueError("bad json")
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def cfg(**changes):
    raw = dict(BASE_CONFIG)
    raw.update(changes)
    return MkkKapApiConfig.from_dict(raw)


def item(disclosure_id="D1", published="2026-08-04T10:00:00+03:00", ticker="GARAN", subject="Rapor"):
    return {
        "id": disclosure_id,
        "publishedAt": published,
        "company": {"ticker": ticker, "id": "101"},
        "type": "FINANCIAL_STATEMENT",
        "subject": subject,
        "url": f"https://kap.org.tr/tr/Bildirim/{disclosure_id}",
    }


def test_config_requires_portal_specific_mapping():
    broken = dict(BASE_CONFIG)
    broken.pop("api_key_header")
    with pytest.raises(KapApiConfigError, match="eksik alanlar"):
        MkkKapApiConfig.from_dict(broken)




@pytest.mark.parametrize(
    "change, message",
    [
        ({"max_future_skew_seconds": True}, "max_future_skew_seconds"),
        ({"max_future_skew_seconds": -1}, "max_future_skew_seconds"),
        ({"next_cursor_path": None}, "birlikte tanimlanmali"),
        ({"page_size_param": None}, "birlikte tanimlanmali"),
        ({"api_key_header": "Bad Header"}, "header adi"),
        ({"static_params": []}, "static_params"),
        ({"fields": {"disclosure_id": "id", "published_at": 42}}, "fields anahtar"),
    ],
)
def test_config_rejects_silent_contract_variants(change, message):
    raw = dict(BASE_CONFIG)
    raw.update(change)
    with pytest.raises(KapApiConfigError, match=message):
        MkkKapApiConfig.from_dict(raw)


def test_api_key_rejects_header_injection():
    with pytest.raises(ValueError, match="satir sonu"):
        MkkKapApiClient(cfg(), "secret\nX-Evil: yes")


def test_fetches_paginated_disclosures_and_sends_api_key():
    session = FakeSession([
        FakeResponse({"data": {"items": [item("D2")], "next": "CUR-2"}}),
        FakeResponse({"data": {"items": [item("D1", "2026-08-04T09:00:00+03:00")], "next": None}}),
    ])
    client = MkkKapApiClient(
        cfg(), "secret", session=session,
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        sleeper=lambda _: None,
    )
    result = client.fetch_disclosures(
        start_at=datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
    )
    assert [x.disclosure_id for x in result.disclosures] == ["D1", "D2"]
    assert result.pages_fetched == 2
    assert result.next_cursor is None
    assert session.calls[0]["headers"]["X-Test-Key"] == "secret"
    assert session.calls[0]["params"]["limit"] == 100
    assert session.calls[1]["params"]["cursor"] == "CUR-2"
    assert result.disclosures[0].ticker == "GARAN"
    assert len(result.disclosures[0].payload_sha256) == 64


def test_identical_duplicate_is_deduplicated_but_mutated_duplicate_is_rejected():
    duplicate = item("D1")
    client = MkkKapApiClient(
        cfg(), "secret",
        session=FakeSession([FakeResponse({"data": {"items": [duplicate, dict(duplicate)], "next": None}})]),
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    result = client.fetch_disclosures(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    assert len(result.disclosures) == 1

    changed = item("D1", subject="Degisti")
    client = MkkKapApiClient(
        cfg(), "secret",
        session=FakeSession([FakeResponse({"data": {"items": [duplicate, changed], "next": None}})]),
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(KapApiProtocolError, match="farkli payload"):
        client.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )


def test_retryable_status_is_retried_then_succeeds():
    sleeps = []
    session = FakeSession([
        FakeResponse({}, status=503),
        FakeResponse({"data": {"items": [item()], "next": None}}),
    ])
    client = MkkKapApiClient(
        cfg(), "secret", session=session,
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        sleeper=sleeps.append,
    )
    result = client.fetch_disclosures(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    assert len(result.disclosures) == 1
    assert len(session.calls) == 2
    assert sleeps == [0.5]


@pytest.mark.parametrize(
    "published, message",
    [
        ("2026-08-04T10:00:00", "timezone icermeli"),
        ("not-a-date", "gecersiz zaman damgasi"),
        ("2026-08-05T12:00:00+00:00", "gelecekte gorunuyor"),
    ],
)
def test_publication_timestamp_is_fail_closed(published, message):
    client = MkkKapApiClient(
        cfg(max_future_skew_seconds=0), "secret",
        session=FakeSession([FakeResponse({"data": {"items": [item(published=published)], "next": None}})]),
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(KapApiProtocolError, match=message):
        client.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
        )


def test_repeated_cursor_and_unfinished_page_limit_are_rejected():
    repeated = FakeSession([
        FakeResponse({"data": {"items": [item("D1")], "next": "SAME"}}),
        FakeResponse({"data": {"items": [item("D2")], "next": "SAME"}}),
    ])
    client = MkkKapApiClient(
        cfg(), "secret", session=repeated,
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(KapApiProtocolError, match="cursor tekrarlandi"):
        client.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )

    limited = MkkKapApiClient(
        cfg(), "secret",
        session=FakeSession([FakeResponse({"data": {"items": [item()], "next": "MORE"}})]),
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(KapApiProtocolError, match="max_pages=1"):
        limited.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            max_pages=1,
        )


def test_window_and_max_pages_require_strict_types():
    client = MkkKapApiClient(cfg(), "secret", session=FakeSession([]))
    with pytest.raises(ValueError, match="timezone"):
        client.fetch_disclosures(
            start_at=datetime(2026, 8, 4),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="pozitif Python int"):
        client.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            max_pages=True,
        )


@pytest.mark.parametrize(
    "change, message",
    [
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"timeout_seconds": float("inf")}, "timeout_seconds"),
        ({"timeout_seconds": 301}, "guvenli siniri"),
        ({"max_retries": 11}, "guvenli siniri"),
        ({"page_size": 100001}, "guvenli API sinirini"),
        ({"max_future_skew_seconds": 86401}, "guvenli siniri"),
    ],
)
def test_api_resource_limits_are_strict(change, message):
    raw = dict(BASE_CONFIG)
    raw.update(change)
    with pytest.raises(KapApiConfigError, match=message):
        MkkKapApiConfig.from_dict(raw)


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda x: x.update(id=["D1"]), "disclosure_id"),
        (lambda x: x["company"].update(ticker={"code": "GARAN"}), "ticker string"),
        (lambda x: x.update(subject=["Rapor"]), "subject string"),
        (lambda x: x["company"].update(id={"id": 101}), "company_id"),
    ],
)
def test_structured_identifier_and_text_values_are_not_stringified(mutator, message):
    bad = item()
    mutator(bad)
    client = MkkKapApiClient(
        cfg(), "secret",
        session=FakeSession([FakeResponse({"data": {"items": [bad], "next": None}})]),
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(KapApiProtocolError, match=message):
        client.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )


def test_cursor_values_and_page_limits_reject_structured_or_excessive_inputs():
    client = MkkKapApiClient(cfg(), "secret", session=FakeSession([]))
    with pytest.raises(ValueError, match="initial_cursor"):
        client.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            initial_cursor=123,
        )
    with pytest.raises(ValueError, match="guvenli siniri"):
        client.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            max_pages=10001,
        )

    bad_cursor = MkkKapApiClient(
        cfg(), "secret",
        session=FakeSession([FakeResponse({"data": {"items": [item()], "next": {"cursor": "X"}}})]),
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(KapApiProtocolError, match="next_cursor"):
        bad_cursor.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )


def test_direct_dataclass_construction_cannot_bypass_static_params_contract():
    config = cfg()
    object.__setattr__(config, "static_params", [])
    with pytest.raises(KapApiConfigError, match="static_params"):
        MkkKapApiClient(config, "secret")


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("source_name", [], "source_name"),
        ("max_response_bytes", True, "max_response_bytes"),
        ("cursor_param", {"x": 1}, "cursor_param"),
        ("min_request_interval_seconds", float("nan"), "min_request_interval_seconds"),
    ],
)
def test_direct_dataclass_construction_cannot_bypass_any_config_gate(field, value, message):
    config = cfg()
    object.__setattr__(config, field, value)
    with pytest.raises(KapApiConfigError, match=message):
        MkkKapApiClient(config, "secret")


def test_live_readiness_rejects_example_placeholders_but_normal_config_parses():
    example = dict(BASE_CONFIG)
    example["base_url"] = "https://API-PORTAL-URUN-BASE-URL.example.invalid"
    config = MkkKapApiConfig.from_dict(example)
    with pytest.raises(KapApiConfigError, match="placeholder"):
        config.validate_live_ready()

    live = dict(BASE_CONFIG)
    live["base_url"] = "https://api.provider.example.org"
    MkkKapApiConfig.from_dict(live).validate_live_ready()


def test_probe_validates_one_page_without_requiring_pagination_completion():
    session = FakeSession([
        FakeResponse({"data": {"items": [item("D1"), item("D2")], "next": "MORE"}}),
    ])
    live_config = cfg(base_url="https://api.provider.example.org")
    client = MkkKapApiClient(
        live_config,
        "secret",
        session=session,
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    report = client.probe(
        start_at=datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
        validate_items_limit=1,
    )
    assert report.items_seen == 2
    assert report.items_validated == 1
    assert report.next_cursor_present is True
    assert report.first_disclosure_id == "D1"
    assert report.last_disclosure_id == "D1"


def test_retry_after_header_is_respected_and_capped():
    sleeps = []
    session = FakeSession([
        FakeResponse({}, status=429, headers={"Retry-After": "999"}),
        FakeResponse({"data": {"items": [item()], "next": None}}),
    ])
    client = MkkKapApiClient(
        cfg(max_retry_after_seconds=7),
        "secret",
        session=session,
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        sleeper=sleeps.append,
    )
    result = client.fetch_disclosures(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    assert len(result.disclosures) == 1
    assert sleeps == [7.0]


def test_minimum_request_interval_paces_paginated_calls():
    sleeps = []
    monotonic_values = iter([0.0, 0.25, 1.0])
    session = FakeSession([
        FakeResponse({"data": {"items": [item("D1")], "next": "NEXT"}}),
        FakeResponse({"data": {"items": [item("D2")], "next": None}}),
    ])
    client = MkkKapApiClient(
        cfg(min_request_interval_seconds=1.0),
        "secret",
        session=session,
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        sleeper=sleeps.append,
        monotonic=lambda: next(monotonic_values),
    )
    result = client.fetch_disclosures(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    assert len(result.disclosures) == 2
    assert sleeps == [0.75]


def test_invalid_items_can_be_quarantined_without_advancing_complete_flag():
    bad = item("BAD")
    bad["publishedAt"] = "not-a-date"
    client = MkkKapApiClient(
        cfg(),
        "secret",
        session=FakeSession([
            FakeResponse({"data": {"items": [item("GOOD"), bad], "next": None}}),
        ]),
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    result = client.fetch_disclosures(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        quarantine_invalid_items=True,
    )
    assert [x.disclosure_id for x in result.disclosures] == ["GOOD"]
    assert result.complete is False
    assert len(result.quarantined_items) == 1
    assert result.quarantined_items[0].page_number == 1
    assert result.quarantined_items[0].item_index == 1
    assert "gecersiz zaman damgasi" in result.quarantined_items[0].reason


def test_quarantine_flag_requires_python_bool():
    client = MkkKapApiClient(cfg(), "secret", session=FakeSession([]))
    with pytest.raises(ValueError, match="Python bool"):
        client.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            quarantine_invalid_items=1,
        )


@pytest.mark.parametrize(
    "change, message",
    [
        ({"cursor_param": {"name": "cursor"}, "next_cursor_path": "data.next"}, "cursor_param"),
        ({"cursor_param": "cursor", "next_cursor_path": ["data", "next"]}, "next_cursor_path"),
        ({"page_size_param": {"name": "limit"}}, "page_size_param"),
    ],
)
def test_optional_config_names_reject_structured_values(change, message):
    raw = dict(BASE_CONFIG)
    raw.update(change)
    with pytest.raises(KapApiConfigError, match=message):
        MkkKapApiConfig.from_dict(raw)


def test_conflicting_duplicate_can_be_quarantined_fail_closed():
    changed = item("DUP")
    changed["subject"] = "Farkli payload"
    client = MkkKapApiClient(
        cfg(),
        "secret",
        session=FakeSession([
            FakeResponse({"data": {"items": [item("DUP"), changed], "next": None}}),
        ]),
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    result = client.fetch_disclosures(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        quarantine_invalid_items=True,
    )
    assert len(result.disclosures) == 1
    assert result.complete is False
    assert len(result.quarantined_items) == 1
    assert "farkli payload" in result.quarantined_items[0].reason


@pytest.mark.parametrize(
    "change, message",
    [
        ({"api_key_header": "Accept"}, "cakismamali"),
        ({"base_url": "https://user:pass@api.example.org"}, "kullanici bilgisi"),
        ({"base_url": "https://api.example.org?x=1"}, "query"),
        ({"path": "/kap?x=1"}, "path query"),
        ({"static_params": {1: "x"}}, "static_params gecersiz JSON"),
        ({"static_params": {"x": float("nan")}}, "sonlu olmayan"),
        ({"static_params": {"startAt": "override"}}, "cakismamali"),
        ({"end_param": "startAt"}, "benzersiz"),
        ({"source_name": []}, "source_name"),
    ],
)
def test_live_config_rejects_header_url_json_and_param_collisions(change, message):
    raw = dict(BASE_CONFIG)
    raw.update(change)
    with pytest.raises(KapApiConfigError, match=message):
        MkkKapApiConfig.from_dict(raw)


def test_configured_source_name_reaches_result_and_envelopes():
    client = MkkKapApiClient(
        cfg(source_name="MKK_KAP_FINANCIALS"),
        "secret",
        session=FakeSession([
            FakeResponse({"data": {"items": [item()], "next": None}}),
        ]),
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    result = client.fetch_disclosures(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    assert result.source == "MKK_KAP_FINANCIALS"
    assert result.disclosures[0].source == "MKK_KAP_FINANCIALS"


@pytest.mark.parametrize(
    "change, message",
    [
        ({"max_response_bytes": True}, "max_response_bytes"),
        ({"max_response_bytes": 100_000_001}, "max_response_bytes"),
        ({"max_item_payload_bytes": 25_000_001}, "max_item_payload_bytes"),
        ({"max_response_bytes": 100, "max_item_payload_bytes": 101}, "asamaz"),
    ],
)
def test_response_and_item_byte_limits_are_strict(change, message):
    raw = dict(BASE_CONFIG)
    raw.update(change)
    with pytest.raises(KapApiConfigError, match=message):
        MkkKapApiConfig.from_dict(raw)


def test_content_length_and_actual_content_size_are_fail_closed():
    for response in (
        FakeResponse({}, headers={"Content-Length": "101"}),
        FakeResponse({}, content=b"x" * 101),
        FakeResponse({}, headers={"Content-Length": "not-an-int"}),
    ):
        client = MkkKapApiClient(
            cfg(max_response_bytes=100, max_item_payload_bytes=100),
            "secret",
            session=FakeSession([response]),
        )
        with pytest.raises(KapApiProtocolError, match="Content-Length|byte sinirini"):
            client.fetch_disclosures(
                start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
                end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            )


def test_nonfinite_json_and_oversized_page_are_rejected():
    nonfinite = MkkKapApiClient(
        cfg(), "secret",
        session=FakeSession([FakeResponse({"data": {"items": [], "ratio": float("nan")}})]),
    )
    with pytest.raises(KapApiProtocolError, match="sonlu olmayan"):
        nonfinite.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )

    oversized_page = MkkKapApiClient(
        cfg(page_size=1), "secret",
        session=FakeSession([
            FakeResponse({"data": {"items": [item("D1"), item("D2")], "next": None}}),
        ]),
    )
    with pytest.raises(KapApiProtocolError, match="page_size"):
        oversized_page.fetch_disclosures(
            start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )


def test_oversized_item_can_be_quarantined_and_blocks_checkpoint_completion():
    huge = item("HUGE")
    huge["subject"] = "x" * 1000
    client = MkkKapApiClient(
        cfg(max_response_bytes=10_000, max_item_payload_bytes=200),
        "secret",
        session=FakeSession([
            FakeResponse({"data": {"items": [huge], "next": None}}),
        ]),
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    result = client.fetch_disclosures(
        start_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        quarantine_invalid_items=True,
    )
    assert result.complete is False
    assert len(result.quarantined_items) == 1
    assert "byte sinirini" in result.quarantined_items[0].reason


def test_live_ready_requires_https_and_dns_host():
    with pytest.raises(KapApiConfigError, match="HTTPS"):
        cfg(base_url="http://api.mkk.example.org").validate_live_ready()
    with pytest.raises(KapApiConfigError, match="IP literal"):
        cfg(base_url="https://127.0.0.1").validate_live_ready()
