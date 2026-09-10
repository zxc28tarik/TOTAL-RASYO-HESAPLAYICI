from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.total_rasyo_score import MODULE_KEYS, compute_total_rasyo

CONTRACT = "CURRENT_TOTAL_RASYO_MATERIALIZATION_V1"
OUTPUT = ROOT / "data/live/current_total_scores_v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def materialize(output_dir: Path = OUTPUT) -> dict:
    universe_path = ROOT / "data/live/current_total_rasyo_v1/universe.csv"
    core_path = ROOT / "data/live/current_core_modules_v1/modules.jsonl"
    market_path = ROOT / "data/live/current_market_modules_v1/modules.csv"
    m2_path = ROOT / "data/live/current_nonfin_valuation_v1/m2.jsonl"
    universe = pd.read_csv(universe_path)
    core = [json.loads(line) for line in (
        core_path
    ).read_text(encoding="utf-8").splitlines()]
    market = pd.read_csv(market_path)
    core_map = {row["ticker"]: row for row in core}
    market_map = market.set_index("ticker").to_dict("index")
    m2_rows = [json.loads(line) for line in (
        m2_path
    ).read_text(encoding="utf-8").splitlines()]
    m2_map = {row["ticker"]: row["m2"] for row in m2_rows}
    totals, rejections = [], []
    for ticker in sorted(set(universe.ticker.astype(str))):
        c, m = core_map.get(ticker, {}), market_map.get(ticker, {})
        scores = {
            "M2": m2_map.get(ticker), "M1": c.get("m1"), "M3": m.get("m3"),
            "Ek4": m.get("ek4"), "Ek1": c.get("ek1"), "Ek9": m.get("ek9"),
        }
        missing = [key for key in MODULE_KEYS if pd.isna(scores[key])]
        if missing or c.get("good_count_ge8") is None:
            rejections.append({
                "ticker": ticker, "reason": "MISSING_CURRENT_COMPONENTS",
                "missing_modules": missing,
                "good_count_missing": c.get("good_count_ge8") is None,
            })
            continue
        result = compute_total_rasyo(scores, good_count_ge8=c["good_count_ge8"])
        totals.append({
            "ticker": ticker, **{key.lower(): scores[key] for key in MODULE_KEYS},
            "good_count_ge8": c["good_count_ge8"], "base_score": result["base_score"],
            "final_score": result["final_score"], "total_rasyo_100": result["total_rasyo_100"],
            "veto_flag": result["veto_flag"], "decision": result["decision"],
        })
    ranking = sorted(totals, key=lambda row: (-row["final_score"], row["ticker"]))
    for rank, row in enumerate(ranking, start=1):
        row["rank"] = rank
    output_dir.mkdir(parents=True, exist_ok=True)
    total_path, ranking_path = output_dir / "totals.jsonl", output_dir / "ranking.jsonl"
    rejection_path = output_dir / "rejections.jsonl"
    total_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in totals), encoding="utf-8")
    ranking_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in ranking), encoding="utf-8")
    rejection_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rejections), encoding="utf-8")
    receipt = {
        "contract": CONTRACT, "materialized_at": datetime.now(timezone.utc).isoformat(),
        "universe_count": len(universe), "total_valid_count": len(totals),
        "ranking_count": len(ranking), "explicit_rejection_count": len(rejections),
        "missing_module_counts": dict(Counter(
            module for row in rejections for module in row["missing_modules"]
        )),
        "production_formula": "src.analytics.total_rasyo_score.compute_total_rasyo",
        "m2_input_count": len(m2_rows),
        "inputs": {path.name: _sha(path) for path in (
            universe_path, core_path, market_path, m2_path,
        )},
        "weight_redistribution": False, "neutral_fill": False,
        "ranking_order": "final_score DESC, ticker ASC",
        "outputs": {p.name: _sha(p) for p in (total_path, ranking_path, rejection_path)},
    }
    (output_dir / "receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    print(json.dumps(materialize(), indent=2))
