from decimal import Decimal

import pandas as pd
import pytest

from src.ingest.historical_backtest_schedules import (
    CutoffScheduleRecord,
    HistoricalBacktestScheduleError,
    WageScheduleRecord,
    cutoff_records_from_frame,
    wage_records_from_frame,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _wage_frame(**overrides):
    row = {
        "schedule_key": "TR_NET_MIN_WAGE",
        "valid_from": "2022-01-01",
        "valid_to": "2022-07-01",
        "net_min_wage": "4253.40",
        "source": "OFFICIAL_FIXTURE",
        "source_ref": "wage-2022-h1",
        "source_sha256": SHA_A,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _cutoff_frame(**overrides):
    row = {
        "profile_key": "MONTHLY_FIRST_OPEN_V1",
        "signal_date": "2022-01-03",
        "cutoff_at": "2022-01-02T20:00:00+03:00",
        "execution_at": "2022-01-03T10:00:00+03:00",
        "source": "POLICY_FIXTURE",
        "source_ref": "jan-2022",
        "source_sha256": SHA_B,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_wage_record_is_canonical_and_decimal_stable():
    a = wage_records_from_frame(_wage_frame(net_min_wage="4253.400"))[0]
    b = wage_records_from_frame(_wage_frame(net_min_wage=Decimal("4253.40")))[0]
    assert a.row_sha256 == b.row_sha256
    assert a.net_min_wage == Decimal("4253.400")
    assert a.canonical_dict()["net_min_wage"] == "4253.4"


@pytest.mark.parametrize("bad", [0, -1, "NaN", "Infinity", "-Infinity", True, None])
def test_wage_rejects_non_positive_or_non_finite_values(bad):
    with pytest.raises(HistoricalBacktestScheduleError, match="net_min_wage"):
        wage_records_from_frame(_wage_frame(net_min_wage=bad))


def test_wage_exact_duplicate_collapses_but_conflicting_identity_fails():
    row = _wage_frame().iloc[0].to_dict()
    exact = pd.DataFrame([row, dict(row)])
    assert len(wage_records_from_frame(exact)) == 1

    conflict = dict(row)
    conflict["net_min_wage"] = "5000"
    with pytest.raises(HistoricalBacktestScheduleError, match="farkli icerik"):
        wage_records_from_frame(pd.DataFrame([row, conflict]))


def test_wage_overlap_rejected_and_touching_intervals_allowed():
    first = _wage_frame(valid_from="2022-01-01", valid_to="2022-07-01").iloc[0].to_dict()
    overlap = _wage_frame(valid_from="2022-06-30", valid_to="2023-01-01", source_ref="overlap").iloc[0].to_dict()
    with pytest.raises(HistoricalBacktestScheduleError, match="cakisan"):
        wage_records_from_frame(pd.DataFrame([first, overlap]))

    touching = _wage_frame(valid_from="2022-07-01", valid_to="2023-01-01", source_ref="touch").iloc[0].to_dict()
    rows = wage_records_from_frame(pd.DataFrame([first, touching]))
    assert [r.valid_from.isoformat() for r in rows] == ["2022-01-01", "2022-07-01"]


def test_cutoff_requires_timezone_and_strictly_precedes_execution():
    with pytest.raises(HistoricalBacktestScheduleError, match="timezone-aware"):
        cutoff_records_from_frame(_cutoff_frame(cutoff_at="2022-01-02 20:00:00"))
    with pytest.raises(HistoricalBacktestScheduleError, match="once olmali"):
        cutoff_records_from_frame(_cutoff_frame(cutoff_at="2022-01-03T10:00:00+03:00"))
    with pytest.raises(HistoricalBacktestScheduleError, match="once olmali"):
        cutoff_records_from_frame(_cutoff_frame(cutoff_at="2022-01-03T11:00:00+03:00"))


def test_execution_must_land_on_signal_date_in_istanbul():
    with pytest.raises(HistoricalBacktestScheduleError, match="Istanbul"):
        cutoff_records_from_frame(_cutoff_frame(execution_at="2022-01-04T00:01:00+03:00"))


def test_cutoff_hash_is_timezone_representation_invariant():
    a = cutoff_records_from_frame(_cutoff_frame())[0]
    b = cutoff_records_from_frame(_cutoff_frame(
        cutoff_at="2022-01-02T17:00:00Z",
        execution_at="2022-01-03T07:00:00Z",
    ))[0]
    assert a.row_sha256 == b.row_sha256


def test_cutoff_exact_duplicate_collapses_and_conflict_fails():
    row = _cutoff_frame().iloc[0].to_dict()
    assert len(cutoff_records_from_frame(pd.DataFrame([row, dict(row)]))) == 1
    conflict = dict(row)
    conflict["cutoff_at"] = "2022-01-02T19:00:00+03:00"
    with pytest.raises(HistoricalBacktestScheduleError, match="farkli icerik"):
        cutoff_records_from_frame(pd.DataFrame([row, conflict]))


def test_source_sha_must_be_64_hex_for_both_schedule_types():
    with pytest.raises(HistoricalBacktestScheduleError, match="64-hex"):
        wage_records_from_frame(_wage_frame(source_sha256="abc"))
    with pytest.raises(HistoricalBacktestScheduleError, match="64-hex"):
        cutoff_records_from_frame(_cutoff_frame(source_sha256="xyz"))


def test_direct_record_construction_rebuild_contract_examples():
    w = WageScheduleRecord.from_mapping(_wage_frame().iloc[0].to_dict())
    c = CutoffScheduleRecord.from_mapping(_cutoff_frame().iloc[0].to_dict())
    assert WageScheduleRecord.from_mapping(w.canonical_dict()) == w
    assert CutoffScheduleRecord.from_mapping(c.canonical_dict()) == c
