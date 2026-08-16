from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.ingest.api.kap_financial_facts import (
    KapFinancialFactConfig,
    KapFinancialFactExtractor,
)
from src.ingest.api.mkk_kap import KapApiConfigError, KapApiProtocolError, KapDisclosureEnvelope
from src.ingest.kap_raw import persist_kap_financial_facts


CONFIG = {
    "mapping_profile": "TEST_FACTS",
    "mapping_version": 1,
    "facts_path": "statement.facts",
    "version_tag_path": "statement.versionTag",
    "version_sequence_path": "statement.versionSequence",
    "dimensions_path": "dimensions",
    "default_unit_scale": 1,
    "default_currency": "TRY",
    "default_statement_scope": "CONSOLIDATED",
    "fields": {
        "fact_code": "code",
        "value": "value",
        "period_start": "periodStart",
        "period_end": "periodEnd",
        "currency": "currency",
        "unit_scale": "unitScale",
        "statement_scope": "scope",
    },
}


def envelope(facts):
    return KapDisclosureEnvelope(
        disclosure_id="D1",
        published_at=datetime(2026, 5, 10, 9, 30, tzinfo=timezone.utc),
        ticker="GARAN",
        company_id="101",
        notification_type="FINANCIAL_STATEMENT",
        subject="Finansal Rapor",
        source_url="https://kap.org.tr/x",
        payload={
            "statement": {
                "versionTag": "RESTATED",
                "versionSequence": 2,
                "facts": facts,
            }
        },
        payload_sha256="a" * 64,
        fetched_at=datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc),
    )


def fact(code="TOTAL_EQUITY", value="123.45", **changes):
    row = {
        "code": code,
        "value": value,
        "periodStart": "2026-01-01",
        "periodEnd": "2026-03-31",
        "currency": "try",
        "unitScale": 1000,
        "scope": "consolidated",
        "dimensions": {"axis": "TOTAL"},
    }
    row.update(changes)
    return row


def test_extracts_lossless_fact_context_and_scaled_value():
    extractor = KapFinancialFactExtractor(KapFinancialFactConfig.from_dict(CONFIG))
    facts = extractor.extract(
        envelope([fact(), fact("NET_INCOME", "42")]),
        extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
    )
    assert len(facts) == 2
    equity = next(x for x in facts if x.fact_code == "TOTAL_EQUITY")
    assert equity.version_tag == "RESTATED"
    assert equity.version_sequence == 2
    assert equity.currency == "TRY"
    assert equity.statement_scope == "CONSOLIDATED"
    assert equity.normalized_value == Decimal("123.45")
    assert equity.scaled_value == Decimal("123450.00")
    assert equity.period_start.isoformat() == "2026-01-01"
    assert equity.period_end.isoformat() == "2026-03-31"
    assert equity.dimensions == {"axis": "TOTAL"}
    assert len(equity.fact_key) == 64


def test_identical_context_is_deduped_but_conflicting_value_is_rejected():
    extractor = KapFinancialFactExtractor(KapFinancialFactConfig.from_dict(CONFIG))
    out = extractor.extract(
        envelope([fact(), fact()]),
        extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
    )
    assert len(out) == 1
    with pytest.raises(KapApiProtocolError, match="ayni fact context"):
        extractor.extract(
            envelope([fact(value="1"), fact(value="2")]),
            extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    "changed, message",
    [
        ({"value": True}, "gercek sayi"),
        ({"value": "NaN"}, "sonlu"),
        ({"unitScale": 0}, "pozitif tam sayi"),
        ({"unitScale": 1.5}, "pozitif tam sayi"),
        ({"periodStart": "2027-01-01"}, "period_start period_end'den sonra"),
        ({"dimensions": ["bad"]}, "dimensions nesne"),
    ],
)
def test_invalid_fact_values_fail_closed(changed, message):
    extractor = KapFinancialFactExtractor(KapFinancialFactConfig.from_dict(CONFIG))
    with pytest.raises(KapApiProtocolError, match=message):
        extractor.extract(
            envelope([fact(**changed)]),
            extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    "change, message",
    [
        ({"mapping_version": True}, "mapping_version"),
        ({"mapping_version": 0}, "mapping_version"),
        ({"default_unit_scale": False}, "default_unit_scale"),
        ({"fields": []}, "fields nesne"),
        ({"facts_path": ""}, "facts_path"),
    ],
)
def test_mapping_config_is_strict(change, message):
    raw = dict(CONFIG)
    raw.update(change)
    with pytest.raises(KapApiConfigError, match=message):
        KapFinancialFactConfig.from_dict(raw)


class Cursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def __enter__(self): return self
    def __exit__(self, *_): return False


class Conn:
    def __init__(self): self.cur = Cursor()
    def cursor(self): return self.cur
    def __enter__(self): return self
    def __exit__(self, *_): return False


def test_fact_persistence_is_idempotent_and_does_not_update_values():
    extractor = KapFinancialFactExtractor(KapFinancialFactConfig.from_dict(CONFIG))
    facts = extractor.extract(
        envelope([fact()]),
        extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
    )
    conn = Conn()
    assert persist_kap_financial_facts(conn, facts) == 1
    sql, params = conn.cur.executed[0]
    assert "INSERT INTO raw.kap_financial_facts" in sql
    assert "DO UPDATE SET extracted_at" in sql
    assert "normalized_value = EXCLUDED" not in sql
    assert params[0:5] == ("MKK_KAP_API", "D1", "TEST_FACTS", 1, facts[0].fact_key)


def test_migration_has_fact_integrity_guards():
    sql = (Path(__file__).resolve().parents[1] / "sql" / "015_kap_official_ingestion.sql").read_text().lower()
    for token in (
        "create table if not exists raw.kap_financial_facts",
        "foreign key (source, disclosure_id)",
        "ck_kap_fact_unit_scale",
        "ck_kap_fact_period",
        "reject_kap_fact_mutation",
        "old.normalized_value <> new.normalized_value",
    ):
        assert token in sql


def test_dates_are_not_silently_truncated_and_iso_timestamps_are_supported():
    extractor = KapFinancialFactExtractor(KapFinancialFactConfig.from_dict(CONFIG))
    with pytest.raises(KapApiProtocolError, match="gecersiz tarih"):
        extractor.extract(
            envelope([fact(periodEnd="2026-03-31BOZUK")]),
            extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
        )
    out = extractor.extract(
        envelope([fact(periodEnd="2026-03-31T00:00:00+03:00")]),
        extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
    )
    assert out[0].period_end.isoformat() == "2026-03-31"


@pytest.mark.parametrize(
    "changed, message",
    [
        ({"value": "1e101"}, "sayisal siniri"),
        ({"value": "1e95", "unitScale": 10**6}, "scaled_value"),
        ({"unitScale": 10**12 + 1}, "birim olcegi"),
        ({"dimensions": {"x": "a" * 70000}}, "dimensions cok buyuk"),
    ],
)
def test_database_boundary_values_fail_before_persistence(changed, message):
    extractor = KapFinancialFactExtractor(KapFinancialFactConfig.from_dict(CONFIG))
    with pytest.raises(KapApiProtocolError, match=message):
        extractor.extract(
            envelope([fact(**changed)]),
            extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
        )


def test_default_unit_scale_has_same_upper_bound_as_fact_units():
    raw = dict(CONFIG)
    raw["default_unit_scale"] = 10**12 + 1
    with pytest.raises(KapApiConfigError, match="desteklenen siniri"):
        KapFinancialFactConfig.from_dict(raw)


def test_empty_or_excessive_fact_batches_are_rejected():
    extractor = KapFinancialFactExtractor(KapFinancialFactConfig.from_dict(CONFIG))
    with pytest.raises(KapApiProtocolError, match="bos liste"):
        extractor.extract(
            envelope([]),
            extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
        )


def test_text_context_fields_do_not_stringify_structured_values():
    extractor = KapFinancialFactExtractor(KapFinancialFactConfig.from_dict(CONFIG))
    with pytest.raises(KapApiProtocolError, match="string olmali"):
        extractor.extract(
            envelope([fact(code=["TOTAL_EQUITY"])]),
            extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
        )
    with pytest.raises(KapApiProtocolError, match="string olmali"):
        extractor.extract(
            envelope([fact(currency={"code": "TRY"})]),
            extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
        )


def test_future_publication_is_rejected_at_extraction_boundary():
    extractor = KapFinancialFactExtractor(KapFinancialFactConfig.from_dict(CONFIG))
    with pytest.raises(KapApiProtocolError, match="look-ahead"):
        extractor.extract(
            envelope([fact()]),
            extracted_at=datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc),
        )


def test_mapping_and_version_sequence_fit_database_integer_contract():
    raw = dict(CONFIG)
    raw["mapping_version"] = 2_147_483_648
    with pytest.raises(KapApiConfigError, match="PostgreSQL INT"):
        KapFinancialFactConfig.from_dict(raw)

    bad = envelope([fact()])
    payload = dict(bad.payload)
    payload["statement"] = dict(payload["statement"])
    payload["statement"]["versionSequence"] = 2_147_483_648
    bad = KapDisclosureEnvelope(
        disclosure_id=bad.disclosure_id,
        published_at=bad.published_at,
        ticker=bad.ticker,
        company_id=bad.company_id,
        notification_type=bad.notification_type,
        subject=bad.subject,
        source_url=bad.source_url,
        payload=payload,
        payload_sha256=bad.payload_sha256,
        fetched_at=bad.fetched_at,
        source=bad.source,
    )
    with pytest.raises(KapApiProtocolError, match="PostgreSQL INT"):
        KapFinancialFactExtractor(KapFinancialFactConfig.from_dict(CONFIG)).extract(
            bad,
            extracted_at=datetime(2026, 5, 10, 10, 1, tzinfo=timezone.utc),
        )


def test_migration_makes_all_fact_context_immutable_and_bounded():
    sql = (Path(__file__).resolve().parents[1] / "sql" / "015_kap_official_ingestion.sql").read_text().lower()
    for token in (
        "ck_kap_fact_publication_time",
        "ck_kap_fact_numeric_bounds",
        "octet_length(dimensions::text) <= 65536",
        "old.fact_code <> new.fact_code",
        "old.version_tag <> new.version_tag",
        "old.raw_value_text <> new.raw_value_text",
        "old.statement_scope is distinct from new.statement_scope",
    ):
        assert token in sql
