from __future__ import annotations

from src.analytics.live_total_rasyo_readiness import build_live_readiness


def test_live_readiness_keeps_every_universe_ticker_and_never_injects_neutral_scores():
    universe = [{"ticker": "BANK"}, {"ticker": "GENEL"}, {"ticker": "YOK"}]
    reports = [
        {"source_entity_code": "BANK", "family": "BANK", "schema": "SPECIALIST"},
        {"source_entity_code": "GENEL", "family": None, "schema": "GENERAL"},
    ]
    receipt, rejections = build_live_readiness(
        universe_rows=universe, report_rows=reports, database_available=False,
        share_basis_tickers={"BANK"}, raw_close_tickers={"BANK", "GENEL"},
    )
    assert receipt["universe_count"] == 3
    assert receipt["explicit_rejection_count"] == 3
    assert receipt["silent_drop_count"] == 0
    assert receipt["neutral_score_injection"] is False
    assert receipt["total_rasyo_count"] == 0
    assert receipt["explicit_share_basis_count"] == 1
    assert receipt["raw_close_count"] == 2
    assert receipt["price_and_share_ready_count"] == 1
    assert {row["ticker"] for row in rejections} == {"BANK", "GENEL", "YOK"}
    reasons = {row["ticker"]: row["reasons"] for row in rejections}
    assert "GENERAL_SCHEMA_NOT_ECONOMIC_NONFIN_PROOF" in reasons["GENEL"]
    assert "CURRENT_RAW_CLOSE_NOT_CAPTURED" not in reasons["GENEL"]
    assert "CURRENT_FINANCIAL_ENTITY_UNBOUND" in reasons["YOK"]
    assert "PERSISTENCE_DATABASE_NOT_CONFIGURED" in reasons["BANK"]


def test_composite_current_report_binding_does_not_drop_share_class_tickers():
    receipt, rejections = build_live_readiness(
        universe_rows=[{"ticker": "AAA"}, {"ticker": "AAB"}],
        report_rows=[{"source_entity_code": "AAA-AAB", "family": "BANK", "schema": "SPECIALIST"}],
        database_available=True,
    )
    assert receipt["family_counts"] == {"BANK": 2}
    assert all(row["source_entity_codes"] == ["AAA-AAB"] for row in rejections)
    assert all("PERSISTENCE_DATABASE_NOT_CONFIGURED" not in row["reasons"] for row in rejections)


def test_live_readiness_counts_real_modules_without_filling_missing_ones():
    receipt, rejections = build_live_readiness(
        universe_rows=[{"ticker": "AAA"}, {"ticker": "BBB"}],
        report_rows=[], database_available=False,
        module_tickers={
            "M1": {"AAA", "BBB"}, "Ek1": {"AAA"}, "M3": {"AAA"}, "Ek4": {"AAA"},
        },
    )
    assert receipt["module_coverage"] == {
        "M1": 2, "M2": 0, "M3": 1, "Ek1": 1, "Ek4": 1, "Ek9": 0,
    }
    assert receipt["at_least_4_modules_count"] == 1
    assert receipt["at_least_5_modules_count"] == 0
    aaa = next(row for row in rejections if row["ticker"] == "AAA")
    assert "CURRENT_M2_NOT_MATERIALIZED" in aaa["reasons"]
    assert "CURRENT_M1_NOT_MATERIALIZED" not in aaa["reasons"]
