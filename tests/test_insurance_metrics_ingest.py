import json
from pathlib import Path

import pytest

from src.ingest.insurance_metrics import (
    InsuranceMetricsIngestError,
    InsuranceMetricsRecord,
    load_insurance_metrics_jsonl,
    persist_insurance_metrics_records,
)

SHA = "b" * 64


def row(**patch):
    data = {
        "ticker": "ANSGR",
        "period_end": "2026-06-30",
        "published_at": "2026-07-20T10:00:00+03:00",
        "version_tag": "ORIGINAL",
        "version_sequence": 1,
        "business_type": "NON_LIFE",
        "accounting_profile": "TFRS17_LOCAL_STATUTORY",
        "accounting_version": 1,
        "currency": "TRY",
        "shares_out": "100",
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "total_equity": "1000",
        "net_income_ttm": "100",
        "written_premiums_ttm": "1000",
        "technical_result_ttm": "100",
        "investment_income_ttm": "20",
        "earned_premiums_ttm": "900",
        "net_claims_ttm": "540",
        "operating_expenses_ttm": "180",
        "solvency_ratio": "1.6",
        "source_confidence": "0.9",
        "source_type": "KAP_INSURANCE_FINANCIAL_REPORT",
        "source_document_id": "DOC-1",
        "source_uri": None,
        "source_sha256": SHA,
        "metrics_profile": "KAP_INSURANCE_TTM",
        "metrics_version": 1,
        "lineage": {"source": "KAP"},
    }
    data.update(patch)
    return data


def test_record_canonical_identity_and_metrics():
    record = InsuranceMetricsRecord.from_mapping(row())
    assert record.business_type == "NON_LIFE"
    assert len(record.metrics_id) == 64
    assert len(record.canonical_sha256) == 64
    assert record.canonical_dict()["published_at"].endswith("+00:00")


def test_life_rejects_combined_ratio_fields():
    with pytest.raises(InsuranceMetricsIngestError, match="LIFE_PENSION"):
        InsuranceMetricsRecord.from_mapping(row(business_type="LIFE_PENSION"))
    life = row(
        business_type="LIFE_PENSION",
        earned_premiums_ttm=None,
        net_claims_ttm=None,
        operating_expenses_ttm=None,
    )
    assert InsuranceMetricsRecord.from_mapping(life).business_type == "LIFE_PENSION"


def test_partial_combined_ratio_and_fake_quarter_rejected():
    with pytest.raises(InsuranceMetricsIngestError, match="birlikte"):
        InsuranceMetricsRecord.from_mapping(row(net_claims_ttm=None))
    with pytest.raises(InsuranceMetricsIngestError, match="ceyrek"):
        InsuranceMetricsRecord.from_mapping(row(period_end="2026-06-29"))


def test_jsonl_deduplicates_identical_and_rejects_conflict(tmp_path: Path):
    path = tmp_path / "metrics.jsonl"
    path.write_text(json.dumps(row()) + "\n" + json.dumps(row()) + "\n", encoding="utf-8")
    assert len(load_insurance_metrics_jsonl(path)) == 1
    path.write_text(json.dumps(row()) + "\n" + json.dumps(row(total_equity="999")) + "\n", encoding="utf-8")
    with pytest.raises(InsuranceMetricsIngestError, match="farkli icerik"):
        load_insurance_metrics_jsonl(path)


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
    record = InsuranceMetricsRecord.from_mapping(row())
    assert persist_insurance_metrics_records(Conn((record.canonical_sha256,)), [record]) == 1
    with pytest.raises(InsuranceMetricsIngestError, match="farkli icerik"):
        persist_insurance_metrics_records(Conn(None), [record])


def test_structured_naive_and_unknown_values_rejected():
    with pytest.raises(InsuranceMetricsIngestError):
        InsuranceMetricsRecord.from_mapping(row(ticker=["ANSGR"]))
    with pytest.raises(InsuranceMetricsIngestError, match="timezone"):
        InsuranceMetricsRecord.from_mapping(row(published_at="2026-07-20T10:00:00"))
    with pytest.raises(InsuranceMetricsIngestError, match="bilinmeyen"):
        InsuranceMetricsRecord.from_mapping({**row(), "typo": 1})


def test_checked_in_example_loads():
    path = Path(__file__).resolve().parents[1] / "data" / "insurance_metrics.example.jsonl"
    rows = load_insurance_metrics_jsonl(path)
    assert len(rows) == 1
    assert rows[0].ticker == "ANSGR"


def test_metrics_identity_includes_source_document_id():
    first = InsuranceMetricsRecord.from_mapping(row(source_document_id="DOC-A"))
    second = InsuranceMetricsRecord.from_mapping(row(source_document_id="DOC-B"))
    assert first.metrics_id != second.metrics_id
