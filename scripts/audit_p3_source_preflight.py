"""Exhaustive membership accounting when the original financial catalog is absent.

This is a source preflight, not a successful P3 score-input materialization.
It deliberately cannot emit ready cells, scores, portfolio returns or PASS.
"""
from pathlib import Path
import argparse
import hashlib
import json
from scripts.audit_astra_source_inventory import audit
from scripts.build_historical_m3_source_package import _historical_membership

ROOT = Path(__file__).resolve().parents[1]

def build():
    inventory = audit(ROOT)
    blockers = [r for r in inventory["entries"] if r["status"] != "MATCH"]
    if not blockers:
        raise RuntimeError("Source bytes recovered; run real P3 materialization instead of blocked preflight")
    members = _historical_membership(ROOT).sort_values(["signal_date", "ticker"])
    if len(members) != 6000 or members.duplicated(["signal_date", "ticker"]).any():
        raise RuntimeError("6000 exact membership keys required")
    counts = members.groupby("signal_date").size()
    if len(counts) != 60 or not counts.eq(100).all():
        raise RuntimeError("60 cutoffs each with 100 members required")
    members["status"] = "REJECTED_AT_SOURCE_PREFLIGHT"
    members["reason"] = "ORIGINAL_FINANCIAL_CATALOG_BYTES_MISSING"
    members["source_audit"] = "source_inventory.json"
    raw = members.to_csv(index=False, lineterminator="\n").encode()
    receipt = {"contract": "P3_SOURCE_PREFLIGHT_V1", "profile": "EXPERIMENTAL_RISK_ACCEPTED_5Y",
        "status": "BLOCKED", "materialization_completed": False,
        "membership_cells": 6000, "cutoffs": 60, "ready_cells": 0,
        "rejected_at_source_preflight": 6000, "cells_sha256": hashlib.sha256(raw).hexdigest(),
        "missing_sources": blockers,
        "P4": "NOT_RUN_NO_VERIFIED_SCORE_INPUTS", "P5": "NOT_RUN_NO_RANKINGS",
        "P6": "NOT_ELIGIBLE", "P7": "HISTORICAL_VERSION_ENUMERATION_UNPROVEN"}
    return raw, receipt

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    first, receipt = build()
    second, second_receipt = build()
    if first != second or receipt != second_receipt:
        raise RuntimeError("P3 preflight is not deterministic")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "p3_source_preflight_cells.csv").write_bytes(first)
    (args.output_dir / "p3_source_preflight.json").write_bytes((json.dumps(receipt,indent=2,sort_keys=True)+"\n").encode())
    print("BLOCKED: 6000/6000 membership cells explicitly accounted; no score-input-ready claim")
