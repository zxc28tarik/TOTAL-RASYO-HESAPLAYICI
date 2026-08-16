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

from src.analytics.nonfin_valuation import (
    NonfinSnapshot,
    NonfinValuationConfig,
    NonfinValuationError,
    combine_nonfin_m2,
    evaluate_nonfin_batch,
    value_nonfin_snapshot,
)

RNG = random.Random(20260805)
TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 20, 0, tzinfo=TZ)
ANCHOR = date(2026, 6, 30)
PRICE_DATE = date(2026, 8, 5)

CONFIG = NonfinValuationConfig.from_dict({
    "valuation_profile": "SELF_AUDIT_NONFIN_RELATIVE",
    "valuation_version": 1,
    "source_derivation_profile": "SELF_AUDIT_COMPANY_METRICS",
    "source_derivation_version": 1,
    "multiple_weights": {"PE": 0.30, "EV_EBIT": 0.30, "PS": 0.20, "PB": 0.20},
    "minimum_peer_count": 5,
    "full_confidence_peer_count": 8,
    "minimum_coverage_weight": 0.50,
    "max_halfwidth": 1.25,
    "band_width_shadow_mode": True,
    "valuation_axis_weight": 0.60,
    "follow_axis_weight": 0.40,
    "max_price_age_days": 7,
})


def snapshot(ticker: str, seed: int, *, price_date: date = PRICE_DATE) -> NonfinSnapshot:
    scale = 0.70 + (seed % 40) / 40.0
    revenue = 400.0 * scale + RNG.uniform(1.0, 30.0)
    ebit = 70.0 * scale + RNG.uniform(1.0, 12.0)
    net_income = 45.0 * scale + RNG.uniform(1.0, 9.0)
    equity = 300.0 * scale + RNG.uniform(5.0, 40.0)
    net_debt = RNG.uniform(-30.0, 120.0)
    shares = 100.0 + seed % 50
    fairish_price = (net_income * (8.0 + (seed % 8))) / shares
    return NonfinSnapshot(
        ticker=ticker,
        analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR,
        sector_code="XUSIN",
        current_price=max(0.2, fairish_price * RNG.uniform(0.65, 1.35)),
        price_trade_date=price_date,
        revenue_ttm=revenue,
        ebit_ttm=ebit,
        net_income_ttm=net_income,
        total_equity=equity,
        net_debt=net_debt,
        shares_out=shares,
    )


def peer_group(seed: int, count: int = 8) -> list[NonfinSnapshot]:
    return [snapshot(f"P{seed:05d}{idx:02d}", seed * 20 + idx) for idx in range(count)]


def assert_ok(result: dict) -> None:
    if result["status"] != "OK":
        raise AssertionError(result)
    low, mid, high = result["V_low"], result["V_mid"], result["V_high"]
    if not (0 < low <= mid <= high):
        raise AssertionError("invalid geometry")
    for key in ("valuation_score", "v_conf", "coverage_weight"):
        if not (math.isfinite(float(result[key])) and 0.0 <= float(result[key]) <= 1.0):
            raise AssertionError(f"invalid {key}")


def main() -> None:
    counts = {
        "valid_valuation": 0,
        "controlled_insufficient": 0,
        "stale_price": 0,
        "order_invariance": 0,
        "config_bypass_rejected": 0,
        "snapshot_bypass_rejected": 0,
        "uncontrolled_exception": 0,
        "silent_bad_acceptance": 0,
    }

    for seed in range(5000):
        target = snapshot(f"T{seed:05d}", seed)
        result = value_nonfin_snapshot(target, peer_group(seed), CONFIG)
        assert_ok(result)
        m2 = combine_nonfin_m2(
            result,
            follow_score=RNG.random(),
            follow_active=bool(seed % 2),
            config=CONFIG,
        )
        if not (0.0 <= m2["m2"] <= 1.0):
            raise AssertionError("invalid M2")
        counts["valid_valuation"] += 1

    for seed in range(2500):
        target = snapshot(f"I{seed:05d}", seed)
        mode = seed % 3
        if mode == 0:
            peers = peer_group(seed, 3)
        elif mode == 1:
            target = replace(target, net_income_ttm=None, ebit_ttm=None, revenue_ttm=None)
            peers = peer_group(seed)
        else:
            target = replace(target, total_equity=None, net_debt=None, net_income_ttm=None)
            peers = peer_group(seed)
        result = value_nonfin_snapshot(target, peers, CONFIG)
        if result["status"] != "YETERSIZ_VERI":
            counts["silent_bad_acceptance"] += 1
            raise AssertionError(result)
        counts["controlled_insufficient"] += 1

    stale_date = PRICE_DATE - timedelta(days=30)
    for seed in range(2500):
        target = snapshot(f"S{seed:05d}", seed, price_date=stale_date)
        result = value_nonfin_snapshot(target, peer_group(seed), CONFIG)
        if result["status"] != "YETERSIZ_VERI" or result["reason"] != "HEDEF_FIYAT_BAYAT":
            counts["silent_bad_acceptance"] += 1
            raise AssertionError(result)
        counts["stale_price"] += 1

    for seed in range(2000):
        items = [snapshot(f"O{seed:05d}{idx}", seed * 10 + idx) for idx in range(9)]
        contexts = {row.ticker: {"follow_score": 0.4 + idx * 0.02, "follow_active": True}
                    for idx, row in enumerate(items)}
        first = evaluate_nonfin_batch(items, config=CONFIG, follow_contexts=contexts)
        shuffled = list(items)
        RNG.shuffle(shuffled)
        second = evaluate_nonfin_batch(shuffled, config=CONFIG, follow_contexts=contexts)
        left = [(row["ticker"], row["m2"]["m2"], row["valuation"]["V_mid"]) for row in first["results"]]
        right = [(row["ticker"], row["m2"]["m2"], row["valuation"]["V_mid"]) for row in second["results"]]
        if left != right:
            raise AssertionError("order dependence")
        counts["order_invariance"] += 1

    for seed in range(1500):
        broken = replace(CONFIG, multiple_weights=[seed])
        try:
            value_nonfin_snapshot(snapshot(f"C{seed:05d}", seed), peer_group(seed), broken)
        except NonfinValuationError:
            counts["config_bypass_rejected"] += 1
        except Exception:
            counts["uncontrolled_exception"] += 1
            raise
        else:
            counts["silent_bad_acceptance"] += 1
            raise AssertionError("config bypass accepted")

    for seed in range(1500):
        valid = snapshot(f"B{seed:05d}", seed)
        broken = replace(valid, current_price=[1, 2])
        try:
            value_nonfin_snapshot(broken, peer_group(seed), CONFIG)
        except NonfinValuationError:
            counts["snapshot_bypass_rejected"] += 1
        except Exception:
            counts["uncontrolled_exception"] += 1
            raise
        else:
            counts["silent_bad_acceptance"] += 1
            raise AssertionError("snapshot bypass accepted")

    total = sum(value for key, value in counts.items() if key not in {"uncontrolled_exception", "silent_bad_acceptance"})
    output = {
        "status": "PASS",
        "scenario_count": total,
        **counts,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
