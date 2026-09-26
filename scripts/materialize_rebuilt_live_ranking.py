from __future__ import annotations

"""Publish the live ranking under the rebuilt scoring layer, beside the old one.

The old artifact under data/live/current_total_scores_v1 is left untouched so
the two can be compared row for row. This writes data/live/rebuilt_total_scores_v1.

Raw inputs, not clipped scores
------------------------------
Ranking is monotone, so ranking an unclipped score equals ranking its input. But
M3, Ek4 and Ek9 were clipped by their old bands -- 44% of M3 sits at exactly 0
in the live snapshot -- and ranking a clipped score leaves that whole group tied
at the bottom. So the three production replays are re-run on the persisted live
market data to recover the exact values they compute BEFORE the band:
alpha_trailing, excess_return_20d and volatility. Nothing is approximated; the
same functions that produced the live modules produce these.

Direction
---------
Every input is ranked so that higher means better, as the ORIGINAL design
intended. Volatility is negated because the design scores low volatility as good
(Ek9 = 1 - vol/cap). Ek1 is NOT flipped even though R2 found it negatively signed
against forward returns: choosing a sign because the sample rewarded it is the
in-sample fitting this rebuild refuses. The finding is reported, not exploited.

M2 is an overlay
----------------
M2 is published alongside but kept out of the score. It is absent from all 6000
historical cells, so it cannot be validated, and an unvalidatable module should
not sit in the score that everything else is judged by. It stays visible because
the information has value; it goes back into the score if and when it can be
tested.
"""

import argparse
from collections import Counter
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.historical_pit_ek4_replay import run_historical_pit_ek4_replay
from src.analytics.historical_pit_ek9_replay import run_historical_pit_ek9_replay
from src.analytics.historical_pit_m3_replay import run_historical_pit_m3_replay
from src.analytics.rebuilt_total_score import RebuiltScoreConfig, rebuilt_scores
from scripts.materialize_current_market_modules import scored_routes

CONTRACT = "REBUILT_LIVE_RANKING_V1"
MARKET = ROOT / "data/live/current_market_modules_v1"
CORE = ROOT / "data/live/current_core_modules_v1/modules.jsonl"
M2 = ROOT / "data/live/current_nonfin_valuation_v1/m2.jsonl"
ROUTES = ROOT / "data/live/current_sector_routes_v1/sector_routes.csv.gz"
OLD_RANKING = ROOT / "data/live/current_total_scores_v1/ranking.jsonl"
OUTPUT = ROOT / "data/live/rebuilt_total_scores_v1"

# Raw input columns, oriented so that higher is better as the design intended.
CORE_MODULES = ("M1", "M3", "Ek4", "Ek1", "Ek9")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def raw_market_inputs() -> pd.DataFrame:
    """Re-run the three production replays on the persisted live market data."""
    receipt = json.loads((MARKET / "receipt.json").read_text(encoding="utf-8"))
    analysis = datetime.fromisoformat(receipt["captured_at"])
    cutoff = date.fromisoformat(receipt["cutoff_date"])
    market_asof = date.fromisoformat(receipt["market_asof_date"])

    stocks = pd.read_csv(MARKET / "stock_prices.csv.gz")
    stocks["trade_date"] = pd.to_datetime(stocks.trade_date).dt.date
    indices = pd.read_csv(MARKET / "index_prices.csv.gz")
    indices["trade_date"] = pd.to_datetime(indices.trade_date).dt.date
    calendar = pd.DataFrame({"trade_date": sorted(
        day for day in indices.loc[indices.index_code.eq("XU100"), "trade_date"].unique()
        if day <= market_asof)})

    routes = pd.read_csv(ROUTES, dtype=str)
    routes["valid_from"] = pd.to_datetime(routes.valid_from)
    routes["valid_to"] = pd.to_datetime(routes.valid_to, errors="coerce")
    day = pd.Timestamp(cutoff)
    universe = routes.loc[
        scored_routes(routes) & routes.valid_from.le(day)
        & (routes.valid_to.isna() | routes.valid_to.gt(day)),
        ["ticker", "sector_index_code"]].drop_duplicates().sort_values("ticker")

    common = dict(analysis_at=analysis, asof_date=cutoff, market_asof_date=market_asof,
                  universe=universe, trading_calendar=calendar, stock_prices=stocks)
    m3 = run_historical_pit_m3_replay(**common, index_prices=indices).m3_scores
    ek4 = run_historical_pit_ek4_replay(**common, index_prices=indices).ek4_scores
    ek9 = run_historical_pit_ek9_replay(**common).ek9_scores

    frame = universe[["ticker"]].copy()
    frame = frame.merge(m3[["ticker", "alpha_trailing", "m3"]], on="ticker", how="left")
    frame = frame.merge(ek4[["ticker", "excess_return_20d", "ek4"]], on="ticker", how="left")
    frame = frame.merge(ek9[["ticker", "volatility", "ek9"]], on="ticker", how="left")
    return frame, receipt


def build(*, output_dir: Path = OUTPUT) -> dict:
    market, market_receipt = raw_market_inputs()
    core = pd.DataFrame(_rows(CORE))[["ticker", "m1", "good_count_ge8"]]
    m2 = pd.DataFrame(_rows(M2))[["ticker", "m2"]] if M2.exists() else pd.DataFrame(columns=["ticker", "m2"])

    frame = market.merge(core, on="ticker", how="inner").merge(m2, on="ticker", how="left")
    frame["month"] = market_receipt["market_asof_date"][:7]
    # Oriented inputs: higher is better, as the original design intended.
    frame["M1"] = frame["m1"]
    frame["M3"] = frame["alpha_trailing"]
    frame["Ek4"] = frame["excess_return_20d"]
    frame["Ek1"] = frame["good_count_ge8"]
    frame["Ek9"] = -frame["volatility"]

    config = RebuiltScoreConfig(modules=list(CORE_MODULES))
    scored = rebuilt_scores(frame, config=config)
    valid = scored.loc[scored.total_rasyo_100.notna()].copy()
    valid = valid.sort_values(["total_rasyo_100", "ticker"], ascending=[False, True])
    valid["rank"] = range(1, len(valid) + 1)

    ranking = [{
        "rank": int(row.rank), "ticker": row.ticker,
        "total_rasyo_100": float(row.total_rasyo_100), "decision": row.decision,
        "module_ranks": {m: float(getattr(row, f"{m}__rank")) for m in CORE_MODULES},
        "raw_inputs": {
            "alpha_trailing": float(row.alpha_trailing),
            "excess_return_20d": float(row.excess_return_20d),
            "volatility": float(row.volatility),
            "m1": float(row.m1), "good_count_ge8": int(row.good_count_ge8),
        },
        "m2_overlay": None if pd.isna(row.m2) else float(row.m2),
    } for row in valid.itertuples()]

    old = pd.DataFrame(_rows(OLD_RANKING))
    old_rank = dict(zip(old.ticker, old["rank"]))
    common_tickers = [row["ticker"] for row in ranking if row["ticker"] in old_rank]
    new_rank = {row["ticker"]: row["rank"] for row in ranking}
    old_top = set(old.nsmallest(25, "rank").ticker)
    new_top = {row["ticker"] for row in ranking[:25]}
    old_clip = float((market.m3 <= 1e-9).mean())

    output_dir.mkdir(parents=True, exist_ok=True)
    ranking_path = output_dir / "ranking.jsonl"
    ranking_path.write_text("".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ranking),
        encoding="utf-8", newline="\n")

    receipt = {
        "contract": CONTRACT,
        "market_asof_date": market_receipt["market_asof_date"],
        "scoring_layer": "src.analytics.rebuilt_total_score",
        "core_modules": list(CORE_MODULES),
        "weights": dict(config.weights),
        "veto": None,
        "bands": {"al_share": config.bands.al_share, "izle_share": config.bands.izle_share},
        "m2_role": "OVERLAY_PUBLISHED_NOT_SCORED_UNVALIDATABLE_HISTORICALLY",
        "ek1_direction": "AS_DESIGNED_NOT_FLIPPED_DESPITE_NEGATIVE_R2_SIGN",
        "ek9_direction": "NEGATED_VOLATILITY_AS_DESIGNED",
        "raw_inputs_recovered_from_production_replays": [
            "run_historical_pit_m3_replay.alpha_trailing",
            "run_historical_pit_ek4_replay.excess_return_20d",
            "run_historical_pit_ek9_replay.volatility",
        ],
        "old_m3_share_pinned_at_zero": old_clip,
        "scored_count": len(ranking),
        "old_scored_count": int(len(old)),
        "decision_counts": dict(Counter(row["decision"] for row in ranking)),
        "old_decision_counts": dict(Counter(old.decision)),
        "top25_overlap_with_old": len(old_top & new_top),
        # DataFrame.corr ranks internally; Series.corr(method="spearman") would
        # pull in scipy, which this environment does not carry.
        "rank_spearman_with_old_on_common": (
            float(pd.DataFrame({
                "old": [old_rank[t] for t in common_tickers],
                "new": [new_rank[t] for t in common_tickers],
            }).corr(method="spearman").iloc[0, 1])
            if len(common_tickers) >= 3 else None),
        "common_ticker_count": len(common_tickers),
        "predictive_claim": "NONE_R2_FOUND_NO_DEFENSIBLE_FORWARD_SKILL",
        "source_sha256": {
            "market_receipt": _sha(MARKET / "receipt.json"),
            "stock_prices": _sha(MARKET / "stock_prices.csv.gz"),
            "index_prices": _sha(MARKET / "index_prices.csv.gz"),
            "core": _sha(CORE), "routes": _sha(ROUTES),
        },
        "outputs": {"ranking.jsonl": _sha(ranking_path)},
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return receipt


def main() -> None:
    argparse.ArgumentParser().parse_args()
    print(json.dumps(build(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
