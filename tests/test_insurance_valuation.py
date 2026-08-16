from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.analytics.insurance_valuation import (
    InsuranceSnapshot,
    InsuranceValuationConfig,
    InsuranceValuationError,
    build_insurance_snapshot,
    combine_insurance_m2,
    evaluate_insurance_batch,
    value_insurance_snapshot,
)

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
SHA = "a" * 64


def cfg(**patch):
    data = {
        "valuation_profile": "INSURANCE_PB_PE",
        "valuation_version": 1,
        "source_metrics_profile": "KAP_INSURANCE_TTM",
        "source_metrics_version": 1,
        "accounting_profile": "TFRS17_LOCAL_STATUTORY",
        "accounting_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "minimum_peer_count": 2,
        "full_confidence_peer_count": 4,
    }
    data.update(patch)
    return InsuranceValuationConfig.from_dict(data)


def snap(
    ticker,
    *,
    price=10.0,
    equity=1000.0,
    income=100.0,
    premiums=1000.0,
    technical=100.0,
    investment=20.0,
    shares=100.0,
    business="NON_LIFE",
    period=date(2026, 6, 30),
    price_date=date(2026, 8, 5),
    published=datetime(2026, 7, 20, 10, 0, tzinfo=TZ),
    confidence=0.9,
    earned=900.0,
    claims=540.0,
    expenses=180.0,
    accounting="TFRS17_LOCAL_STATUTORY",
    metrics="KAP_INSURANCE_TTM",
    currency="TRY",
    basis="ADJUSTED_PRICE_SERIES_V1",
):
    if business == "LIFE_PENSION":
        earned = claims = expenses = None
    return build_insurance_snapshot(
        ticker=ticker,
        analysis_at=ANALYSIS,
        business_type=business,
        currency=currency,
        share_basis=basis,
        current_price=price,
        price_trade_date=price_date,
        period_end=period,
        published_at=published,
        total_equity=equity,
        net_income_ttm=income,
        written_premiums_ttm=premiums,
        technical_result_ttm=technical,
        investment_income_ttm=investment,
        shares_out=shares,
        earned_premiums_ttm=earned,
        net_claims_ttm=claims,
        operating_expenses_ttm=expenses,
        solvency_ratio=1.6,
        source_confidence=confidence,
        source_document_id=f"DOC-{ticker}",
        source_sha256=SHA,
        metrics_profile=metrics,
        metrics_version=1,
        accounting_profile=accounting,
        accounting_version=1,
    )


def peers():
    # PB = 0.8, 1.0, 1.2, 1.4; PE = 8, 10, 12, 14
    return [snap(f"P{i}", price=p) for i, p in enumerate([8, 10, 12, 14], start=1)]


def test_snapshot_ratios_and_combined_ratio():
    item = snap("AAA")
    assert item.current_pb == pytest.approx(1.0)
    assert item.current_pe == pytest.approx(10.0)
    assert item.roe_ttm == pytest.approx(0.1)
    assert item.technical_margin == pytest.approx(0.1)
    assert item.combined_ratio == pytest.approx(0.8)


def test_pb_pe_valuation_band_is_geometric_combination():
    out = value_insurance_snapshot(snap("AAA", price=8), peers(), cfg())
    assert out["status"] == "OK"
    assert out["method_count"] == 2
    assert out["V_low"] > 0
    assert out["V_low"] <= out["V_mid"] <= out["V_high"]
    assert out["target_pb"] == pytest.approx(1.1)
    assert out["target_pe"] == pytest.approx(11.0)
    assert out["valuation_score"] > 0.5
    assert 0 < out["v_conf"] <= 1


def test_negative_income_uses_pb_only():
    target = snap("AAA", income=-20)
    out = value_insurance_snapshot(target, peers(), cfg())
    assert out["status"] == "OK"
    assert out["method_count"] == 1
    assert out["target_pe"] is None


def test_minimum_two_methods_can_reject_negative_income():
    out = value_insurance_snapshot(snap("AAA", income=-20), peers(), cfg(minimum_method_count=2))
    assert out["status"] == "YETERSIZ_VERI"
    assert out["reason"] == "YETERSIZ_DEGERLEME_YONTEMI"


def test_insufficient_peers_fails_closed():
    out = value_insurance_snapshot(snap("AAA"), peers()[:1], cfg(minimum_peer_count=2))
    assert out["status"] == "YETERSIZ_VERI"
    assert out["reason"] == "YETERSIZ_SIGORTA_EMSALI"
    assert out["V_mid"] is None


def test_stale_price_statement_and_low_confidence_fail_closed():
    assert value_insurance_snapshot(snap("AAA", price_date=date(2026, 7, 1)), peers(), cfg())["reason"] == "HEDEF_FIYAT_BAYAT"
    assert value_insurance_snapshot(snap("AAA", period=date(2025, 9, 30)), peers(), cfg())["reason"] == "HEDEF_FINANSAL_BILGI_BAYAT"
    assert value_insurance_snapshot(snap("AAA", confidence=0.1), peers(), cfg())["reason"] == "HEDEF_KAYNAK_GUVENI_DUSUK"


def test_accounting_currency_share_basis_and_profile_contracts():
    assert value_insurance_snapshot(snap("AAA", accounting="OLD"), peers(), cfg())["reason"] == "HEDEF_MUHASEBE_PROFILI_UYUSMUYOR"
    assert value_insurance_snapshot(snap("AAA", currency="USD"), peers(), cfg())["reason"] == "HEDEF_PARA_BIRIMI_UYUSMUYOR"
    assert value_insurance_snapshot(snap("AAA", basis="RAW"), peers(), cfg())["reason"] == "HEDEF_PAY_BAZI_UYUSMUYOR"
    with pytest.raises(InsuranceValuationError, match="profil"):
        value_insurance_snapshot(snap("AAA", metrics="OTHER"), peers(), cfg())


def test_life_and_non_life_never_mix():
    target = snap("AAA", business="LIFE_PENSION")
    with pytest.raises(InsuranceValuationError, match="business_type"):
        value_insurance_snapshot(target, peers(), cfg())


def test_period_mismatch_is_excluded_not_compressed():
    mixed = peers() + [snap("OLD", period=date(2026, 3, 31))]
    out = value_insurance_snapshot(snap("AAA"), mixed, cfg())
    assert out["status"] == "OK"
    assert out["diagnostics"]["excluded_peers"]["OLD"] == "PERIOD_MISMATCH"


def test_leave_one_out_and_duplicate_peer_rejected():
    target = snap("AAA")
    with pytest.raises(InsuranceValuationError, match="leave-one-out"):
        value_insurance_snapshot(target, [target] + peers(), cfg())
    duplicate = peers()[:2] + [peers()[0]]
    with pytest.raises(InsuranceValuationError, match="yinelenen"):
        value_insurance_snapshot(target, duplicate, cfg())


def test_technical_quality_changes_confidence_not_band():
    good = value_insurance_snapshot(snap("AAA", technical=120, investment=10), peers(), cfg())
    weak = value_insurance_snapshot(snap("AAA", technical=-150, investment=300), peers(), cfg())
    assert good["V_mid"] == pytest.approx(weak["V_mid"])
    assert good["v_conf"] > weak["v_conf"]


def test_m2_shrinks_valuation_axis_by_confidence():
    val = value_insurance_snapshot(snap("AAA", price=8), peers(), cfg())
    good = combine_insurance_m2(val, follow_score=0.4, follow_active=True, config=cfg())
    neutral = combine_insurance_m2({**val, "v_conf": 0.0}, follow_score=0.4, follow_active=True, config=cfg())
    assert good["m2"] > neutral["m2"]
    assert neutral["score_inputs"]["valuation_score_effective"] == pytest.approx(0.5)


def test_batch_order_independent_and_grouped():
    items = [snap("AAA", price=8)] + peers()
    contexts = {item.ticker: {"follow_score": 0.5, "follow_active": True} for item in items}
    first = evaluate_insurance_batch(items, config=cfg(), follow_contexts=contexts)
    second = evaluate_insurance_batch(list(reversed(items)), config=cfg(), follow_contexts=contexts)
    assert [(x["ticker"], x["m2"]["m2"]) for x in first["results"]] == [
        (x["ticker"], x["m2"]["m2"]) for x in second["results"]
    ]


def test_direct_dataclass_bypass_revalidated():
    valid = cfg()
    broken = InsuranceValuationConfig(**{**valid.__dict__, "minimum_peer_count": 0})
    with pytest.raises(InsuranceValuationError, match="minimum_peer_count"):
        value_insurance_snapshot(snap("AAA"), peers(), broken)
    valid_snap = snap("AAA")
    broken_snap = InsuranceSnapshot(**{**valid_snap.__dict__, "source_sha256": "bad"})
    with pytest.raises(InsuranceValuationError, match="SHA256"):
        value_insurance_snapshot(broken_snap, peers(), valid)


def test_invalid_combined_ratio_contracts_and_life_fields():
    with pytest.raises(InsuranceValuationError, match="birlikte"):
        build_insurance_snapshot(**{**snap("AAA").__dict__, "net_claims_ttm": None})
    with pytest.raises(InsuranceValuationError, match="LIFE_PENSION"):
        build_insurance_snapshot(**{**snap("AAA").__dict__, "business_type": "LIFE_PENSION"})


@pytest.mark.parametrize("patch", [
    {"valuation_version": True},
    {"source_metrics_version": 0},
    {"accounting_version": 0},
    {"lower_quantile": 0.6},
    {"minimum_peer_count": 0},
    {"minimum_method_count": 3},
    {"minimum_pb": 2.0, "maximum_pb": 1.0},
    {"minimum_pe": 20.0, "maximum_pe": 10.0},
    {"pb_weight": 0.8, "pe_weight": 0.3},
    {"band_width_shadow_mode": 1},
    {"valuation_axis_weight": 0.8, "follow_axis_weight": 0.3},
    {"unexpected": 1},
])
def test_config_rejects_invalid_contract(patch):
    with pytest.raises(InsuranceValuationError):
        cfg(**patch)


def test_checked_in_config_loads_and_has_canonical_sha():
    config = InsuranceValuationConfig.from_json_file("config/insurance_valuation.pb_pe_v1.json")
    assert config.valuation_profile == "INSURANCE_PB_PE"
    assert config.accounting_profile == "TFRS17_LOCAL_STATUTORY"
    assert len(config.config_sha256) == 64
