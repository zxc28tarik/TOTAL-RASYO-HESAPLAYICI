from __future__ import annotations

try:
    from scripts._repo_bootstrap import ensure_repo_root
except ModuleNotFoundError:
    from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

import json
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.analytics.insurance_valuation import (  # noqa: E402
    InsuranceSnapshot,
    InsuranceValuationConfig,
    InsuranceValuationError,
    build_insurance_snapshot,
    evaluate_insurance_batch,
    value_insurance_snapshot,
)
from src.ingest.insurance_metrics import InsuranceMetricsIngestError, InsuranceMetricsRecord  # noqa: E402

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
SHA = "e" * 64
RNG = random.Random(17082026)


def config(**patch):
    data = {
        "valuation_profile": "INSURANCE_PB_PE",
        "valuation_version": 1,
        "source_metrics_profile": "KAP_INSURANCE_TTM",
        "source_metrics_version": 1,
        "accounting_profile": "TFRS17_LOCAL_STATUTORY",
        "accounting_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "minimum_peer_count": 2,
        "full_confidence_peer_count": 6,
    }
    data.update(patch)
    return InsuranceValuationConfig.from_dict(data)


def snap(ticker, *, pb=None, pe=None, business="NON_LIFE", statement_age=36, price_age=1,
         technical_margin=None, investment_dependency=None, accounting="TFRS17_LOCAL_STATUTORY"):
    equity = RNG.uniform(5e8, 5e10)
    shares = RNG.uniform(1e7, 2e9)
    pb = RNG.uniform(.5, 3.0) if pb is None else pb
    income = equity / (RNG.uniform(6.0, 25.0) if pe is None else pe)
    if pe is not None and pe < 0:
        income = -abs(equity / pe)
    premiums = RNG.uniform(5e8, 1e11)
    technical_margin = RNG.uniform(-.03, .18) if technical_margin is None else technical_margin
    technical = premiums * technical_margin
    investment_dependency = RNG.uniform(.1, 1.5) if investment_dependency is None else investment_dependency
    investment = abs(income) * investment_dependency
    price = (equity / shares) * pb
    kwargs = dict(
        ticker=ticker, analysis_at=ANALYSIS, business_type=business, currency="TRY",
        share_basis="ADJUSTED_PRICE_SERIES_V1", current_price=price,
        price_trade_date=ANALYSIS.date() - timedelta(days=price_age),
        period_end=date(2026, 6, 30) if statement_age < 100 else date(2025, 9, 30),
        published_at=ANALYSIS - timedelta(days=max(1, min(statement_age, 20))),
        total_equity=equity, net_income_ttm=income, written_premiums_ttm=premiums,
        technical_result_ttm=technical, investment_income_ttm=investment, shares_out=shares,
        solvency_ratio=RNG.uniform(1.1, 2.5), source_confidence=RNG.uniform(.65, 1.0),
        source_document_id=f"DOC-{ticker}", source_sha256=SHA,
        metrics_profile="KAP_INSURANCE_TTM", metrics_version=1,
        accounting_profile=accounting, accounting_version=1,
    )
    if business == "NON_LIFE":
        earned = premiums * RNG.uniform(.75, .98)
        combined = RNG.uniform(.7, 1.2)
        claims = earned * combined * .75
        expenses = earned * combined * .25
        kwargs.update(earned_premiums_ttm=earned, net_claims_ttm=claims, operating_expenses_ttm=expenses)
    return build_insurance_snapshot(**kwargs)


def peers(prefix="P", *, business="NON_LIFE"):
    return [snap(f"{prefix}{i}", pb=pb, pe=pe, business=business) for i, (pb, pe) in enumerate([
        (.7, 8), (1.0, 10), (1.3, 12), (1.6, 14), (2.0, 18),
    ], 1)]


def run():
    stats = {
        "valid_valuation": 0,
        "controlled_insufficient": 0,
        "stale_or_profile_rejection": 0,
        "order_invariance": 0,
        "bypass_rejection": 0,
        "controlled_exception": 0,
        "uncontrolled_exception": 0,
        "silent_bad_acceptance": 0,
    }

    for i in range(5000):
        try:
            business = "NON_LIFE" if i % 3 else "LIFE_PENSION"
            target = snap(f"V{i}", business=business)
            out = value_insurance_snapshot(target, peers(f"V{i}_", business=business), config())
            if out["status"] != "OK" or not (0 < out["V_low"] <= out["V_mid"] <= out["V_high"]):
                stats["silent_bad_acceptance"] += 1
            elif not (0 <= out["valuation_score"] <= 1 and 0 <= out["v_conf"] <= 1):
                stats["silent_bad_acceptance"] += 1
            else:
                stats["valid_valuation"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    for i in range(2500):
        try:
            target = snap(f"I{i}", pe=-10 if i % 2 else 10)
            cfg = config(minimum_method_count=2) if i % 2 else config(minimum_peer_count=3)
            peer_items = peers(f"I{i}_") if i % 2 else peers(f"I{i}_")[:2]
            out = value_insurance_snapshot(target, peer_items, cfg)
            if out["status"] == "YETERSIZ_VERI" and out["V_mid"] is None:
                stats["controlled_insufficient"] += 1
            else:
                stats["silent_bad_acceptance"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    for i in range(2500):
        try:
            if i % 3 == 0:
                target = snap(f"S{i}", price_age=30)
            elif i % 3 == 1:
                target = snap(f"S{i}", statement_age=300)
            else:
                target = snap(f"S{i}", accounting="OLD_PROFILE")
            out = value_insurance_snapshot(target, peers(f"S{i}_"), config())
            if out["status"] == "YETERSIZ_VERI" and out["reason"] in {
                "HEDEF_FIYAT_BAYAT", "HEDEF_FINANSAL_BILGI_BAYAT", "HEDEF_MUHASEBE_PROFILI_UYUSMUYOR",
            }:
                stats["stale_or_profile_rejection"] += 1
            else:
                stats["silent_bad_acceptance"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    for i in range(2000):
        try:
            items = [snap(f"O{i}_T", pb=.6)] + peers(f"O{i}_")
            contexts = {item.ticker: {"follow_score": .5, "follow_active": True} for item in items}
            first = evaluate_insurance_batch(items, config=config(), follow_contexts=contexts)
            second = evaluate_insurance_batch(list(reversed(items)), config=config(), follow_contexts=contexts)
            sig1 = [(x["ticker"], x["m2"]["m2"], x["valuation"].get("V_mid")) for x in first["results"]]
            sig2 = [(x["ticker"], x["m2"]["m2"], x["valuation"].get("V_mid")) for x in second["results"]]
            if sig1 == sig2:
                stats["order_invariance"] += 1
            else:
                stats["silent_bad_acceptance"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    for i in range(3000):
        try:
            if i % 3 == 0:
                valid = config()
                broken = InsuranceValuationConfig(**{**valid.__dict__, "minimum_peer_count": 0})
                value_insurance_snapshot(snap(f"B{i}"), peers(f"B{i}_"), broken)
            elif i % 3 == 1:
                valid = snap(f"B{i}")
                broken = InsuranceSnapshot(**{**valid.__dict__, "source_sha256": "bad"})
                value_insurance_snapshot(broken, peers(f"B{i}_"), config())
            else:
                InsuranceMetricsRecord.from_mapping({
                    "ticker": ["BAD"], "period_end": "2026-06-30",
                    "published_at": "2026-07-20T10:00:00+03:00", "business_type": "NON_LIFE",
                    "accounting_profile": "TFRS17_LOCAL_STATUTORY", "accounting_version": 1,
                    "currency": "TRY", "shares_out": 100, "share_basis": "ADJUSTED_PRICE_SERIES_V1",
                    "total_equity": 1000, "net_income_ttm": 100, "written_premiums_ttm": 1000,
                    "technical_result_ttm": 100, "investment_income_ttm": 20,
                    "source_confidence": .9, "source_type": "KAP", "source_document_id": "X",
                    "source_sha256": SHA, "metrics_profile": "KAP_INSURANCE_TTM", "metrics_version": 1,
                })
            stats["silent_bad_acceptance"] += 1
        except (InsuranceValuationError, InsuranceMetricsIngestError):
            stats["bypass_rejection"] += 1
            stats["controlled_exception"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    scenario_count = sum(stats[key] for key in [
        "valid_valuation", "controlled_insufficient", "stale_or_profile_rejection",
        "order_invariance", "bypass_rejection",
    ])
    result = {
        "status": "PASS" if scenario_count == 15000 and stats["uncontrolled_exception"] == 0 and stats["silent_bad_acceptance"] == 0 else "FAIL",
        "scenario_count": scenario_count,
        **stats,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    run()
