import json
from pathlib import Path

import pytest

from src.ingest.gyo_nav import GyoNavIngestError, GyoNavRecord, load_gyo_nav_jsonl, persist_gyo_nav_records

SHA = "b" * 64


def row(**patch):
    data = {
        "ticker": "AGYO",
        "nav_asof_date": "2026-06-30",
        "published_at": "2026-07-20T10:00:00+03:00",
        "version_tag": "ORIGINAL",
        "version_sequence": 1,
        "nav_total": "1000",
        "shares_out": "100",
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "currency": "TRY",
        "property_portfolio_value": "1300",
        "source_confidence": "0.9",
        "source_type": "KAP_GYO_PORTFOLIO_REPORT",
        "source_document_id": "DOC-1",
        "source_sha256": SHA,
        "nav_profile": "GYO_REPORTED_NAV",
        "nav_version": 1,
        "lineage": {"source": "KAP"},
    }
    data.update(patch)
    return data


def test_direct_nav_record_and_per_share():
    r = GyoNavRecord.from_mapping(row(nav_per_share="10"))
    assert r.nav_source_method == "DIRECT"
    assert str(r.nav_per_share) == "10"


def test_components_can_derive_nav():
    data = row(nav_total=None, cash_and_financial_assets="100", other_assets="50", total_liabilities="450")
    r = GyoNavRecord.from_mapping(data)
    assert r.nav_source_method == "DERIVED"
    assert r.nav_total == 1000


def test_partial_components_and_reconciliation_mismatch_rejected():
    with pytest.raises(GyoNavIngestError, match="tamamen"):
        GyoNavRecord.from_mapping(row(cash_and_financial_assets="10"))
    with pytest.raises(GyoNavIngestError, match="uzlasmiyor"):
        GyoNavRecord.from_mapping(row(cash_and_financial_assets="100", other_assets="50", total_liabilities="10"))


def test_load_jsonl_deduplicates_identical_and_rejects_conflict(tmp_path: Path):
    p = tmp_path / "nav.jsonl"
    p.write_text(json.dumps(row()) + "\n" + json.dumps(row()) + "\n", encoding="utf-8")
    assert len(load_gyo_nav_jsonl(p)) == 1
    p.write_text(json.dumps(row()) + "\n" + json.dumps(row(nav_total="999")) + "\n", encoding="utf-8")
    with pytest.raises(GyoNavIngestError, match="farkli icerik"):
        load_gyo_nav_jsonl(p)


class Cur:
    def __init__(self, returned): self.returned = returned; self.calls = []
    def execute(self, sql, params): self.calls.append((sql, params))
    def fetchone(self): return self.returned
    def __enter__(self): return self
    def __exit__(self, *args): return False


class Conn:
    def __init__(self, returned): self.cur = Cur(returned)
    def cursor(self): return self.cur
    def __enter__(self): return self
    def __exit__(self, *args): return False


def test_persist_is_idempotent_only_for_same_canonical_sha():
    r = GyoNavRecord.from_mapping(row())
    assert persist_gyo_nav_records(Conn((r.canonical_sha256,)), [r]) == 1
    with pytest.raises(GyoNavIngestError, match="farkli icerik"):
        persist_gyo_nav_records(Conn(None), [r])


def test_structured_and_naive_values_rejected():
    with pytest.raises(GyoNavIngestError):
        GyoNavRecord.from_mapping(row(ticker=["AGYO"]))
    with pytest.raises(GyoNavIngestError, match="timezone"):
        GyoNavRecord.from_mapping(row(published_at="2026-07-20T10:00:00"))
