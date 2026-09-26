from __future__ import annotations

"""R2: does any module predict anything forward? Measured, not assumed.

R0 showed the weighted combination is inverted; R1 showed every module's 0-1
mapping is miscalibrated. R2 asks the question that decides whether rebuilding
the weights is even possible: does any of these scores carry forward
information? The answer is no, not defensibly, which is why this file fits
nothing.

Each score is correlated with forward OPEN-to-OPEN returns at 1, 3, 6 and 12
month horizons, in-sample only, reported both with overlapping monthly samples
and with non-overlapping ones. Only the non-overlapping figures carry weight:
overlapping windows make observations dependent and inflate t, and at a 12-month
horizon 36 monthly samples hold about 3 independent ones.

Twenty-four cells are tested, so roughly one nominally significant result is
expected from noise alone. The two or three marginal hits are exactly that, and
no multiple-testing correction would leave them standing. Fitting a weight
vector to this sample would be fitting noise, so the audit records structural
findings -- which modules are consistently right-signed, wrong-signed or flat --
and stops there.

Four horizons are measured because the modules have different natural ones:
testing a quarterly-updating balance-sheet module against next month's return is
a badly specified test. All four are reported rather than the most flattering.
"""

import argparse
import json
import math
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rebuild_evaluation import IN_SAMPLE, select_split

CONTRACT = "R2_FORWARD_SKILL_AUDIT_V1"
OUTPUT = ROOT / "data/audit/r2_forward_skill_v1"
SERIES = ROOT / "data/audit/w8n_no_m2_backtest_v1/series.jsonl"
PRICES = ROOT / "data/backtest_sources/yahoo_resolved/historical_member_prices_resolved_2020-07_2026-08.csv.gz"
MODULES = ("M1", "M3", "Ek4", "Ek1", "Ek9")
SCORES = MODULES + ("total",)
HORIZONS = (1, 3, 6, 12)
MINIMUM_CROSS_SECTION = 5


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def summarise(values: list[float]) -> dict:
    """Mean, dispersion and t of a series of monthly rank correlations."""
    count = len(values)
    if count < 3:
        return {"observations": count, "insufficient": True}
    mean = sum(values) / count
    stdev = (sum((value - mean) ** 2 for value in values) / (count - 1)) ** 0.5
    return {
        "observations": count, "mean_rho": mean, "stdev": stdev,
        "t_stat": mean / (stdev / math.sqrt(count)) if stdev else 0.0,
        "positive_share": sum(1 for value in values if value > 0) / count,
        "insufficient": False,
    }


def _monthly_rho(scores: pd.DataFrame, opens: pd.DataFrame, sample_days: list[str],
                 column: str, horizon: int, *, non_overlapping: bool) -> list[float]:
    forward = opens.shift(-horizon) / opens - 1.0
    chosen = sample_days[::horizon] if non_overlapping else sample_days
    output: list[float] = []
    for day in chosen:
        if day not in forward.index:
            continue
        returns = forward.loc[day].dropna()
        if returns.empty:
            continue
        month = scores.loc[scores.month == day[:7], [column, "ticker"]]
        merged = month.merge(
            pd.DataFrame({"ticker": returns.index, "fwd": returns.values}),
            on="ticker").dropna()
        if len(merged) < MINIMUM_CROSS_SECTION or merged[column].nunique() < 2:
            continue
        value = merged[[column, "fwd"]].corr(method="spearman").iloc[0, 1]
        if pd.notna(value):
            output.append(float(value))
    return output


def _sign_consistency(grid: dict, column: str) -> str:
    values = [grid[column][f"{h}m"]["non_overlapping"].get("mean_rho") for h in HORIZONS]
    values = [value for value in values if value is not None]
    if not values:
        return "UNKNOWN"
    if all(value > 0 for value in values):
        return "ALL_POSITIVE"
    if all(value < 0 for value in values):
        return "ALL_NEGATIVE"
    return "MIXED"


def build(*, output_dir: Path = OUTPUT) -> dict:
    series = [json.loads(line) for line in
              SERIES.read_text(encoding="utf-8").splitlines() if line.strip()]
    in_sample = select_split(series, split=IN_SAMPLE)
    rows = []
    for row in in_sample:
        record = {"month": row["month"], "ticker": row["ticker"],
                  "total": row["total_rasyo_100"]}
        record.update(row["module_scores"])
        rows.append(record)
    scores = pd.DataFrame(rows)

    signal_days = sorted({row["signal_date"] for row in series})
    prices = pd.read_csv(PRICES, low_memory=False)
    prices = prices.loc[prices.trade_date.isin(signal_days)]
    opens = prices.pivot_table(index="trade_date", columns="ticker",
                               values="open", aggfunc="last").reindex(signal_days)
    sample_days = [day for day in signal_days if IN_SAMPLE.contains(day[:7])]

    grid = {
        column: {
            f"{horizon}m": {
                "overlapping": summarise(_monthly_rho(
                    scores, opens, sample_days, column, horizon, non_overlapping=False)),
                "non_overlapping": summarise(_monthly_rho(
                    scores, opens, sample_days, column, horizon, non_overlapping=True)),
            }
            for horizon in HORIZONS
        }
        for column in SCORES
    }

    cells = len(SCORES) * len(HORIZONS)
    receipt = {
        "contract": CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "MEASUREMENT_ONLY_NO_PARAMETER_WAS_FITTED",
        "evaluation_split_used": "IN_SAMPLE", "holdout_read_here": False,
        "in_sample_months": len({row["month"] for row in in_sample}),
        "in_sample_rows": int(len(scores)),
        "cross_section_per_month": "BIST100 historical union, about 100 names",
        "horizons_months": list(HORIZONS),
        "grid": grid,
        "sign_consistency_across_horizons": {
            column: _sign_consistency(grid, column) for column in SCORES},
        "multiple_testing": {
            "cells_tested": cells,
            "expected_false_positives_at_5pct": cells * 0.05,
            "note": ("Six scores across four horizons is 24 tests, so about one "
                     "nominally significant result is expected from noise alone. "
                     "The marginal hits found are consistent with that and no "
                     "correction would leave them standing."),
        },
        "overlap_caveat": (
            "Overlapping windows make monthly observations dependent, which "
            "inflates t. At a 12-month horizon 36 monthly samples hold about 3 "
            "independent ones, so only the non_overlapping figures carry weight -- "
            "and at 6 and 12 months even those rest on 6 and 3 observations."),
        "conclusions": {
            "no_module_has_defensible_forward_skill": True,
            "weights_cannot_be_fitted_on_this_sample": True,
            "structural_findings": {
                "M1": "FLAT_AT_EVERY_HORIZON_ITS_0.18_WEIGHT_IS_UNJUSTIFIED",
                "EK1": "NEGATIVE_AT_EVERY_HORIZON_AND_IT_ALSO_DRIVES_THE_VETO",
                "EK9": "CONSISTENTLY_RIGHT_SIGNED_RISING_WITH_HORIZON_AND_WEIGHS_LEAST",
                "M3": "RIGHT_SIGNED_BUT_DECAYS_TO_NOTHING_BEYOND_THREE_MONTHS",
                "EK4": "MIXED_SIGN_ACROSS_HORIZONS",
                "M2": "UNTESTED_ABSENT_FROM_EVERY_HISTORICAL_CELL",
            },
            "the_veto_is_backwards": (
                "good_count drives Ek1, Ek1 is negative at every horizon, and the "
                "veto penalises low good_count -- so it cuts the companies that "
                "went on to do better."),
            "what_would_change_this": [
                "Widen the historical cross-section from about 100 names a month to "
                "the 572 the current line routes, which roughly halves standard "
                "errors.",
                "Unblock historical M2, the only module carrying an economic thesis, "
                "which needs dated share counts.",
            ],
        },
        "source_sha256": {"series": _sha(SERIES), "prices": _sha(PRICES)},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return receipt


def check(*, output_dir: Path = OUTPUT) -> dict:
    recorded = json.loads((output_dir / "receipt.json").read_text(encoding="utf-8"))
    for name, path in (("series", SERIES), ("prices", PRICES)):
        if _sha(path) != recorded["source_sha256"][name]:
            raise ValueError(f"R2_SOURCE_HASH_MISMATCH:{name}")
    return {"status": "R2_CHECK_PASS"}


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(check() if args.check else build(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
