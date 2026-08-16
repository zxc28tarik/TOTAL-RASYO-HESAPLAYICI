from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.analytics.bank_valuation_pipeline import (
    BankValuationInputs,
    CanonicalizationError,
    build_quarter_slots,
    compute_v_conf,
    persist_bank_valuation,
    run_bank_valuation,
    to_canonical_row,
)

TZ = ZoneInfo("Europe/Istanbul")
COE, MACRO, BVPS = 0.3705, 0.140135, 21.24
BNK1 = [0.156, 0.1898, 0.1952, 0.2346, 0.2689, 0.2809]


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=TZ)


def _empty_row(period_end: date) -> dict:
    return {
        "period_end": period_end,
        "record_id": None,
        "selected_version_tag": None,
        "selected_version_sequence": None,
        "selected_published_at": None,
        "roe_ttm": None,
        "bvps": None,
        "payout_sus": None,
    }


def _selected_row(
    period_end: date,
    record_id: int,
    roe: object,
    published_at: str,
    *,
    version_tag: str = "ORIGINAL",
    version_sequence: object = 1,
    bvps: object = None,
    payout: object = None,
) -> dict:
    return {
        "period_end": period_end,
        "record_id": record_id,
        "selected_version_tag": version_tag,
        "selected_version_sequence": version_sequence,
        "selected_published_at": dt(published_at),
        "roe_ttm": roe,
        "bvps": bvps,
        "payout_sus": payout,
    }


def acceptance_rows_2026() -> list[dict]:
    slots = build_quarter_slots(date(2025, 12, 31))
    values = [0.1560, 0.1898, None, 0.2346, 0.2100, 0.2400, 0.2950, 0.3080]
    publications = [
        "2024-05-10T10:00:00",
        "2024-08-09T10:00:00",
        None,
        "2025-02-14T10:00:00",
        "2025-11-20T10:00:00",
        "2025-08-08T17:00:00",
        "2025-11-07T10:00:00",
        "2026-02-13T10:00:00",
    ]
    versions = ["ORIGINAL", "ORIGINAL", None, "ORIGINAL", "RESTATED", "RESTATED", "ORIGINAL", "ORIGINAL"]
    seqs = [1, 1, None, 1, 2, 2, 1, 1]
    rows = []
    rid = 1
    for i, (slot, roe, pub, version, seq) in enumerate(zip(slots, values, publications, versions, seqs)):
        if pub is None:
            rows.append(_empty_row(slot))
            continue
        rows.append(
            _selected_row(
                slot,
                rid,
                roe,
                pub,
                version_tag=version,
                version_sequence=seq,
                bvps=Decimal("21.24") if i == 7 else None,
                payout=Decimal("0.25") if i == 7 else None,
            )
        )
        rid += 1
    return rows


def bnk1_rows(*, payout=0.25, outlier=False) -> list[dict]:
    slots = build_quarter_slots(date(2025, 12, 31))
    values = [None, None] + list(BNK1)
    if outlier:
        values[2 + 3] = 0.95
    rows = []
    rid = 100
    for i, (slot, value) in enumerate(zip(slots, values)):
        if value is None:
            rows.append(_empty_row(slot))
            continue
        rows.append(
            _selected_row(
                slot,
                rid,
                value,
                f"{slot.year}-{min(slot.month + 2, 12):02d}-01T10:00:00",
                bvps=BVPS if i == 7 else None,
                payout=payout if i == 7 else None,
            )
        )
        rid += 1
    return rows


def canonical(rows, *, analysis_at=dt("2026-03-01T12:00:00")):
    return to_canonical_row(
        rows,
        ticker="fixbnk",
        analysis_at=analysis_at,
        anchor_period_end=date(2025, 12, 31),
    )


def test_quarter_slots_are_calendar_quarters_not_last_records():
    assert build_quarter_slots(date(2025, 11, 15)) == (
        date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31),
        date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31),
    )


def test_acceptance_fixture_canonical_series_and_reference_uncertainty():
    c = canonical(acceptance_rows_2026())
    assert c.ticker == "FIXBNK"
    assert c.roe_series == (0.156, 0.1898, None, 0.2346, 0.21, 0.24, 0.295, 0.308)
    assert c.roe_missing_count == 1
    assert c.selected_version_tags[4:6] == ("RESTATED", "RESTATED")
    assert all(v is None or type(v) is float for v in c.roe_series)

    result = run_bank_valuation(
        c,
        BankValuationInputs(coe=COE, macro_cap=MACRO, band_width_shadow_mode=True),
    )
    u = result["uncertainty"]
    assert u["trend_slope"] == pytest.approx(0.021040, abs=1e-9)
    assert u["sd_roe_effective"] == pytest.approx(0.01192010, abs=1e-8)
    assert u["n_valid"] == 7
    assert u["roe_missing_count"] == 1


def test_bnk1_regression_and_two_step_confidence_chain():
    c = canonical(bnk1_rows())
    r = run_bank_valuation(
        c,
        BankValuationInputs(coe=COE, macro_cap=MACRO, band_width_shadow_mode=False),
    )
    assert r["status"] == "OK"
    assert r["valuation"]["V_mid"] == pytest.approx(6.8934456, rel=1e-8)
    assert r["valuation"]["V_low"] == pytest.approx(5.2707, abs=1e-4)
    assert r["valuation"]["V_high"] == pytest.approx(10.1128, abs=1e-4)
    assert r["valuation"]["V_high"] / r["valuation"]["V_low"] == pytest.approx(1.9187, abs=1e-4)
    assert r["v_conf"] == pytest.approx(0.800)
    assert r["confidence_factors"] == {
        "tier_cap": 0.8,
        "payout_factor": 1.0,
        "outlier_conf_penalty": 1.0,
        "corner_conf_penalty": 1.0,
    }


def test_v_conf_payout_and_outlier_scenarios():
    inputs = BankValuationInputs(coe=COE, macro_cap=MACRO, band_width_shadow_mode=False)
    payout_missing = run_bank_valuation(canonical(bnk1_rows(payout=None)), inputs)
    outlier = run_bank_valuation(canonical(bnk1_rows(outlier=True)), inputs)
    both = run_bank_valuation(canonical(bnk1_rows(payout=None, outlier=True)), inputs)

    assert payout_missing["v_conf"] == pytest.approx(0.560)
    assert outlier["uncertainty"]["outlier_flag"] is True
    assert outlier["v_conf"] == pytest.approx(0.680)
    assert both["v_conf"] == pytest.approx(0.476)


def test_compute_v_conf_includes_corner_penalty():
    value, factors = compute_v_conf(
        tier_cap=0.8,
        payout_defaulted=False,
        payout_missing_factor=0.7,
        outlier_conf_penalty=0.85,
        corner_conf_penalty=0.70,
    )
    assert value == pytest.approx(0.8 * 0.85 * 0.70)
    assert factors["corner_conf_penalty"] == 0.70


def test_reversed_rows_are_rejected_instead_of_silently_sorted():
    with pytest.raises(CanonicalizationError, match="takvim yuvalari/sirasi"):
        canonical(list(reversed(acceptance_rows_2026())))


def test_wrong_window_is_rejected_even_when_eight_rows_arrive():
    rows = acceptance_rows_2026()
    rows = [_empty_row(date(2023, 12, 31))] + rows[:-1]
    with pytest.raises(CanonicalizationError, match="takvim yuvalari/sirasi"):
        canonical(rows)


def test_future_publication_is_rejected():
    rows = acceptance_rows_2026()
    rows[-1]["selected_published_at"] = dt("2026-03-01T13:00:00")
    with pytest.raises(CanonicalizationError, match="gelecekte yayimlanan"):
        canonical(rows, analysis_at=dt("2026-03-01T12:00:00"))


def test_analysis_at_must_be_timezone_aware():
    with pytest.raises(CanonicalizationError, match="timezone"):
        canonical(acceptance_rows_2026(), analysis_at=datetime(2026, 3, 1, 12, 0))


def test_nullable_pandas_and_numpy_values_become_python_float_none():
    rows = acceptance_rows_2026()
    rows[0]["roe_ttm"] = np.float64(0.156)
    rows[2]["roe_ttm"] = pd.NA
    rows[-1]["bvps"] = np.float64(BVPS)
    c = canonical(rows)
    assert type(c.roe_series[0]) is float
    assert c.roe_series[2] is None
    assert type(c.bvps) is float


def test_numpy_bool_cannot_be_record_or_version_sequence():
    rows = acceptance_rows_2026()
    rows[0]["record_id"] = np.bool_(True)
    with pytest.raises(CanonicalizationError, match="bool"):
        canonical(rows)
    rows = acceptance_rows_2026()
    rows[0]["selected_version_sequence"] = np.bool_(False)
    with pytest.raises(CanonicalizationError, match="bool"):
        canonical(rows)


def test_missing_slot_cannot_contain_hidden_data():
    rows = acceptance_rows_2026()
    rows[2]["roe_ttm"] = 0.20
    with pytest.raises(CanonicalizationError, match="eksik yuva"):
        canonical(rows)


def test_latest_missing_bvps_fails_closed_before_valuation():
    rows = bnk1_rows()
    rows[-1]["bvps"] = None
    result = run_bank_valuation(
        canonical(rows),
        BankValuationInputs(coe=COE, macro_cap=MACRO),
    )
    assert result["status"] == "YETERSIZ_VERI"
    assert result["reason"] == "LATEST_BVPS_MISSING"
    assert "valuation" not in result


class FakeCursor:
    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    def cursor(self):
        return self.cursor_instance

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_persistence_keeps_intermediate_fields_and_is_idempotent():
    result = run_bank_valuation(
        canonical(bnk1_rows()),
        BankValuationInputs(coe=COE, macro_cap=MACRO, band_width_shadow_mode=False),
    )
    conn = FakeConnection()
    persist_bank_valuation(conn, result)
    sql = conn.cursor_instance.sql
    params = conn.cursor_instance.params
    assert "ON CONFLICT (ticker, analysis_at, anchor_period_end)" in sql
    assert params["trend_slope"] == pytest.approx(result["uncertainty"]["trend_slope"])
    assert params["v_conf"] == pytest.approx(0.8)
    assert params["justified_pb"] == pytest.approx(result["valuation"]["V_mid"] / BVPS)
    assert '"outlier_conf_penalty": 1.0' in params["confidence_factors"]


def test_production_sql_calls_single_point_in_time_function():
    root = Path(__file__).resolve().parents[1]
    call_sql = (root / "sql" / "012_bank_point_in_time_slots.sql").read_text()
    call_norm = " ".join(call_sql.lower().split())
    assert "analytics.bank_point_in_time_slots" in call_norm
    assert "%(analysis_at)s::timestamptz" in call_norm

    migration = (root / "sql" / "011_bank_valuation_integration.sql").read_text()
    normalized = " ".join(migration.lower().split())
    assert "generate_series(7, 0, -1)" in normalized
    assert "m.published_at <= p.analysis_at" in normalized
    assert "m.published_at desc, m.version_sequence desc, m.record_id desc" in normalized
    assert "left join selected" in normalized
    assert "order by s.period_end;" in normalized
    assert "order by s.period_end desc" not in normalized
    assert "m.published_at::date" not in normalized


def test_sector_scales_are_point_in_time_and_leave_one_out():
    from src.analytics.bank_valuation_pipeline import compute_sector_residual_scales

    tickers = ["TARGET"] + [f"BNK{i:02d}" for i in range(20)]
    rows_by_ticker = {}
    for idx, ticker in enumerate(tickers):
        rows = bnk1_rows()
        # Bankalar arasında küçük, deterministik artık ölçeği farkı oluştur.
        for j, row in enumerate(rows):
            if row["roe_ttm"] is not None:
                row["roe_ttm"] = float(row["roe_ttm"]) + ((j % 2) * (idx + 1) * 0.0005)
        rows_by_ticker[ticker] = rows

    result = compute_sector_residual_scales(
        tickers,
        lambda ticker: rows_by_ticker[ticker],
        analysis_at=dt("2026-03-01T12:00:00"),
        anchor_period_end=date(2025, 12, 31),
        target_ticker="target",
        leave_one_out=True,
    )
    assert result["sample_size"] == 20
    assert result["excluded_tickers"] == ["TARGET"]
    assert "TARGET" not in result["included_tickers"]
    assert result["sector_asof_cutoff"] == dt("2026-03-01T12:00:00")
    assert all(type(x) is float and x >= 0 for x in result["scales"])


def test_sector_scale_bad_bank_is_reported_not_silently_counted():
    from src.analytics.bank_valuation_pipeline import compute_sector_residual_scales

    good = bnk1_rows()
    bad = list(reversed(bnk1_rows()))
    result = compute_sector_residual_scales(
        ["GOOD", "BAD"],
        lambda ticker: good if ticker == "GOOD" else bad,
        analysis_at=dt("2026-03-01T12:00:00"),
        anchor_period_end=date(2025, 12, 31),
    )
    assert result["sample_size"] == 1
    assert result["included_tickers"] == ["GOOD"]
    assert "BAD" in result["rejected_tickers"]
    assert "takvim yuvalari/sirasi" in result["rejected_tickers"]["BAD"]


def test_sector_quantile_floor_reaches_two_step_motor():
    c = canonical(bnk1_rows())
    result = run_bank_valuation(
        c,
        BankValuationInputs(coe=COE, macro_cap=MACRO, band_width_shadow_mode=True),
        sector_residual_scales=[0.02] * 20,
    )
    assert result["uncertainty"]["floor_source"] == "SECTOR_QUANTILE"
    assert result["uncertainty"]["sd_roe_floor"] == pytest.approx(0.02)

@pytest.mark.parametrize("bad", [[], [1, 2], "abc", 3])
def test_pipeline_kwargs_must_be_mapping(bad):
    c = canonical(bnk1_rows())
    with pytest.raises(CanonicalizationError, match="mapping"):
        run_bank_valuation(
            c,
            BankValuationInputs(coe=COE, macro_cap=MACRO),
            uncertainty_kwargs=bad,
        )


@pytest.mark.parametrize(
    "which,bad",
    [
        ("uncertainty", {1: 2, "x": 3}),
        ("valuation", {None: 2}),
        ("uncertainty", {"roe_series": []}),
        ("valuation", {"sd_roe": 0.1}),
        ("valuation", {"max_halfwidth": 1.0}),
    ],
)
def test_pipeline_kwargs_reject_mixed_or_managed_keys(which, bad):
    c = canonical(bnk1_rows())
    kwargs = {"uncertainty_kwargs": bad} if which == "uncertainty" else {"valuation_kwargs": bad}
    with pytest.raises(CanonicalizationError):
        run_bank_valuation(c, BankValuationInputs(coe=COE, macro_cap=MACRO), **kwargs)


@pytest.mark.parametrize("field,value", [("tier_cap", "abc"), ("payout_missing_factor", np.bool_(True))])
def test_confidence_configuration_is_validated_even_when_valuation_fails(field, value):
    rows = bnk1_rows()
    rows[-1]["bvps"] = None
    c = canonical(rows)
    base = dict(coe=COE, macro_cap=MACRO)
    base[field] = value
    with pytest.raises(CanonicalizationError):
        run_bank_valuation(c, BankValuationInputs(**base))

@pytest.mark.parametrize(
    "kwargs,scales",
    [
        ({"dof_correction": "false"}, None),
        ({"sector_quantile": 1.5}, None),
        (None, {0.01, 0.02}),
    ],
)
def test_invalid_uncertainty_configuration_is_controlled_at_pipeline_boundary(kwargs, scales):
    with pytest.raises(CanonicalizationError, match="belirsizlik ayarlari/girdisi gecersiz"):
        run_bank_valuation(
            canonical(bnk1_rows()),
            BankValuationInputs(coe=COE, macro_cap=MACRO),
            sector_residual_scales=scales,
            uncertainty_kwargs=kwargs,
        )


def test_persistence_json_safe_orders_sets_deterministically():
    from src.analytics.bank_valuation_pipeline import _json_safe
    assert _json_safe({"flags": {"Z", "A", "M"}}) == {"flags": ["A", "M", "Z"]}
