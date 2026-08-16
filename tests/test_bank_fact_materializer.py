from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.ingest.api.mkk_kap import KapApiConfigError
from src.ingest.api.semantic_facts import SemanticFinancialFact
from src.ingest.bank_fact_materializer import (
    BankDerivationConfig,
    BankDerivationError,
    build_quarter_ends,
    derive_bank_metrics,
    persist_bank_derived_metrics,
)


CONFIG = BankDerivationConfig.from_dict({
    "derivation_profile": "BANK_METRICS_TEST",
    "derivation_version": 1,
    "semantic_profile": "BANK_CORE_TEST",
    "semantic_version": 1,
    "total_equity_field": "TOTAL_EQUITY",
    "shares_out_field": "SHARES_OUT",
    "net_income_field": "NET_INCOME",
    "payout_ratio_field": "PAYOUT_RATIO",
    "dividends_paid_field": "DIVIDENDS_PAID",
    "currency": "TRY",
    "target_periods": 8,
    "history_periods": 12,
})

ANCHOR = date(2026, 3, 31)
ANALYSIS = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)


def sf(
    field: str,
    period: date,
    value,
    *,
    nature: str,
    period_start: date | None = None,
    published_at: datetime | None = None,
    version_sequence: int = 1,
    lineage: str | None = None,
    currency: str = "TRY",
    ticker: str = "GARAN",
):
    pub = published_at or datetime.combine(period + timedelta(days=45), time(9), tzinfo=timezone.utc)
    raw = lineage or f"{field}-{period}-{value}-{pub.isoformat()}"
    digest = __import__("hashlib").sha256(raw.encode()).hexdigest()
    return SemanticFinancialFact(
        source="MKK_KAP_API",
        disclosure_id="D-" + digest[:12],
        ticker=ticker,
        published_at=pub,
        version_tag="RESTATED" if version_sequence > 1 else "ORIGINAL",
        version_sequence=version_sequence,
        sector_family="BANK",
        semantic_profile="BANK_CORE_TEST",
        semantic_version=1,
        canonical_field=field,
        nature=nature,
        period_start=period_start,
        period_end=period,
        currency=currency,
        statement_scope="CONSOLIDATED",
        value=Decimal(str(value)),
        source_fact_code=field,
        source_fact_key=digest,
        source_mapping_profile="PORTAL",
        source_mapping_version=1,
        dimensions={},
        lineage_sha256=digest,
        mapped_at=pub + timedelta(minutes=1),
    )


def calendar_year_start(period):
    return date(period.year, 1, 1)


def complete_facts(*, direct_payout=True, dividends=False):
    slots = build_quarter_ends(ANCHOR, 12)
    rows = []
    cumulative_by_year = {}
    div_cumulative_by_year = {}
    for idx, period in enumerate(slots):
        equity = Decimal("1000") + Decimal(idx * 100)
        rows.append(sf("TOTAL_EQUITY", period, equity, nature="INSTANT"))
        rows.append(sf("SHARES_OUT", period, 100, nature="INSTANT"))
        quarter_no = (period.month - 1) // 3 + 1
        quarter_profit = Decimal(quarter_no * 10)
        cumulative_by_year[period.year] = cumulative_by_year.get(period.year, Decimal("0")) + quarter_profit
        rows.append(sf(
            "NET_INCOME", period, cumulative_by_year[period.year], nature="YTD",
            period_start=calendar_year_start(period),
        ))
        if dividends:
            q_div = Decimal(quarter_no * 2)
            div_cumulative_by_year[period.year] = div_cumulative_by_year.get(period.year, Decimal("0")) + q_div
            rows.append(sf(
                "DIVIDENDS_PAID", period, div_cumulative_by_year[period.year], nature="YTD",
                period_start=calendar_year_start(period),
            ))
    if direct_payout:
        rows.append(sf("PAYOUT_RATIO", slots[-1], "0.25", nature="RATIO"))
    return rows


def metric_for(metrics, period=ANCHOR):
    return next(row for row in metrics if row.period_end == period)


def test_derives_bvps_and_roe_ttm_from_ytd_without_compressing_quarters():
    metrics = derive_bank_metrics(
        complete_facts(), config=CONFIG, ticker="GARAN",
        analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
    )
    assert len(metrics) == 8
    row = metric_for(metrics)
    # Current equity 2100, lag4 equity 1700, TTM quarter profit = 20+30+40+10 = 100.
    assert row.bvps == pytest.approx(21.0)
    assert row.roe_ttm == pytest.approx(100 / 1900)
    assert row.payout_sus == pytest.approx(0.25)
    assert row.diagnostics["ttm_net_income"] == "100"
    assert row.diagnostics["average_equity"] == "1900"
    assert row.diagnostics["payout_source"] == "DIRECT_RATIO"


def test_missing_previous_ytd_makes_ttm_unavailable_instead_of_compressing_time():
    rows = complete_facts()
    q3 = date(2025, 9, 30)
    rows = [r for r in rows if not (r.canonical_field == "NET_INCOME" and r.period_end == q3)]
    metrics = derive_bank_metrics(
        rows, config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    )
    row = metric_for(metrics)
    assert row.roe_ttm is None
    assert row.diagnostics["roe_reason"] in {"MISSING", "MISSING_PREVIOUS_YTD"}
    assert row.bvps == pytest.approx(21.0)


def test_latest_point_in_time_restatement_is_selected_but_future_restatement_is_not():
    rows = complete_facts()
    period = ANCHOR
    original = next(r for r in rows if r.canonical_field == "TOTAL_EQUITY" and r.period_end == period)
    later = sf(
        "TOTAL_EQUITY", period, 3000, nature="INSTANT",
        published_at=datetime(2026, 5, 15, 10, tzinfo=timezone.utc), version_sequence=2,
    )
    future = sf(
        "TOTAL_EQUITY", period, 9999, nature="INSTANT",
        published_at=datetime(2026, 5, 16, 10, tzinfo=timezone.utc), version_sequence=3,
    )
    rows.extend([later, future])
    row = metric_for(derive_bank_metrics(
        rows, config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert row.bvps == pytest.approx(30.0)
    assert all(item["value"] != "9999" for item in row.source_lineage)
    before = metric_for(derive_bank_metrics(
        rows, config=CONFIG, ticker="GARAN",
        analysis_at=datetime(2026, 5, 15, 9, 30, tzinfo=timezone.utc), anchor_period_end=ANCHOR
    ))
    assert before.bvps == pytest.approx(float(original.value / Decimal("100")))


def test_same_timestamp_tie_break_uses_version_then_full_lineage_hash():
    rows = complete_facts()
    period = ANCHOR
    rows = [r for r in rows if not (r.canonical_field == "TOTAL_EQUITY" and r.period_end == period)]
    pub = datetime(2026, 5, 14, 10, tzinfo=timezone.utc)
    rows.extend([
        sf("TOTAL_EQUITY", period, 2000, nature="INSTANT", published_at=pub, version_sequence=1, lineage="a"),
        sf("TOTAL_EQUITY", period, 3000, nature="INSTANT", published_at=pub, version_sequence=2, lineage="b"),
    ])
    row = metric_for(derive_bank_metrics(
        rows, config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert row.bvps == pytest.approx(30.0)


def test_input_order_does_not_change_metric_or_lineage():
    rows = complete_facts()
    first = metric_for(derive_bank_metrics(
        rows, config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    random.Random(42).shuffle(rows)
    second = metric_for(derive_bank_metrics(
        rows, config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert first == second


def test_dividend_fallback_derives_payout_when_direct_ratio_missing():
    row = metric_for(derive_bank_metrics(
        complete_facts(direct_payout=False, dividends=True),
        config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
    ))
    # Last four dividends 4+6+8+2 = 20; income = 100.
    assert row.payout_sus == pytest.approx(0.20)
    assert row.diagnostics["payout_source"] == "TTM_DIVIDENDS"


def test_out_of_range_direct_and_derived_payout_are_not_silently_clamped():
    rows = complete_facts(direct_payout=False, dividends=False)
    rows.append(sf("PAYOUT_RATIO", ANCHOR, "1.2", nature="RATIO"))
    row = metric_for(derive_bank_metrics(
        rows, config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert row.payout_sus is None
    assert row.diagnostics["payout_reason"] == "DIRECT_PAYOUT_OUT_OF_RANGE"


def test_direct_ttm_net_income_is_supported():
    rows = complete_facts()
    rows = [r for r in rows if not (r.canonical_field == "NET_INCOME" and r.period_end == ANCHOR)]
    rows.append(sf("NET_INCOME", ANCHOR, 120, nature="TTM"))
    row = metric_for(derive_bank_metrics(
        rows, config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert row.roe_ttm == pytest.approx(120 / 1900)


def test_nonpositive_equity_or_shares_fail_closed_per_metric():
    rows = complete_facts()
    rows = [r for r in rows if not (r.canonical_field == "SHARES_OUT" and r.period_end == ANCHOR)]
    rows.append(sf("SHARES_OUT", ANCHOR, 0, nature="INSTANT"))
    row = metric_for(derive_bank_metrics(
        rows, config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert row.bvps is None
    assert row.diagnostics["bvps_reason"] == "NONPOSITIVE_SHARES"


def test_foreign_currency_wrong_ticker_and_wrong_profile_are_ignored():
    rows = complete_facts()
    rows.extend([
        sf("TOTAL_EQUITY", ANCHOR, 9999, nature="INSTANT", currency="USD"),
        sf("TOTAL_EQUITY", ANCHOR, 8888, nature="INSTANT", ticker="AKBNK"),
    ])
    row = metric_for(derive_bank_metrics(
        rows, config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert row.bvps == pytest.approx(21.0)


def test_lineage_and_source_id_are_deterministic_and_trace_all_components():
    row = metric_for(derive_bank_metrics(
        complete_facts(), config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert len(row.lineage_sha256) == 64
    assert row.source_disclosure_id == "SEMANTIC:" + row.lineage_sha256
    assert row.version_tag.startswith("DERIVED_")
    fields = {item["canonical_field"] for item in row.source_lineage}
    assert {"TOTAL_EQUITY", "SHARES_OUT", "NET_INCOME", "PAYOUT_RATIO"} <= fields
    assert row.published_at == max(datetime.fromisoformat(item["published_at"]) for item in row.source_lineage)


@pytest.mark.parametrize(
    "change, message",
    [
        ({"derivation_version": True}, "derivation_version"),
        ({"semantic_version": 0}, "semantic_version"),
        ({"history_periods": 11}, r"target_periods \+ 4"),
        ({"target_periods": 0}, "target_periods"),
        ({"roe_formula": "OTHER"}, "roe_formula"),
        ({"payout_policy": "CLAMP"}, "payout_policy"),
        ({"shares_out_field": "EQ"}, "birbirinden farkli"),
    ],
)
def test_derivation_config_is_strict(change, message):
    raw = {
        "derivation_profile": "P", "derivation_version": 1,
        "semantic_profile": "S", "semantic_version": 1,
        "total_equity_field": "EQ", "shares_out_field": "SH",
        "net_income_field": "NI", "target_periods": 8, "history_periods": 12,
    }
    raw.update(change)
    with pytest.raises(KapApiConfigError, match=message):
        BankDerivationConfig.from_dict(raw)


def test_analysis_at_and_persist_contract_are_strict():
    with pytest.raises(BankDerivationError, match="timezone"):
        derive_bank_metrics(
            complete_facts(), config=CONFIG, ticker="GARAN",
            analysis_at=datetime(2026, 5, 15), anchor_period_end=ANCHOR,
        )


class Cursor:
    def __init__(self): self.executed = []
    def execute(self, sql, params=None): self.executed.append((" ".join(sql.split()), params))
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Conn:
    def __init__(self): self.cur = Cursor()
    def cursor(self): return self.cur
    def __enter__(self): return self
    def __exit__(self, *_): return False


def test_derived_metric_persistence_is_idempotent_and_does_not_overwrite_values():
    row = metric_for(derive_bank_metrics(
        complete_facts(), config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    conn = Conn()
    assert persist_bank_derived_metrics(conn, [row]) == 1
    sql, params = conn.cur.executed[0]
    assert "INSERT INTO core.bank_metrics_quarterly" in sql
    assert "ON CONFLICT (source_disclosure_id)" in sql
    assert "roe_ttm = EXCLUDED" not in sql
    assert params[0] == "GARAN"
    assert params[9] == row.lineage_sha256


def test_migration_and_example_config_capture_formula_and_lineage_contract():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "sql" / "016_semantic_sector_materialization.sql").read_text().lower()
    assert "add column if not exists source_lineage" in sql
    assert "lineage_sha256 desc nulls last" in sql
    assert "reject_derived_bank_metric_mutation" in sql
    cfg = BankDerivationConfig.from_json_file(str(root / "config" / "bank_fact_derivation.example.json"))
    assert cfg.roe_formula == "TTM_NET_INCOME_OVER_ENDPOINT_AVERAGE_EQUITY"
    assert cfg.history_periods == cfg.target_periods + 4


def test_invalid_direct_payout_is_not_hidden_by_valid_dividend_fallback():
    rows = complete_facts(direct_payout=False, dividends=True)
    rows.append(sf("PAYOUT_RATIO", ANCHOR, "25", nature="RATIO"))
    row = metric_for(derive_bank_metrics(
        rows, config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert row.payout_sus is None
    assert row.diagnostics["payout_source"] is None
    assert row.diagnostics["payout_reason"] == "DIRECT_PAYOUT_OUT_OF_RANGE"


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"version_sequence": True}, "version_sequence"),
        ({"nature": "BAD"}, "nature"),
        ({"lineage_sha256": "bad"}, "lineage_sha256"),
        ({"value": Decimal("NaN")}, "value"),
        ({"published_at": datetime(2026, 3, 1)}, "published_at timezone"),
        ({"period_end": "2026-03-31"}, "period_end date"),
    ],
)
def test_malformed_semantic_fact_is_controlled_rejection(mutation, message):
    rows = complete_facts()
    original = rows[0]
    values = original.__dict__.copy()
    values.update(mutation)
    rows[0] = SemanticFinancialFact(**values)
    with pytest.raises(BankDerivationError, match=message):
        derive_bank_metrics(
            rows, config=CONFIG, ticker="GARAN", analysis_at=ANALYSIS,
            anchor_period_end=ANCHOR,
        )


def test_issued_capital_fallback_derives_shares_and_bvps_explicitly():
    cfg = BankDerivationConfig.from_dict({
        "derivation_profile": "BANK_ISSUED_CAPITAL_TEST",
        "derivation_version": 1,
        "semantic_profile": "BANK_CORE_TEST",
        "semantic_version": 1,
        "total_equity_field": "TOTAL_EQUITY",
        "shares_out_field": None,
        "issued_capital_field": "ISSUED_CAPITAL",
        "share_nominal_value": "1",
        "net_income_field": "NET_INCOME",
        "currency": "TRY",
        "target_periods": 8,
        "history_periods": 12,
    })
    rows = [r for r in complete_facts(direct_payout=False) if r.canonical_field != "SHARES_OUT"]
    for period in build_quarter_ends(ANCHOR, 12):
        rows.append(sf("ISSUED_CAPITAL", period, 100, nature="INSTANT"))
    row = metric_for(derive_bank_metrics(
        rows, config=cfg, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert row.bvps == pytest.approx(21.0)
    assert row.diagnostics["shares_source"] == "ISSUED_CAPITAL_DIV_NOMINAL"
    assert row.diagnostics["share_nominal_value"] == "1"
    assert "ISSUED_CAPITAL" in {item["canonical_field"] for item in row.source_lineage}


def test_direct_shares_take_priority_over_issued_capital_but_invalid_direct_is_not_hidden():
    cfg = BankDerivationConfig.from_dict({
        "derivation_profile": "BANK_SHARES_PRIORITY_TEST",
        "derivation_version": 1,
        "semantic_profile": "BANK_CORE_TEST",
        "semantic_version": 1,
        "total_equity_field": "TOTAL_EQUITY",
        "shares_out_field": "SHARES_OUT",
        "issued_capital_field": "ISSUED_CAPITAL",
        "share_nominal_value": "1",
        "net_income_field": "NET_INCOME",
        "currency": "TRY",
        "target_periods": 8,
        "history_periods": 12,
    })
    rows = complete_facts(direct_payout=False)
    for period in build_quarter_ends(ANCHOR, 12):
        rows.append(sf("ISSUED_CAPITAL", period, 200, nature="INSTANT"))
    direct = metric_for(derive_bank_metrics(
        rows, config=cfg, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert direct.bvps == pytest.approx(21.0)
    assert direct.diagnostics["shares_source"] == "DIRECT_SHARES"

    rows = [r for r in rows if not (r.canonical_field == "SHARES_OUT" and r.period_end == ANCHOR)]
    rows.append(sf("SHARES_OUT", ANCHOR, 0, nature="INSTANT"))
    invalid = metric_for(derive_bank_metrics(
        rows, config=cfg, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    ))
    assert invalid.bvps is None
    assert invalid.diagnostics["bvps_reason"] == "NONPOSITIVE_SHARES"
    assert invalid.diagnostics["shares_source"] is None


@pytest.mark.parametrize(
    "change, message",
    [
        ({"shares_out_field": None}, "shares_out_field veya issued_capital_field"),
        ({"shares_out_field": None, "issued_capital_field": "CAP"}, "share_nominal_value"),
        ({"share_nominal_value": "1"}, "yalniz issued_capital_field"),
        ({"shares_out_field": None, "issued_capital_field": "CAP", "share_nominal_value": 0}, "share_nominal_value"),
        ({"shares_out_field": None, "issued_capital_field": "CAP", "share_nominal_value": True}, "share_nominal_value"),
    ],
)
def test_issued_capital_fallback_config_is_fail_closed(change, message):
    raw = {
        "derivation_profile": "P", "derivation_version": 1,
        "semantic_profile": "S", "semantic_version": 1,
        "total_equity_field": "EQ", "shares_out_field": "SH",
        "net_income_field": "NI", "target_periods": 8, "history_periods": 12,
    }
    raw.update(change)
    with pytest.raises(KapApiConfigError, match=message):
        BankDerivationConfig.from_dict(raw)
