from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src.analytics.company_ratio_pipeline import (
    CompanyRatioPipelineError,
    compute_company_core_ratios_from_frame,
    fetch_company_financials_asof,
)

UTC = timezone.utc


def frame_with_gap():
    base = {
        "ticker": "AAA",
        "version_tag": "DERIVED",
        "report_date": date(2025, 1, 1),
        "t0_date": date(2025, 1, 1),
        "unit_scale": 1,
        "revenue": None,
        "cogs": None,
        "gross_profit": None,
        "ebit": None,
        "net_income": None,
        "interest_exp": None,
        "total_assets": 1000.0,
        "total_equity": 400.0,
        "current_assets": 300.0,
        "current_liabilities": 150.0,
        "cash_and_eq": 50.0,
        "st_investments": 0.0,
        "receivables": 80.0,
        "inventory": 60.0,
        "debt_st": 100.0,
        "debt_lt": 200.0,
        "cfo": None,
        "capex": None,
        "shares_out": 100.0,
        "shares_diluted": None,
    }
    rows = []
    for pe, revenue in [
        (date(2024, 3, 31), 90.0),
        # 2024-Q2 deliberately missing. A row-index lag would incorrectly
        # use 2024-Q1 as the four-row-back comparison for 2025-Q2.
        (date(2024, 9, 30), 110.0),
        (date(2024, 12, 31), 120.0),
        (date(2025, 3, 31), 130.0),
        (date(2025, 6, 30), 150.0),
    ]:
        row = dict(base)
        row.update({
            "period_end": pe,
            "revenue": revenue,
            "cogs": revenue * 0.6,
            "gross_profit": revenue * 0.4,
            "ebit": revenue * 0.15,
            "net_income": revenue * 0.10,
            "cfo": revenue * 0.12,
            "capex": revenue * 0.03,
        })
        rows.append(row)
    return pd.DataFrame(rows)


def test_company_core_ratios_exclude_valuation_and_preserve_missing_quarter():
    result = compute_company_core_ratios_from_frame(
        frame_with_gap(), ratios_json_path="config/ratios.json"
    )
    assert not result.empty
    assert not {"PE_TTM", "PB", "EV_EBIT_TTM"} & set(result["ratio_name"])
    latest = result[result["period_end"] == date(2025, 6, 30)].set_index("ratio_name")
    # Four historical rows exist, but the actual 2024-Q2 comparison slot is
    # missing; row-index compression must not substitute 2024-Q1.
    assert bool(latest.loc["REVENUE_YOY_GROWTH", "is_na"]) is True


def test_company_core_ratio_formula_still_computes_available_current_ratio():
    result = compute_company_core_ratios_from_frame(
        frame_with_gap(), ratios_json_path="config/ratios.json"
    )
    latest = result[result["period_end"] == date(2025, 6, 30)].set_index("ratio_name")
    assert latest.loc["CURRENT_RATIO", "ratio_value"] == pytest.approx(2.0)
    assert bool(latest.loc["CURRENT_RATIO", "is_na"]) is False


def test_frame_contract_rejects_missing_identity_columns():
    with pytest.raises(CompanyRatioPipelineError, match="eksik alanlar"):
        compute_company_core_ratios_from_frame(
            pd.DataFrame([{"ticker": "AAA"}]), ratios_json_path="config/ratios.json"
        )


def test_fetch_query_is_point_in_time_and_deterministic(monkeypatch):
    captured = {}

    def fake_read_sql(sql, conn, params):
        captured["sql"] = sql
        captured["params"] = params
        return pd.DataFrame()

    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    result = fetch_company_financials_asof(
        object(), analysis_at=datetime(2026, 1, 1, tzinfo=UTC), tickers=["aaa"]
    )
    assert result.empty
    assert "published_at <=" in captured["sql"]
    assert "version_sequence DESC" in captured["sql"]
    assert "lineage_sha256 DESC" in captured["sql"]
    assert captured["params"]["tickers"] == ["AAA"]


def test_fetch_rejects_naive_analysis_time_before_database(monkeypatch):
    monkeypatch.setattr(pd, "read_sql", lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB touched")))
    with pytest.raises(CompanyRatioPipelineError, match="timezone"):
        fetch_company_financials_asof(object(), analysis_at=datetime(2026, 1, 1))
