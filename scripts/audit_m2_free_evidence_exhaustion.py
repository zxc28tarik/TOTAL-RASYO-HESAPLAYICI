"""Emit an exact fail-closed disposition for every 5/6-module M2 priority cell."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIORITY = ROOT / "data/audit/m2_priority_matrix_v1/m2_priority_cells.jsonl.gz"
KAP = ROOT / "data/backtest_sources/kap_share_class_history_v1/share_class_observations.jsonl.gz"
BRIDGES = ROOT / "data/backtest_sources/borsa_thb_action_bridge_v1/cell_bridges.jsonl.gz"
OUT = ROOT / "data/audit/m2_free_evidence_exhaustion_v1"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]


def _encode(value: object, *, indent: int | None = None) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=None if indent else (",", ":"), indent=indent,
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def family_blocker(family: str) -> str | None:
    if family == "HOLDING":
        return "PIT_NAV_SOURCE_MISSING"
    if family == "GYO":
        return "PIT_NAV_AND_PROPERTY_PORTFOLIO_SOURCE_MISSING"
    return None


def audit(output_dir: Path = OUT) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    kap_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for row in _rows(KAP):
        if row["usable"]:
            kap_by_ticker[row["ticker"]].append(row)
    bridge_by_cell = {(row["ticker"], row["month"]): row for row in _rows(BRIDGES)}
    dispositions = []
    for cell in _rows(PRIORITY):
        blocker = family_blocker(cell["historical_family"])
        detail: dict[str, object] = {}
        if blocker is None:
            cutoff = datetime.fromisoformat(cell["knowledge_cutoff_at"])
            eligible = []
            for row in kap_by_ticker[cell["ticker"]]:
                published = datetime.strptime(
                    row["published_at_local"], "%d/%m/%Y %H:%M:%S").replace(
                        tzinfo=timezone(timedelta(hours=3)))
                if published <= cutoff:
                    eligible.append((published, row))
            if not eligible:
                blocker = "EXPLICIT_NOMINAL_SHARE_STATE_MISSING_AT_CUTOFF"
            else:
                published, state = max(eligible, key=lambda item: item[0])
                detail = {
                    "share_state_published_at_local": state["published_at_local"],
                    "derived_shares": state["derived_shares"],
                    "total_nominal_value_try": state["total_nominal_value_try"],
                }
                bridge = bridge_by_cell.get((cell["ticker"], cell["month"]))
                if bridge is None:
                    blocker = "ACTION_COMPLETENESS_BRIDGE_NOT_MATERIALIZED"
                elif bridge["status"] != "BRIDGE_COMPLETE_BEFORE_CUTOFF":
                    blocker = "ACTION_COMPLETENESS_EVIDENCE_REJECTED"
                    detail["bridge_reasons"] = bridge["reasons"]
                else:
                    blocker = None
        status = "M2_PREREQUISITES_PROVEN" if blocker is None else "EXPLICIT_REJECTION"
        dispositions.append({
            "ticker": cell["ticker"], "month": cell["month"],
            "historical_family": cell["historical_family"],
            "knowledge_cutoff_at": cell["knowledge_cutoff_at"],
            "price_trade_date": cell["price_trade_date"], "status": status,
            "primary_blocker": blocker, **detail,
        })
    dispositions.sort(key=lambda row: (row["month"], row["ticker"]))
    out_path = output_dir / "cell_dispositions.jsonl.gz"
    out_path.write_bytes(gzip.compress(b"".join(_encode(row) for row in dispositions), mtime=0))
    blockers = Counter(row["primary_blocker"] for row in dispositions if row["primary_blocker"])
    families = Counter(row["historical_family"] for row in dispositions)
    proven = sum(row["status"] == "M2_PREREQUISITES_PROVEN" for row in dispositions)
    receipt = {
        "contract": "M2_FREE_EVIDENCE_EXHAUSTION_V1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "target_definition": "P4 module_count=5 and missing_modules contains M2",
        "target_cells": len(dispositions), "family_counts": dict(sorted(families.items())),
        "share_action_prerequisites_proven_cells": proven,
        "m2_authoritative_cells_materialized": 0,
        "total_scores_materialized": 0,
        "p5_backtest_started": False,
        "explicit_rejections": len(dispositions) - proven,
        "proven_but_not_materialized_reasons": ([
            "VERIFIED_SHARE_PEER_COHORT_INSUFFICIENT",
            "SOURCE_DERIVATION_PROFILE_MISMATCH",
        ] if proven else []),
        "primary_blocker_counts": dict(sorted(blockers.items())),
        "fail_closed_preserved": True,
        "one_try_equals_one_share_assumed": False,
        "inputs": {str(path.relative_to(ROOT)).replace("\\", "/"): _sha(path)
                   for path in (PRIORITY, KAP, BRIDGES)},
        "outputs": {out_path.name: _sha(out_path)},
    }
    (output_dir / "receipt.json").write_bytes(_encode(receipt, indent=2))
    return receipt


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
