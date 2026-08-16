from __future__ import annotations

from datetime import date

from src.analytics.ratios_calc import QuarterSeries, safe_eval_expr


def row(period_end, revenue, version="ORIGINAL", t0=None):
    return {
        "period_end": period_end,
        "version_tag": version,
        "t0_date": t0,
        "report_date": t0,
        "revenue": revenue,
    }


def test_lag4q_uses_calendar_quarter_not_four_previous_rows():
    rows = [
        row(date(2024, 3, 31), 100),
        row(date(2024, 6, 30), 110),
        # 2024-Q3 deliberately missing
        row(date(2024, 12, 31), 130),
        row(date(2025, 3, 31), 140),
        row(date(2025, 6, 30), 150),
    ]
    qs = QuarterSeries(rows)
    assert qs.lag(date(2025, 6, 30), "revenue", 4) == 110
    assert safe_eval_expr("lag4q(revenue)", rows[-1], qs, date(2025, 6, 30)) == 110


def test_sum4q_requires_four_consecutive_calendar_quarters():
    rows = [
        row(date(2024, 6, 30), 10),
        row(date(2024, 9, 30), 20),
        # 2024-Q4 missing
        row(date(2025, 3, 31), 30),
        row(date(2025, 6, 30), 40),
    ]
    qs = QuarterSeries(rows)
    assert qs.sum4q(date(2025, 6, 30), "revenue") is None


def test_duplicate_period_is_selected_deterministically_not_by_input_order():
    original = row(date(2025, 3, 31), 100, "ORIGINAL", date(2025, 5, 1))
    restated = row(date(2025, 3, 31), 120, "RESTATED", date(2025, 7, 1))
    assert QuarterSeries([original, restated]).get(date(2025, 3, 31), "revenue") == 120
    assert QuarterSeries([restated, original]).get(date(2025, 3, 31), "revenue") == 120


def test_days_in_period_does_not_bridge_missing_quarter():
    qs = QuarterSeries([
        row(date(2024, 12, 31), 10),
        row(date(2025, 6, 30), 20),
    ])
    assert qs.days_in_period(date(2025, 6, 30)) == 91
