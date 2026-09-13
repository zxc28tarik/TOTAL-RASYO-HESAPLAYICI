"""Capture once / verify the W1 pre-change evidence without modifying live data."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "data/audit/w1_live_fail_closed_v1/baseline.json"
CODE = ["src/analytics/run_daily_pipeline.py", "src/analytics/ek9_volatility.py",
        "src/analytics/historical_pit_ek9_replay.py", "src/analytics/total_rasyo_score.py"]
SNAPSHOTS = {
    "m2": "data/live/current_nonfin_valuation_v1/m2.jsonl",
    "follow": "data/live/current_nonfin_valuation_v1/follow.jsonl",
    "market": "data/live/current_market_modules_v1/modules.csv",
    "core": "data/live/current_core_modules_v1/modules.jsonl",
    "totals": "data/live/current_total_scores_v1/totals.jsonl",
    "ranking": "data/live/current_total_scores_v1/ranking.jsonl",
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture():
    if BASELINE.exists():
        raise RuntimeError("BASELINE_ALREADY_EXISTS: use --check; never overwrite")
    if git("diff", "HEAD", "--", "src", "sql", "config", "data/live"):
        raise RuntimeError("PRODUCTION_OR_LIVE_DATA_ALREADY_MODIFIED")
    tracked = git("ls-files", "data/live", "sql").splitlines()
    paths = [p for p in tracked if p.startswith("sql/") or
             ("/raw/" not in p and "/raw_" not in p and
              (p.endswith("receipt.json") or p in SNAPSHOTS.values() or
               p.endswith(("valuations.jsonl", "universe.csv"))))]
    entries = {p: {"sha256": sha(ROOT / p), "bytes": (ROOT / p).stat().st_size,
                   "git_blob": git("rev-parse", f"HEAD:{p}")}
               for p in sorted(set(paths + CODE))}
    snapshots = {}
    for name, p in SNAPSHOTS.items():
        with (ROOT / p).open(encoding="utf-8", newline="") as stream:
            snapshots[name] = list(csv.DictReader(stream)) if p.endswith(".csv") else [
                json.loads(line) for line in stream if line.strip()]
    receipt = json.loads((ROOT / "data/live/current_total_scores_v1/receipt.json").read_text())
    assert len(snapshots["m2"]) == len(snapshots["follow"]) == 48
    assert len(snapshots["totals"]) == len(snapshots["ranking"]) == 2
    assert sum(bool(row.get("ek9")) for row in snapshots["market"]) == 11
    document = {
        "contract": "W1_PRE_CHANGE_BASELINE_V1", "captured_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": git("rev-parse", "HEAD"), "production_diff_empty": True,
        "working_tree_at_capture": git("status", "--porcelain").splitlines(),
        "command": "python scripts/audit_w1_baseline.py --capture",
        "schema_inventory_kind": "VERSIONED_DDL_NOT_LIVE_DATABASE_INSPECTION",
        "schema_scores": {
            "analytics.m2_period_comparison.m2_final": "NUMERIC nullable (006)",
            "analytics.alpha_trailing.alpha_score": "NUMERIC nullable (006)",
            "analytics.bank_m2_scores.m2_score": "NUMERIC NOT NULL (013)",
            **{f"analytics.{name}_m2_scores.m2_score": "NUMERIC NOT NULL CHECK 0..1"
               for name in ("nonfin", "holding", "gyo", "insurance", "financial_institution")},
        },
        "counts": {"m2": 48, "follow": 48, "ek9": 11, "total": 2, "ranking": 2,
                   "universe": receipt["universe_count"], "rejections": receipt["explicit_rejection_count"]},
        "known_stale_receipt": "current_total_rasyo_run_v1/receipt.json describes the older Sep 8 M2=0 run; preserved, not relabeled",
        "files": entries, "snapshot_sources": SNAPSHOTS, "snapshots": snapshots,
    }
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    with BASELINE.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"path": BASELINE.relative_to(ROOT).as_posix(), "sha256": sha(BASELINE),
                      "counts": document["counts"], "captured_before_production_edit": True}))


def check():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    changed = [p for p, item in baseline["files"].items() if sha(ROOT / p) != item["sha256"]]
    live_changed = [p for p in changed if p.startswith("data/live/")]
    print(json.dumps({"baseline_sha256": sha(BASELINE), "changed_files": changed,
                      "live_artifacts_byte_identical": not live_changed, "live_changed": live_changed}))
    if live_changed:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    capture() if args.capture else check()
