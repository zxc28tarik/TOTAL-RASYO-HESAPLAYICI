from __future__ import annotations

"""The no-M2 ledger must state its assumptions and never flatter itself."""

import json
from pathlib import Path

from scripts.audit_w8n_no_m2_ledger import CONTRIBUTION_MULTIPLE, check


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/audit/w8n_no_m2_ledger_v1"


def _receipt() -> dict:
    return json.loads((OUTPUT / "receipt.json").read_text(encoding="utf-8"))


def _ledger() -> list[dict]:
    return json.loads((OUTPUT / "ledger.json").read_text(encoding="utf-8"))["months"]


def test_ledger_declares_itself_a_diagnostic_and_names_the_excluded_module():
    receipt = _receipt()
    assert receipt["purpose"] == "DIAGNOSTIC_ONLY_FIVE_MODULE_VARIANT_NOT_THE_PRODUCTION_MODEL"
    assert receipt["excluded_module"] == "M2"
    assert receipt["production_portfolio_engine"] == "MonthlyTotalRasyoSimulator"


def test_every_cost_assumption_is_on_the_record():
    assumptions = _receipt()["assumptions"]
    # Gross returns must be labelled gross; a reader must not have to guess.
    assert assumptions["transaction_cost_and_slippage"] == "ZERO_OUT_OF_SCOPE_RETURNS_ARE_GROSS"
    assert assumptions["tax"] == "ZERO_OUT_OF_SCOPE"
    assert assumptions["cash_interest"] == "ZERO"
    assert assumptions["price_basis"] == "RAW_OPEN_AND_CLOSE_NOT_ADJUSTED_CLOSE"
    # The corporate-action source is vendor-derived and must say so.
    assert "NOT_KAP_VERIFIED" in assumptions["corporate_action_source"]
    assert assumptions["contribution"].startswith(f"{CONTRIBUTION_MULTIPLE} x CSGB")


def test_cash_only_months_are_counted_and_never_called_a_success():
    receipt = _receipt()
    assert receipt["cash_only_months_are_not_performance_success"] is True
    assert receipt["cash_only_month_count"] == len(receipt["cash_only_months"])
    ledger = _ledger()
    zero = [row["month"] for row in ledger if row["position_count"] == 0]
    assert zero == receipt["cash_only_months"]


def test_corporate_actions_that_touched_a_holding_are_enumerated_not_assumed_away():
    receipt = _receipt()
    rows = receipt["corporate_action_rows_touching_held_tickers"]
    assert receipt["corporate_actions_touching_held_tickers"] == len(rows)
    # This ledger really is exposed to corporate actions, so the exposure must
    # be listed rather than waved through.
    assert rows, "a ledger holding 44 tickers over five years cannot have zero actions"
    traded = set(receipt["tickers_traded"])
    for row in rows:
        assert row["ticker"] in traded


def test_span_and_month_count_cover_the_declared_backtest_window():
    receipt = _receipt()
    assert receipt["month_count"] == 60
    assert receipt["signal_date_span"][0].startswith("2021-08")
    assert receipt["signal_date_span"][1].startswith("2026-07")
    assert len(_ledger()) == 60


def test_cash_never_goes_negative_and_nav_is_cash_plus_holdings():
    for row in _ledger():
        assert row["cash"] >= 0.0
        assert abs(row["nav"] - (row["cash"] + row["holdings_value"])) < 1e-6
        assert row["position_count"] == len(row["holdings"])
        assert row["position_count"] <= 6


def test_benchmark_and_cash_conservation_were_independently_reconciled():
    receipt = _receipt()
    assert receipt["cash_conservation_independently_reconciled"] is True
    assert receipt["benchmark_independently_reconciled"] is True
    # The benchmark source is explicitly non-authoritative vendor data.
    assert receipt["benchmark_source"]["authoritative"] is False


def test_contributions_accumulate_monotonically_to_the_reported_total():
    ledger = _ledger()
    total = _receipt()["economics"]["total_contributions"]
    running = 0.0
    for row in ledger:
        running += row["contribution"]
        assert abs(row["cumulative_contribution"] - running) < 1e-6
    assert abs(ledger[-1]["cumulative_contribution"] - total) < 1e-6


def test_published_ledger_reproduces():
    assert check()["status"] == "W8N_LEDGER_CHECK_PASS"
