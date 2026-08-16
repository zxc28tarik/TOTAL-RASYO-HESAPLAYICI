from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.ingest.holding_nav import (
    HoldingNavIngestError,
    HoldingNavRecord,
    load_holding_nav_jsonl,
    persist_holding_nav_records,
)

TZ = ZoneInfo("Europe/Istanbul")
SHA = "a" * 64


def payload(**patch):
    row = {
        "ticker": "KCHOL",
        "nav_asof_date": "2026-06-30",
        "published_at": "2026-07-25T10:00:00+03:00",
        "version_tag": "ORIGINAL",
        "version_sequence": 1,
        "nav_total": "1000000000",
        "shares_out": "100000000",
        "nav_per_share": "10",
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "currency": "TRY",
        "source_confidence": "0.9",
        "source_type": "COMPANY_REPORTED_ADJUSTED_NAV",
        "source_document_id": "DOC-1",
        "source_uri": "https://example.invalid/doc",
        "source_sha256": SHA,
        "nav_profile": "HOLDING_ADJUSTED_NAV",
        "nav_version": 1,
        "lineage": {"page": 5},
    }
    row.update(patch)
    return row


def test_record_canonicalizes_and_can_derive_total_from_per_share():
    row = HoldingNavRecord.from_mapping(payload(nav_total=None, nav_per_share="12"))
    assert row.ticker == "KCHOL"
    assert row.nav_total == 1200000000
    assert row.nav_per_share == 12
    assert len(row.canonical_sha256) == 64


def test_record_rejects_mismatched_nav_math_and_future_nav_date():
    with pytest.raises(HoldingNavIngestError, match="uyusmuyor"):
        HoldingNavRecord.from_mapping(payload(nav_per_share="9"))
    with pytest.raises(HoldingNavIngestError, match="sonra olamaz"):
        HoldingNavRecord.from_mapping(payload(nav_asof_date="2026-08-01"))


@pytest.mark.parametrize(
    "patch",
    [
        {"ticker": []},
        {"version_sequence": True},
        {"nav_total": "NaN"},
        {"shares_out": 0},
        {"source_confidence": 1.1},
        {"source_sha256": "bad"},
        {"lineage": []},
        {"unknown": 1},
    ],
)
def test_record_rejects_invalid_contract(patch):
    with pytest.raises(HoldingNavIngestError):
        HoldingNavRecord.from_mapping(payload(**patch))


def test_jsonl_loader_deduplicates_identical_and_rejects_conflict(tmp_path: Path):
    p = tmp_path / "nav.jsonl"
    line = json.dumps(payload())
    p.write_text(line + "\n" + line + "\n", encoding="utf-8")
    rows = load_holding_nav_jsonl(p)
    assert len(rows) == 1

    changed = payload(nav_total="1100000000", nav_per_share="11")
    p.write_text(line + "\n" + json.dumps(changed) + "\n", encoding="utf-8")
    with pytest.raises(HoldingNavIngestError, match="farkli icerik"):
        load_holding_nav_jsonl(p)


def test_jsonl_loader_rejects_empty_and_bad_json(tmp_path: Path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(HoldingNavIngestError, match="bos"):
        load_holding_nav_jsonl(empty)
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{bad}\n", encoding="utf-8")
    with pytest.raises(HoldingNavIngestError, match="satir 1"):
        load_holding_nav_jsonl(bad)


_AUTO = object()

class Cur:
    def __init__(self, returned=_AUTO):
        self.calls = []
        self.returned = returned
    def execute(self, sql, params):
        self.calls.append((sql, params))
        if self.returned is _AUTO:
            self.returned = (params[-1],)
    def fetchone(self):
        return self.returned
    def __enter__(self): return self
    def __exit__(self, *args): return False


class Conn:
    def __init__(self, returned=_AUTO): self.cur = Cur(returned)
    def cursor(self): return self.cur
    def __enter__(self): return self
    def __exit__(self, *args): return False


def test_persist_uses_single_transaction_and_immutable_insert():
    conn = Conn()
    row = HoldingNavRecord.from_mapping(payload())
    assert persist_holding_nav_records(conn, [row]) == 1
    assert len(conn.cur.calls) == 1
    sql, params = conn.cur.calls[0]
    assert "ON CONFLICT" in sql
    assert "RETURNING canonical_sha256" in sql
    assert "WHERE core.holding_nav_snapshots.canonical_sha256 = EXCLUDED.canonical_sha256" in sql
    assert params[0] == "KCHOL"


def test_persist_revalidates_direct_dataclass_bypass():
    valid = HoldingNavRecord.from_mapping(payload())
    broken = HoldingNavRecord(**{**valid.__dict__, "source_sha256": "bad"})
    with pytest.raises(HoldingNavIngestError, match="SHA256"):
        persist_holding_nav_records(Conn(), [broken])


def test_equivalent_publication_instants_have_same_canonical_identity():
    istanbul = HoldingNavRecord.from_mapping(payload(published_at="2026-07-25T10:00:00+03:00"))
    utc = HoldingNavRecord.from_mapping(payload(published_at="2026-07-25T07:00:00Z"))
    assert istanbul.published_at == utc.published_at
    assert istanbul.canonical_sha256 == utc.canonical_sha256


def test_persist_rejects_existing_identity_with_different_hash():
    row = HoldingNavRecord.from_mapping(payload())
    with pytest.raises(HoldingNavIngestError, match="farkli icerik"):
        persist_holding_nav_records(Conn(returned=None), [row])


def test_lineage_rejects_non_string_nested_json_keys():
    with pytest.raises(HoldingNavIngestError, match="anahtarlari metin"):
        HoldingNavRecord.from_mapping(payload(lineage={"ok": [{1: "bad"}]}))


def test_jsonl_loader_rejects_oversized_line(tmp_path: Path):
    p = tmp_path / "huge.jsonl"
    p.write_text(" " * 2_000_001 + "\n", encoding="utf-8")
    with pytest.raises(HoldingNavIngestError, match="2MB"):
        load_holding_nav_jsonl(p)


def test_nav_record_requires_explicit_share_basis():
    row = payload()
    del row["share_basis"]
    with pytest.raises(HoldingNavIngestError, match="share_basis"):
        HoldingNavRecord.from_mapping(row)
