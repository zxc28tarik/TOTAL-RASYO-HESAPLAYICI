from __future__ import annotations

"""R0: the immutable, measured indictment of the pre-rebuild scoring layer.

Every later change to weights, the veto, the decision bands or a module's own
0-1 mapping must be answerable to these numbers. Recording them once, hash-bound
and reproducible, is what stops the rebuild from becoming one person's intuition
replacing another's.

Four defects are measured, not asserted:

1. **Inverted weighting.** Against realised twelve-month returns the six-module
   score separates the best names from the worst by a NEGATIVE margin: M3 and Ek4
   discriminate correctly but M2, at weight 0.40 and pointing the other way,
   cancels them by a factor of about 1.4.

2. **No forward predictive power.** Scored forward -- month T's score against the
   OPEN-to-OPEN return from T to T+1 -- the model's monthly rank correlation is
   near zero and the share of months with a positive correlation is near a half.
   This is measured on the IN_SAMPLE months only; the holdout is never read here.

3. **A veto that fires on half the population.** Its threshold sits essentially
   at the median good_count, and the group it penalises by 40% differs from the
   unpenalised group by a very small margin in base score.

4. **An unused scale.** The six-module score inhabits a fraction of 0-100, so the
   decision bands calibrated at 0.70/0.55 are close to unreachable.

Nothing here changes the model. This script only measures it.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rebuild_evaluation import (
    HOLDOUT, IN_SAMPLE, discrimination, forward_returns, scale_usage, select_split,
)

CONTRACT = "R0_PRE_REBUILD_MODEL_BASELINE_V1"
OUTPUT = ROOT / "data/audit/r0_model_baseline_v1"

CURRENT_RANKING = ROOT / "data/live/current_total_scores_v1/ranking.jsonl"
CURRENT_PRICES = ROOT / "data/live/current_market_modules_v1/stock_prices.csv.gz"
SERIES = ROOT / "data/audit/w8n_no_m2_backtest_v1/series.jsonl"
HISTORICAL_PRICES = ROOT / "data/backtest_sources/yahoo_resolved/historical_member_prices_resolved_2020-07_2026-08.csv.gz"
MODULE_KEYS = ("m1", "m2", "m3", "ek1", "ek4", "ek9")
PRODUCTION_WEIGHTS = {"m2": 0.40, "m1": 0.18, "m3": 0.12, "ek4": 0.16, "ek1": 0.08, "ek9": 0.06}
VETO_THRESHOLD, VETO_FACTOR = 5, 0.60
DECISION_BANDS = {"AL": 0.70, "IZLE": 0.55}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def trailing_returns(prices_path: Path, days: int = 365) -> pd.Series:
    prices = pd.read_csv(prices_path)
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices = prices.dropna(subset=["adj_close"])
    asof = prices.trade_date.max()
    start = asof - pd.Timedelta(days=days)
    output: dict[str, float] = {}
    for ticker, group in prices.groupby("ticker"):
        group = group.sort_values("trade_date")
        earlier = group.loc[group.trade_date <= start]
        if earlier.empty:
            continue
        first, last = float(earlier.iloc[-1].adj_close), float(group.iloc[-1].adj_close)
        if first > 0 and last > 0:
            output[str(ticker)] = last / first - 1.0
    return pd.Series(output, name="trailing_return")


def weighted_contribution_decomposition(frame: pd.DataFrame, return_column: str,
                                        extreme_n: int = 20) -> dict:
    """Which module drives the gap between the best and worst realised outcomes."""
    usable = frame.dropna(subset=[return_column, *MODULE_KEYS])
    best = usable.nlargest(extreme_n, return_column)
    worst = usable.nsmallest(extreme_n, return_column)
    per_module, net = {}, 0.0
    for key in MODULE_KEYS:
        gap = float(best[key].mean() - worst[key].mean())
        contribution = gap * PRODUCTION_WEIGHTS[key]
        net += contribution
        per_module[key.upper()] = {
            "top_mean": float(best[key].mean()), "bottom_mean": float(worst[key].mean()),
            "gap": gap, "weight": PRODUCTION_WEIGHTS[key], "weighted_contribution": contribution,
        }
    momentum = sum(per_module[k]["weighted_contribution"] for k in ("M3", "EK4"))
    valuation = per_module["M2"]["weighted_contribution"]
    return {
        "extreme_n": extreme_n, "row_count": int(len(usable)),
        "per_module": per_module,
        "net_weighted_gap": net,
        "net_weighted_gap_on_100_scale": net * 100.0,
        "momentum_weighted_gap": momentum,
        "valuation_weighted_gap": valuation,
        "valuation_cancels_momentum_by": (
            abs(valuation / momentum) if momentum else None),
        "verdict": ("VALUATION_AXIS_INVERTS_THE_SCORE" if net < 0
                    else "NET_DISCRIMINATION_POSITIVE"),
    }


def veto_diagnostics(frame: pd.DataFrame) -> dict:
    good = frame["good_count_ge8"]
    vetoed = frame.loc[frame.veto_flag]
    clear = frame.loc[~frame.veto_flag]
    ranked = frame.sort_values(["final_score", "ticker"], ascending=[False, True]).head(25)
    unvetoed_rank = frame.sort_values(["base_score", "ticker"], ascending=[False, True]).head(25)
    return {
        "threshold": VETO_THRESHOLD, "factor": VETO_FACTOR,
        "median_good_count": float(good.median()),
        "threshold_minus_median": float(VETO_THRESHOLD - good.median()),
        "population": int(len(frame)),
        "vetoed_count": int(len(vetoed)),
        "vetoed_share": float(len(vetoed) / len(frame)),
        "vetoed_base_score_mean": float(vetoed.base_score.mean()),
        "clear_base_score_mean": float(clear.base_score.mean()),
        "base_score_separation": float(clear.base_score.mean() - vetoed.base_score.mean()),
        "top25_overlap_without_veto": int(len(set(ranked.ticker) & set(unvetoed_rank.ticker))),
        "verdict": ("THRESHOLD_AT_MEDIAN_PENALTY_IS_NOT_SELECTIVE"
                    if abs(VETO_THRESHOLD - good.median()) <= 1
                    and len(vetoed) / len(frame) > 0.4 else "SELECTIVE"),
    }


def build(*, output_dir: Path = OUTPUT) -> dict:
    ranking = pd.DataFrame(_rows(CURRENT_RANKING))
    ranking["trailing_return"] = ranking.ticker.map(trailing_returns(CURRENT_PRICES))

    series = _rows(SERIES)
    in_sample = select_split(series, split=IN_SAMPLE)
    signal_days = sorted({row["signal_date"] for row in series})
    forward = forward_returns(pd.read_csv(HISTORICAL_PRICES, low_memory=False), signal_days)
    scored_forward = pd.DataFrame(in_sample)[["month", "ticker", "total_rasyo_100"]].merge(
        forward[["month", "ticker", "forward_return"]], on=["month", "ticker"], how="inner")

    receipt = {
        "contract": CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "IMMUTABLE_PRE_REBUILD_MEASUREMENT_NOT_A_MODEL_CHANGE",
        "pre_rebuild_parameters": {
            "weights": PRODUCTION_WEIGHTS,
            "veto_threshold": VETO_THRESHOLD, "veto_factor": VETO_FACTOR,
            "decision_bands": DECISION_BANDS,
        },
        "evaluation_split": {
            "in_sample": [IN_SAMPLE.first_month, IN_SAMPLE.last_month],
            "holdout": [HOLDOUT.first_month, HOLDOUT.last_month],
            "holdout_read_here": False,
        },
        # Defect 1 and 3 are measured on the six-module live snapshot, which is
        # the only place M2 carries a real score.
        "six_module_live_snapshot": {
            "as_of_company_count": int(len(ranking)),
            "trailing_discrimination": discrimination(
                ranking, score_column="total_rasyo_100", return_column="trailing_return"),
            "weighted_contribution_decomposition": weighted_contribution_decomposition(
                ranking, "trailing_return"),
            "veto": veto_diagnostics(ranking),
            "scale_usage": scale_usage(ranking.total_rasyo_100.tolist()),
            "decision_counts": {
                key: int((ranking.decision == key).sum()) for key in ("AL", "IZLE", "UZAK")},
        },
        # Defect 2 needs a time series, which only the five-module variant has.
        "five_module_in_sample_forward": {
            "note": ("M2 carries a real score in 0 of 6000 historical cells, so the "
                     "only model that can be scored forward over time is the "
                     "five-module variant. Its weights are the production weights "
                     "rescaled by 1/0.60."),
            "month_count": len({row["month"] for row in in_sample}),
            "row_count": int(len(scored_forward)),
            "forward_discrimination": discrimination(
                scored_forward, score_column="total_rasyo_100", return_column="forward_return"),
            "scale_usage": scale_usage(scored_forward.total_rasyo_100.tolist()),
        },
        "source_sha256": {
            "current_ranking": _sha(CURRENT_RANKING), "current_prices": _sha(CURRENT_PRICES),
            "five_module_series": _sha(SERIES), "historical_prices": _sha(HISTORICAL_PRICES),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return receipt


def check(*, output_dir: Path = OUTPUT) -> dict:
    recorded = json.loads((output_dir / "receipt.json").read_text(encoding="utf-8"))
    paths = {"current_ranking": CURRENT_RANKING, "current_prices": CURRENT_PRICES,
             "five_module_series": SERIES, "historical_prices": HISTORICAL_PRICES}
    for name, digest in recorded["source_sha256"].items():
        if _sha(paths[name]) != digest:
            raise ValueError(f"R0_SOURCE_HASH_MISMATCH:{name}")
    return {"status": "R0_CHECK_PASS"}


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(check() if args.check else build(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
