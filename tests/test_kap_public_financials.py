from __future__ import annotations

from datetime import date, datetime

import pytest
import requests

from src.ingest.api.kap_public_financials import (
    KAP_DISCLOSURE_DETAIL_URL,
    KAP_DISCLOSURE_QUERY_URL,
    ISTANBUL,
    MAX_QUERY_RESULTS,
    KapFinancialDisclosureSummary,
    KapPublicFinancialClient,
    KapPublicFinancialError,
    extract_taxonomy_rows,
    taxonomy_tags,
)


class FakeResponse:
    def __init__(self, payload, *, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


def fr_item(**overrides):
    row = {
        "publishDate": "28.04.2026 18:17:10",
        "fundCode": None,
        "kapTitle": "GARANTİ FAKTORİNG A.Ş.",
        "isOldKap": False,
        "disclosureClass": "FR",
        "disclosureType": "FR",
        "disclosureCategory": "FR",
        "summary": "31/03/2026 Finansal Tablolar",
        "subject": "Finansal Rapor",
        "relatedStocks": None,
        "year": 2026,
        "ruleType": "3 Aylık",
        "period": 1,
        "disclosureIndex": 1598274,
        "isLate": False,
        "stockCodes": "GARFA",
        "hasMultiLanguageSupport": True,
        "attachmentCount": 1,
        "modifyStatus": None,
    }
    row.update(overrides)
    return row


def non_statement_item():
    return fr_item(
        disclosureIndex=1598276,
        subject="Faaliyet Raporu",
        summary="Yönetim Kurulu Ara Dönem Faaliyet Raporu",
    )


def detail_payload(*, disclosure_index=1598274, publish_date="28.04.2026 18:17:10"):
    return [
        {
            "disclosure": {
                "disclosureBasic": {
                    "title": "Finansal Rapor",
                    "mkkMemberOid": "4028example",
                    "companyTitle": "GARANTİ FAKTORİNG A.Ş.",
                    "stockCode": "GARFA",
                    "relatedStocks": None,
                    "disclosureClass": "FR",
                    "disclosureType": "FR",
                    "disclosureCategory": "FR",
                    "publishDate": publish_date,
                    "disclosureId": "4028328c-example-financial-report",
                    "disclosureIndex": disclosure_index,
                    "summary": "31/03/2026 Finansal Tablolar",
                    "attachmentCount": 1,
                    "isLate": False,
                    "relatedDisclosureOid": None,
                    "isChanged": None,
                    "isBlocked": False,
                },
                "disclosureDetail": {"memberType": "IGS"},
            },
            "disclosureBody": [
                """
                <table>
                  <tr data-xbrl-code="kap-fr_StatementOfFinancialPositionBalanceSheetLineItems">
                    <td>Finansal Durum Tablosu (Bilanço)</td>
                  </tr>
                  <tr>
                    <td data-taxonomy="kap-fr_CashAndCashBalancesAtCentralBanks">NAKİT</td>
                    <td>1.234</td><td>1.100</td>
                  </tr>
                </table>
                """
            ],
            "attachments": [
                {
                    "objId": "4028328c-example-pdf",
                    "fileName": "GARFA_31032026.pdf",
                    "fileExtension": "pdf",
                }
            ],
        }
    ]


def client_for(*responses):
    session = FakeSession([FakeResponse(response) for response in responses])
    client = KapPublicFinancialClient(
        session=session,
        min_request_interval_seconds=0,
        sleeper=lambda _: None,
        monotonic=lambda: 1.0,
    )
    return client, session


def test_list_uses_public_kap_criteria_and_keeps_only_actual_financial_statement_subject():
    client, session = client_for([fr_item(), non_statement_item()])
    rows = client.list_financial_reports(date(2026, 4, 28), date(2026, 4, 28))

    assert [row.disclosure_index for row in rows] == [1598274]
    assert rows[0].stock_codes == ("GARFA",)
    assert rows[0].published_at == datetime(2026, 4, 28, 18, 17, 10, tzinfo=ISTANBUL)
    assert rows[0].year == 2026
    assert rows[0].rule_type == "3 Aylık"
    assert rows[0].period == "1"
    assert len(rows[0].raw_sha256) == 64

    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == KAP_DISCLOSURE_QUERY_URL
    assert call["json"]["fromDate"] == "2026-04-28"
    assert call["json"]["toDate"] == "2026-04-28"
    assert call["json"]["memberType"] == "IGS"
    assert call["json"]["mkkMemberOidList"] == []
    assert call["headers"]["Referer"].endswith("/tr/bildirim-sorgu")
    assert "api" not in call["headers"].get("Authorization", "").lower()


def test_ticker_filter_never_turns_foreign_report_into_candidate():
    client, _ = client_for([fr_item(), fr_item(disclosureIndex=1599000, stockCodes="EREGL")])
    rows = client.list_financial_reports(
        date(2026, 4, 28), date(2026, 4, 28), ticker_filter={"GARFA"}
    )
    assert [row.stock_codes for row in rows] == [("GARFA",)]


def test_correction_is_preserved_as_its_own_source_version():
    client, _ = client_for(
        [
            fr_item(),
            fr_item(
                disclosureIndex=1598275,
                publishDate="28.04.2026 19:00:00",
                modifyStatus="DUZELTILEN",
            ),
        ]
    )
    rows = client.list_financial_reports(date(2026, 4, 28), date(2026, 4, 28))
    assert [r.disclosure_index for r in rows] == [1598274, 1598275]
    assert rows[1].modify_status == "DUZELTILEN"
    assert rows[0].raw_sha256 != rows[1].raw_sha256


def test_single_query_refuses_more_than_seven_days():
    client, session = client_for([])
    with pytest.raises(ValueError, match="en fazla 7 gun"):
        client.list_financial_reports(date(2026, 4, 1), date(2026, 4, 8))
    assert session.calls == []


def test_query_hard_cap_fails_closed_instead_of_accepting_truncated_history():
    payload = [dict(fr_item(), disclosureIndex=1 + i) for i in range(MAX_QUERY_RESULTS)]
    client, _ = client_for(payload)
    with pytest.raises(KapPublicFinancialError, match="pencere daraltilmali"):
        client.list_financial_reports(date(2026, 4, 28), date(2026, 4, 28))


def test_duplicate_index_with_different_payload_fails_closed():
    first = fr_item()
    second = fr_item(summary="MUTATED SAME ID")
    client, _ = client_for([first, second])
    with pytest.raises(KapPublicFinancialError, match="farkli payload"):
        client.list_financial_reports(date(2026, 4, 28), date(2026, 4, 28))


def test_financial_summary_rejects_missing_publication_time_or_loose_type_alias():
    client, _ = client_for([fr_item(publishDate=None)])
    with pytest.raises(KapPublicFinancialError, match="publishDate"):
        client.list_financial_reports(date(2026, 4, 28), date(2026, 4, 28))

    client2, _ = client_for([fr_item(disclosureType="ODA")])
    assert client2.list_financial_reports(date(2026, 4, 28), date(2026, 4, 28)) == ()


def test_fetch_detail_locks_index_time_and_ticker_to_list_summary():
    list_client, _ = client_for([fr_item()])
    summary = list_client.list_financial_reports(date(2026, 4, 28), date(2026, 4, 28))[0]

    client, session = client_for(detail_payload())
    detail = client.fetch_detail(summary)
    assert detail.disclosure_index == 1598274
    assert detail.stock_codes == ("GARFA",)
    assert detail.published_at == summary.published_at
    assert len(detail.raw_sha256) == 64
    assert detail.attachments[0].file_name == "GARFA_31032026.pdf"
    assert session.calls[0]["method"] == "GET"
    assert session.calls[0]["url"] == KAP_DISCLOSURE_DETAIL_URL.format(disclosure_index=1598274)
    assert session.calls[0]["headers"]["Referer"].endswith("/tr/Bildirim/1598274")


def test_detail_time_or_index_mismatch_fails_closed():
    summary = KapFinancialDisclosureSummary(
        disclosure_index=1598274,
        published_at=datetime(2026, 4, 28, 18, 17, 10, tzinfo=ISTANBUL),
        stock_codes=("GARFA",),
        year=2026,
        rule_type="3 Aylık",
        period="1",
        subject="Finansal Rapor",
        modify_status=None,
        is_old_kap=False,
        raw_sha256="a" * 64,
    )
    client, _ = client_for(detail_payload(disclosure_index=1599999))
    with pytest.raises(KapPublicFinancialError, match="disclosureIndex"):
        client.fetch_detail(summary)

    client2, _ = client_for(detail_payload(publish_date="28.04.2026 18:17:11"))
    with pytest.raises(KapPublicFinancialError, match="publishDate"):
        client2.fetch_detail(summary)


def test_taxonomy_parser_preserves_real_kap_style_tags_without_semantic_guessing():
    list_client, _ = client_for([fr_item()])
    summary = list_client.list_financial_reports(date(2026, 4, 28), date(2026, 4, 28))[0]
    detail_client, _ = client_for(detail_payload())
    detail = detail_client.fetch_detail(summary)

    rows = extract_taxonomy_rows(detail)
    assert len(rows) == 2
    assert rows[0].taxonomy_tags == ("kap-fr_StatementOfFinancialPositionBalanceSheetLineItems",)
    assert "kap-fr_CashAndCashBalancesAtCentralBanks" in taxonomy_tags(detail)
    assert rows[1].cells == ("NAKİT", "1.234", "1.100")
    assert all(len(row.row_sha256) == 64 for row in rows)


def test_taxonomy_parser_fails_closed_when_detail_is_not_a_financial_table():
    payload = detail_payload()
    payload[0]["disclosureBody"] = ["<table><tr><td>plain activity report</td></tr></table>"]
    list_client, _ = client_for([fr_item()])
    summary = list_client.list_financial_reports(date(2026, 4, 28), date(2026, 4, 28))[0]
    detail_client, _ = client_for(payload)
    detail = detail_client.fetch_detail(summary)
    with pytest.raises(KapPublicFinancialError, match="taxonomy"):
        extract_taxonomy_rows(detail)


def test_discovery_slices_range_into_exact_non_overlapping_seven_day_windows():
    class RecordingClient(KapPublicFinancialClient):
        def __init__(self):
            self.windows = []

        def list_financial_reports(self, from_date, to_date, *, ticker_filter=None):
            self.windows.append((from_date, to_date, ticker_filter))
            return ()

    client = RecordingClient()
    result = client.discover_financial_reports(
        date(2026, 4, 1), date(2026, 4, 20), ticker_filter={"GARFA"}
    )
    assert result == ()
    assert client.windows == [
        (date(2026, 4, 1), date(2026, 4, 7), {"GARFA"}),
        (date(2026, 4, 8), date(2026, 4, 14), {"GARFA"}),
        (date(2026, 4, 15), date(2026, 4, 20), {"GARFA"}),
    ]


def test_retryable_http_status_retries_then_uses_real_payload():
    session = FakeSession([FakeResponse({}, status_code=503), FakeResponse([fr_item()])])
    client = KapPublicFinancialClient(
        session=session,
        min_request_interval_seconds=0,
        max_retries=1,
        sleeper=lambda _: None,
        monotonic=lambda: 1.0,
    )
    rows = client.list_financial_reports(date(2026, 4, 28), date(2026, 4, 28))
    assert len(rows) == 1
    assert len(session.calls) == 2


def test_client_rejects_non_kap_endpoint_injection():
    with pytest.raises(ValueError, match="resmi KAP HTTPS"):
        KapPublicFinancialClient(query_url="https://example.com/fake")
