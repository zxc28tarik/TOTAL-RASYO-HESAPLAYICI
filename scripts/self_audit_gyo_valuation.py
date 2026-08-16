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

from src.analytics.gyo_valuation import (  # noqa: E402
    GyoSnapshot,
    GyoValuationConfig,
    GyoValuationError,
    build_gyo_snapshot,
    evaluate_gyo_batch,
    value_gyo_snapshot,
)
from src.ingest.gyo_nav import GyoNavIngestError, GyoNavRecord  # noqa: E402

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
SHA = "d" * 64
RNG = random.Random(16082026)


def config(**patch):
    data = {
        "valuation_profile": "GYO_PD_NAV", "valuation_version": 1,
        "source_nav_profile": "GYO_REPORTED_NAV", "source_nav_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1", "minimum_peer_count": 3,
        "full_confidence_peer_count": 8,
    }
    data.update(patch)
    return GyoValuationConfig.from_dict(data)


def snap(ticker, *, ratio=None, nav_total=None, shares=None, group="GYO_MIXED", method="DIRECT",
         confidence=None, nav_age=None, price_age=None):
    nav_total = nav_total or RNG.uniform(1e8, 5e10)
    shares = shares or RNG.uniform(1e7, 2e9)
    ratio = ratio if ratio is not None else RNG.uniform(.25, 1.4)
    confidence = confidence if confidence is not None else RNG.uniform(.65, 1.0)
    nav_age = nav_age if nav_age is not None else RNG.randint(0, 150)
    price_age = price_age if price_age is not None else RNG.randint(0, 5)
    nav_per_share = nav_total / shares
    return build_gyo_snapshot(
        ticker=ticker, analysis_at=ANALYSIS, peer_group=group, currency="TRY",
        share_basis="ADJUSTED_PRICE_SERIES_V1", current_price=nav_per_share * ratio,
        price_trade_date=ANALYSIS.date() - timedelta(days=price_age),
        nav_asof_date=ANALYSIS.date() - timedelta(days=nav_age),
        nav_published_at=ANALYSIS - timedelta(days=max(0, min(nav_age, 10))),
        nav_total=nav_total, shares_out=shares, property_portfolio_value=nav_total * RNG.uniform(1.0, 1.8),
        nav_source_method=method, source_confidence=confidence,
        source_document_id=f"DOC-{ticker}", source_sha256=SHA,
        nav_profile="GYO_REPORTED_NAV", nav_version=1,
    )


def peer_set(prefix="P"):
    return [snap(f"{prefix}{i}", ratio=ratio) for i, ratio in enumerate([.4, .55, .7, .85, 1.0], 1)]


def run():
    stats = {
        "valid_valuation": 0, "controlled_insufficient": 0, "stale_data": 0,
        "order_invariance": 0, "bypass_rejection": 0,
        "controlled_exception": 0, "unexpected_exception": 0, "silent_bad_acceptance": 0,
    }

    for i in range(5000):
        try:
            target = snap(f"T{i}")
            out = value_gyo_snapshot(target, peer_set(f"V{i}_"), config())
            if out["status"] != "OK" or not (0 < out["V_low"] <= out["V_mid"] <= out["V_high"]):
                stats["silent_bad_acceptance"] += 1
            elif not (0 <= out["valuation_score"] <= 1 and 0 <= out["v_conf"] <= 1):
                stats["silent_bad_acceptance"] += 1
            else:
                stats["valid_valuation"] += 1
        except Exception:
            stats["unexpected_exception"] += 1

    for i in range(2500):
        try:
            out = value_gyo_snapshot(snap(f"I{i}"), peer_set(f"I{i}_")[:2], config())
            if out["status"] == "YETERSIZ_VERI" and out["V_mid"] is None:
                stats["controlled_insufficient"] += 1
            else:
                stats["silent_bad_acceptance"] += 1
        except Exception:
            stats["unexpected_exception"] += 1

    for i in range(2500):
        try:
            target = snap(f"S{i}", nav_age=250 if i % 2 == 0 else 20, price_age=2 if i % 2 == 0 else 20)
            out = value_gyo_snapshot(target, peer_set(f"S{i}_"), config())
            if out["status"] == "YETERSIZ_VERI" and out["reason"] in {"HEDEF_NAV_BAYAT", "HEDEF_FIYAT_BAYAT"}:
                stats["stale_data"] += 1
            else:
                stats["silent_bad_acceptance"] += 1
        except Exception:
            stats["unexpected_exception"] += 1

    for i in range(2000):
        try:
            items = [snap(f"O{i}_T", ratio=.45)] + peer_set(f"O{i}_")
            ctx = {x.ticker: {"follow_score": .5, "follow_active": True} for x in items}
            a = evaluate_gyo_batch(items, config=config(), follow_contexts=ctx)
            b = evaluate_gyo_batch(list(reversed(items)), config=config(), follow_contexts=ctx)
            sig_a = [(x["ticker"], x["m2"]["m2"], x["valuation"].get("V_mid")) for x in a["results"]]
            sig_b = [(x["ticker"], x["m2"]["m2"], x["valuation"].get("V_mid")) for x in b["results"]]
            if sig_a == sig_b:
                stats["order_invariance"] += 1
            else:
                stats["silent_bad_acceptance"] += 1
        except Exception:
            stats["unexpected_exception"] += 1

    for i in range(3000):
        try:
            if i % 3 == 0:
                valid = config()
                broken = GyoValuationConfig(**{**valid.__dict__, "minimum_peer_count": 0})
                value_gyo_snapshot(snap(f"B{i}"), peer_set(f"B{i}_"), broken)
            elif i % 3 == 1:
                valid = snap(f"B{i}")
                broken = GyoSnapshot(**{**valid.__dict__, "source_sha256": "bad"})
                value_gyo_snapshot(broken, peer_set(f"B{i}_"), config())
            else:
                GyoNavRecord.from_mapping({
                    "ticker": ["BAD"], "nav_asof_date": "2026-06-30",
                    "published_at": "2026-07-20T10:00:00+03:00", "nav_total": 1000,
                    "shares_out": 100, "share_basis": "ADJUSTED_PRICE_SERIES_V1", "currency": "TRY",
                    "property_portfolio_value": 1300, "source_type": "KAP", "source_document_id": "X",
                    "source_sha256": SHA, "nav_profile": "GYO_REPORTED_NAV", "nav_version": 1,
                })
            stats["silent_bad_acceptance"] += 1
        except (GyoValuationError, GyoNavIngestError):
            stats["bypass_rejection"] += 1
            stats["controlled_exception"] += 1
        except Exception:
            stats["unexpected_exception"] += 1

    total = sum(stats[key] for key in ["valid_valuation", "controlled_insufficient", "stale_data", "order_invariance", "bypass_rejection"])
    result = {"status": "PASS" if total == 15000 and stats["unexpected_exception"] == 0 and stats["silent_bad_acceptance"] == 0 else "FAIL",
              "scenario_count": total, **stats}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    run()
