from __future__ import annotations

import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.ingest.historical_universe import (
    HistoricalUniverseIngestError,
    UniverseMembershipRecord,
    fetch_historical_universe_membership,
    persist_historical_universe_records,
)


DSN = os.getenv("TOTAL_RASYO_TEST_DSN")
if not DSN:
    pytest.skip("TOTAL_RASYO_TEST_DSN not set", allow_module_level=True)

SHA_A = "a" * 64
SHA_B = "b" * 64


def _ticker(prefix="HU"):
    return (prefix + uuid.uuid4().hex[:8]).upper()


def _record(ticker, **overrides):
    payload = {
        "ticker": ticker,
        "valid_from": "2021-01-01",
        "valid_to": "2022-01-01",
        "is_tradable": True,
        "company_name": "TEST AS",
        "sector_index_code": "XTEST",
        "sector_code": "TEST",
        "source": "TEST_FIXTURE",
        "source_ref": "fixture-1",
        "source_sha256": SHA_A,
    }
    payload.update(overrides)
    return UniverseMembershipRecord.from_mapping(payload)


def _conn():
    return psycopg2.connect(DSN)


def test_exact_replay_is_idempotent_and_fetchable():
    ticker = _ticker()
    row = _record(ticker)
    conn = _conn()
    try:
        assert persist_historical_universe_records(conn, [row]) == 1
        assert persist_historical_universe_records(conn, [row]) == 0
        out = fetch_historical_universe_membership(conn)
        got = out[out["ticker"] == ticker]
        assert len(got) == 1
        assert got.iloc[0]["row_sha256"] == row.row_sha256
    finally:
        conn.close()


def test_same_identity_different_content_fails_closed():
    ticker = _ticker()
    first = _record(ticker)
    second = _record(ticker, source_ref="fixture-2", source_sha256=SHA_B)
    conn = _conn()
    try:
        assert persist_historical_universe_records(conn, [first]) == 1
        with pytest.raises(HistoricalUniverseIngestError, match="ayni kimlikte farkli"):
            persist_historical_universe_records(conn, [second])
    finally:
        conn.close()


def test_database_rejects_overlap_against_existing_history():
    ticker = _ticker()
    first = _record(ticker, valid_from="2021-01-01", valid_to="2022-01-01")
    overlap = _record(
        ticker, valid_from="2021-12-01", valid_to=None,
        source_ref="fixture-2", source_sha256=SHA_B,
    )
    conn = _conn()
    try:
        assert persist_historical_universe_records(conn, [first]) == 1
        with pytest.raises(psycopg2.Error, match="overlapping interval"):
            persist_historical_universe_records(conn, [overlap])
    finally:
        conn.close()


def test_adjacent_database_intervals_are_allowed():
    ticker = _ticker()
    a = _record(ticker, valid_from="2021-01-01", valid_to="2022-01-01")
    b = _record(
        ticker, valid_from="2022-01-01", valid_to=None,
        source_ref="fixture-2", source_sha256=SHA_B,
    )
    conn = _conn()
    try:
        assert persist_historical_universe_records(conn, [a, b]) == 2
    finally:
        conn.close()


def test_history_rows_are_immutable_update_and_delete():
    ticker = _ticker()
    row = _record(ticker)
    conn = _conn()
    try:
        assert persist_historical_universe_records(conn, [row]) == 1
        with conn:
            with conn.cursor() as cur:
                with pytest.raises(psycopg2.Error, match="degistirilemez"):
                    cur.execute(
                        "UPDATE core.universe_membership_history SET is_tradable=false WHERE ticker=%s",
                        (ticker,),
                    )
        with conn:
            with conn.cursor() as cur:
                with pytest.raises(psycopg2.Error, match="degistirilemez"):
                    cur.execute("DELETE FROM core.universe_membership_history WHERE ticker=%s", (ticker,))
    finally:
        conn.close()
