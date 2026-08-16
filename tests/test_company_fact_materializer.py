from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.ingest.api.semantic_facts import SemanticFinancialFact
from src.ingest.company_fact_materializer import (
    CompanyDerivationConfig,
    CompanyDerivationError,
    derive_company_quarters,
    fetch_active_company_tickers,
    fetch_company_semantic_facts_batch_asof,
    materialize_company_metrics_batch,
)

UTC = timezone.utc
ANALYSIS = datetime(2026, 3, 1, 12, tzinfo=UTC)
Q1 = date(2025, 3, 31)
Q2 = date(2025, 6, 30)
Q3 = date(2025, 9, 30)
YEAR_START = date(2025, 1, 1)


def cfg(**patch):
    payload = {
        "derivation_profile": "NONBANK_TEST",
        "derivation_version": 1,
        "semantic_profile": "SEM_TEST",
        "semantic_version": 1,
        "sector_families": ["NONFIN", "HOLDING", "GYO", "INSURANCE", "FINANCIAL"],
        "currency": "TRY",
        "target_periods": 2,
        "history_periods": 3,
        "field_map": {
            "revenue": "REVENUE",
            "cogs": "COST_OF_SALES",
            "net_income": "NET_INCOME",
            "total_assets": "TOTAL_ASSETS",
            "total_equity": "TOTAL_EQUITY",
        },
        "shares_out_field": "SHARES_OUT",
        "issued_capital_field": "ISSUED_CAPITAL",
        "share_nominal_value": 1,
        "required_fields": ["revenue", "net_income", "total_assets", "total_equity"],
        "minimum_present_fields": ["revenue", "net_income", "total_assets", "total_equity"],
        "minimum_present_count": 2,
        "derive_gross_profit": True,
    }
    payload.update(patch)
    return CompanyDerivationConfig.from_dict(payload)


def fact(
    field: str,
    period_end: date,
    value: str,
    *,
    nature: str,
    period_start: date | None = None,
    published_at: datetime = datetime(2025, 8, 10, 9, tzinfo=UTC),
    disclosure_id: str | None = None,
    version_sequence: int = 1,
    ticker: str = "AAA",
    sector_family: str = "NONFIN",
    lineage_char: str = "a",
):
    disclosure = disclosure_id or f"D-{period_end}-{published_at.date()}-{version_sequence}"
    return SemanticFinancialFact(
        source="MKK",
        disclosure_id=disclosure,
        ticker=ticker,
        published_at=published_at,
        version_tag="RESTATED" if version_sequence > 1 else "ORIGINAL",
        version_sequence=version_sequence,
        sector_family=sector_family,
        semantic_profile="SEM_TEST",
        semantic_version=1,
        canonical_field=field,
        nature=nature,
        period_start=period_start,
        period_end=period_end,
        currency=None if field == "SHARES_OUT" else "TRY",
        statement_scope="CONSOLIDATED",
        value=Decimal(value),
        source_fact_code=f"SRC_{field}",
        source_fact_key=(lineage_char * 64)[:64],
        source_mapping_profile="RAW",
        source_mapping_version=1,
        dimensions={},
        lineage_sha256=(lineage_char * 64)[:64],
        mapped_at=published_at,
    )


def basic_facts(*, q2_revenue="250", q2_publication=datetime(2025, 8, 10, 9, tzinfo=UTC)):
    q1_disclosure = "Q1"
    q2_disclosure = "Q2"
    return [
        fact("REVENUE", Q1, "100", nature="YTD", period_start=YEAR_START, disclosure_id=q1_disclosure, published_at=datetime(2025, 5, 10, 9, tzinfo=UTC), lineage_char="a"),
        fact("COST_OF_SALES", Q1, "60", nature="YTD", period_start=YEAR_START, disclosure_id=q1_disclosure, published_at=datetime(2025, 5, 10, 9, tzinfo=UTC), lineage_char="b"),
        fact("NET_INCOME", Q1, "10", nature="YTD", period_start=YEAR_START, disclosure_id=q1_disclosure, published_at=datetime(2025, 5, 10, 9, tzinfo=UTC), lineage_char="c"),
        fact("TOTAL_ASSETS", Q1, "1000", nature="INSTANT", disclosure_id=q1_disclosure, published_at=datetime(2025, 5, 10, 9, tzinfo=UTC), lineage_char="d"),
        fact("TOTAL_EQUITY", Q1, "400", nature="INSTANT", disclosure_id=q1_disclosure, published_at=datetime(2025, 5, 10, 9, tzinfo=UTC), lineage_char="e"),
        fact("ISSUED_CAPITAL", Q1, "100", nature="INSTANT", disclosure_id=q1_disclosure, published_at=datetime(2025, 5, 10, 9, tzinfo=UTC), lineage_char="f"),
        fact("REVENUE", Q2, q2_revenue, nature="YTD", period_start=YEAR_START, disclosure_id=q2_disclosure, published_at=q2_publication, lineage_char="1"),
        fact("REVENUE", Q1, "100", nature="YTD", period_start=YEAR_START, disclosure_id=q2_disclosure, published_at=q2_publication, lineage_char="2"),
        fact("COST_OF_SALES", Q2, "150", nature="YTD", period_start=YEAR_START, disclosure_id=q2_disclosure, published_at=q2_publication, lineage_char="3"),
        fact("COST_OF_SALES", Q1, "60", nature="YTD", period_start=YEAR_START, disclosure_id=q2_disclosure, published_at=q2_publication, lineage_char="4"),
        fact("NET_INCOME", Q2, "30", nature="YTD", period_start=YEAR_START, disclosure_id=q2_disclosure, published_at=q2_publication, lineage_char="5"),
        fact("NET_INCOME", Q1, "10", nature="YTD", period_start=YEAR_START, disclosure_id=q2_disclosure, published_at=q2_publication, lineage_char="6"),
        fact("TOTAL_ASSETS", Q2, "1100", nature="INSTANT", disclosure_id=q2_disclosure, published_at=q2_publication, lineage_char="7"),
        fact("TOTAL_EQUITY", Q2, "450", nature="INSTANT", disclosure_id=q2_disclosure, published_at=q2_publication, lineage_char="8"),
        fact("ISSUED_CAPITAL", Q2, "100", nature="INSTANT", disclosure_id=q2_disclosure, published_at=q2_publication, lineage_char="9"),
    ]


def test_ytd_is_converted_to_independent_quarters_without_compression():
    rows = derive_company_quarters(
        basic_facts(), config=cfg(), ticker="AAA", analysis_at=ANALYSIS, anchor_period_end=Q2,
    )
    assert [row.period_end for row in rows] == [Q1, Q2]
    q1, q2 = rows
    assert q1.values["revenue"] == 100.0
    assert q2.values["revenue"] == 150.0
    assert q2.values["cogs"] == 90.0
    assert q2.values["gross_profit"] == 60.0
    assert q2.values["net_income"] == 20.0
    assert q2.values["shares_out"] == 100.0
    assert q2.diagnostics["field_sources"]["revenue"] == "YTD_DIFFERENCE"


def test_missing_prior_ytd_does_not_turn_cumulative_value_into_quarter_value():
    rows = [row for row in basic_facts() if not (
        row.disclosure_id == "Q2" and row.period_end == Q1 and row.canonical_field == "REVENUE"
    )]
    # Remove the older Q1 revenue too, so no prior YTD is available at all.
    rows = [row for row in rows if not (row.period_end == Q1 and row.canonical_field == "REVENUE")]
    derived = derive_company_quarters(
        rows, config=cfg(), ticker="AAA", analysis_at=ANALYSIS, anchor_period_end=Q2,
    )
    q2 = next(row for row in derived if row.period_end == Q2)
    assert q2.values["revenue"] is None
    assert q2.diagnostics["field_reasons"]["revenue"] == "YTD_PRIOR_QUARTER_MISSING"


def test_point_in_time_restatement_changes_quarter_only_after_publication():
    original = basic_facts()
    restated_at = datetime(2025, 11, 20, 9, tzinfo=UTC)
    restated = [
        fact("REVENUE", Q2, "280", nature="YTD", period_start=YEAR_START, disclosure_id="Q2R", published_at=restated_at, version_sequence=2, lineage_char="b"),
        fact("REVENUE", Q1, "100", nature="YTD", period_start=YEAR_START, disclosure_id="Q2R", published_at=restated_at, version_sequence=2, lineage_char="c"),
    ]
    before = derive_company_quarters(
        original + restated, config=cfg(), ticker="AAA",
        analysis_at=datetime(2025, 10, 1, tzinfo=UTC), anchor_period_end=Q2,
    )
    after = derive_company_quarters(
        original + restated, config=cfg(), ticker="AAA", analysis_at=ANALYSIS, anchor_period_end=Q2,
    )
    assert next(row for row in before if row.period_end == Q2).values["revenue"] == 150.0
    assert next(row for row in after if row.period_end == Q2).values["revenue"] == 180.0


def test_input_order_does_not_change_lineage_or_values():
    rows = basic_facts()
    left = derive_company_quarters(rows, config=cfg(), ticker="AAA", analysis_at=ANALYSIS, anchor_period_end=Q2)
    right = derive_company_quarters(list(reversed(rows)), config=cfg(), ticker="AAA", analysis_at=ANALYSIS, anchor_period_end=Q2)
    assert left == right


def test_invalid_direct_share_count_is_not_hidden_by_capital_fallback():
    rows = basic_facts()
    rows.append(fact("SHARES_OUT", Q2, "0", nature="INSTANT", disclosure_id="Q2", lineage_char="0"))
    derived = derive_company_quarters(rows, config=cfg(), ticker="AAA", analysis_at=ANALYSIS, anchor_period_end=Q2)
    q2 = next(row for row in derived if row.period_end == Q2)
    assert q2.values["shares_out"] is None
    assert q2.diagnostics["field_reasons"]["shares_out"] == "DIRECT_SHARES_NON_POSITIVE"


def test_future_fact_is_ignored_and_wrong_sector_is_fail_closed():
    future = replace(basic_facts()[0], published_at=datetime(2027, 1, 1, tzinfo=UTC))
    assert derive_company_quarters(
        [future], config=cfg(), ticker="AAA", analysis_at=ANALYSIS, anchor_period_end=Q2
    ) == ()
    wrong = replace(basic_facts()[0], sector_family="BANK")
    with pytest.raises(CompanyDerivationError, match="sector_family"):
        derive_company_quarters([wrong], config=cfg(), ticker="AAA", analysis_at=ANALYSIS, anchor_period_end=Q2)


@pytest.mark.parametrize(
    "patch",
    [
        {"sector_families": ["BANK"]},
        {"history_periods": 2},
        {"minimum_present_count": True},
        {"field_map": {"unknown": "X"}},
        {"shares_out_field": None, "issued_capital_field": None},
        {"share_nominal_value": 0},
        {"derive_gross_profit": 1},
        {"unexpected": 1},
    ],
)
def test_config_rejects_unsafe_contracts(patch):
    payload = {
        "derivation_profile": "NONBANK_TEST",
        "derivation_version": 1,
        "semantic_profile": "SEM_TEST",
        "semantic_version": 1,
        "sector_families": ["NONFIN"],
        "target_periods": 2,
        "history_periods": 3,
        "field_map": {"revenue": "REVENUE", "total_assets": "TOTAL_ASSETS"},
        "shares_out_field": "SHARES_OUT",
        "issued_capital_field": "ISSUED_CAPITAL",
        "share_nominal_value": 1,
        "required_fields": ["revenue"],
        "minimum_present_fields": ["revenue", "total_assets"],
        "minimum_present_count": 1,
        "derive_gross_profit": True,
    }
    payload.update(patch)
    with pytest.raises(Exception):
        CompanyDerivationConfig.from_dict(payload)


class Cursor:
    def __init__(self, rows=(), names=()):
        self.rows = list(rows)
        self.description = [(name,) for name in names]
        self.sql = None
        self.params = None
        self.executed = []

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Conn:
    def __init__(self, cursor):
        self.cur = cursor

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_batch_fetch_uses_one_query_and_nonbank_families():
    cur = Cursor([], [
        "source", "disclosure_id", "ticker", "published_at", "version_tag",
        "version_sequence", "sector_family", "semantic_profile", "semantic_version",
        "canonical_field", "nature", "period_start", "period_end", "currency",
        "statement_scope", "value", "source_fact_code", "source_fact_key",
        "source_mapping_profile", "source_mapping_version", "dimensions",
        "lineage_sha256", "mapped_at",
    ])
    grouped = fetch_company_semantic_facts_batch_asof(
        Conn(cur), config=cfg(), tickers=["aaa", "bbb"], analysis_at=ANALYSIS, anchor_period_end=Q2,
    )
    assert grouped == {"AAA": (), "BBB": ()}
    assert "sector_family = ANY" in cur.sql
    assert "published_at <=" in cur.sql
    assert cur.params[0] == ["AAA", "BBB"]
    assert "BANK" not in cur.params[1]


def test_active_ticker_query_uses_explicit_sector_code_not_broad_index():
    cur = Cursor([("AAA",), ("BBB",)], ["ticker"])
    assert fetch_active_company_tickers(Conn(cur), cfg()) == ["AAA", "BBB"]
    assert "sector_code" in cur.sql
    assert "sector_index_code" not in cur.sql


def test_batch_rejection_isolated_and_persisted():
    class BatchConn(Conn):
        pass

    # Empty fetch result makes both tickers reject without uncontrolled failure.
    cur = Cursor([], [
        "source", "disclosure_id", "ticker", "published_at", "version_tag",
        "version_sequence", "sector_family", "semantic_profile", "semantic_version",
        "canonical_field", "nature", "period_start", "period_end", "currency",
        "statement_scope", "value", "source_fact_code", "source_fact_key",
        "source_mapping_profile", "source_mapping_version", "dimensions",
        "lineage_sha256", "mapped_at",
    ])
    report = materialize_company_metrics_batch(
        BatchConn(cur), config=cfg(), tickers=["AAA", "BBB"], analysis_at=ANALYSIS,
        anchor_period_end=Q2, persist=True,
    )
    assert report.tickers_seen == 2
    assert report.tickers_rejected == 2
    assert report.metrics_written == 0
    assert all(reason == "NO_DERIVABLE_METRICS" for reason in report.rejected.values())
    assert any("company_metric_derivation_rejections" in sql for sql, _ in cur.executed)




def test_future_wrong_sector_record_cannot_poison_historical_result():
    rows = basic_facts()
    future_wrong = replace(
        rows[0],
        published_at=datetime(2027, 1, 1, tzinfo=UTC),
        mapped_at=datetime(2027, 1, 1, tzinfo=UTC),
        sector_family="BANK",
        lineage_sha256="e" * 64,
    )
    clean = derive_company_quarters(
        rows, config=cfg(), ticker="AAA", analysis_at=ANALYSIS, anchor_period_end=Q2
    )
    with_future = derive_company_quarters(
        rows + [future_wrong], config=cfg(), ticker="AAA", analysis_at=ANALYSIS, anchor_period_end=Q2
    )
    assert with_future == clean

def test_mixed_sector_families_for_same_ticker_are_rejected():
    rows = basic_facts()
    rows.append(replace(rows[0], sector_family="GYO", lineage_sha256="d" * 64))
    with pytest.raises(CompanyDerivationError, match="tek sector_family"):
        derive_company_quarters(
            rows, config=cfg(), ticker="AAA", analysis_at=ANALYSIS, anchor_period_end=Q2
        )


def test_config_rejects_share_semantic_overlap_and_impossible_gross_profit():
    with pytest.raises(Exception, match="cakismamali"):
        cfg(field_map={
            "revenue": "SHARES_OUT",
            "cogs": "COST_OF_SALES",
            "net_income": "NET_INCOME",
            "total_assets": "TOTAL_ASSETS",
            "total_equity": "TOTAL_EQUITY",
        })
    with pytest.raises(Exception, match="gross_profit"):
        cfg(
            field_map={"revenue": "REVENUE", "total_assets": "TOTAL_ASSETS"},
            required_fields=["gross_profit"],
            minimum_present_fields=["gross_profit"],
            minimum_present_count=1,
            derive_gross_profit=False,
        )

def test_migration_has_point_in_time_and_immutability_contracts():
    sql = Path("sql/021_company_semantic_materialization.sql").read_text().lower()
    for token in (
        "company_metrics_quarterly", "published_at timestamptz",
        "company_metric_derivation_rejections", "lineage_sha256",
        "prevent_company_metric_mutation", "company_metrics_latest",
    ):
        assert token in sql


def test_direct_dataclass_config_cannot_bypass_runtime_validation():
    unsafe = replace(cfg(), target_periods=0)
    with pytest.raises(CompanyDerivationError, match="config gecersiz"):
        derive_company_quarters(
            basic_facts(), config=unsafe, ticker="AAA",
            analysis_at=ANALYSIS, anchor_period_end=Q2,
        )


def test_non_config_object_is_rejected_before_fact_processing():
    with pytest.raises(CompanyDerivationError, match="CompanyDerivationConfig"):
        derive_company_quarters(
            basic_facts(), config={}, ticker="AAA",  # type: ignore[arg-type]
            analysis_at=ANALYSIS, anchor_period_end=Q2,
        )
