"""W3 — contract and mutation tests for the price-population reconciliation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_w3_price_populations import (
    ALIAS_RESOLUTION,
    AUDIT,
    CONTENT_FILES,
    EXECUTION_REASON_CODES,
    W3AuditError,
    build_intersections,
    build_lineage_admissibility,
    build_populations,
    classify_execution_cells,
    derive,
    sha_bytes,
    validate_execution_assignments,
)

RECEIPT = AUDIT / "receipt.json"

pytestmark = pytest.mark.skipif(
    not RECEIPT.exists(), reason="W3 audit artifacts not materialized"
)


@pytest.fixture(scope="module")
def receipt() -> dict:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stored() -> dict:
    return {
        name: json.loads((AUDIT / name).read_text(encoding="utf-8"))
        for name in CONTENT_FILES
        if name.endswith(".json")
    }


# --------------------------------------------------------------------------
# Population identity and the intersection contract
# --------------------------------------------------------------------------


def test_population_counts_match_the_execution_ledger(stored):
    counts = {
        name: value["count"] for name, value in stored["populations.json"]["populations"].items()
    }
    assert counts == {
        "PRICE_MISSING": 163,
        "EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING": 174,
        "STOCK_WINDOW_PRICE_MISSING": 402,
    }


def test_each_population_declares_its_own_key_and_producer(stored):
    populations = stored["populations.json"]["populations"]
    keys = {name: value["key"] for name, value in populations.items()}
    assert keys["PRICE_MISSING"] == "ticker + cutoff/month"
    assert keys["EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING"] == (
        "ticker + signal/execution date"
    )
    assert keys["STOCK_WINDOW_PRICE_MISSING"] == "ticker + analysis window"
    assert len({value["producer"] for value in populations.values()}) == 3


def test_the_three_populations_must_never_be_summed(stored):
    sizes = stored["intersections.json"]["sizes"]
    assert sizes["naive_sum"] == 739
    assert sizes["union"] == 402
    assert sizes["double_counted_by_naive_sum"] == 337


def test_containment_structure_is_published(stored):
    containment = stored["intersections.json"]["containment"]
    assert containment["PRICE_MISSING_subset_of_STOCK_WINDOW"] is True
    assert containment["EXECUTION_subset_of_STOCK_WINDOW"] is True
    assert containment["union_equals_STOCK_WINDOW"] is True
    # 163 is not a subset of 174: one cell sits in the valuation population only.
    assert containment["PRICE_MISSING_subset_of_EXECUTION"] is False


def test_the_single_price_missing_cell_outside_the_execution_set_is_named(stored):
    difference = stored["intersections.json"]["differences"]["PRICE_MISSING_not_EXECUTION"]
    assert difference == [["2025-11-03", "EFOR"]]


def test_stock_window_label_hides_three_module_populations(stored):
    window = stored["populations.json"]["populations"]["STOCK_WINDOW_PRICE_MISSING"]
    assert window["per_module_counts"] == {"Ek4": 180, "Ek9": 402, "M3": 189}
    assert all(item["subset_of_Ek9"] for item in window["per_module_containment"].values())


def test_module_union_mismatch_fails_closed():
    p3 = [
        {
            "signal_date": "2021-08-02",
            "ticker": "AAA",
            "module_reasons": {"Ek9": "STOCK_WINDOW_PRICE_MISSING"},
        }
    ]
    p4 = [{"signal_date": "2021-08-02", "ticker": "AAA", "reasons": []}]
    with pytest.raises(W3AuditError, match="STOCK_WINDOW_MODULE_UNION"):
        build_populations(p3, p4, {"missing_execution_cells": []})


# --------------------------------------------------------------------------
# 174/174 reason-code exhaustiveness
# --------------------------------------------------------------------------


def test_every_execution_cell_carries_exactly_one_closed_taxonomy_reason(stored):
    summary = stored["execution_reason_codes.json"]
    assert summary["assigned_cells"] == 174
    assert summary["exhaustive"] is True
    assert set(summary["counts"]) == set(EXECUTION_REASON_CODES)
    assert sum(summary["counts"].values()) == 174
    assert len(summary["cells"]) == 174
    assert len({(c["signal_date"], c["ticker"]) for c in summary["cells"]}) == 174


def test_execution_reason_distribution(stored):
    counts = stored["execution_reason_codes.json"]["counts"]
    assert counts["TICKER_LINEAGE"] == 162
    assert counts["SOURCE_SYMBOL_GAP"] == 12
    assert counts["UNRESOLVED_BLOCKED"] == 0
    assert counts["OTHER_EVIDENCED"] == 0


def test_the_twelve_source_gap_cells_are_the_known_p2_tickers(stored):
    per_ticker = stored["execution_reason_codes.json"]["per_ticker"]
    gaps = {t: v["SOURCE_SYMBOL_GAP"] for t, v in per_ticker.items() if "SOURCE_SYMBOL_GAP" in v}
    assert gaps == {"INVES": 3, "KLRHO": 6, "ASGYO": 3}


def test_every_ticker_lineage_cell_cites_hashed_official_evidence(stored):
    for cell in stored["execution_reason_codes.json"]["cells"]:
        if cell["reason_code"] != "TICKER_LINEAGE":
            continue
        evidence = cell["evidence"]
        assert len(evidence["source_workbook_sha256"]) == 64
        assert len(evidence["event_sha256"]) == 64
        assert evidence["alias_effective_date"] == evidence["lineage_effective_date"]
        assert evidence["price_row_present_under_alias"] is True


# --------------------------------------------------------------------------
# The exhaustiveness gate must reject, not merely count
# --------------------------------------------------------------------------


KEYS = [("2021-08-02", "AAA"), ("2021-09-01", "BBB")]


def assignment(signal_date, ticker, reason="SOURCE_SYMBOL_GAP", evidence=None) -> dict:
    return {
        "signal_date": signal_date,
        "ticker": ticker,
        "reason_code": reason,
        "evidence": evidence if evidence is not None else {"detail": "x"},
    }


def test_exhaustiveness_gate_accepts_a_complete_assignment():
    validate_execution_assignments(KEYS, [assignment(*key) for key in KEYS])


def test_a_dropped_cell_is_rejected():
    with pytest.raises(W3AuditError, match="NOT_EXHAUSTIVE"):
        validate_execution_assignments(KEYS, [assignment(*KEYS[0])])


def test_a_duplicated_cell_cannot_mask_a_dropped_one():
    # Same length as the input, so a count-only check would pass this.
    duplicated = [assignment(*KEYS[0]), assignment(*KEYS[0])]
    with pytest.raises(W3AuditError, match="ASSIGNED_TWICE"):
        validate_execution_assignments(KEYS, duplicated)


def test_a_reason_outside_the_closed_taxonomy_is_rejected():
    rows = [assignment(*KEYS[0], reason="PRICE_LOOKS_FINE"), assignment(*KEYS[1])]
    with pytest.raises(W3AuditError, match="OUTSIDE_TAXONOMY"):
        validate_execution_assignments(KEYS, rows)


def test_a_reason_without_evidence_is_rejected():
    rows = [assignment(*KEYS[0], evidence={}), assignment(*KEYS[1])]
    with pytest.raises(W3AuditError, match="WITHOUT_EVIDENCE"):
        validate_execution_assignments(KEYS, rows)


def test_derive_actually_calls_the_exhaustiveness_gate(monkeypatch):
    """A gate that derive() never calls is not a gate."""
    import scripts.audit_w3_price_populations as auditor

    original = auditor.classify_execution_cells

    def dropping(execution_keys, *args, **kwargs):
        assignments, summary = original(execution_keys, *args, **kwargs)
        return assignments[:-1], summary  # silently lose one classified cell

    monkeypatch.setattr(auditor, "classify_execution_cells", dropping)
    with pytest.raises(W3AuditError, match="NOT_EXHAUSTIVE"):
        auditor.derive()


def test_an_unexpected_extra_cell_is_rejected():
    rows = [assignment(*key) for key in KEYS] + [assignment("2021-10-01", "CCC")]
    with pytest.raises(W3AuditError, match="NOT_EXHAUSTIVE"):
        validate_execution_assignments(KEYS, rows)


# --------------------------------------------------------------------------
# Reason-code mutations: each wrong input must change the verdict
# --------------------------------------------------------------------------


CALENDAR = {"2021-08-02"}
LINEAGE_ROW = {
    "effective_date": "2025-11-24",
    "old_ticker": "AAA",
    "new_ticker": "BBB",
    "source_workbook_sha256": "a" * 64,
    "event_sha256": "b" * 64,
}


def price_row(**overrides) -> dict:
    row = {
        "ticker": "AAA",
        "yahoo_symbol": "BBB.IS",
        "trade_date": "2021-08-02",
        "price_source_ticker": "BBB",
        "price_resolution": ALIAS_RESOLUTION,
        "alias_effective_date": "2025-11-24",
    }
    row.update(overrides)
    return row


def classify_one(price_rows, lineage_rows=(LINEAGE_ROW,), calendar=None) -> str:
    assignments, _ = classify_execution_cells(
        [("2021-08-02", "AAA")], list(price_rows), list(lineage_rows),
        CALENDAR if calendar is None else calendar,
    )
    return assignments[0]["reason_code"]


def test_alias_row_with_official_lineage_is_ticker_lineage():
    assert classify_one([price_row()]) == "TICKER_LINEAGE"


def test_alias_row_without_an_official_lineage_row_never_becomes_ticker_lineage():
    # Mutation: strip the Borsa workbook row. A relabelled price alone is not proof.
    assert classify_one([price_row()], lineage_rows=()) == "UNRESOLVED_BLOCKED"


def test_exact_ticker_row_is_not_silently_reported_as_lineage():
    row = price_row(price_source_ticker="AAA", price_resolution="DIRECT_YAHOO")
    assert classify_one([row]) == "OTHER_EVIDENCED"


def test_signal_date_outside_the_calendar_is_a_calendar_gap():
    assert classify_one([], calendar=set()) == "CALENDAR_OR_SESSION_GAP"


def test_date_before_the_series_start_is_a_source_symbol_gap():
    rows = [price_row(trade_date="2024-03-11", price_source_ticker="AAA",
                      price_resolution="DIRECT_YAHOO")]
    assert classify_one(rows) == "SOURCE_SYMBOL_GAP"


def test_missing_series_entirely_is_a_source_symbol_gap():
    assert classify_one([]) == "SOURCE_SYMBOL_GAP"


def test_covered_range_without_a_row_on_the_date_is_a_no_trade_gap():
    rows = [
        price_row(trade_date="2021-07-30", price_source_ticker="AAA",
                  price_resolution="DIRECT_YAHOO"),
        price_row(trade_date="2021-08-03", price_source_ticker="AAA",
                  price_resolution="DIRECT_YAHOO"),
    ]
    assert classify_one(rows) == "NO_TRADING_SESSION_OR_NO_TRADE"


# --------------------------------------------------------------------------
# Lineage admissibility: a ticker change is not permission to carry a price
# --------------------------------------------------------------------------


def test_no_alias_is_admissible_and_all_162_cells_stay_blocked(stored):
    admissibility = stored["lineage_admissibility.json"]
    assert admissibility["admissible_alias_count"] == 0
    assert admissibility["blocked_execution_cells"] == 162
    for alias in admissibility["aliases"]:
        assert alias["status"] == "BLOCKED"
        assert alias["admissible_as_exact_execution_price"] is False
        assert "corporate_action_continuity" in alias["conditions_unverified"]
        assert alias["reopen_condition"]


def test_kervt_is_a_separate_event_and_is_not_folded_into_the_koza_cluster(stored):
    aliases = {a["old_ticker"]: a for a in stored["lineage_admissibility.json"]["aliases"]}
    assert aliases["KERVT"]["new_ticker"] == "BESLR"
    assert aliases["KERVT"]["effective_date"] == "2025-06-02"
    koza = {aliases[t]["effective_date"] for t in ("KOZAA", "KOZAL", "IPEKE")}
    assert koza == {"2025-11-24"}
    assert aliases["KERVT"]["effective_date"] not in koza
    assert aliases["EFORC"]["effective_date"] == "2025-11-03"


def test_kervt_share_class_identity_is_the_one_unproven_identity(stored):
    aliases = {a["old_ticker"]: a for a in stored["lineage_admissibility.json"]["aliases"]}
    unproven = {
        old
        for old, alias in aliases.items()
        if not alias["conditions"]["same_company_or_share_class_identity"]["verified"]
    }
    assert unproven == {"KERVT"}


def test_empty_action_inventory_is_never_read_as_proven_continuity():
    # Mutation: hand the builder a fully empty action inventory. Absence of
    # rows must stay "unproven", never "no action occurred".
    assignments = [{"signal_date": "2021-08-02", "ticker": "AAA", "reason_code": "TICKER_LINEAGE"}]
    result = build_lineage_admissibility(
        assignments, [LINEAGE_ROW], [], [{"ticker": "BBB"}]
    )
    alias = result["aliases"][0]
    assert alias["conditions"]["corporate_action_continuity"]["verified"] is False
    assert alias["conditions"]["corporate_action_continuity"]["blocker"] == (
        "ACTION_CONTINUITY_UNPROVEN"
    )
    assert result["admissible_alias_count"] == 0


def test_identity_evidence_requires_successor_share_class_observations():
    assignments = [{"signal_date": "2021-08-02", "ticker": "AAA", "reason_code": "TICKER_LINEAGE"}]
    without = build_lineage_admissibility(assignments, [LINEAGE_ROW], [], [])
    condition = without["aliases"][0]["conditions"]["same_company_or_share_class_identity"]
    assert condition["verified"] is False


# --------------------------------------------------------------------------
# Determinism, receipt binding and the unchanged-production guarantee
# --------------------------------------------------------------------------


def test_stored_artifacts_match_the_receipt(receipt):
    for name in CONTENT_FILES:
        assert sha_bytes((AUDIT / name).read_bytes()) == receipt["output_sha256"][name]


def test_second_derivation_is_byte_identical(receipt):
    content = derive()
    for name in CONTENT_FILES:
        assert sha_bytes(content[name]) == receipt["output_sha256"][name]


def test_receipt_declares_no_model_or_universe_change(receipt):
    assert receipt["policy"] == {
        "model_changed": False,
        "weights_changed": False,
        "veto_changed": False,
        "peer_or_coverage_threshold_changed": False,
        "universe_changed": False,
        "neutral_fill": False,
        "production_code_changed": False,
        "cells_repaired": 0,
        "new_market_capture": False,
    }


def test_receipt_binds_every_source_by_hash(receipt):
    assert set(receipt["source_sha256"]) == set(receipt["source_paths"])
    assert all(len(value) == 64 for value in receipt["source_sha256"].values())
    for relative in receipt["source_paths"].values():
        assert (Path(__file__).resolve().parents[1] / relative).exists()


def test_rows_cover_the_union_and_flag_membership(receipt):
    rows = [
        json.loads(line)
        for line in (AUDIT / "rows.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 402
    assert sum(row["in_price_missing"] for row in rows) == 163
    assert sum(row["in_execution_missing"] for row in rows) == 174
    assert sum(row["in_stock_window_missing"] for row in rows) == 402
    assert all(
        (row["execution_reason_code"] is not None) == row["in_execution_missing"] for row in rows
    )


def test_no_windows_path_separator_leaks_into_the_artifacts():
    """A str(Path) on Windows would embed a backslash and change every hash."""
    for name in CONTENT_FILES:
        assert "\\" not in (AUDIT / name).read_text(encoding="utf-8"), name


def test_receipt_source_paths_are_posix(receipt):
    assert all("\\" not in value for value in receipt["source_paths"].values())
