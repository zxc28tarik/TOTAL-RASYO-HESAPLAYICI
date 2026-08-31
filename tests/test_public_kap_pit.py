from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.ingest.public_kap_pit import (
    PublicKapFinancialReport,
    PublicKapPitError,
    capture_public_kap_snapshot,
    extract_notification_ids_from_query_html,
    parse_public_kap_financial_report,
    parse_public_kap_notification_metadata,
    select_visible_financial_report_versions,
    snapshot_manifest_row,
    validate_financial_report_set,
)

IST = ZoneInfo("Europe/Istanbul")


def _html(*, sent="01.02.2022 18:10:26", ticker="AKBNK", year="2021", period="Yıllık") -> bytes:
    return f"""
    <html><body>
      <a href='/tr/sirket-bilgileri/ozet/83-akbank-t-a-s'>{ticker}</a>
      <div>Gönderim Tarihi</div><div>{sent}</div>
      <div>Bildirim Tipi</div><div>FR</div>
      <div>Yıl</div><div>{year}</div>
      <div>Periyot</div><div>{period}</div>
      <div>ifrs-full_Assets</div><div>VARLIKLAR TOPLAMI</div><div>1.000</div>
    </body></html>
    """.encode()


def _snapshot(raw=None):
    return capture_public_kap_snapshot(
        notification_id=998624,
        source_url="https://kap.org.tr/tr/Bildirim/998624",
        raw_html=raw or _html(),
        fetched_at=datetime(2026, 8, 31, 3, 0, tzinfo=IST),
    )


def test_snapshot_hashes_exact_raw_bytes_and_mutation_changes_hash():
    first = _snapshot(_html())
    second = _snapshot(_html() + b"\n")
    assert first.raw_sha256 != second.raw_sha256
    assert first.raw_size_bytes + 1 == second.raw_size_bytes


def test_snapshot_rejects_non_kap_or_mismatched_notification_url():
    with pytest.raises(PublicKapPitError):
        capture_public_kap_snapshot(
            notification_id=998624,
            source_url="https://example.com/tr/Bildirim/998624",
            raw_html=_html(),
            fetched_at=datetime(2026, 8, 31, 3, 0, tzinfo=IST),
        )
    with pytest.raises(PublicKapPitError):
        capture_public_kap_snapshot(
            notification_id=998624,
            source_url="https://kap.org.tr/tr/Bildirim/998625",
            raw_html=_html(),
            fetched_at=datetime(2026, 8, 31, 3, 0, tzinfo=IST),
        )


def test_query_enumeration_is_ordered_and_deduplicated():
    html = """
    <a href='/tr/Bildirim/10'>a</a>
    <a href='https://kap.org.tr/tr/Bildirim/11'>b</a>
    <a href='/tr/Bildirim/10'>duplicate</a>
    <a href='/tr/sirket/12'>not notification</a>
    """
    assert extract_notification_ids_from_query_html(html) == (10, 11)


def test_financial_report_parser_reads_exact_pit_metadata():
    row = parse_public_kap_financial_report(_snapshot(), expected_ticker="akbnk")
    assert row.notification_id == 998624
    assert row.ticker == "AKBNK"
    assert row.published_at == datetime(2022, 2, 1, 18, 10, 26, tzinfo=IST)
    assert row.report_year == 2021
    assert row.report_period == "Yıllık"
    assert row.disclosure_type == "FR"


def test_parser_fails_closed_if_expected_ticker_not_visible():
    with pytest.raises(PublicKapPitError):
        parse_public_kap_financial_report(_snapshot(), expected_ticker="GARAN")


def test_parser_rejects_future_publication_vs_capture():
    snapshot = capture_public_kap_snapshot(
        notification_id=998624,
        source_url="https://kap.org.tr/tr/Bildirim/998624",
        raw_html=_html(sent="01.09.2026 18:10:26"),
        fetched_at=datetime(2026, 8, 31, 3, 0, tzinfo=IST),
    )
    with pytest.raises(PublicKapPitError):
        parse_public_kap_financial_report(snapshot, expected_ticker="AKBNK")


def test_generic_metadata_parser_captures_correction_lineage():
    html = """
    <html><body>
      <div>BNTAS</div>
      <div>Gönderim Tarihi</div><div>18.05.2026 18:12:00</div>
      <div>Bildirim Tipi</div><div>DKB</div>
      <div>Yıl</div><div>--</div>
      <div>Periyot</div><div>-</div>
      <div>Yapılan Açıklama Düzeltme mi?</div><div>Evet</div>
      <div>Konuya İlişkin Daha Önce Yapılan Açıklamanın Tarihi</div><div>29.04.2026</div>
    </body></html>
    """.encode()
    snap = capture_public_kap_snapshot(
        notification_id=1600506,
        source_url="https://kap.org.tr/tr/Bildirim/1600506",
        raw_html=html,
        fetched_at=datetime(2026, 8, 31, 3, 0, tzinfo=IST),
    )
    meta = parse_public_kap_notification_metadata(snap, expected_ticker="BNTAS")
    assert meta.disclosure_type == "DKB"
    assert meta.is_correction is True
    assert meta.previous_notification_date == "29.04.2026"
    assert meta.report_year is None
    assert meta.report_period is None


def test_correction_without_previous_date_fails_closed():
    html = """
    <html><body><div>BNTAS</div>
    <div>Gönderim Tarihi</div><div>18.05.2026 18:12:00</div>
    <div>Bildirim Tipi</div><div>DKB</div><div>Yıl</div><div>--</div><div>Periyot</div><div>-</div>
    <div>Yapılan Açıklama Düzeltme mi?</div><div>Evet</div>
    </body></html>
    """.encode()
    snap = capture_public_kap_snapshot(
        notification_id=1600506,
        source_url="https://kap.org.tr/tr/Bildirim/1600506",
        raw_html=html,
        fetched_at=datetime(2026, 8, 31, 3, 0, tzinfo=IST),
    )
    with pytest.raises(PublicKapPitError):
        parse_public_kap_notification_metadata(snap, expected_ticker="BNTAS")


def test_snapshot_manifest_is_machine_readable_and_hash_bound():
    snap = _snapshot()
    row = snapshot_manifest_row(snap)
    assert row["contract"] == "PUBLIC_KAP_PIT_RAW_SNAPSHOT_V1"
    assert row["notification_id"] == 998624
    assert row["raw_sha256"] == snap.raw_sha256
    assert row["raw_size_bytes"] == len(snap.raw_html)


def _row(notification_id, published_at):
    return PublicKapFinancialReport(
        notification_id=notification_id,
        ticker="BNTAS",
        published_at=published_at,
        report_year=2026,
        report_period="3 Aylık",
        disclosure_type="FR",
        source_url=f"https://kap.org.tr/tr/Bildirim/{notification_id}",
        raw_sha256=f"{notification_id:064x}"[-64:],
    )


def test_cutoff_selects_latest_visible_version_not_latest_eventually_known():
    first = _row(1, datetime(2026, 4, 29, 18, 12, tzinfo=IST))
    second = _row(2, datetime(2026, 5, 5, 18, 10, tzinfo=IST))
    third = _row(3, datetime(2026, 5, 18, 18, 12, tzinfo=IST))

    early = select_visible_financial_report_versions(
        [third, first, second], cutoff_at=datetime(2026, 5, 4, 23, 0, tzinfo=IST)
    )
    assert [row.notification_id for row in early] == [1]

    middle = select_visible_financial_report_versions(
        [third, first, second], cutoff_at=datetime(2026, 5, 6, 9, 0, tzinfo=IST)
    )
    assert [row.notification_id for row in middle] == [2]


def test_cutoff_rejects_naive_datetime():
    with pytest.raises(PublicKapPitError):
        select_visible_financial_report_versions([], cutoff_at=datetime(2026, 5, 1, 10, 0))


def test_duplicate_notification_id_fails_closed_even_if_rows_identical():
    row = _row(1, datetime(2026, 4, 29, 18, 12, tzinfo=IST))
    with pytest.raises(PublicKapPitError):
        validate_financial_report_set([row, row])
