"""W4 — contract and mutation tests for the Ek4 price/return audit."""
from __future__ import annotations

import json

import pytest

from scripts.audit_w4_ek4_contract import (
    AUDIT,
    BAND_ANOMALOUS,
    BAND_MATERIAL,
    BAND_NEUTRAL,
    CONTENT_FILES,
    MATERIALITY,
    W4AuditError,
    build_contract_compliance,
    build_price_basis_comparison,
    build_price_basis_rows,
    derive,
    sha_bytes,
)

RECEIPT = AUDIT / "receipt.json"

pytestmark = pytest.mark.skipif(
    not RECEIPT.exists(), reason="W4 audit artifacts not materialized"
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
# Locked contract
# --------------------------------------------------------------------------


def test_the_locked_ek4_contract_is_not_violated(stored):
    compliance = stored["contract_compliance.json"]
    assert compliance["verdict"] == "COMPLIANT"
    assert compliance["violations"] == []
    assert compliance["contract_violation_proven"] is False


def test_every_contract_check_is_evidence_backed(stored):
    for name, check in stored["contract_compliance.json"]["checks"].items():
        assert check["verified"] is True, name
        assert check["evidence"]


def test_live_and_historical_use_the_same_stock_and_sector_bases(stored):
    checks = stored["contract_compliance.json"]["checks"]
    assert checks["stock_leg_identical_in_both_paths"]["verified"] is True
    assert checks["sector_leg_is_raw_routed_index_close"]["verified"] is True
    assert checks["live_and_historical_share_one_formula"]["verified"] is True


def test_a_missing_shared_formula_would_be_reported_as_a_violation(monkeypatch, tmp_path):
    import scripts.audit_w4_ek4_contract as auditor

    stub = tmp_path / "daily.py"
    stub.write_text("# no ek4 import, no COALESCE\n", encoding="utf-8")
    monkeypatch.setattr(auditor, "DAILY_PIPELINE", stub)
    compliance = auditor.build_contract_compliance()
    assert compliance["verdict"] == "VIOLATION_PROVEN"
    assert "live_and_historical_share_one_formula" in compliance["violations"]
    assert compliance["contract_violation_proven"] is True


def test_compliance_is_read_from_the_real_modules():
    # The verdict must come from the checked-in code, not a cached constant.
    assert build_contract_compliance()["verdict"] == "COMPLIANT"


# --------------------------------------------------------------------------
# Measured price-basis effect
# --------------------------------------------------------------------------


def test_the_comparison_covers_every_recorded_ek4_cell(stored):
    comparison = stored["price_basis_comparison.json"]
    assert comparison["cells_compared"] == 5820
    assert sum(comparison["band_counts"].values()) == 5820


def test_material_and_rounding_bands_are_separated(stored):
    comparison = stored["price_basis_comparison.json"]
    assert comparison["band_counts"][BAND_MATERIAL] == 310
    assert comparison["band_counts"][BAND_NEUTRAL] == 5510
    assert comparison["band_counts"][BAND_ANOMALOUS] == 0
    # The neutral band is float noise, not a suppressed economic difference.
    assert comparison["neutral_band_max_abs_delta_excess"] < MATERIALITY


def test_the_adjustment_bias_is_one_directional(stored):
    comparison = stored["price_basis_comparison.json"]
    assert comparison["direction"]["no_inverted_adjustment_beyond_rounding"] is True
    assert comparison["direction"]["inverted_windows"] == 0
    assert comparison["delta_score"]["min"] >= 0.0
    # The rounding band's sign flips are noise, and their size proves it.
    assert comparison["direction"]["rounding_band_max_abs_delta_score"] < 1e-5


def test_the_measured_effect_is_material_enough_to_report(stored):
    comparison = stored["price_basis_comparison.json"]
    assert comparison["delta_score"]["max"] > 0.30
    assert comparison["score_shift_buckets"]["gt_0_20"] == 18
    assert comparison["materially_affected_months"] == 49
    assert comparison["materially_affected_tickers"] == 103


def test_the_counterfactual_is_never_applied_to_production(stored, receipt):
    assert stored["price_basis_comparison.json"]["applied_to_production"] is False
    assert receipt["policy"]["ek4_values_rewritten"] == 0
    assert receipt["policy"]["production_code_changed"] is False


def test_recorded_returns_must_reproduce_from_the_price_source():
    # Mutation: the artifact and the price source disagree. The auditor must
    # refuse rather than silently compare two different observations.
    cell = {
        "month": "2021-08",
        "signal_date": "2021-08-02",
        "ticker": "AAA",
        "start_date": "2021-06-25",
        "end_date": "2021-07-30",
        "stock_return_20d": 0.5,  # not what the prices below imply
        "sector_return_20d": 0.0,
        "excess_return_20d": 0.5,
        "ek4": 1.0,
        "sector_index_code": "XUSIN",
    }
    prices = {("AAA", "2021-06-25"): (10.0, 10.0), ("AAA", "2021-07-30"): (11.0, 11.0)}
    with pytest.raises(W4AuditError, match="NOT_REPRODUCIBLE"):
        build_price_basis_rows([cell], prices)


def test_a_missing_endpoint_price_fails_closed():
    cell = {
        "month": "2021-08", "signal_date": "2021-08-02", "ticker": "AAA",
        "start_date": "2021-06-25", "end_date": "2021-07-30",
        "stock_return_20d": 0.1, "sector_return_20d": 0.0, "excess_return_20d": 0.1,
        "ek4": 0.75, "sector_index_code": "XUSIN",
    }
    with pytest.raises(W4AuditError, match="ENDPOINT_PRICE_MISSING"):
        build_price_basis_rows([cell], {})


def synthetic(close_start, close_end, adj_start, adj_end, sector_return=0.0) -> list[dict]:
    """Build one internally consistent Ek4 cell on the current adjusted basis."""
    from src.analytics.ek4_momentum import compute_ek4_momentum_point

    current = compute_ek4_momentum_point(
        stock_start=adj_start, stock_end=adj_end,
        sector_start=1.0, sector_end=1.0 + sector_return,
    )
    cell = {
        "month": "2021-08", "signal_date": "2021-08-02", "ticker": "AAA",
        "start_date": "2021-06-25", "end_date": "2021-07-30",
        "stock_return_20d": current.stock_return,
        "sector_return_20d": sector_return,
        "excess_return_20d": current.excess_return,
        "ek4": current.score, "sector_index_code": "XUSIN",
    }
    prices = {
        ("AAA", "2021-06-25"): (close_start, adj_start),
        ("AAA", "2021-07-30"): (close_end, adj_end),
    }
    return build_price_basis_rows([cell], prices)


def test_a_window_without_an_adjustment_lands_in_the_neutral_band():
    rows = synthetic(10.0, 11.0, 10.0, 11.0)
    assert rows[0]["band"] == BAND_NEUTRAL
    assert rows[0]["delta_excess_return"] == pytest.approx(0.0, abs=1e-12)


def test_a_dividend_inside_the_window_lands_in_the_material_band():
    # Back-adjustment lowers the start price only, so the adjusted return is higher.
    rows = synthetic(10.0, 11.0, 9.0, 11.0)
    assert rows[0]["band"] == BAND_MATERIAL
    assert rows[0]["delta_excess_return"] > MATERIALITY
    assert rows[0]["delta_score"] > 0


def test_an_inverted_adjustment_is_flagged_anomalous_not_hidden():
    rows = synthetic(10.0, 11.0, 11.0, 11.0)
    assert rows[0]["band"] == BAND_ANOMALOUS
    assert rows[0]["delta_excess_return"] < -MATERIALITY


def test_summary_direction_flag_follows_the_data():
    material = synthetic(10.0, 11.0, 9.0, 11.0)
    summary = build_price_basis_comparison(material)
    assert summary["direction"]["no_inverted_adjustment_beyond_rounding"] is True
    # An inverted adjustment must falsify the direction claim, not be absorbed
    # by a band that only ever contains positive deltas.
    anomalous = synthetic(10.0, 11.0, 11.0, 11.0)
    summary = build_price_basis_comparison(anomalous)
    assert summary["band_counts"][BAND_ANOMALOUS] == 1
    assert summary["direction"]["no_inverted_adjustment_beyond_rounding"] is False
    assert summary["direction"]["inverted_windows"] == 1


# --------------------------------------------------------------------------
# Fallback visibility
# --------------------------------------------------------------------------


def test_the_live_sector_fallback_is_reported_with_its_reachability(stored):
    fallback = stored["fallback_visibility.json"]
    assert fallback["finding"] == "LIVE_SECTOR_FALLBACK_NOT_VISIBLE_IN_PROVENANCE"
    assert fallback["evidence"]["live_fallback_marker_present"] is True
    assert fallback["evidence"]["ek4_result_columns_carry_no_sector"] is True
    assert fallback["production_reachability"]["active_chain_applies_xu100_fallback"] is False
    assert fallback["production_reachability"]["conclusion"] == (
        "NO_PRODUCTION_REACHABILITY_IN_ACTIVE_ARTIFACT_CHAIN"
    )
    assert fallback["code_changed"] is False
    assert fallback["reopen_condition"]


def test_the_historical_prohibition_still_holds(stored):
    assert stored["contract_compliance.json"]["checks"][
        "historical_xu100_substitution_forbidden"
    ]["verified"] is True
    assert stored["fallback_visibility.json"]["evidence"][
        "historical_replay_forbids_the_substitution"
    ] is True


# --------------------------------------------------------------------------
# Determinism and receipt binding
# --------------------------------------------------------------------------


def test_stored_artifacts_match_the_receipt(receipt):
    for name in CONTENT_FILES:
        assert sha_bytes((AUDIT / name).read_bytes()) == receipt["output_sha256"][name]


def test_second_derivation_is_byte_identical(receipt):
    content = derive()
    for name in CONTENT_FILES:
        assert sha_bytes(content[name]) == receipt["output_sha256"][name]


def test_receipt_declares_no_model_change(receipt):
    assert receipt["policy"]["model_changed"] is False
    assert receipt["policy"]["weights_changed"] is False
    assert receipt["policy"]["veto_changed"] is False
    assert receipt["policy"]["universe_changed"] is False
    assert receipt["policy"]["neutral_fill"] is False


def test_receipt_binds_every_source_by_hash(receipt):
    assert set(receipt["source_sha256"]) == set(receipt["source_paths"])
    assert all(len(value) == 64 for value in receipt["source_sha256"].values())


def test_rows_carry_both_bases_and_a_band(receipt):
    rows = [
        json.loads(line)
        for line in (AUDIT / "rows.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 5820
    assert all(
        row["band"] in {BAND_MATERIAL, BAND_NEUTRAL, BAND_ANOMALOUS} for row in rows
    )
    assert all(
        row["delta_score"] == pytest.approx(
            row["ek4_current_adjusted_raw"] - row["ek4_counterfactual_raw_raw"], abs=1e-12
        )
        for row in rows
    )


def test_no_windows_path_separator_leaks_into_the_artifacts():
    """A str(Path) on Windows would embed a backslash and change every hash."""
    for name in CONTENT_FILES:
        assert "\\" not in (AUDIT / name).read_text(encoding="utf-8"), name


def test_the_contract_doc_path_is_posix_in_the_artifact(stored):
    # str(Path) on Windows yields a backslash and silently changes the hash.
    assert stored["contract_compliance.json"]["locked_contract_doc"] == (
        "docs/HISTORICAL_PIT_EK4_REPLAY_CONTRACT.md"
    )


def test_receipt_source_paths_are_posix(receipt):
    assert all("\\" not in value for value in receipt["source_paths"].values())


def test_the_mean_is_interpreter_version_stable(stored):
    """CPython 3.12+ sums floats differently, so the artifact must not use sum()."""
    import math

    rows = [
        json.loads(line)
        for line in (AUDIT / "rows.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    material = sorted(
        row["delta_score"] for row in rows if row["band"] == BAND_MATERIAL
    )
    # fsum is correctly rounded, so this expectation is the same on every
    # interpreter. A plain sum() is not: on 3.11 this data gives
    # 0.07159288381297305 and on 3.12+ it gives 0.07159288381297303, which is
    # exactly how the Windows job caught it.
    expected = math.fsum(material) / len(material)
    assert stored["price_basis_comparison.json"]["delta_score"]["mean"] == expected
