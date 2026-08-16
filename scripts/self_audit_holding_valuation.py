#!/usr/bin/env python3
from __future__ import annotations

try:
    from scripts._repo_bootstrap import ensure_repo_root
except ModuleNotFoundError:
    from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

import json
import math
import random
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.analytics.holding_valuation import (
    HoldingSnapshot,
    HoldingValuationConfig,
    HoldingValuationError,
    build_holding_snapshot,
    combine_holding_m2,
    evaluate_holding_batch,
    value_holding_snapshot,
)
from src.ingest.holding_nav import HoldingNavIngestError, HoldingNavRecord

RNG = random.Random(20260805)
TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 20, 0, tzinfo=TZ)
PRICE_DATE = date(2026, 8, 5)
SHA = "a" * 64

CONFIG = HoldingValuationConfig.from_dict({
    "valuation_profile": "SELF_AUDIT_HOLDING_NAV",
    "valuation_version": 1,
    "source_nav_profile": "HOLDING_ADJUSTED_NAV",
    "source_nav_version": 1,
    "share_basis": "ADJUSTED_PRICE_SERIES_V1",
    "minimum_peer_count": 4,
    "full_confidence_peer_count": 8,
    "minimum_discount": -0.50,
    "maximum_discount": 0.90,
    "max_nav_age_days": 370,
    "full_freshness_days": 120,
    "max_price_age_days": 7,
    "minimum_source_confidence": 0.40,
    "band_width_shadow_mode": True,
})


def snapshot(ticker: str, seed: int, *, discount: float | None = None) -> HoldingSnapshot:
    nav_total = 500.0 + (seed % 100) * 10.0
    shares = 50.0 + (seed % 25)
    nav_ps = nav_total / shares
    d = RNG.uniform(0.10, 0.70) if discount is None else discount
    price = nav_ps * (1.0 - d)
    nav_age = seed % 100
    nav_date = PRICE_DATE - timedelta(days=nav_age)
    published = datetime.combine(nav_date + timedelta(days=10), datetime.min.time(), tzinfo=TZ)
    if published > ANALYSIS:
        published = ANALYSIS - timedelta(hours=1)
    return build_holding_snapshot(
        ticker=ticker,
        analysis_at=ANALYSIS,
        peer_group="XHOLD",
        currency="TRY",
        share_basis="ADJUSTED_PRICE_SERIES_V1",
        current_price=price,
        price_trade_date=PRICE_DATE,
        nav_asof_date=nav_date,
        nav_published_at=published,
        nav_total=nav_total,
        shares_out=shares,
        source_confidence=RNG.uniform(0.75, 1.0),
        source_document_id=f"DOC-{ticker}",
        source_sha256=SHA,
        nav_profile="HOLDING_ADJUSTED_NAV",
        nav_version=1,
    )


def peer_group(seed: int, count: int = 8) -> list[HoldingSnapshot]:
    return [snapshot(f"P{seed:05d}{idx:02d}", seed * 20 + idx) for idx in range(count)]


def assert_ok(result: dict) -> None:
    if result["status"] != "OK":
        raise AssertionError(result)
    low, mid, high = result["V_low"], result["V_mid"], result["V_high"]
    if not (0 < low <= mid <= high):
        raise AssertionError("invalid geometry")
    for key in ("valuation_score", "v_conf"):
        value = float(result[key])
        if not (math.isfinite(value) and 0.0 <= value <= 1.0):
            raise AssertionError(f"invalid {key}")


def nav_payload(seed: int) -> dict:
    return {
        "ticker": f"N{seed:05d}",
        "nav_asof_date": "2026-06-30",
        "published_at": "2026-07-20T10:00:00+03:00",
        "version_tag": "ORIGINAL",
        "version_sequence": 1,
        "nav_total": "1000",
        "shares_out": "100",
        "nav_per_share": "10",
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "currency": "TRY",
        "source_confidence": "0.9",
        "source_type": "COMPANY_REPORTED_ADJUSTED_NAV",
        "source_document_id": f"DOC-N{seed:05d}",
        "source_sha256": SHA,
        "nav_profile": "HOLDING_ADJUSTED_NAV",
        "nav_version": 1,
        "lineage": {"seed": seed},
    }


def main() -> None:
    counts = {
        "valid_valuation": 0,
        "controlled_insufficient": 0,
        "order_invariance": 0,
        "config_bypass_rejected": 0,
        "snapshot_bypass_rejected": 0,
        "ingest_boundary_rejected": 0,
        "batch_contract": 0,
        "uncontrolled_exception": 0,
        "silent_bad_acceptance": 0,
    }

    for seed in range(5000):
        target = snapshot(f"T{seed:05d}", seed, discount=RNG.uniform(0.20, 0.60))
        result = value_holding_snapshot(target, peer_group(seed), CONFIG)
        assert_ok(result)
        m2 = combine_holding_m2(
            result,
            follow_score=RNG.random(),
            follow_active=bool(seed % 2),
            config=CONFIG,
        )
        if not 0.0 <= m2["m2"] <= 1.0:
            raise AssertionError("invalid M2")
        counts["valid_valuation"] += 1

    for seed in range(2500):
        target = snapshot(f"I{seed:05d}", seed)
        mode = seed % 5
        if mode == 0:
            peers = peer_group(seed, 2)
        elif mode == 1:
            target = replace(target, nav_asof_date=date(2024, 12, 31))
            peers = peer_group(seed)
        elif mode == 2:
            target = replace(target, price_trade_date=date(2026, 7, 1))
            peers = peer_group(seed)
        elif mode == 3:
            target = replace(target, source_confidence=0.1)
            peers = peer_group(seed)
        else:
            target = replace(target, currency="USD")
            peers = peer_group(seed)
        result = value_holding_snapshot(target, peers, CONFIG)
        if result["status"] != "YETERSIZ_VERI":
            counts["silent_bad_acceptance"] += 1
            raise AssertionError(result)
        counts["controlled_insufficient"] += 1

    for seed in range(2000):
        items = [snapshot(f"O{seed:05d}{idx}", seed * 10 + idx) for idx in range(9)]
        contexts = {
            row.ticker: {"follow_score": 0.35 + idx * 0.03, "follow_active": True}
            for idx, row in enumerate(items)
        }
        first = evaluate_holding_batch(items, config=CONFIG, follow_contexts=contexts)
        shuffled = list(items)
        RNG.shuffle(shuffled)
        second = evaluate_holding_batch(shuffled, config=CONFIG, follow_contexts=contexts)
        left = [(row["ticker"], row["m2"]["m2"], row["valuation"]["V_mid"]) for row in first["results"]]
        right = [(row["ticker"], row["m2"]["m2"], row["valuation"]["V_mid"]) for row in second["results"]]
        if left != right:
            raise AssertionError("order dependence")
        counts["order_invariance"] += 1

    for seed in range(1500):
        broken = replace(CONFIG, minimum_peer_count=0)
        try:
            value_holding_snapshot(snapshot(f"C{seed:05d}", seed), peer_group(seed), broken)
        except HoldingValuationError:
            counts["config_bypass_rejected"] += 1
        except Exception:
            counts["uncontrolled_exception"] += 1
            raise
        else:
            counts["silent_bad_acceptance"] += 1
            raise AssertionError("config bypass accepted")

    for seed in range(1500):
        valid = snapshot(f"B{seed:05d}", seed)
        broken = replace(valid, source_sha256="bad")
        try:
            value_holding_snapshot(broken, peer_group(seed), CONFIG)
        except HoldingValuationError:
            counts["snapshot_bypass_rejected"] += 1
        except Exception:
            counts["uncontrolled_exception"] += 1
            raise
        else:
            counts["silent_bad_acceptance"] += 1
            raise AssertionError("snapshot bypass accepted")

    for seed in range(1500):
        bad = nav_payload(seed)
        mode = seed % 5
        if mode == 0:
            bad["source_sha256"] = "bad"
        elif mode == 1:
            bad["nav_total"] = "NaN"
        elif mode == 2:
            bad["shares_out"] = 0
        elif mode == 3:
            bad["lineage"] = []
        else:
            bad["nav_per_share"] = "9"
        try:
            HoldingNavRecord.from_mapping(bad)
        except HoldingNavIngestError:
            counts["ingest_boundary_rejected"] += 1
        except Exception:
            counts["uncontrolled_exception"] += 1
            raise
        else:
            counts["silent_bad_acceptance"] += 1
            raise AssertionError("bad NAV input accepted")

    for seed in range(1000):
        items = [snapshot(f"G{seed:05d}{idx}", seed * 10 + idx) for idx in range(6)]
        report = evaluate_holding_batch(items, config=CONFIG)
        if report["result_count"] != 6 or len(report["results"]) != 6:
            raise AssertionError("batch contract")
        if any(not 0.0 <= row["m2"]["m2"] <= 1.0 for row in report["results"]):
            raise AssertionError("batch M2")
        counts["batch_contract"] += 1

    total = sum(value for key, value in counts.items() if key not in {"uncontrolled_exception", "silent_bad_acceptance"})
    print(json.dumps({"status": "PASS", "scenario_count": total, **counts}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
