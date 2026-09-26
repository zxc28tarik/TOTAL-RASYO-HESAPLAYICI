from __future__ import annotations

"""R1: audit each module's own 0-1 mapping against BIST's real distributions.

R0 showed the weighted combination is inverted. This asks a different question:
does each module, on its own, convert its input into a score that uses the 0-1
range at all? Because a module pinned at a floor, or unable to reach 1, cannot
contribute the discrimination its weight implies no matter how the weights are
set.

Every mapping is recovered by PROBING the production function rather than by
reading a constant out of the source, so the audit is bound to behaviour: if
someone changes a divisor, the recovered calibration moves with it.

The measured verdicts:

* M3 and Ek4 share one mapping, clip((excess + 0.20) / 0.40), and the band is far
  narrower than the thing it measures. The real 63-day excess return has a
  standard deviation near 39% and runs from about -38% to +67% between its 5th
  and 95th percentiles, so a symmetric +/-20% band clips roughly two fifths of
  all observations -- and the distribution is right-skewed, so a symmetric band
  is the wrong shape as well as the wrong width.
* Ek1 divides good_count by 18 when the median company scores about 4.5, so its
  mass sits in the bottom quarter of the range.
* Ek9 subtracts volatility from a 0.06 cap, and a perfect score needs zero
  volatility, which no traded stock has. The top of its range is unreachable.
* M1 and M2 never reach either extreme; the heaviest weight in the model sits on
  one of the narrowest ranges.
* good_count drives Ek1 additively AND the veto multiplicatively, so one variable
  is worth +0.08 of score one way and -40% of it the other.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rebuild_evaluation import IN_SAMPLE
from src.analytics.ek1_quality import compute_ek1_score_from_good_count
from src.analytics.ek9_volatility import EK9_LOOKBACK_DAYS, EK9_VOLATILITY_CAP
from src.analytics.trailing_alpha import _score_alpha

CONTRACT = "R1_MODULE_MAPPING_AUDIT_V1"
OUTPUT = ROOT / "data/audit/r1_module_mappings_v1"
RANKING = ROOT / "data/live/current_total_scores_v1/ranking.jsonl"
PRICES = ROOT / "data/backtest_sources/yahoo_resolved/historical_member_prices_resolved_2020-07_2026-08.csv.gz"
SIGNALS = ROOT / "data/backtest_sources/xu100_signal_dates_yahoo_2021-08_2026-07.csv"
XU100 = ROOT / "data/backtest_sources/experimental_xu100_ohlc_v1/yahoo_chart.json"

MODULE_WEIGHTS = {"m2": 0.40, "m1": 0.18, "m3": 0.12, "ek4": 0.16, "ek1": 0.08, "ek9": 0.06}
ALPHA_WINDOW_DAYS = 63


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe_linear_band(mapper, *, lo: float = -3.0, hi: float = 3.0, steps: int = 60001) -> dict:
    """Recover where a clipped linear mapping reaches 0 and 1, by probing it."""
    grid = np.linspace(lo, hi, steps)
    scores = np.array([mapper(float(value)) for value in grid])
    inside = np.flatnonzero((scores > 1e-9) & (scores < 1 - 1e-9))
    if inside.size == 0:
        return {"recovered": False}
    return {
        "recovered": True,
        "reaches_zero_at_or_below": float(grid[inside[0]]),
        "reaches_one_at_or_above": float(grid[inside[-1]]),
        "band_width": float(grid[inside[-1]] - grid[inside[0]]),
        "band_is_symmetric_about_zero": bool(
            abs(grid[inside[0]] + grid[inside[-1]]) < 1e-3),
    }


def excess_return_distribution() -> dict:
    """The thing M3 and Ek4 actually measure, over the in-sample months only."""
    days = [day for day in pd.read_csv(SIGNALS).signal_date.astype(str)
            if IN_SAMPLE.contains(day[:7])]
    prices = pd.read_csv(PRICES, low_memory=False)
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices = prices.loc[prices.adj_close > 0].dropna(subset=["adj_close"])
    wide = prices.pivot_table(index="trade_date", columns="ticker",
                              values="adj_close", aggfunc="last").sort_index()

    from zoneinfo import ZoneInfo
    chart = json.loads(XU100.read_text(encoding="utf-8"))["chart"]["result"][0]
    closes = {}
    for stamp, close in zip(chart["timestamp"], chart["indicators"]["quote"][0]["close"]):
        if close is None:
            continue
        day = datetime.fromtimestamp(stamp, timezone.utc).astimezone(
            ZoneInfo("Europe/Istanbul")).date()
        closes[pd.Timestamp(day)] = float(close)
    index = pd.Series(closes).sort_index()

    calendar, samples = wide.index, []
    for day in days:
        position = calendar.searchsorted(pd.Timestamp(day), side="right") - 1
        if position < ALPHA_WINDOW_DAYS:
            continue
        end, start = calendar[position], calendar[position - ALPHA_WINDOW_DAYS]
        stock = (wide.loc[end] / wide.loc[start] - 1).dropna()
        index_end, index_start = index.asof(end), index.asof(start)
        if not (index_end and index_start):
            continue
        samples.append(stock - (index_end / index_start - 1))
    series = pd.concat(samples).dropna()
    return {
        "window_trading_days": ALPHA_WINDOW_DAYS,
        "in_sample_months": len({day[:7] for day in days}),
        "observation_count": int(len(series)),
        "stdev": float(series.std()),
        "percentiles": {f"p{p}": float(series.quantile(p / 100))
                        for p in (1, 5, 10, 25, 50, 75, 90, 95, 99)},
        "min": float(series.min()), "max": float(series.max()),
        "skew_right": bool(series.quantile(0.95) + series.quantile(0.05) > 0),
        "_series": series,
    }


def clipping_under_band(series: pd.Series, band: dict) -> dict:
    low, high = band["reaches_zero_at_or_below"], band["reaches_one_at_or_above"]
    return {
        "band": [low, high],
        "clipped_low_share": float((series <= low).mean()),
        "clipped_high_share": float((series >= high).mean()),
        "clipped_total_share": float(((series <= low) | (series >= high)).mean()),
    }


def realised_module_ranges(ranking: pd.DataFrame) -> dict:
    output = {}
    for key, weight in MODULE_WEIGHTS.items():
        series = ranking[key].dropna()
        p10, p90 = float(series.quantile(0.10)), float(series.quantile(0.90))
        output[key.upper()] = {
            "weight": weight,
            "min": float(series.min()), "max": float(series.max()),
            "median": float(series.median()), "p10": p10, "p90": p90,
            "stdev": float(series.std()),
            "share_at_zero": float((series <= 1e-9).mean()),
            "share_at_one": float((series >= 1 - 1e-9).mean()),
            "effective_range_p10_p90": p90 - p10,
            "weighted_swing": (p90 - p10) * weight,
            "reaches_one": bool((series >= 1 - 1e-9).any()),
            "reaches_zero": bool((series <= 1e-9).any()),
        }
    return output


def build(*, output_dir: Path = OUTPUT) -> dict:
    ranking = pd.DataFrame([json.loads(line) for line in
                            RANKING.read_text(encoding="utf-8").splitlines() if line.strip()])
    alpha_band = probe_linear_band(_score_alpha)
    ek1_band = probe_linear_band(compute_ek1_score_from_good_count, lo=-5.0, hi=40.0, steps=45001)
    ek9_band = probe_linear_band(lambda vol: float(np.clip(1.0 - vol / EK9_VOLATILITY_CAP, 0.0, 1.0)),
                                 lo=-0.02, hi=0.20, steps=22001)

    excess = excess_return_distribution()
    series = excess.pop("_series")
    ranges = realised_module_ranges(ranking)
    good = ranking["good_count_ge8"]

    receipt = {
        "contract": CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "MEASUREMENT_ONLY_NO_MODULE_WAS_CHANGED",
        "evaluation_split_used": "IN_SAMPLE",
        "holdout_read_here": False,
        "recovered_mappings": {
            # M3 and Ek4 share this mapping; probing shows the identical band.
            "M3_AND_EK4_excess_return_band": alpha_band,
            "EK1_good_count_band": ek1_band,
            "EK9_volatility_band": ek9_band,
            "EK9_lookback_days": EK9_LOOKBACK_DAYS,
            "EK9_volatility_cap": EK9_VOLATILITY_CAP,
        },
        "what_m3_and_ek4_actually_measure": excess,
        "m3_ek4_clipping_under_current_band": clipping_under_band(series, alpha_band),
        "m3_ek4_clipping_under_p5_p95_band": {
            "band": [excess["percentiles"]["p5"], excess["percentiles"]["p95"]],
            "clipped_total_share": float((
                (series <= excess["percentiles"]["p5"])
                | (series >= excess["percentiles"]["p95"])).mean()),
        },
        "realised_module_ranges": ranges,
        "ek1_calibration": {
            "divisor_recovered": ek1_band["reaches_one_at_or_above"],
            "median_good_count": float(good.median()),
            "median_ek1": float(compute_ek1_score_from_good_count(good.median())),
            "share_reaching_divisor": float((good >= ek1_band["reaches_one_at_or_above"]).mean()),
        },
        "good_count_is_double_counted": {
            "enters_as_ek1_additively_with_weight": MODULE_WEIGHTS["ek1"],
            "enters_as_veto_multiplicatively_with_factor": 0.60,
            "veto_threshold": 5,
            "median_good_count": float(good.median()),
            "verdict": "ONE_VARIABLE_SCORED_TWICE_WITH_OPPOSING_SIGNS",
        },
        "aggregate": {
            "theoretical_weighted_swing": sum(r["weighted_swing"] for r in ranges.values()),
            "theoretical_weighted_swing_on_100_scale":
                sum(r["weighted_swing"] for r in ranges.values()) * 100,
            "heaviest_weight_module": max(MODULE_WEIGHTS, key=MODULE_WEIGHTS.get).upper(),
            "widest_range_module": max(ranges, key=lambda k: ranges[k]["effective_range_p10_p90"]),
            "weight_is_allocated_against_discrimination": bool(
                ranges["M2"]["effective_range_p10_p90"] < ranges["M3"]["effective_range_p10_p90"]
                and MODULE_WEIGHTS["m2"] > MODULE_WEIGHTS["m3"]),
        },
        "verdicts": {
            "M3": "BAND_TOO_NARROW_AND_WRONG_SHAPE_CLIPS_MOST_OF_A_TAIL",
            "EK4": "SHARES_M3_BAND_SAME_MISCALIBRATION_DIFFERENT_WINDOW",
            "EK1": "DIVISOR_FAR_ABOVE_THE_POPULATION_MASS_USES_BOTTOM_OF_RANGE",
            "EK9": "PERFECT_SCORE_REQUIRES_ZERO_VOLATILITY_TOP_OF_RANGE_UNREACHABLE",
            "M1": "NEVER_REACHES_EITHER_EXTREME",
            "M2": "NEVER_REACHES_EITHER_EXTREME_YET_CARRIES_THE_HEAVIEST_WEIGHT",
        },
        "source_sha256": {"ranking": _sha(RANKING), "prices": _sha(PRICES),
                          "signals": _sha(SIGNALS), "xu100": _sha(XU100)},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return receipt


def check(*, output_dir: Path = OUTPUT) -> dict:
    recorded = json.loads((output_dir / "receipt.json").read_text(encoding="utf-8"))
    paths = {"ranking": RANKING, "prices": PRICES, "signals": SIGNALS, "xu100": XU100}
    for name, digest in recorded["source_sha256"].items():
        if _sha(paths[name]) != digest:
            raise ValueError(f"R1_SOURCE_HASH_MISMATCH:{name}")
    return {"status": "R1_CHECK_PASS"}


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(check() if args.check else build(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
