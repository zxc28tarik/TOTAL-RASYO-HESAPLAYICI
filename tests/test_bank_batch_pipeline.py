from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.analytics.bank_batch_pipeline import (
    BankM2Context,
    ResolvedBankAssumption,
    _assumption_from_row,
    compute_bank_m2_score,
    evaluate_bank_batch,
    fetch_active_bank_tickers,
    fetch_bank_quarter_slots_batch,
    persist_bank_m2_score,
)
from src.analytics.bank_valuation_pipeline import (
    BankValuationInputs,
    CanonicalizationError,
    build_quarter_slots,
    run_bank_valuation,
    to_canonical_row,
)

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 3, 1, 12, 0, tzinfo=TZ)
ANCHOR = date(2025, 12, 31)
COE, MACRO, BVPS = 0.3705, 0.140135, 21.24
BNK1 = [0.156, 0.1898, 0.1952, 0.2346, 0.2689, 0.2809]


def rows_for(ticker: str, bump: float = 0.0):
    rows = []
    values = [None, None] + [v + bump * (i % 2) for i, v in enumerate(BNK1)]
    for i, (slot, value) in enumerate(zip(build_quarter_slots(ANCHOR), values)):
        if value is None:
            rows.append({
                "period_end": slot,
                "record_id": None,
                "selected_version_tag": None,
                "selected_version_sequence": None,
                "selected_published_at": None,
                "roe_ttm": None,
                "bvps": None,
                "payout_sus": None,
            })
            continue
        rows.append({
            "period_end": slot,
            "record_id": i + 1,
            "selected_version_tag": "ORIGINAL",
            "selected_version_sequence": 1,
            "selected_published_at": datetime(2025, 1, 1, 10, 0, tzinfo=TZ),
            "roe_ttm": value,
            "bvps": Decimal("21.24") if i == 7 else None,
            "payout_sus": Decimal("0.25") if i == 7 else None,
        })
    return rows


def canonical(ticker="BNK1", bump=0.0):
    return to_canonical_row(
        rows_for(ticker, bump),
        ticker=ticker,
        analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR,
    )


def assumption(scope="BANK", code="BANK", coe=COE):
    return ResolvedBankAssumption(
        inputs=BankValuationInputs(
            coe=coe, macro_cap=MACRO, band_width_shadow_mode=False,
        ),
        scope_type=scope,
        scope_code=code,
        effective_at=datetime(2026, 1, 1, tzinfo=TZ),
        source="TEST",
        metadata={},
    )


def test_assumption_row_is_point_in_time_and_strictly_typed():
    r = _assumption_from_row({
        "scope_type": "ticker",
        "scope_code": "garan",
        "effective_at": "2026-01-01T00:00:00+03:00",
        "coe": "0.37",
        "macro_cap": 0.14,
        "risk_free_rate": 0.30,
        "tier_cap": 0.8,
        "payout_missing_factor": 0.7,
        "band_width_shadow_mode": True,
        "max_halfwidth": 0.8,
        "source": "MANUAL",
        "metadata": '{"rf": 0.30}',
    })
    assert r.scope_type == "TICKER"
    assert r.scope_code == "GARAN"
    assert r.effective_at.utcoffset().total_seconds() == 3 * 3600
    assert r.inputs.coe == pytest.approx(0.37)
    assert r.risk_free_rate == pytest.approx(0.30)
    assert r.metadata == {"rf": 0.30}


@pytest.mark.parametrize(
    "field,value",
    [
        ("tier_cap", 1.1),
        ("payout_missing_factor", -0.1),
        ("band_width_shadow_mode", "true"),
        ("effective_at", "2026-01-01T00:00:00"),
    ],
)
def test_bad_assumption_contract_is_rejected(field, value):
    row = {
        "scope_type": "BANK", "scope_code": "BANK",
        "effective_at": "2026-01-01T00:00:00+03:00",
        "coe": 0.37, "macro_cap": 0.14, "risk_free_rate": 0.30, "tier_cap": 0.8,
        "payout_missing_factor": 0.7, "band_width_shadow_mode": True,
        "max_halfwidth": 0.8, "source": "TEST", "metadata": {},
    }
    row[field] = value
    with pytest.raises(CanonicalizationError):
        _assumption_from_row(row)


def test_bank_m2_uses_only_two_score_axes_and_keeps_diagnostics_separate():
    valuation = run_bank_valuation(
        canonical(),
        BankValuationInputs(coe=COE, macro_cap=MACRO, band_width_shadow_mode=False),
    )
    result = compute_bank_m2_score(
        valuation,
        BankM2Context(
            current_price=5.5,
            s_lag_effective=0.8,
            lag_active=True,
            lag_source="TEST_LAG",
        ),
    )
    assert set(result["score_inputs"]) == {
        "s_val_effective", "v_status", "v_conf", "s_lag_effective", "lag_active"
    }
    assert "z_val" not in result["score_inputs"]
    assert result["diagnostics"]["z_val"] is not None
    assert result["m2_score"] > 0.5
    assert result["valuation_usable"] is True


def test_missing_price_neutralizes_valuation_but_lag_axis_survives():
    valuation = run_bank_valuation(
        canonical(),
        BankValuationInputs(coe=COE, macro_cap=MACRO, band_width_shadow_mode=False),
    )
    result = compute_bank_m2_score(
        valuation,
        BankM2Context(current_price=None, s_lag_effective=0.8, lag_active=True),
    )
    assert result["s_val_effective"] == 0.5
    assert result["valuation_usable"] is False
    assert result["m2_score"] == pytest.approx(0.5 + 0.4 * 0.3)


def test_batch_uses_leave_one_out_sector_scales():
    canonicals = {f"BNK{i:02d}": canonical(f"BNK{i:02d}", bump=i * 0.0004) for i in range(21)}
    assumptions = {ticker: assumption() for ticker in canonicals}
    contexts = {ticker: BankM2Context(current_price=5.5) for ticker in canonicals}
    results = evaluate_bank_batch(canonicals, assumptions, contexts)
    assert len(results) == 21
    assert all(r["sector_sample_size"] == 20 for r in results)
    assert all(r["ticker"] not in r["sector_scale_rejected_tickers"] for r in results)
    assert all(0 <= r["m2"]["m2_score"] <= 1 for r in results)


def test_missing_point_in_time_assumption_is_controlled_reject():
    results = evaluate_bank_batch(
        {"BNK1": canonical()},
        {},
        {"BNK1": BankM2Context(current_price=5.5)},
    )
    assert results == [{
        "ticker": "BNK1",
        "status": "YETERSIZ_VERI",
        "reason": "POINT_IN_TIME_ASSUMPTION_MISSING",
        "analysis_at": ANALYSIS,
        "anchor_period_end": ANCHOR,
    }]


class Cursor:
    def __init__(self, rows, names):
        self.rows = rows
        self.description = [(name,) for name in names]
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Conn:
    def __init__(self, cursor):
        self.cur = cursor

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_batch_slot_fetch_is_one_query_and_groups_tickers():
    names = [
        "ticker", "period_end", "record_id", "selected_version_tag",
        "selected_version_sequence", "selected_published_at", "roe_ttm", "bvps", "payout_sus",
    ]
    rows = []
    for ticker in ("AAA", "BBB"):
        for i, slot in enumerate(build_quarter_slots(ANCHOR)):
            rows.append((ticker, slot, None, None, None, None, None, None, None))
    cur = Cursor(rows, names)
    grouped = fetch_bank_quarter_slots_batch(
        Conn(cur), tickers=["aaa", "bbb"], analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
    )
    assert list(grouped) == ["AAA", "BBB"]
    assert len(grouped["AAA"]) == 8 and len(grouped["BBB"]) == 8
    assert "unnest(%(tickers)s::text[])" in cur.sql
    assert cur.params["tickers"] == ["AAA", "BBB"]


def test_bank_universe_does_not_route_broad_financial_index_to_bank_motor():
    cur = Cursor([("GARAN",), ("AKBNK",)], ["ticker"])
    tickers = fetch_active_bank_tickers(Conn(cur))
    assert tickers == ["GARAN", "AKBNK"]
    assert "= 'XBANK'" in cur.sql
    assert "XUMAL" not in cur.sql


def test_bank_m2_persistence_keeps_score_inputs_separate():
    valuation = run_bank_valuation(
        canonical(),
        BankValuationInputs(coe=COE, macro_cap=MACRO, band_width_shadow_mode=False),
    )
    m2_result = compute_bank_m2_score(
        valuation,
        BankM2Context(current_price=5.5, s_lag_effective=0.7, lag_active=True),
    )
    cur = Cursor([], [])
    persist_bank_m2_score(Conn(cur), m2_result)
    assert "ON CONFLICT (ticker, analysis_at, anchor_period_end)" in cur.sql
    assert '"s_val_effective"' in cur.params["score_inputs"]
    assert '"z_val"' not in cur.params["score_inputs"]
    assert '"z_val"' in cur.params["diagnostics"]


def test_migration_contains_assumption_m2_and_module_traceability():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "sql" / "013_bank_batch_m2_integration.sql").read_text().lower()
    assert "bank_valuation_assumptions" in sql
    assert "bank_m2_scores" in sql
    assert "m2_source" in sql
    assert "m2_score_inputs" in sql
    assert "effective_at timestamptz" in sql
    for constraint in (
        "ck_bank_assumption_rf",
        "ck_bank_assumption_payload",
        "ck_bank_metrics_period_before_publish",
        "ck_bank_valuation_assumption_trace",
        "ck_bank_valuation_point_in_time_trace",
        "ck_bank_valuation_json_payloads",
        "ck_bank_m2_local_asof",
        "ck_bank_m2_daily_close_cutoff",
        "ck_bank_m2_json_payloads",
    ):
        assert constraint in sql


class QueueConn:
    def __init__(self, cursors):
        self.cursors = list(cursors)

    def cursor(self):
        return self.cursors.pop(0)


def test_resolver_prefers_latest_ticker_override_over_bank_default():
    from src.analytics.bank_batch_pipeline import resolve_bank_assumptions

    names = [
        "scope_type", "scope_code", "effective_at", "coe", "macro_cap",
        "risk_free_rate",
        "tier_cap", "payout_missing_factor", "band_width_shadow_mode",
        "max_halfwidth", "source", "metadata",
    ]
    rows = [
        ("TICKER", "GARAN", datetime(2026, 2, 1, tzinfo=TZ), 0.36, 0.14, 0.29, 0.8, 0.7, True, 0.8, "OVERRIDE", {}),
        ("BANK", "BANK", datetime(2026, 1, 1, tzinfo=TZ), 0.37, 0.14, 0.30, 0.8, 0.7, True, 0.8, "DEFAULT", {}),
    ]
    cur = Cursor(rows, names)
    resolved, missing = resolve_bank_assumptions(
        QueueConn([cur]), tickers=["garan", "akbnk"], analysis_at=ANALYSIS,
    )
    assert not missing
    assert resolved["GARAN"].inputs.coe == pytest.approx(0.36)
    assert resolved["GARAN"].risk_free_rate == pytest.approx(0.29)
    assert resolved["GARAN"].source == "OVERRIDE"
    assert resolved["AKBNK"].inputs.coe == pytest.approx(0.37)
    assert resolved["AKBNK"].risk_free_rate == pytest.approx(0.30)
    assert "effective_at <= %(analysis_at)s" in cur.sql


def test_market_context_uses_previous_day_before_close():
    from src.analytics.bank_batch_pipeline import fetch_bank_m2_contexts

    cur = Cursor(
        [("GARAN", date(2026, 8, 3), Decimal("100.5"), Decimal("0.65"))],
        ["ticker", "price_trade_date", "current_price", "m2_follow_score"],
    )
    contexts = fetch_bank_m2_contexts(
        QueueConn([cur]),
        tickers=["GARAN"],
        analysis_at=datetime(2026, 8, 4, 12, 0, tzinfo=TZ),
    )
    assert cur.params["asof_date"] == date(2026, 8, 3)
    assert contexts["GARAN"].price_trade_date == date(2026, 8, 3)
    assert contexts["GARAN"].s_lag_effective == pytest.approx(0.65)
    assert contexts["GARAN"].lag_active is True


def test_one_invalid_m2_context_does_not_abort_other_banks():
    canonicals = {"AAA": canonical("AAA"), "BBB": canonical("BBB", bump=0.001)}
    assumptions = {ticker: assumption() for ticker in canonicals}
    contexts = {
        "AAA": BankM2Context(current_price=5.5, s_lag_effective=2.0, lag_active=True),
        "BBB": BankM2Context(current_price=5.5, s_lag_effective=0.7, lag_active=True),
    }
    results = {r["ticker"]: r for r in evaluate_bank_batch(canonicals, assumptions, contexts)}
    assert results["AAA"]["m2"] is None
    assert "s_lag_effective" in results["AAA"]["m2_error"]
    assert results["BBB"]["m2"] is not None


def test_m2_context_rejects_daily_close_not_yet_available_at_analysis_time():
    valuation = run_bank_valuation(
        canonical(),
        BankValuationInputs(coe=COE, macro_cap=MACRO, band_width_shadow_mode=False),
    )
    with pytest.raises(CanonicalizationError, match="daily close kesimini asiyor"):
        compute_bank_m2_score(
            valuation,
            BankM2Context(
                current_price=5.5,
                price_trade_date=ANALYSIS.date(),
                price_source="DAILY_CLOSE",
            ),
        )


def test_m2_context_rejects_negative_price_even_when_valuation_is_unusable():
    valuation = {
        "ticker": "BNK1",
        "analysis_at": ANALYSIS,
        "anchor_period_end": ANCHOR,
        "status": "YETERSIZ_VERI",
        "reason": "TEST",
        "v_conf": None,
    }
    with pytest.raises(CanonicalizationError, match="current_price"):
        compute_bank_m2_score(valuation, BankM2Context(current_price=-1.0))


@pytest.mark.parametrize("field", ["price_source", "lag_source"])
def test_m2_context_sources_must_be_nonempty_text(field):
    valuation = run_bank_valuation(
        canonical(),
        BankValuationInputs(coe=COE, macro_cap=MACRO, band_width_shadow_mode=False),
    )
    kwargs = {"current_price": 5.5, field: "  "}
    with pytest.raises(CanonicalizationError, match=field):
        compute_bank_m2_score(valuation, BankM2Context(**kwargs))
