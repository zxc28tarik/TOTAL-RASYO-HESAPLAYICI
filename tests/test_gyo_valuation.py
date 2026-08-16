from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.analytics.gyo_valuation import (
    GyoSnapshot,
    GyoValuationConfig,
    GyoValuationError,
    build_gyo_snapshot,
    combine_gyo_m2,
    evaluate_gyo_batch,
    value_gyo_snapshot,
)

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
SHA = "a" * 64


def cfg(**patch):
    data = {
        "valuation_profile": "GYO_PD_NAV",
        "valuation_version": 1,
        "source_nav_profile": "GYO_REPORTED_NAV",
        "source_nav_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "minimum_peer_count": 3,
        "full_confidence_peer_count": 5,
    }
    data.update(patch)
    return GyoValuationConfig.from_dict(data)


def snap(ticker, *, price=6.0, nav_total=1000.0, shares=100.0, group="GYO_MIXED", method="DIRECT",
         nav_date=date(2026, 6, 30), price_date=date(2026, 8, 5), confidence=0.9,
         currency="TRY", basis="ADJUSTED_PRICE_SERIES_V1", profile="GYO_REPORTED_NAV"):
    return build_gyo_snapshot(
        ticker=ticker, analysis_at=ANALYSIS, peer_group=group, currency=currency, share_basis=basis,
        current_price=price, price_trade_date=price_date, nav_asof_date=nav_date,
        nav_published_at=datetime(2026, 7, 20, 10, 0, tzinfo=TZ), nav_total=nav_total,
        shares_out=shares, property_portfolio_value=1300.0, nav_source_method=method,
        source_confidence=confidence, source_document_id=f"DOC-{ticker}", source_sha256=SHA,
        nav_profile=profile, nav_version=1,
    )


def peers():
    # PD/NAD: .4, .5, .6, .7, .8
    return [snap(f"P{i}", price=p) for i, p in enumerate([4, 5, 6, 7, 8], start=1)]


def test_snapshot_computes_nav_and_pd_nav():
    s = snap("AAA", price=9, nav_total=1200, shares=100)
    assert s.nav_per_share == pytest.approx(12)
    assert s.current_pd_nav == pytest.approx(0.75)


def test_valuation_produces_expected_pd_nav_band():
    out = value_gyo_snapshot(snap("AAA", price=4.5), peers(), cfg())
    assert out["status"] == "OK"
    assert out["V_low"] == pytest.approx(5.0)
    assert out["V_mid"] == pytest.approx(6.0)
    assert out["V_high"] == pytest.approx(7.0)
    assert out["target_pd_nav"] == pytest.approx(0.6)
    assert out["valuation_score"] > 0.5
    assert 0 < out["v_conf"] <= 1


def test_direct_source_has_more_confidence_than_derived():
    direct = value_gyo_snapshot(snap("AAA", method="DIRECT"), peers(), cfg())
    derived = value_gyo_snapshot(snap("AAA", method="DERIVED"), peers(), cfg())
    assert direct["v_conf"] > derived["v_conf"]


def test_insufficient_peers_fails_closed():
    out = value_gyo_snapshot(snap("AAA"), peers()[:2], cfg(minimum_peer_count=3))
    assert out["status"] == "YETERSIZ_VERI"
    assert out["reason"] == "YETERSIZ_GYO_EMSALI"
    assert out["V_mid"] is None


def test_stale_nav_and_price_fail_closed():
    assert value_gyo_snapshot(snap("AAA", nav_date=date(2025, 1, 1)), peers(), cfg())["reason"] == "HEDEF_NAV_BAYAT"
    assert value_gyo_snapshot(snap("AAA", price_date=date(2026, 7, 1)), peers(), cfg())["reason"] == "HEDEF_FIYAT_BAYAT"


def test_currency_share_basis_and_profile_contracts():
    assert value_gyo_snapshot(snap("AAA", currency="USD"), peers(), cfg())["reason"] == "HEDEF_NAV_PARA_BIRIMI_UYUSMUYOR"
    assert value_gyo_snapshot(snap("AAA", basis="RAW"), peers(), cfg())["reason"] == "HEDEF_PAY_BAZI_UYUSMUYOR"
    with pytest.raises(GyoValuationError, match="profil"):
        value_gyo_snapshot(snap("AAA", profile="OTHER"), peers(), cfg())


def test_leave_one_out_and_mixed_group_rejected():
    target = snap("AAA")
    with pytest.raises(GyoValuationError, match="leave-one-out"):
        value_gyo_snapshot(target, [target] + peers(), cfg())
    with pytest.raises(GyoValuationError, match="peer_group"):
        value_gyo_snapshot(target, [snap("P0", group="OTHER")] + peers(), cfg())


def test_m2_shrinks_valuation_axis_by_confidence():
    val = value_gyo_snapshot(snap("AAA", price=4), peers(), cfg())
    good = combine_gyo_m2(val, follow_score=0.4, follow_active=True, config=cfg())
    neutral = combine_gyo_m2({**val, "v_conf": 0.0}, follow_score=0.4, follow_active=True, config=cfg())
    assert good["m2"] > neutral["m2"]
    assert neutral["score_inputs"]["valuation_score_effective"] == pytest.approx(0.5)


def test_batch_order_independent():
    items = [snap("AAA", price=4.5)] + peers()
    contexts = {x.ticker: {"follow_score": 0.5, "follow_active": True} for x in items}
    a = evaluate_gyo_batch(items, config=cfg(), follow_contexts=contexts)
    b = evaluate_gyo_batch(list(reversed(items)), config=cfg(), follow_contexts=contexts)
    assert [(x["ticker"], x["m2"]["m2"]) for x in a["results"]] == [(x["ticker"], x["m2"]["m2"]) for x in b["results"]]


def test_direct_dataclass_bypass_revalidated():
    valid = cfg()
    broken = GyoValuationConfig(**{**valid.__dict__, "minimum_peer_count": 0})
    with pytest.raises(GyoValuationError, match="minimum_peer_count"):
        value_gyo_snapshot(snap("AAA"), peers(), broken)
    valid_snap = snap("AAA")
    broken_snap = GyoSnapshot(**{**valid_snap.__dict__, "source_sha256": "bad"})
    with pytest.raises(GyoValuationError, match="SHA256"):
        value_gyo_snapshot(broken_snap, peers(), valid)


@pytest.mark.parametrize("patch", [
    {"valuation_version": True}, {"source_nav_version": 0}, {"lower_quantile": 0.6},
    {"minimum_peer_count": 0}, {"minimum_pd_nav": 2.0, "maximum_pd_nav": 1.0},
    {"band_width_shadow_mode": 1}, {"valuation_axis_weight": .8, "follow_axis_weight": .3},
    {"unexpected": 1},
])
def test_config_rejects_invalid_contract(patch):
    with pytest.raises(GyoValuationError):
        cfg(**patch)


def test_checked_in_gyo_config_loads_and_has_canonical_sha():
    config = GyoValuationConfig.from_json_file("config/gyo_valuation.pd_nav_v1.json")
    assert config.valuation_profile == "GYO_PD_NAV"
    assert config.source_nav_profile == "GYO_REPORTED_NAV"
    assert len(config.config_sha256) == 64
