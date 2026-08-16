from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.analytics.nonfin_valuation import (
    NonfinValuationConfig,
    NonfinValuationError,
    build_nonfin_snapshot,
    combine_nonfin_m2,
    evaluate_nonfin_batch,
    value_nonfin_snapshot,
)

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
PERIODS = [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]


def config(**patch):
    payload = {
        "valuation_profile": "T",
        "valuation_version": 1,
        "source_derivation_profile": "DERIVE_T",
        "source_derivation_version": 1,
        "multiple_weights": {"PE": 0.30, "EV_EBIT": 0.30, "PS": 0.20, "PB": 0.20},
        "lower_quantile": 0.25,
        "upper_quantile": 0.75,
        "minimum_peer_count": 3,
        "full_confidence_peer_count": 5,
        "minimum_coverage_weight": 0.50,
        "max_halfwidth": 1.25,
        "band_width_shadow_mode": True,
        "valuation_axis_weight": 0.60,
        "follow_axis_weight": 0.40,
    }
    payload.update(patch)
    return NonfinValuationConfig.from_dict(payload)


def quarters(scale=1.0, *, missing=None):
    rows = []
    for idx, period in enumerate(PERIODS, start=1):
        row = {
            "period_end": period,
            "revenue": 100.0 * scale + idx,
            "ebit": 15.0 * scale + idx,
            "net_income": 10.0 * scale + idx,
            "total_equity": 300.0 * scale,
            "debt_st": 20.0,
            "debt_lt": 40.0,
            "cash_and_eq": 25.0,
            "st_investments": 5.0,
            "shares_out": 100.0,
        }
        if missing:
            row[missing] = None
        rows.append(row)
    return rows


def snap(ticker, price, scale=1.0, sector="XUSIN", missing=None):
    return build_nonfin_snapshot(
        ticker=ticker,
        analysis_at=ANALYSIS,
        sector_code=sector,
        current_price=price,
        price_trade_date=date(2026, 8, 5),
        quarters=quarters(scale, missing=missing),
    )


def peer_set():
    return [
        snap("P1", 6.0, 0.8), snap("P2", 8.0, 0.9), snap("P3", 10.0, 1.0),
        snap("P4", 12.0, 1.1), snap("P5", 14.0, 1.2),
    ]


def test_snapshot_requires_four_contiguous_quarters():
    broken = quarters()
    broken[1]["period_end"] = date(2025, 6, 30)
    with pytest.raises(NonfinValuationError, match="bitisik"):
        build_nonfin_snapshot(
            ticker="AAA", analysis_at=ANALYSIS, sector_code="XUSIN",
            current_price=10, price_trade_date=date(2026, 8, 5), quarters=broken,
        )


def test_snapshot_does_not_compress_missing_ttm_values():
    snapshot = snap("AAA", 10, missing="net_income")
    assert snapshot.net_income_ttm is None
    assert "PE" not in snapshot.multiples()
    assert "PS" in snapshot.multiples()


def test_leave_one_out_target_is_rejected_in_peer_list():
    target = snap("AAA", 10)
    with pytest.raises(NonfinValuationError, match="leave-one-out"):
        value_nonfin_snapshot(target, [target] + peer_set(), config())


def test_relative_valuation_produces_ordered_band_and_confidence():
    out = value_nonfin_snapshot(snap("AAA", 7.0), peer_set(), config())
    assert out["status"] == "OK"
    assert 0 < out["V_low"] <= out["V_mid"] <= out["V_high"]
    assert 0 < out["v_conf"] <= 1
    assert 0 <= out["valuation_score"] <= 1
    assert out["coverage_weight"] == pytest.approx(1.0)
    assert set(out["diagnostics"]["aggregation"]["usable_multiples"]) == {"PE", "EV_EBIT", "PS", "PB"}


def test_cheaper_price_scores_higher_for_same_fundamentals():
    cheap = value_nonfin_snapshot(snap("AAA", 5.0), peer_set(), config())
    expensive = value_nonfin_snapshot(snap("AAA", 20.0), peer_set(), config())
    assert cheap["valuation_score"] > expensive["valuation_score"]


def test_insufficient_peer_coverage_fails_closed():
    out = value_nonfin_snapshot(snap("AAA", 8.0), peer_set()[:2], config(minimum_peer_count=3))
    assert out["status"] == "YETERSIZ_VERI"
    assert out["V_mid"] is None
    assert out["valuation_score"] == 0.5
    assert out["v_conf"] == 0.0


def test_too_wide_band_can_be_shadow_or_hard_gate():
    shadow = value_nonfin_snapshot(snap("AAA", 8.0), peer_set(), config(max_halfwidth=0.0001, band_width_shadow_mode=True))
    hard = value_nonfin_snapshot(snap("AAA", 8.0), peer_set(), config(max_halfwidth=0.0001, band_width_shadow_mode=False))
    assert shadow["status"] == "OK"
    assert shadow["diagnostics"]["aggregation"]["shadow_too_wide"] is True
    assert hard["status"] == "BAND_TOO_WIDE"
    assert hard["valuation_score"] == 0.5
    assert hard["v_conf"] == 0.0


def test_m2_confidence_shrinks_valuation_axis_to_neutral():
    valuation = value_nonfin_snapshot(snap("AAA", 5.0), peer_set(), config())
    confident = combine_nonfin_m2(valuation, follow_score=0.4, follow_active=True, config=config())
    no_conf = combine_nonfin_m2({**valuation, "v_conf": 0.0}, follow_score=0.4, follow_active=True, config=config())
    assert confident["m2"] > no_conf["m2"]
    assert no_conf["score_inputs"]["valuation_score_effective"] == pytest.approx(0.5)


def test_batch_is_order_independent_and_leave_one_out():
    items = [snap("AAA", 7, 1.0)] + peer_set()
    contexts = {item.ticker: {"follow_score": 0.5, "follow_active": True} for item in items}
    a = evaluate_nonfin_batch(items, config=config(), follow_contexts=contexts)
    b = evaluate_nonfin_batch(list(reversed(items)), config=config(), follow_contexts=contexts)
    assert [(r["ticker"], r["m2"]["m2"]) for r in a["results"]] == [
        (r["ticker"], r["m2"]["m2"]) for r in b["results"]
    ]
    aaa = next(row for row in a["results"] if row["ticker"] == "AAA")
    assert "AAA" not in aaa["valuation"]["diagnostics"]["peer_tickers"]


def test_batch_rejects_mixed_sector_group():
    target = snap("AAA", 7, sector="XUSIN")
    peer = snap("BBB", 8, sector="XUHIZ")
    out = evaluate_nonfin_batch([target, peer], config=config())
    assert all(row["valuation"]["status"] == "YETERSIZ_VERI" for row in out["results"])


@pytest.mark.parametrize(
    "patch",
    [
        {"valuation_version": True},
        {"multiple_weights": []},
        {"multiple_weights": {"PE": 1.1}},
        {"lower_quantile": 0.6},
        {"minimum_peer_count": 0},
        {"full_confidence_peer_count": 2},
        {"band_width_shadow_mode": 1},
        {"valuation_axis_weight": 0.8, "follow_axis_weight": 0.4},
        {"unexpected": 1},
    ],
)
def test_config_rejects_invalid_contract(patch):
    with pytest.raises(NonfinValuationError):
        config(**patch)


def test_checked_in_config_is_valid_and_stable():
    cfg = NonfinValuationConfig.from_json_file("config/nonfin_valuation.relative_v1.json")
    assert cfg.valuation_profile == "NONFIN_RELATIVE_MULTIPLES"
    assert len(cfg.config_sha256) == 64


def test_fake_quarter_end_is_rejected():
    broken = quarters()
    broken[-1]["period_end"] = date(2026, 6, 29)
    with pytest.raises(NonfinValuationError, match="ceyrek sonu"):
        build_nonfin_snapshot(
            ticker="AAA", analysis_at=ANALYSIS, sector_code="XUSIN",
            current_price=10, price_trade_date=date(2026, 6, 29), quarters=broken,
        )


def test_direct_config_bypass_is_revalidated():
    valid = config()
    broken = NonfinValuationConfig(
        valuation_profile=valid.valuation_profile, valuation_version=valid.valuation_version,
        source_derivation_profile=valid.source_derivation_profile,
        source_derivation_version=valid.source_derivation_version,
        multiple_weights=[], lower_quantile=valid.lower_quantile, upper_quantile=valid.upper_quantile,
        minimum_peer_count=valid.minimum_peer_count,
        full_confidence_peer_count=valid.full_confidence_peer_count,
        minimum_coverage_weight=valid.minimum_coverage_weight, max_halfwidth=valid.max_halfwidth,
        band_width_shadow_mode=valid.band_width_shadow_mode,
        valuation_axis_weight=valid.valuation_axis_weight, follow_axis_weight=valid.follow_axis_weight,
        max_price_age_days=valid.max_price_age_days,
    )
    with pytest.raises(NonfinValuationError, match="multiple_weights"):
        value_nonfin_snapshot(snap("AAA", 7), peer_set(), broken)


def test_direct_snapshot_bypass_is_revalidated():
    valid = snap("AAA", 7)
    broken = type(valid)(
        ticker=valid.ticker, analysis_at=valid.analysis_at, anchor_period_end=date(2026, 6, 29),
        sector_code=valid.sector_code, current_price=valid.current_price,
        price_trade_date=valid.price_trade_date, revenue_ttm=valid.revenue_ttm,
        ebit_ttm=valid.ebit_ttm, net_income_ttm=valid.net_income_ttm,
        total_equity=valid.total_equity, net_debt=valid.net_debt, shares_out=valid.shares_out,
    )
    with pytest.raises(NonfinValuationError, match="ceyrek sonu"):
        value_nonfin_snapshot(broken, peer_set(), config())


def test_stale_target_fails_and_stale_peer_is_excluded():
    valid = snap("AAA", 7)
    stale_target = type(valid)(**{**valid.__dict__, "price_trade_date": date(2026, 7, 1)})
    out = value_nonfin_snapshot(stale_target, peer_set(), config(max_price_age_days=7))
    assert out["status"] == "YETERSIZ_VERI"
    assert out["reason"] == "HEDEF_FIYAT_BAYAT"

    peers = peer_set()
    peers[0] = type(peers[0])(**{**peers[0].__dict__, "price_trade_date": date(2026, 7, 1)})
    out = value_nonfin_snapshot(valid, peers, config(max_price_age_days=7, minimum_peer_count=3))
    assert "P1" in out["diagnostics"]["stale_peer_tickers"]
    assert "P1" not in out["diagnostics"]["peer_tickers"]


def test_utc_analysis_uses_istanbul_calendar_date():
    utc = ZoneInfo("UTC")
    snapshot = build_nonfin_snapshot(
        ticker="AAA", analysis_at=datetime(2026, 8, 4, 21, 30, tzinfo=utc),
        sector_code="XUSIN", current_price=7, price_trade_date=date(2026, 8, 5),
        quarters=quarters(),
    )
    assert snapshot.price_trade_date == date(2026, 8, 5)
