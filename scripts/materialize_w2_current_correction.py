from __future__ import annotations

"""Apply and verify the frozen-input W2 CORE/Total correction."""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_w2_current_provenance import differences
from scripts.materialize_current_core_modules import materialize as materialize_core
from scripts.materialize_current_total_rasyo import materialize as materialize_total


LIVE = ROOT / "data/live"
AUDIT = ROOT / "data/audit/w2_current_correction_v1"
PROVENANCE = ROOT / "data/audit/w2_current_provenance_v1"
PRE = AUDIT / "pre_correction"
AUDIT_HEAD = "9b4d8fe64bb05e32786c9f469696eb1d6cf5fd59"
ROOT_CAUSE = "CORE_ARTIFACT_NOT_REGENERATED_AFTER_PRE_W1_FLOW_DERIVATION_FIX"
SNAPSHOT_FILES = (
    "current_core_modules_v1/modules.jsonl",
    "current_core_modules_v1/receipt.json",
    "current_core_modules_v1/rejections.jsonl",
    "current_total_scores_v1/totals.jsonl",
    "current_total_scores_v1/ranking.jsonl",
    "current_total_scores_v1/rejections.jsonl",
    "current_total_scores_v1/receipt.json",
    "current_total_rasyo_run_v1/receipt.json",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def expected_current_core() -> list[dict]:
    evidence = json.loads(gzip.decompress((PROVENANCE / "primary_replay.json.gz").read_bytes()))
    return evidence["regenerated"]["core"]


def ensure_snapshot() -> dict:
    manifest_path = AUDIT / "pre_correction_manifest.json"
    if manifest_path.exists():
        manifest = read(manifest_path)
        for relative, expected in manifest["files"].items():
            if sha(PRE / relative) != expected:
                raise ValueError("W2_PRE_CORRECTION_SNAPSHOT_CHANGED:" + relative)
        return manifest

    baseline = read(PROVENANCE / "baseline.json")
    baseline_hashes = {row["path"].removeprefix("data/live/"): row["sha256"] for row in baseline["live_files"]}
    for relative in SNAPSHOT_FILES:
        source = LIVE / relative
        if sha(source) != baseline_hashes[relative]:
            raise ValueError("W2_PRE_CORRECTION_INPUT_NOT_AT_AUDITED_STATE:" + relative)
        target = PRE / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    manifest = {
        "contract": "W2_PRE_CORRECTION_SNAPSHOT_V1",
        "source_audit_head": AUDIT_HEAD,
        "source_baseline_sha256": sha(PROVENANCE / "baseline.json"),
        "files": {relative: sha(PRE / relative) for relative in SNAPSHOT_FILES},
    }
    write_json(manifest_path, manifest)
    return manifest


def correction_metadata(completed_at: datetime) -> dict:
    return {
        "contract": "W2_FROZEN_CORE_TOTAL_CORRECTION_V1",
        "completed_at": completed_at.isoformat(),
        "source_audit_head": AUDIT_HEAD,
        "root_cause": ROOT_CAUSE,
        "derivation_fix_commit": "fad20cccc88f230628999c1a06770c0f7329a12c",
        "new_market_capture": False,
        "model_policy_changed": False,
        "universe_changed": False,
    }


def _promote(staged: Path, destination: Path, names: tuple[str, ...]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        os.replace(staged / name, destination / name)


def _reseal_windows_artifacts() -> None:
    """One-time conversion of this uncommitted correction to portable LF hashes."""
    for base in (PRE, LIVE):
        for relative in SNAPSHOT_FILES:
            path = base / relative
            if path.exists():
                path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    manifest = read(AUDIT / "pre_correction_manifest.json")
    manifest["hash_mode"] = "LF_CANONICAL_SHA256_V1"
    manifest["files"] = {relative: sha(PRE / relative) for relative in SNAPSHOT_FILES}
    write_json(AUDIT / "pre_correction_manifest.json", manifest)

    core_receipt_path = LIVE / "current_core_modules_v1/receipt.json"
    core_receipt = read(core_receipt_path)
    core_receipt["outputs"] = {name: sha(LIVE / "current_core_modules_v1" / name)
                               for name in ("modules.jsonl", "rejections.jsonl")}
    write_json(core_receipt_path, core_receipt)

    total_dir = LIVE / "current_total_scores_v1"
    total_receipt = read(total_dir / "receipt.json")
    total_receipt["inputs"] = {
        path.name: sha(path) for path in (
            LIVE / "current_total_rasyo_v1/universe.csv",
            LIVE / "current_core_modules_v1/modules.jsonl",
            LIVE / "current_market_modules_v1/modules.csv",
            LIVE / "current_nonfin_valuation_v1/m2.jsonl",
        )
    }
    total_receipt["outputs"] = {name: sha(total_dir / name)
                                for name in ("totals.jsonl", "ranking.jsonl", "rejections.jsonl")}
    write_json(total_dir / "receipt.json", total_receipt)

    assembly_path = LIVE / "current_total_rasyo_run_v1/receipt.json"
    assembly = read(assembly_path)
    assembly["pre_correction_snapshot_manifest"]["sha256"] = sha(AUDIT / "pre_correction_manifest.json")
    for item in assembly["receipts"].values():
        item["sha256"] = sha(ROOT / item["path"])
    write_json(assembly_path, assembly)

    receipt = read(AUDIT / "receipt.json")
    receipt["hash_mode"] = "LF_CANONICAL_SHA256_V1"
    receipt["pre_correction_manifest_sha256"] = sha(AUDIT / "pre_correction_manifest.json")
    receipt["pre_correction_files"] = manifest["files"]
    receipt["live_files"] = {relative: sha(LIVE / relative) for relative in SNAPSHOT_FILES}
    write_json(AUDIT / "receipt.json", receipt)


def apply_correction() -> dict:
    if (AUDIT / "receipt.json").exists():
        if read(AUDIT / "receipt.json").get("hash_mode") != "LF_CANONICAL_SHA256_V1":
            _reseal_windows_artifacts()
        return check()
    snapshot = ensure_snapshot()
    old_core_receipt = read(PRE / "current_core_modules_v1/receipt.json")
    analysis_at = datetime.fromisoformat(old_core_receipt["analysis_at"])
    completed_at = datetime.now(timezone.utc)
    metadata = correction_metadata(completed_at)

    with tempfile.TemporaryDirectory(prefix="rasyo-w2-correction-") as directory:
        staged = Path(directory)
        core_dir, total_dir = staged / "core", staged / "total"
        core_receipt = materialize_core(
            archive_dir=ROOT / "private/reconstructed_kap_archives",
            routes_path=ROOT / "data/backtest_sources/m3_source_package/sector_routes.csv.gz",
            route_manifest_path=ROOT / "data/backtest_sources/m3_source_package/manifest.json",
            output_dir=core_dir,
            analysis_at=analysis_at,
        )
        core_rows = read_rows(core_dir / "modules.jsonl")
        if differences(expected_current_core(), core_rows):
            raise ValueError("W2_CORRECTED_CORE_DOES_NOT_MATCH_AUDITED_REPLAY")
        core_receipt["correction"] = metadata
        write_json(core_dir / "receipt.json", core_receipt)

        total_receipt = materialize_total(
            output_dir=total_dir,
            core_path=core_dir / "modules.jsonl",
            materialized_at=completed_at,
            receipt_metadata=metadata,
        )
        total_rows = read_rows(total_dir / "totals.jsonl")
        ranking_rows = read_rows(total_dir / "ranking.jsonl")
        expected_scores = {"RGYAS": 46.6021011290, "TABGD": 43.9061751217}
        if [row["ticker"] for row in ranking_rows] != ["RGYAS", "TABGD"]:
            raise ValueError("W2_CORRECTED_RANKING_POPULATION_OR_ORDER_CHANGED")
        for row in total_rows:
            if abs(row["total_rasyo_100"] - expected_scores[row["ticker"]]) > 1e-9:
                raise ValueError("W2_CORRECTED_TOTAL_DIFFERS_FROM_AUDIT:" + row["ticker"])
            if row["decision"] != "UZAK":
                raise ValueError("W2_CORRECTED_DECISION_CHANGED:" + row["ticker"])
        if total_receipt["total_valid_count"] != 2 or total_receipt["explicit_rejection_count"] != 805:
            raise ValueError("W2_CORRECTED_COVERAGE_CHANGED")

        _promote(core_dir, LIVE / "current_core_modules_v1", ("modules.jsonl", "rejections.jsonl", "receipt.json"))
        _promote(total_dir, LIVE / "current_total_scores_v1", ("totals.jsonl", "ranking.jsonl", "rejections.jsonl", "receipt.json"))

    component_receipts = {
        name: {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(path)}
        for name, path in {
            "readiness": LIVE / "current_total_rasyo_v1/receipt.json",
            "shares": LIVE / "current_share_basis_v1/receipt.json",
            "prices": LIVE / "current_raw_close_v1/receipt.json",
            "price_level_basis": LIVE / "current_price_level_basis_v1/receipt.json",
            "nonfin_valuation": LIVE / "current_nonfin_valuation_v1/receipt.json",
            "core_modules": LIVE / "current_core_modules_v1/receipt.json",
            "quote_unit": LIVE / "current_quote_unit_v1/receipt.json",
            "market_modules": LIVE / "current_market_modules_v1/receipt.json",
            "total_scores": LIVE / "current_total_scores_v1/receipt.json",
        }.items()
    }
    assembly = {
        "contract": "CURRENT_TOTAL_RASYO_CORRECTION_ASSEMBLY_V1",
        "completed_at": completed_at.isoformat(),
        "mode": "FROZEN_COMPONENT_CORRECTION_NO_NEW_CAPTURE",
        "correction": metadata,
        "pre_correction_snapshot_manifest": {
            "path": "data/audit/w2_current_correction_v1/pre_correction_manifest.json",
            "sha256": sha(AUDIT / "pre_correction_manifest.json"),
        },
        "historical_pit_mixed_into_current": False,
        "neutral_score_injection": False,
        "stage_counts": {
            "universe": 807, "m1": 131, "ek1": 131, "m2": 48,
            "m3": 146, "ek4": 146, "ek9": 11, "total_rasyo": 2,
            "ranking": 2, "explicit_rejections": 805,
        },
        "receipts": component_receipts,
    }
    write_json(LIVE / "current_total_rasyo_run_v1/receipt.json", assembly)
    result = {
        "contract": "W2_CURRENT_CORRECTION_RECEIPT_V1",
        "completed_at": completed_at.isoformat(),
        "status": "CORRECTED_AND_VERIFIED",
        "hash_mode": "LF_CANONICAL_SHA256_V1",
        "root_cause": ROOT_CAUSE,
        "source_audit_head": AUDIT_HEAD,
        "pre_correction_manifest_sha256": sha(AUDIT / "pre_correction_manifest.json"),
        "pre_correction_files": snapshot["files"],
        "live_files": {relative: sha(LIVE / relative) for relative in SNAPSHOT_FILES},
        "counts": {"core": 131, "m2": 48, "ek9": 11, "total": 2, "rejections": 805},
        "total_changes": {
            "RGYAS": {"before": 46.2949460644, "after": 46.6021011290, "decision": "UZAK", "rank": 1},
            "TABGD": {"before": 40.7156025426, "after": 43.9061751217, "decision": "UZAK", "rank": 2},
        },
        "policy": {"model_changed": False, "weights_changed": False, "veto_changed": False,
                   "peer_or_coverage_threshold_changed": False, "universe_changed": False,
                   "neutral_fill": False},
    }
    write_json(AUDIT / "receipt.json", result)
    return check()


def check() -> dict:
    receipt = read(AUDIT / "receipt.json")
    manifest = ensure_snapshot()
    if sha(AUDIT / "pre_correction_manifest.json") != receipt["pre_correction_manifest_sha256"]:
        raise ValueError("W2_CORRECTION_SNAPSHOT_MANIFEST_CHANGED")
    for relative, expected in receipt["live_files"].items():
        if sha(LIVE / relative) != expected:
            raise ValueError("W2_CORRECTED_LIVE_ARTIFACT_CHANGED:" + relative)

    baseline = read(PROVENANCE / "baseline.json")
    allowed = set(SNAPSHOT_FILES)
    for row in baseline["live_files"]:
        relative = row["path"].removeprefix("data/live/")
        if relative not in allowed:
            actual = (LIVE / relative).read_bytes().replace(b"\r\n", b"\n")
            if hashlib.sha256(actual).hexdigest() != row["lf_sha256"]:
                raise ValueError("W2_UNRELATED_LIVE_INPUT_CHANGED:" + relative)

    old_core = read_rows(PRE / "current_core_modules_v1/modules.jsonl")
    original_evidence = json.loads(gzip.decompress((PROVENANCE / "original_core_replay.json.gz").read_bytes()))
    if differences(old_core, original_evidence["regenerated_core"]):
        raise ValueError("W2_PRE_CORRECTION_CORE_NOT_REPRODUCED")
    new_core = read_rows(LIVE / "current_core_modules_v1/modules.jsonl")
    if differences(new_core, expected_current_core()):
        raise ValueError("W2_CORRECTED_CORE_NOT_REPRODUCED")

    metadata = read(LIVE / "current_core_modules_v1/receipt.json")["correction"]
    completed_at = datetime.fromisoformat(receipt["completed_at"])
    with tempfile.TemporaryDirectory(prefix="rasyo-w2-total-check-") as directory:
        regenerated = Path(directory)
        materialize_total(output_dir=regenerated, materialized_at=completed_at, receipt_metadata=metadata)
        for name in ("totals.jsonl", "ranking.jsonl", "rejections.jsonl", "receipt.json"):
            if (regenerated / name).read_bytes().replace(b"\r\n", b"\n") != (LIVE / "current_total_scores_v1" / name).read_bytes().replace(b"\r\n", b"\n"):
                raise ValueError("W2_CORRECTED_TOTAL_NOT_REPRODUCIBLE:" + name)
    assembly = read(LIVE / "current_total_rasyo_run_v1/receipt.json")
    if assembly["contract"] != "CURRENT_TOTAL_RASYO_CORRECTION_ASSEMBLY_V1":
        raise ValueError("W2_CURRENT_ASSEMBLY_RECEIPT_MISSING")
    if assembly["pre_correction_snapshot_manifest"]["sha256"] != sha(AUDIT / "pre_correction_manifest.json"):
        raise ValueError("W2_CURRENT_ASSEMBLY_SNAPSHOT_BINDING_CHANGED")
    for name, item in assembly["receipts"].items():
        if sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("W2_CURRENT_ASSEMBLY_COMPONENT_CHANGED:" + name)
    if assembly["stage_counts"]["total_rasyo"] != 2 or assembly["stage_counts"]["explicit_rejections"] != 805:
        raise ValueError("W2_CURRENT_ASSEMBLY_COUNTS_CHANGED")
    if not all(value is False for value in receipt["policy"].values()):
        raise ValueError("W2_POLICY_OR_UNIVERSE_CHANGED")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = apply_correction() if args.apply else check()
    print(json.dumps({"status": result["status"], "counts": result["counts"],
                      "total_changes": result["total_changes"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
