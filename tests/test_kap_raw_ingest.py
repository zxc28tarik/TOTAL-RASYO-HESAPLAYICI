from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.ingest.api.kap_public_universe import KapPublicUniverseClient, KapUniverseSnapshot
from src.ingest.api.mkk_kap import (
    KapDisclosureEnvelope,
    KapFetchResult,
    KapQuarantinedItem,
)
from src.ingest.kap_raw import persist_kap_disclosures, persist_kap_universe


class Cursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Conn:
    def __init__(self):
        self.cur = Cursor()

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def env(source="MKK_KAP_API"):
    fetched = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    return KapDisclosureEnvelope(
        disclosure_id="D1",
        published_at=datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc),
        ticker="GARAN",
        company_id="101",
        notification_type="FINANCIAL_STATEMENT",
        subject="Rapor",
        source_url="https://kap.org.tr/tr/Bildirim/D1",
        payload={"id": "D1"},
        payload_sha256="a" * 64,
        fetched_at=fetched,
        source=source,
    )


def result(*items):
    return KapFetchResult(
        disclosures=tuple(items),
        next_cursor=None,
        pages_fetched=1,
        start_at=datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 4, 12, 5, tzinfo=timezone.utc),
    )


def test_disclosure_payload_and_checkpoint_are_same_transaction():
    conn = Conn()
    count = persist_kap_disclosures(conn, result(env()), stream_name="financials")
    assert count == 1
    assert len(conn.cur.executed) == 3
    insert_sql, insert_params = conn.cur.executed[0]
    run_sql, run_params = conn.cur.executed[1]
    checkpoint_sql, checkpoint_params = conn.cur.executed[2]
    assert "INSERT INTO raw.kap_disclosures" in insert_sql
    assert "ON CONFLICT (source, disclosure_id)" in insert_sql
    assert "payload_sha256 = EXCLUDED.payload_sha256" in insert_sql
    assert insert_params[0:3] == ("MKK_KAP_API", "D1", env().published_at)
    assert "INSERT INTO raw.kap_sync_state" in checkpoint_sql
    assert "WHERE EXCLUDED.window_end >= raw.kap_sync_state.window_end" in checkpoint_sql
    assert checkpoint_params[1] == "financials"
    assert checkpoint_params[6:8] == (1, 1)
    assert "INSERT INTO raw.kap_sync_runs" in run_sql
    assert run_params[5:9] == ("COMPLETE", 1, 0, 1)


def test_mixed_sources_are_rejected_before_database_write():
    conn = Conn()
    with pytest.raises(ValueError, match="birden fazla source"):
        persist_kap_disclosures(conn, result(env(), env(source="OTHER")))
    assert conn.cur.executed == []


def test_universe_upsert_preserves_existing_sector_classification():
    html = """
    <html><table><tr><td>GARAN</td><td><a href="/tr/sirket-bilgileri/ozet/100-garanti">T. GARANTI BANKASI A.S.</a></td></tr></table></html>
    """
    frame = KapPublicUniverseClient.parse_html(html)
    snap = KapUniverseSnapshot(
        frame=frame,
        fetched_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        source_url="https://kap.org.tr/tr/bist-sirketler",
        html_sha256="b" * 64,
    )
    conn = Conn()
    assert persist_kap_universe(conn, snap) == 1
    sql, params = conn.cur.executed[0]
    assert "COALESCE(core.universe_stocks.sector_index_code" in sql
    assert "COALESCE(core.universe_stocks.sector_code" in sql
    assert params[0] == "GARAN"
    assert params[5] == "100"


def test_migration_has_lossless_payload_and_immutable_hash_guards():
    sql = (Path(__file__).resolve().parents[1] / "sql" / "015_kap_official_ingestion.sql").read_text(encoding="utf-8").lower()
    for token in (
        "create table if not exists raw.kap_disclosures",
        "payload jsonb not null",
        "payload_sha256 char(64) not null",
        "ck_kap_disclosure_timezone_future",
        "reject_kap_payload_mutation",
        "create table if not exists raw.kap_sync_state",
        "add column if not exists kap_company_id",
    ):
        assert token in sql
    assert "old.payload_sha256 <> new.payload_sha256" in sql
    assert "old.published_at <> new.published_at" in sql
    assert "before update of payload_sha256, payload, published_at" in sql


def test_empty_result_uses_completed_at_not_window_end_for_checkpoint():
    conn = Conn()
    empty = result()
    assert persist_kap_disclosures(conn, empty) == 0
    _, params = conn.cur.executed[-1]
    assert params[5] == empty.completed_at
    assert params[5] != empty.end_at


def test_quarantined_batch_is_persisted_but_does_not_advance_checkpoint():
    bad = KapQuarantinedItem(
        page_number=1,
        item_index=2,
        cursor_value="CUR-1",
        reason="published_at gecersiz",
        payload={"id": "BAD"},
        payload_sha256="c" * 64,
        fetched_at=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    partial = replace(
        result(env()),
        quarantined_items=(bad,),
        complete=False,
    )
    conn = Conn()
    assert persist_kap_disclosures(conn, partial, stream_name="financials") == 1
    sqls = [sql for sql, _ in conn.cur.executed]
    assert any("INSERT INTO raw.kap_api_quarantine" in sql for sql in sqls)
    assert any("INSERT INTO raw.kap_sync_runs" in sql for sql in sqls)
    assert not any("INSERT INTO raw.kap_sync_state" in sql for sql in sqls)
    run_sql, run_params = next(
        (sql, params) for sql, params in conn.cur.executed
        if "INSERT INTO raw.kap_sync_runs" in sql
    )
    assert run_params[5:9] == ("QUARANTINED", 1, 1, 1)


def test_empty_custom_source_result_keeps_configured_source_in_run_and_checkpoint():
    conn = Conn()
    custom = replace(result(), source="MKK_KAP_FINANCIALS")
    assert persist_kap_disclosures(conn, custom, stream_name="financials") == 0
    run_params = next(
        params for sql, params in conn.cur.executed
        if "INSERT INTO raw.kap_sync_runs" in sql
    )
    checkpoint_params = next(
        params for sql, params in conn.cur.executed
        if "INSERT INTO raw.kap_sync_state" in sql
    )
    assert run_params[0] == "MKK_KAP_FINANCIALS"
    assert checkpoint_params[0] == "MKK_KAP_FINANCIALS"


class PendingCursor(Cursor):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def fetchall(self):
        return list(self.rows)


class PendingConn(Conn):
    def __init__(self, rows):
        self.cur = PendingCursor(rows)


def _fact_config():
    from src.ingest.api.kap_financial_facts import KapFinancialFactConfig
    return KapFinancialFactConfig.from_dict({
        "mapping_profile": "TEST_FACTS",
        "mapping_version": 1,
        "facts_path": "statement.facts",
        "version_tag_path": "statement.versionTag",
        "version_sequence_path": "statement.versionSequence",
        "dimensions_path": "dimensions",
        "default_unit_scale": 1,
        "default_currency": "TRY",
        "fields": {
            "fact_code": "code",
            "value": "value",
            "period_end": "periodEnd",
            "unit_scale": "unitScale",
        },
    })


def _pending_row(disclosure_id, payload):
    return (
        "MKK_KAP_API",
        disclosure_id,
        datetime(2026, 5, 10, 9, 30, tzinfo=timezone.utc),
        "GARAN",
        "101",
        "FINANCIAL_STATEMENT",
        "Finansal rapor",
        "https://kap.org.tr/x",
        payload,
        ("a" if disclosure_id == "D1" else "b") * 64,
        datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc),
    )


def _valid_payload(value="123"):
    return {
        "statement": {
            "versionTag": "ORIGINAL",
            "versionSequence": 1,
            "facts": [{
                "code": "TOTAL_EQUITY",
                "value": value,
                "periodEnd": "2026-03-31",
                "unitScale": 1000,
                "dimensions": {},
            }],
        }
    }


def test_pending_fact_extraction_persists_success_and_rejection_in_one_transaction():
    from src.ingest.kap_raw import extract_pending_kap_financial_facts

    conn = PendingConn([
        _pending_row("D1", _valid_payload()),
        _pending_row("D2", {"statement": {"facts": "not-a-list"}}),
    ])
    report = extract_pending_kap_financial_facts(
        conn,
        _fact_config(),
        extracted_at=datetime(2026, 5, 10, 10, 5, tzinfo=timezone.utc),
    )
    assert report.disclosures_seen == 2
    assert report.disclosures_extracted == 1
    assert report.disclosures_rejected == 1
    assert report.facts_written == 1
    assert report.rejected_ids == ("D2",)
    sqls = [sql for sql, _ in conn.cur.executed]
    assert any("INSERT INTO raw.kap_financial_facts" in sql for sql in sqls)
    assert any("INSERT INTO raw.kap_fact_extraction_rejections" in sql for sql in sqls)
    assert any("DELETE FROM raw.kap_fact_extraction_rejections" in sql for sql in sqls)


def test_pending_extraction_accepts_json_text_and_retry_flag_reaches_query():
    from src.ingest.kap_raw import extract_pending_kap_financial_facts
    import json

    conn = PendingConn([_pending_row("D1", json.dumps(_valid_payload()))])
    report = extract_pending_kap_financial_facts(
        conn,
        _fact_config(),
        retry_rejections=True,
        notification_type=None,
        limit=25,
        extracted_at=datetime(2026, 5, 10, 10, 5, tzinfo=timezone.utc),
    )
    assert report.disclosures_extracted == 1
    select_sql, params = conn.cur.executed[0]
    assert "FROM raw.kap_disclosures" in select_sql
    assert params[1:3] == (None, None)
    assert params[5] is True
    assert params[-1] == 25


def test_invalid_json_payload_is_recorded_as_rejection_not_uncontrolled_exception():
    from src.ingest.kap_raw import extract_pending_kap_financial_facts

    conn = PendingConn([_pending_row("D1", "{broken")])
    report = extract_pending_kap_financial_facts(
        conn,
        _fact_config(),
        extracted_at=datetime(2026, 5, 10, 10, 5, tzinfo=timezone.utc),
    )
    assert report.disclosures_rejected == 1
    assert report.rejected_ids == ("D1",)
    rejection = next((params for sql, params in conn.cur.executed if "INSERT INTO raw.kap_fact_extraction_rejections" in sql), None)
    assert rejection is not None
    assert "gecersiz JSON" in rejection[5]


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"limit": True}, "limit"),
        ({"limit": 0}, "limit"),
        ({"retry_rejections": 1}, "retry_rejections"),
        ({"notification_type": ""}, "notification_type"),
        ({"extracted_at": datetime(2026, 5, 10, 10, 5)}, "timezone"),
    ],
)
def test_pending_extraction_contract_is_strict(kwargs, message):
    from src.ingest.kap_raw import extract_pending_kap_financial_facts

    base = {"extracted_at": datetime(2026, 5, 10, 10, 5, tzinfo=timezone.utc)}
    base.update(kwargs)
    with pytest.raises(ValueError, match=message):
        extract_pending_kap_financial_facts(PendingConn([]), _fact_config(), **base)


def test_migration_has_rejection_ledger_and_financial_fact_bounds():
    sql = (Path(__file__).resolve().parents[1] / "sql" / "015_kap_official_ingestion.sql").read_text(encoding="utf-8").lower()
    for token in (
        "create table if not exists raw.kap_fact_extraction_rejections",
        "payload_sha256 char(64) not null",
        "attempts int not null default 1",
        "ck_kap_fact_rejection_time",
    ):
        assert token in sql
