from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.ingest.api.kap_financial_facts import KapFinancialFact
from src.ingest.api.mkk_kap import KapApiConfigError, KapApiProtocolError
from src.ingest.api.semantic_facts import (
    SemanticFactMapper,
    SemanticMappingConfig,
)


BASE_CONFIG = {
    "mapping_profile": "BANK_CORE_TEST",
    "mapping_version": 1,
    "sector_family": "BANK",
    "fields": {
        "TOTAL_EQUITY": {
            "source_codes": ["EQ_PRIMARY", "EQ_FALLBACK"],
            "nature": "INSTANT",
            "statement_scope_priority": ["CONSOLIDATED", "SOLO"],
        },
        "NET_INCOME": {
            "source_codes": ["NET_PROFIT"],
            "nature": "YTD",
            "period_start_policy": "REQUIRED",
            "statement_scope_priority": ["CONSOLIDATED", "SOLO"],
        },
        "DIVIDENDS_PAID": {
            "source_codes": ["DIVIDENDS"],
            "nature": "YTD",
            "sign": "ABS",
            "period_start_policy": "REQUIRED",
        },
    },
}


def raw_fact(
    code: str,
    value: str = "100",
    *,
    scope: str | None = "CONSOLIDATED",
    currency: str | None = "TRY",
    period_start: date | None = None,
    period_end: date = date(2026, 3, 31),
    dimensions=None,
    fact_key: str | None = None,
    disclosure_id: str = "D1",
    ticker: str | None = "GARAN",
    published_at: datetime = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc),
):
    normalized = Decimal(value)
    key = fact_key or __import__("hashlib").sha256(
        f"{code}|{period_start}|{period_end}|{currency}|{scope}|{dimensions}".encode()
    ).hexdigest()
    return KapFinancialFact(
        source="MKK_KAP_API",
        disclosure_id=disclosure_id,
        mapping_profile="PORTAL_FACTS",
        mapping_version=3,
        fact_key=key,
        ticker=ticker,
        published_at=published_at,
        version_tag="RESTATED",
        version_sequence=2,
        fact_code=code,
        period_start=period_start,
        period_end=period_end,
        currency=currency,
        unit_scale=1,
        raw_value_text=value,
        normalized_value=normalized,
        scaled_value=normalized,
        statement_scope=scope,
        dimensions={} if dimensions is None else dimensions,
        extracted_at=published_at + __import__("datetime").timedelta(minutes=5),
    )


def mapper(config=None):
    return SemanticFactMapper(SemanticMappingConfig.from_dict(config or BASE_CONFIG))


def mapped_at():
    return datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc)


def test_maps_source_fact_to_versioned_semantic_fact_with_lineage():
    out = mapper().map_facts([raw_fact("EQ_PRIMARY")], mapped_at=mapped_at())
    assert len(out) == 1
    row = out[0]
    assert row.ticker == "GARAN"
    assert row.sector_family == "BANK"
    assert row.semantic_profile == "BANK_CORE_TEST"
    assert row.semantic_version == 1
    assert row.canonical_field == "TOTAL_EQUITY"
    assert row.nature == "INSTANT"
    assert row.value == Decimal("100")
    assert row.source_fact_code == "EQ_PRIMARY"
    assert len(row.lineage_sha256) == 64


def test_primary_source_code_beats_fallback_even_if_fallback_fact_key_sorts_first():
    out = mapper().map_facts(
        [
            raw_fact("EQ_FALLBACK", "999", fact_key="0" * 64),
            raw_fact("EQ_PRIMARY", "100", fact_key="f" * 64),
        ],
        mapped_at=mapped_at(),
    )
    assert out[0].value == Decimal("100")
    assert out[0].source_fact_code == "EQ_PRIMARY"


def test_consolidated_scope_beats_solo_scope():
    out = mapper().map_facts(
        [
            raw_fact("EQ_PRIMARY", "80", scope="SOLO", fact_key="1" * 64),
            raw_fact("EQ_PRIMARY", "100", scope="CONSOLIDATED", fact_key="2" * 64),
        ],
        mapped_at=mapped_at(),
    )
    assert out[0].statement_scope == "CONSOLIDATED"
    assert out[0].value == Decimal("100")


def test_same_priority_conflicting_values_are_rejected_not_picked_by_row_order():
    with pytest.raises(KapApiProtocolError, match="celiskili deger"):
        mapper().map_facts(
            [
                raw_fact("EQ_PRIMARY", "100", fact_key="1" * 64),
                raw_fact("EQ_PRIMARY", "101", fact_key="2" * 64),
            ],
            mapped_at=mapped_at(),
        )


def test_same_priority_identical_value_is_deduped_deterministically():
    out = mapper().map_facts(
        [
            raw_fact("EQ_PRIMARY", "100", fact_key="2" * 64),
            raw_fact("EQ_PRIMARY", "100", fact_key="1" * 64),
        ],
        mapped_at=mapped_at(),
    )
    assert len(out) == 1
    assert out[0].source_fact_key == "1" * 64


def test_unmapped_facts_are_ignored_but_all_unmapped_fails_closed():
    out = mapper().map_facts(
        [raw_fact("UNKNOWN"), raw_fact("EQ_PRIMARY")], mapped_at=mapped_at()
    )
    assert len(out) == 1
    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        mapper().map_facts([raw_fact("UNKNOWN")], mapped_at=mapped_at())


def test_ytd_requires_period_start_and_abs_sign_is_applied():
    with pytest.raises(KapApiProtocolError, match="period_start"):
        mapper().map_facts([raw_fact("NET_PROFIT")], mapped_at=mapped_at())
    out = mapper().map_facts(
        [
            raw_fact(
                "DIVIDENDS", "-25", period_start=date(2026, 1, 1)
            )
        ],
        mapped_at=mapped_at(),
    )
    assert out[0].value == Decimal("25")


def test_currency_and_dimension_filters_are_fail_closed():
    config = dict(BASE_CONFIG)
    config["fields"] = {
        "TOTAL_EQUITY": {
            "source_codes": ["EQ_PRIMARY"],
            "nature": "INSTANT",
            "currency": "TRY",
            "dimensions_equals": {"member": "TOTAL"},
        }
    }
    m = mapper(config)
    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        m.map_facts(
            [raw_fact("EQ_PRIMARY", currency="USD", dimensions={"member": "TOTAL"})],
            mapped_at=mapped_at(),
        )
    with pytest.raises(KapApiProtocolError, match="hic kalem"):
        m.map_facts(
            [raw_fact("EQ_PRIMARY", dimensions={"member": "MINORITY"})],
            mapped_at=mapped_at(),
        )
    out = m.map_facts(
        [raw_fact("EQ_PRIMARY", dimensions={"member": "TOTAL", "extra": 1})],
        mapped_at=mapped_at(),
    )
    assert len(out) == 1


def test_single_batch_cannot_mix_disclosures_versions_or_tickers():
    with pytest.raises(KapApiProtocolError, match="tek disclosure"):
        mapper().map_facts(
            [raw_fact("EQ_PRIMARY"), raw_fact("NET_PROFIT", disclosure_id="D2", period_start=date(2026, 1, 1))],
            mapped_at=mapped_at(),
        )
    with pytest.raises(KapApiProtocolError, match="ticker zorunlu"):
        mapper().map_facts([raw_fact("EQ_PRIMARY", ticker=None)], mapped_at=mapped_at())


def test_mapping_rejects_lookahead_and_naive_mapped_at():
    with pytest.raises(KapApiProtocolError, match="look-ahead"):
        mapper().map_facts(
            [raw_fact("EQ_PRIMARY", published_at=datetime(2026, 5, 11, tzinfo=timezone.utc))],
            mapped_at=mapped_at(),
        )
    with pytest.raises(ValueError, match="timezone"):
        mapper().map_facts([raw_fact("EQ_PRIMARY")], mapped_at=datetime(2026, 5, 10))


@pytest.mark.parametrize(
    "change, message",
    [
        ({"mapping_version": True}, "mapping_version"),
        ({"mapping_version": 0}, "mapping_version"),
        ({"sector_family": ""}, "sector_family"),
        ({"fields": []}, "fields"),
    ],
)
def test_semantic_mapping_config_is_strict(change, message):
    raw = dict(BASE_CONFIG)
    raw.update(change)
    with pytest.raises(KapApiConfigError, match=message):
        SemanticMappingConfig.from_dict(raw)


def test_overlapping_source_code_selectors_are_rejected():
    raw = dict(BASE_CONFIG)
    raw["fields"] = {
        "TOTAL_EQUITY": {"source_codes": ["SAME"], "nature": "INSTANT"},
        "SHARES_OUT": {"source_codes": ["SAME"], "nature": "INSTANT"},
    }
    with pytest.raises(KapApiConfigError, match="seciciler cakismamali"):
        SemanticMappingConfig.from_dict(raw)


def test_same_source_code_can_map_disjoint_dimension_members():
    raw = dict(BASE_CONFIG)
    raw["fields"] = {
        "TOTAL_LOANS": {
            "source_codes": ["LOANS"], "nature": "INSTANT",
            "dimensions_equals": {"member": "TOTAL"},
        },
        "NPL_LOANS": {
            "source_codes": ["LOANS"], "nature": "INSTANT",
            "dimensions_equals": {"member": "NPL"},
        },
    }
    m = SemanticFactMapper(SemanticMappingConfig.from_dict(raw))
    out = m.map_facts(
        [
            raw_fact("LOANS", "100", dimensions={"member": "TOTAL"}, fact_key="1" * 64),
            raw_fact("LOANS", "5", dimensions={"member": "NPL"}, fact_key="2" * 64),
        ],
        mapped_at=mapped_at(),
    )
    assert {row.canonical_field: row.value for row in out} == {
        "TOTAL_LOANS": Decimal("100"),
        "NPL_LOANS": Decimal("5"),
    }


@pytest.mark.parametrize(
    "rule_change, message",
    [
        ({"source_codes": []}, "source_codes"),
        ({"source_codes": ["A", "A"]}, "yinelenemez"),
        ({"nature": "BAD"}, "nature"),
        ({"sign": "BAD"}, "sign"),
        ({"statement_scope_priority": "CONSOLIDATED"}, "liste"),
        ({"dimensions_equals": []}, "nesne"),
        ({"period_start_policy": "BAD"}, "period_start_policy"),
    ],
)
def test_field_rule_contract_is_strict(rule_change, message):
    rule = {"source_codes": ["EQ"], "nature": "INSTANT"}
    rule.update(rule_change)
    raw = dict(BASE_CONFIG)
    raw["fields"] = {"TOTAL_EQUITY": rule}
    with pytest.raises(KapApiConfigError, match=message):
        SemanticMappingConfig.from_dict(raw)


def test_semantic_mapping_rejects_period_after_publication_time():
    with pytest.raises(KapApiProtocolError, match="period_end yayin anindan sonra"):
        mapper().map_facts(
            [
                raw_fact(
                    "EQ_PRIMARY",
                    period_end=date(2026, 6, 30),
                    published_at=datetime(2026, 6, 29, 20, tzinfo=timezone.utc),
                )
            ],
            mapped_at=datetime(2026, 6, 29, 21, tzinfo=timezone.utc),
        )


def test_non_string_canonical_field_key_is_rejected_not_stringified():
    raw = dict(BASE_CONFIG)
    raw["fields"] = {1: {"source_codes": ["EQ"], "nature": "INSTANT"}}
    with pytest.raises(KapApiConfigError, match="fields anahtarlari"):
        SemanticMappingConfig.from_dict(raw)

@pytest.mark.parametrize(
    "bad, message",
    [
        ({"fact_code": "EQ"}, "KapFinancialFact"),
        (None, "KapFinancialFact"),
    ],
)
def test_semantic_mapper_rejects_non_fact_elements_without_attribute_error(bad, message):
    with pytest.raises(KapApiProtocolError, match=message):
        mapper().map_facts([bad], mapped_at=mapped_at())


def test_semantic_mapper_rejects_non_iterable_input_controlled():
    with pytest.raises(KapApiProtocolError, match="iterable"):
        mapper().map_facts(123, mapped_at=mapped_at())


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"fact_key": "bad"}, "fact_key"),
        ({"version_sequence": True}, "version_sequence"),
        ({"scaled_value": Decimal("NaN")}, "scaled_value"),
        ({"dimensions": []}, "dimensions"),
        ({"published_at": datetime(2026, 5, 10)}, "published_at timezone"),
        ({"period_end": "2026-03-31"}, "period_end date"),
    ],
)
def test_semantic_mapper_raw_fact_contract_is_strict(mutation, message):
    original = raw_fact("EQ_PRIMARY")
    values = original.__dict__.copy()
    values.update(mutation)
    malformed = KapFinancialFact(**values)
    with pytest.raises(KapApiProtocolError, match=message):
        mapper().map_facts([malformed], mapped_at=mapped_at())
