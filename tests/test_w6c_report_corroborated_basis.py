"""W6-C — contract and mutation tests for the report-corroborated-basis audit."""
from __future__ import annotations

from datetime import date
import json

import pytest

from scripts.audit_w6c_report_corroborated_basis import (
    AUDIT,
    CONTENT_FILES,
    CONTRACT,
    W6CAuditError,
    build_report_series,
    build_summary,
    chain_extend,
    derive,
    sha_bytes,
)

RECEIPT = AUDIT / "receipt.json"

pytestmark = pytest.mark.skipif(
    not RECEIPT.exists(), reason="W6-C audit artifacts not materialized"
)


@pytest.fixture(scope="module")
def receipt() -> dict:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stored() -> dict:
    return {name: json.loads((AUDIT / name).read_text(encoding="utf-8")) for name in CONTENT_FILES}


# --------------------------------------------------------------------------
# chain_extend
# --------------------------------------------------------------------------


def test_matching_reports_extend_the_chain():
    reports = [
        (date(2023, 3, 31), date(2023, 5, 8), 100.0),
        (date(2023, 6, 30), date(2023, 8, 15), 100.0),  # after cutoff, ignored
    ]
    result = chain_extend(date(2021, 11, 4), 100.0, reports, date(2023, 7, 31))
    assert result["corroborating_quarters"] == 1
    assert result["basis_date_published_at"] == "2023-05-08"
    assert result["basis_date_period_end"] == "2023-03-31"


def test_a_mismatched_value_stops_the_chain():
    reports = [
        (date(2022, 3, 31), date(2022, 5, 8), 100.0),
        (date(2022, 6, 30), date(2022, 8, 15), 200.0),  # real change
        (date(2022, 9, 30), date(2022, 11, 6), 200.0),
    ]
    result = chain_extend(date(2021, 11, 4), 100.0, reports, date(2023, 7, 31))
    assert result["corroborating_quarters"] == 1
    assert result["basis_date_published_at"] == "2022-05-08"


def test_no_matching_reports_leaves_the_anchor_unchanged():
    result = chain_extend(date(2021, 11, 4), 100.0, [], date(2023, 7, 31))
    assert result["corroborating_quarters"] == 0
    assert result["basis_date_published_at"] == "2021-11-04"
    assert result["basis_date_period_end"] == "2021-11-04"


def test_a_report_published_after_the_cutoff_is_never_used():
    reports = [(date(2023, 6, 30), date(2023, 8, 15), 100.0)]
    result = chain_extend(date(2021, 11, 4), 100.0, reports, date(2023, 7, 31))
    assert result["corroborating_quarters"] == 0


def test_a_report_exactly_at_the_anchor_date_is_not_double_counted():
    reports = [(date(2021, 9, 30), date(2021, 11, 4), 100.0)]  # published == anchor date
    result = chain_extend(date(2021, 11, 4), 100.0, reports, date(2023, 7, 31))
    assert result["corroborating_quarters"] == 0  # strictly after anchor_date only


def test_a_materially_different_value_is_treated_as_a_real_change():
    # Real share counts are whole numbers in the hundreds of millions; a
    # difference this small can only be a genuine capital change, not
    # floating-point noise from the source pipeline.
    reports = [(date(2022, 3, 31), date(2022, 5, 8), 1_000_100.0)]
    result = chain_extend(date(2021, 11, 4), 1_000_000.0, reports, date(2023, 7, 31))
    assert result["corroborating_quarters"] == 0


def test_sub_share_floating_point_noise_does_not_break_the_chain():
    reports = [(date(2022, 3, 31), date(2022, 5, 8), 1_000_000.4)]
    result = chain_extend(date(2021, 11, 4), 1_000_000.0, reports, date(2023, 7, 31))
    assert result["corroborating_quarters"] == 1


# --------------------------------------------------------------------------
# build_report_series
# --------------------------------------------------------------------------


def test_report_series_excludes_future_publications():
    records = [{
        "analysis_at": "2023-07-31T18:10:00+03:00",
        "per_ticker": {
            "AAA": {
                "technical_family": "NONFIN",
                "quarters": [
                    {"period_end": "2023-03-31", "published_at": "2023-05-08T00:00:00+03:00",
                     "values": {"shares_out": 100.0}},
                    {"period_end": "2023-06-30", "published_at": "2023-08-15T00:00:00+03:00",
                     "values": {"shares_out": 100.0}},
                ] * 2,  # len>=4 to satisfy nonfin_candidates
            }
        },
    }]
    series = build_report_series(records)
    assert "AAA" in series
    dates = [pub for _, pub, _ in series["AAA"]]
    assert date(2023, 8, 15) not in dates
    assert date(2023, 5, 8) in dates


# --------------------------------------------------------------------------
# build_summary
# --------------------------------------------------------------------------


def test_summary_counts_are_internally_consistent(stored):
    summary = stored["summary.json"]
    assert (
        summary["cells_with_external_anchor"] + summary["cells_without_external_anchor"]
        == summary["total_cells"]
    )
    assert summary["cells_with_any_corroboration"] <= summary["cells_with_external_anchor"]
    assert summary["cells_shrunk"] <= summary["cells_with_any_corroboration"]


def test_summary_never_reports_a_negative_or_zero_count_wrongly():
    rows = [
        {"has_external_anchor": True, "corroborating_quarters": 2,
         "gap_before_days": 100, "gap_after_published_at_days": 50,
         "gap_after_period_end_days": 60, "shrunk": True, "ticker": "AAA"},
        {"has_external_anchor": True, "corroborating_quarters": 0,
         "gap_before_days": 100, "gap_after_published_at_days": 100,
         "gap_after_period_end_days": 100, "shrunk": False, "ticker": "BBB"},
        {"has_external_anchor": False},
    ]
    summary = build_summary(rows)
    assert summary["cells_with_external_anchor"] == 2
    assert summary["cells_without_external_anchor"] == 1
    assert summary["cells_with_any_corroboration"] == 1
    assert summary["cells_shrunk"] == 1
    assert summary["distinct_tickers_shrunk"] == ["AAA"]


def test_the_stored_summary_shows_a_genuine_reduction_not_100_percent(stored):
    """The finding must not overclaim: this is a partial, honest improvement."""
    summary = stored["summary.json"]
    assert 0 < summary["cells_shrunk"] < summary["cells_with_external_anchor"]
    before = summary["gap_before_days"]["median"]
    after = summary["gap_after_published_at_days"]["median"]
    assert after <= before
    assert after > 0  # never claims the gap reaches zero system-wide


# --------------------------------------------------------------------------
# Anchor lookup integrity
# --------------------------------------------------------------------------


def test_derive_refuses_a_missing_anchor_share_lookup(monkeypatch):
    import scripts.audit_w6c_report_corroborated_basis as module

    def empty_derived_values():
        return {}

    monkeypatch.setattr(module, "read_share_class_derived_values", empty_derived_values)
    with pytest.raises(W6CAuditError, match="ANCHOR_SHARES_LOOKUP_FAILED"):
        module.derive()


# --------------------------------------------------------------------------
# Receipt and reproducibility
# --------------------------------------------------------------------------


def test_stored_artifacts_match_the_receipt(receipt):
    for name in CONTENT_FILES:
        assert sha_bytes((AUDIT / name).read_bytes()) == receipt["output_sha256"][name]


def test_second_derivation_is_byte_identical(receipt):
    content = derive()
    for name in CONTENT_FILES:
        assert sha_bytes(content[name]) == receipt["output_sha256"][name]


def test_verdict_never_claims_m2_materialized(stored):
    verdict = stored["verdict.json"]
    assert verdict["policy"]["m2_materialized"] is False
    assert verdict["policy"]["production_code_changed"] is False
    assert verdict["status"] == "GAP_NARROWED_NOT_CLOSED"


def test_every_artifact_declares_the_same_contract(stored):
    for name in CONTENT_FILES:
        assert stored[name]["contract"] == CONTRACT
