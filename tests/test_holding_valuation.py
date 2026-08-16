from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.analytics.holding_valuation import (
    HoldingSnapshot,
    HoldingValuationConfig,
    HoldingValuationError,
    build_holding_snapshot,
    combine_holding_m2,
    evaluate_holding_batch,
    value_holding_snapshot,
)

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
SHA = "a" * 64


def config(**patch):
    payload = {
        "valuation_profile": "TEST_HOLDING_NAV",
        "valuation_version": 1,
        "source_nav_profile": "HOLDING_ADJUSTED_NAV",
        "source_nav_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "lower_quantile": 0.25,
        "upper_quantile": 0.75,
        "minimum_peer_count": 3,
        "full_confidence_peer_count": 5,
        "minimum_discount": -0.50,
        "maximum_discount": 0.90,
        "max_nav_age_days": 370,
        "full_freshness_days": 120,
        "max_price_age_days": 7,
        "minimum_source_confidence": 0.40,
        "max_halfwidth": 1.25,
        "band_width_shadow_mode": True,
        "valuation_axis_weight": 0.65,
        "follow_axis_weight": 0.35,
    }
    payload.update(patch)
    return HoldingValuationConfig.from_dict(payload)


def snap(
    ticker: str,
    *,
    nav_total: float = 1000.0,
    shares: float = 100.0,
    price: float = 6.0,
    group: str = "XHOLD",
    currency: str = "TRY",
    nav_date: date = date(2026, 6, 30),
    publish: datetime = datetime(2026, 7, 20, 10, 0, tzinfo=TZ),
    price_date: date = date(2026, 8, 5),
    confidence: float = 0.9,
    profile: str = "HOLDING_ADJUSTED_NAV",
    version: int = 1,
):
    return build_holding_snapshot(
        ticker=ticker,
        analysis_at=ANALYSIS,
        peer_group=group,
        currency=currency,
        share_basis="ADJUSTED_PRICE_SERIES_V1",
        current_price=price,
        price_trade_date=price_date,
        nav_asof_date=nav_date,
        nav_published_at=publish,
        nav_total=nav_total,
        shares_out=shares,
        source_confidence=confidence,
        source_document_id=f"DOC-{ticker}",
        source_sha256=SHA,
        nav_profile=profile,
        nav_version=version,
    )


def peers():
    # discounts: 20%, 30%, 40%, 50%, 60%
    return [
        snap("P1", price=8.0),
        snap("P2", price=7.0),
        snap("P3", price=6.0),
        snap("P4", price=5.0),
        snap("P5", price=4.0),
    ]


def test_snapshot_computes_nav_and_discount():
    s = snap("AAA", nav_total=1200, shares=100, price=9)
    assert s.nav_per_share == pytest.approx(12)
    assert s.current_discount == pytest.approx(0.25)


def test_snapshot_rejects_future_publication_and_fake_sha():
    with pytest.raises(HoldingValuationError, match="analysis_at sonrasinda"):
        snap("AAA", publish=datetime(2026, 8, 6, 10, 0, tzinfo=TZ))
    with pytest.raises(HoldingValuationError, match="SHA256"):
        build_holding_snapshot(
            ticker="AAA", analysis_at=ANALYSIS, peer_group="XHOLD", currency="TRY",
            share_basis="ADJUSTED_PRICE_SERIES_V1", current_price=6,
            price_trade_date=date(2026, 8, 5), nav_asof_date=date(2026, 6, 30),
            nav_published_at=datetime(2026, 7, 20, 10, 0, tzinfo=TZ), nav_total=1000,
            shares_out=100, source_confidence=0.9, source_document_id="DOC",
            source_sha256="bad", nav_profile="HOLDING_ADJUSTED_NAV", nav_version=1,
        )


def test_leave_one_out_target_is_rejected():
    target = snap("AAA")
    with pytest.raises(HoldingValuationError, match="leave-one-out"):
        value_holding_snapshot(target, [target] + peers(), config())


def test_nav_discount_valuation_produces_expected_band():
    out = value_holding_snapshot(snap("AAA", price=4.5), peers(), config())
    assert out["status"] == "OK"
    assert out["V_low"] == pytest.approx(5.0)
    assert out["V_mid"] == pytest.approx(6.0)
    assert out["V_high"] == pytest.approx(7.0)
    assert out["target_discount"] == pytest.approx(0.4)
    assert 0 < out["v_conf"] <= 1
    assert out["valuation_score"] > 0.5


def test_cheaper_price_scores_higher_for_same_nav():
    cheap = value_holding_snapshot(snap("AAA", price=3.5), peers(), config())
    expensive = value_holding_snapshot(snap("AAA", price=8.5), peers(), config())
    assert cheap["valuation_score"] > expensive["valuation_score"]


def test_insufficient_peers_fails_closed():
    out = value_holding_snapshot(snap("AAA"), peers()[:2], config(minimum_peer_count=3))
    assert out["status"] == "YETERSIZ_VERI"
    assert out["reason"] == "YETERSIZ_HOLDING_EMSALI"
    assert out["V_mid"] is None
    assert out["v_conf"] == 0.0


def test_stale_target_nav_and_price_fail_closed():
    stale_nav = value_holding_snapshot(
        snap("AAA", nav_date=date(2024, 12, 31)), peers(), config(max_nav_age_days=200)
    )
    stale_price = value_holding_snapshot(
        snap("AAA", price_date=date(2026, 7, 1)), peers(), config(max_price_age_days=7)
    )
    assert stale_nav["reason"] == "HEDEF_NAV_BAYAT"
    assert stale_price["reason"] == "HEDEF_FIYAT_BAYAT"


def test_low_source_confidence_target_fails_and_peer_is_excluded():
    target = value_holding_snapshot(snap("AAA", confidence=0.2), peers(), config())
    assert target["reason"] == "HEDEF_NAV_KAYNAK_GUVENI_DUSUK"
    p = peers()
    p[0] = snap("P1", price=8, confidence=0.2)
    out = value_holding_snapshot(snap("AAA"), p, config(minimum_peer_count=3))
    assert out["status"] == "OK"
    assert out["diagnostics"]["excluded_peers"]["P1"] == "NAV_KAYNAK_GUVENI_DUSUK"


def test_out_of_model_discount_peer_is_excluded():
    p = peers()
    p[0] = snap("P1", price=20.0)  # -100% premium, outside configured floor
    out = value_holding_snapshot(snap("AAA"), p, config(minimum_peer_count=3))
    assert out["status"] == "OK"
    assert out["diagnostics"]["excluded_peers"]["P1"] == "ISKONTO_MODEL_ARALIGI_DISINDA"


def test_mixed_peer_group_is_rejected():
    with pytest.raises(HoldingValuationError, match="peer_group"):
        value_holding_snapshot(snap("AAA"), [snap("BBB", group="OTHER")] + peers(), config())


def test_mismatched_nav_profile_is_excluded_but_target_mismatch_rejected():
    p = peers()
    p[0] = snap("P1", profile="OTHER")
    out = value_holding_snapshot(snap("AAA"), p, config(minimum_peer_count=3))
    assert out["diagnostics"]["excluded_peers"]["P1"] == "NAV_PROFILE_MISMATCH"
    with pytest.raises(HoldingValuationError, match="profil"):
        value_holding_snapshot(snap("AAA", profile="OTHER"), peers(), config())


def test_too_wide_band_shadow_or_hard_gate():
    wide_peers = [
        snap("P1", price=14.0), snap("P2", price=12.0), snap("P3", price=8.0),
        snap("P4", price=4.0), snap("P5", price=1.0),
    ]
    shadow = value_holding_snapshot(snap("AAA"), wide_peers, config(max_halfwidth=0.01, band_width_shadow_mode=True))
    hard = value_holding_snapshot(snap("AAA"), wide_peers, config(max_halfwidth=0.01, band_width_shadow_mode=False))
    assert shadow["status"] == "OK"
    assert shadow["diagnostics"]["aggregation"]["shadow_too_wide"] is True
    assert hard["status"] == "BAND_TOO_WIDE"
    assert hard["valuation_score"] == 0.5
    assert hard["v_conf"] == 0.0


def test_m2_confidence_shrinks_valuation_axis_to_neutral():
    valuation = value_holding_snapshot(snap("AAA", price=4), peers(), config())
    confident = combine_holding_m2(valuation, follow_score=0.4, follow_active=True, config=config())
    neutral = combine_holding_m2({**valuation, "v_conf": 0.0}, follow_score=0.4, follow_active=True, config=config())
    assert confident["m2"] > neutral["m2"]
    assert neutral["score_inputs"]["valuation_score_effective"] == pytest.approx(0.5)


def test_batch_is_order_independent_and_leave_one_out():
    items = [snap("AAA", price=4.5)] + peers()
    contexts = {item.ticker: {"follow_score": 0.5, "follow_active": True} for item in items}
    a = evaluate_holding_batch(items, config=config(), follow_contexts=contexts)
    b = evaluate_holding_batch(list(reversed(items)), config=config(), follow_contexts=contexts)
    assert [(r["ticker"], r["m2"]["m2"]) for r in a["results"]] == [
        (r["ticker"], r["m2"]["m2"]) for r in b["results"]
    ]
    aaa = next(row for row in a["results"] if row["ticker"] == "AAA")
    assert "AAA" not in aaa["valuation"]["diagnostics"]["peer_tickers"]


def test_direct_config_and_snapshot_bypass_are_revalidated():
    valid = config()
    broken_config = HoldingValuationConfig(**{**valid.__dict__, "minimum_peer_count": 0})
    with pytest.raises(HoldingValuationError, match="minimum_peer_count"):
        value_holding_snapshot(snap("AAA"), peers(), broken_config)
    valid_snap = snap("AAA")
    broken_snap = HoldingSnapshot(**{**valid_snap.__dict__, "source_sha256": "bad"})
    with pytest.raises(HoldingValuationError, match="SHA256"):
        value_holding_snapshot(broken_snap, peers(), valid)


@pytest.mark.parametrize(
    "patch",
    [
        {"valuation_version": True},
        {"source_nav_version": 0},
        {"lower_quantile": 0.6},
        {"minimum_peer_count": 0},
        {"full_confidence_peer_count": 2},
        {"minimum_discount": 0.9, "maximum_discount": 0.5},
        {"full_freshness_days": 400, "max_nav_age_days": 300},
        {"band_width_shadow_mode": 1},
        {"valuation_axis_weight": 0.8, "follow_axis_weight": 0.4},
        {"unexpected": 1},
    ],
)
def test_config_rejects_invalid_contract(patch):
    with pytest.raises(HoldingValuationError):
        config(**patch)


def test_checked_in_config_is_valid_and_stable():
    cfg = HoldingValuationConfig.from_json_file("config/holding_valuation.nav_discount_v1.json")
    assert cfg.valuation_profile == "HOLDING_NAV_DISCOUNT"
    assert len(cfg.config_sha256) == 64


def test_currency_mismatch_fails_closed_and_peer_is_excluded():
    target = value_holding_snapshot(snap("AAA", currency="USD"), peers(), config(currency="TRY"))
    assert target["status"] == "YETERSIZ_VERI"
    assert target["reason"] == "HEDEF_NAV_PARA_BIRIMI_UYUSMUYOR"

    p = peers()
    p[0] = snap("P1", price=8.0, currency="USD")
    out = value_holding_snapshot(snap("AAA"), p, config(currency="TRY", minimum_peer_count=3))
    assert out["status"] == "OK"
    assert out["diagnostics"]["excluded_peers"]["P1"] == "CURRENCY_MISMATCH"


def test_config_rejects_structured_currency():
    with pytest.raises(HoldingValuationError, match="currency"):
        config(currency=["TRY"])


def test_share_basis_mismatch_fails_closed_and_peer_is_excluded():
    target = value_holding_snapshot(
        snap("AAA"), peers(), config(share_basis="UNADJUSTED_AS_REPORTED")
    )
    assert target["status"] == "YETERSIZ_VERI"
    assert target["reason"] == "HEDEF_PAY_BAZI_UYUSMUYOR"

    p = peers()
    p[0] = build_holding_snapshot(**{
        **p[0].__dict__, "share_basis": "UNADJUSTED_AS_REPORTED"
    })
    out = value_holding_snapshot(snap("AAA"), p, config(minimum_peer_count=3))
    assert out["status"] == "OK"
    assert out["diagnostics"]["excluded_peers"]["P1"] == "SHARE_BASIS_MISMATCH"


def test_config_requires_explicit_share_basis():
    with pytest.raises(HoldingValuationError, match="share_basis"):
        HoldingValuationConfig.from_dict({
            "valuation_profile": "X", "valuation_version": 1,
            "source_nav_profile": "Y", "source_nav_version": 1,
        })
    with pytest.raises(HoldingValuationError, match="share_basis"):
        config(share_basis=["ADJUSTED_PRICE_SERIES_V1"])
