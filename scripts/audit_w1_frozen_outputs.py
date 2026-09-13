"""Offline W1 smoke check; not the W2 row-level financial provenance audit."""
from __future__ import annotations

import argparse
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
import subprocess
import tempfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.materialize_current_total_rasyo import materialize
from src.analytics.historical_pit_ek9_replay import run_historical_pit_ek9_replay


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def run():
    baseline_path = ROOT / "data/audit/w1_live_fail_closed_v1/baseline.json"
    correction_snapshot = ROOT / "data/audit/w2_current_correction_v1/pre_correction"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    live = {p: item for p, item in baseline["files"].items() if p.startswith("data/live/")}
    changed, newline_only = [], []
    for p, item in live.items():
        frozen = correction_snapshot / p.removeprefix("data/live/")
        candidate = frozen if frozen.exists() else ROOT / p
        if digest(candidate) == item["sha256"]:
            continue
        canonical = subprocess.check_output(["git", "cat-file", "blob", item["git_blob"]], cwd=ROOT)
        if candidate.read_bytes().replace(b"\r\n", b"\n") == canonical.replace(b"\r\n", b"\n"):
            newline_only.append(p)
        else:
            changed.append(p)
    if changed:
        raise RuntimeError(f"frozen live artifacts changed: {changed}")
    market = ROOT / "data/live/current_market_modules_v1"
    receipt = json.loads((market / "receipt.json").read_text())
    modules = pd.read_csv(market / "modules.csv")
    stocks = pd.read_csv(market / "stock_prices.csv.gz")
    indices = pd.read_csv(market / "index_prices.csv.gz")
    calendar = indices.loc[indices.index_code.eq("XU100"), ["trade_date"]]
    result = run_historical_pit_ek9_replay(
        analysis_at=datetime.fromisoformat(receipt["captured_at"]),
        asof_date=date.fromisoformat(receipt["cutoff_date"]),
        market_asof_date=date.fromisoformat(receipt["market_asof_date"]),
        universe=modules[["ticker"]], trading_calendar=calendar, stock_prices=stocks)
    before = modules.set_index("ticker").ek9.dropna().sort_index()
    after = result.ek9_scores.set_index("ticker").ek9.sort_index()
    if not before.index.equals(after.index) or not np.allclose(before, after, rtol=0, atol=1e-15):
        raise RuntimeError("current frozen Ek9 score population/value changed")
    rejections = [json.loads(line) for line in (market / "rejections.jsonl").read_text().splitlines()]
    before_rejections = sorted((r["ticker"], r["reason"]) for r in rejections if r["module"] == "Ek9")
    after_rejections = sorted(zip(result.rejections.ticker, result.rejections.reason))
    if before_rejections != after_rejections:
        raise RuntimeError("current frozen Ek9 rejections changed")
    total_matches = {}
    with tempfile.TemporaryDirectory(prefix="rasyo-w1-totals-") as directory:
        regenerated = Path(directory)
        total_receipt = materialize(
            output_dir=regenerated,
            core_path=correction_snapshot / "current_core_modules_v1/modules.jsonl",
        )
        for name in ("totals.jsonl", "ranking.jsonl", "rejections.jsonl"):
            original = correction_snapshot / "current_total_scores_v1" / name
            # Compare decoded JSON, not platform-specific newline bytes.
            old = [json.loads(s) for s in original.read_text().splitlines()]
            new = [json.loads(s) for s in (regenerated / name).read_text().splitlines()]
            if old != new:
                raise RuntimeError(f"current frozen Total output changed: {name}")
            total_matches[name] = {"rows": len(new), "records_identical": True,
                                   "original_sha256": digest(original)}
    return {"contract": "W1_FROZEN_OUTPUT_SMOKE_V1", "baseline_sha256": digest(baseline_path),
            "network_access": False, "live_artifacts_modified": False,
            "frozen_artifacts_byte_identical": not newline_only,
            "frozen_artifacts_content_identical": True,
            "checkout_newline_only_differences": newline_only,
            "w2_pre_correction_snapshot_used": correction_snapshot.exists(),
            "frozen_artifact_count": len(live),
            "m2_follow": {"m2": baseline["counts"]["m2"], "follow": baseline["counts"]["follow"],
                          "check": "IMMUTABLE_SNAPSHOT_ONLY_NOT_REDERIVED"},
            "ek9": {"production_engine": "run_historical_pit_ek9_replay",
                    "scope": "EXISTING_CURRENT_CAPTURE_NOT_HISTORICAL_COVERAGE_GAIN",
                    "valid": len(after), "rejected": len(after_rejections),
                    "max_absolute_score_delta": float((before-after).abs().max()),
                    "rejection_records_identical": True,
                    "inputs_sha256": {p.name: digest(p) for p in (
                        market / "receipt.json", market / "stock_prices.csv.gz", market / "index_prices.csv.gz")}},
            "total": {"production_engine": "materialize_current_total_rasyo.materialize",
                      "valid": total_receipt["total_valid_count"],
                      "universe": total_receipt["universe_count"], "outputs": total_matches},
            "w2_full_source_provenance_audit": "NOT_PERFORMED",
            "known_stale_receipt": baseline["known_stale_receipt"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"ek9": result["ek9"]["valid"], "total": result["total"]["valid"],
                      "frozen_artifacts_content_identical": True,
                      "checkout_newline_only_differences": len(result["checkout_newline_only_differences"])}))
