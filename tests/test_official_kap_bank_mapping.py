from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.ingest.api.kap_financial_facts import KapFinancialFact
from src.ingest.api.semantic_facts import SemanticFactMapper, SemanticMappingConfig
from src.ingest.bank_fact_materializer import (
    BankDerivationConfig,
    build_quarter_ends,
    derive_bank_metrics,
)

ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_CONFIG = SemanticMappingConfig.from_json_file(
    str(ROOT / "config" / "kap_bank_semantic_mapping.official_v1.json")
)
DERIVATION_CONFIG = BankDerivationConfig.from_json_file(
    str(ROOT / "config" / "bank_fact_derivation.official_v1.json")
)
ANCHOR = date(2026, 3, 31)
ANALYSIS_AT = datetime(2026, 5, 15, 12, tzinfo=timezone.utc)


def raw_fact(
    disclosure_id: str,
    code: str,
    value: str,
    *,
    period_end: date,
    period_start: date | None,
    published_at: datetime,
    scope: str = "CONSOLIDATED",
    unit_scale: int = 1000,
):
    normalized = Decimal(value)
    scaled = normalized * Decimal(unit_scale)
    key = __import__("hashlib").sha256(
        f"{disclosure_id}|{code}|{period_start}|{period_end}|{scope}|{value}".encode()
    ).hexdigest()
    return KapFinancialFact(
        source="MKK_KAP_API",
        disclosure_id=disclosure_id,
        mapping_profile="MKK_KAP_FINANCIAL_FACTS",
        mapping_version=1,
        fact_key=key,
        ticker="ISCTR",
        published_at=published_at,
        version_tag="ORIGINAL",
        version_sequence=1,
        fact_code=code,
        period_start=period_start,
        period_end=period_end,
        currency="TRY",
        unit_scale=unit_scale,
        raw_value_text=value,
        normalized_value=normalized,
        scaled_value=scaled,
        statement_scope=scope,
        dimensions={},
        extracted_at=published_at + timedelta(minutes=1),
    )


def period_disclosure(period: date, idx: int):
    pub = datetime.combine(period + timedelta(days=40), datetime.min.time(), tzinfo=timezone.utc)
    disclosure_id = f"ISCTR-{period.isoformat()}"
    year_start = date(period.year, 1, 1)
    quarter_no = (period.month - 1) // 3 + 1
    equity_thousand_try = Decimal("300000000") + Decimal(idx) * Decimal("10000000")
    capital_thousand_try = Decimal("25000000")
    ytd_profit_thousand_try = Decimal(quarter_no) * Decimal("10000000")
    ytd_dividend_thousand_try = Decimal(quarter_no) * Decimal("1000000")
    return [
        raw_fact(
            disclosure_id, "ifrs-full_Equity", str(equity_thousand_try),
            period_end=period, period_start=None, published_at=pub,
        ),
        raw_fact(
            disclosure_id, "ifrs-full_IssuedCapital", str(capital_thousand_try),
            period_end=period, period_start=None, published_at=pub,
        ),
        # Primary owners-of-parent fact must beat generic ProfitLoss fallback.
        raw_fact(
            disclosure_id, "ifrs-full_ProfitLossAttributableToOwnersOfParent",
            str(ytd_profit_thousand_try), period_end=period,
            period_start=year_start, published_at=pub,
        ),
        raw_fact(
            disclosure_id, "ifrs-full_ProfitLoss",
            str(ytd_profit_thousand_try + Decimal("999999")), period_end=period,
            period_start=year_start, published_at=pub,
        ),
        raw_fact(
            disclosure_id, "ifrs-full_DividendsPaid",
            str(-ytd_dividend_thousand_try), period_end=period,
            period_start=year_start, published_at=pub,
        ),
    ]


def test_official_configs_load_and_expose_explicit_nominal_share_assumption():
    assert SEMANTIC_CONFIG.mapping_profile == "KAP_TFRS_BANK_V1"
    assert DERIVATION_CONFIG.semantic_profile == SEMANTIC_CONFIG.mapping_profile
    assert DERIVATION_CONFIG.shares_out_field is None
    assert DERIVATION_CONFIG.issued_capital_field == "ISSUED_CAPITAL"
    assert DERIVATION_CONFIG.share_nominal_value == Decimal("1")
    assert {
        "TOTAL_EQUITY", "ISSUED_CAPITAL", "NET_INCOME", "DIVIDENDS_PAID"
    } <= set(DERIVATION_CONFIG.required_fields)


def test_official_xbrl_mapping_prefers_parent_profit_and_scales_thousand_try():
    period = ANCHOR
    mapper = SemanticFactMapper(SEMANTIC_CONFIG)
    facts = period_disclosure(period, 11)
    mapped = mapper.map_facts(facts, mapped_at=ANALYSIS_AT)
    by_field = {row.canonical_field: row for row in mapped}
    assert by_field["NET_INCOME"].source_fact_code == (
        "ifrs-full_ProfitLossAttributableToOwnersOfParent"
    )
    assert by_field["NET_INCOME"].value == Decimal("10000000") * Decimal(1000)
    assert by_field["DIVIDENDS_PAID"].value == Decimal("1000000") * Decimal(1000)
    assert by_field["ISSUED_CAPITAL"].value == Decimal("25000000") * Decimal(1000)


def test_official_mapping_to_bank_derivation_is_end_to_end_and_deterministic():
    mapper = SemanticFactMapper(SEMANTIC_CONFIG)
    semantic = []
    for idx, period in enumerate(build_quarter_ends(ANCHOR, 12)):
        semantic.extend(
            mapper.map_facts(period_disclosure(period, idx), mapped_at=ANALYSIS_AT)
        )
    first = derive_bank_metrics(
        semantic,
        config=DERIVATION_CONFIG,
        ticker="ISCTR",
        analysis_at=ANALYSIS_AT,
        anchor_period_end=ANCHOR,
    )
    second = derive_bank_metrics(
        list(reversed(semantic)),
        config=DERIVATION_CONFIG,
        ticker="ISCTR",
        analysis_at=ANALYSIS_AT,
        anchor_period_end=ANCHOR,
    )
    assert first == second
    row = next(item for item in first if item.period_end == ANCHOR)
    expected_equity = Decimal("410000000") * Decimal(1000)
    expected_shares = Decimal("25000000") * Decimal(1000) / Decimal("1")
    assert row.bvps == pytest.approx(float(expected_equity / expected_shares))
    assert row.diagnostics["shares_source"] == "ISSUED_CAPITAL_DIV_NOMINAL"
    assert row.diagnostics["share_nominal_value"] == "1"
    # Standalone quarterly profit is 10bn TRY; four quarters = 40bn TRY.
    lag4_equity = Decimal("370000000") * Decimal(1000)
    expected_roe = Decimal("40000000") * Decimal(1000) / (
        (expected_equity + lag4_equity) / Decimal(2)
    )
    assert row.roe_ttm == pytest.approx(float(expected_roe))
    assert row.payout_sus == pytest.approx(0.10)
    fields = {item["canonical_field"] for item in row.source_lineage}
    assert {"TOTAL_EQUITY", "ISSUED_CAPITAL", "NET_INCOME", "DIVIDENDS_PAID"} <= fields
