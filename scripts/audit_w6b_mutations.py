"""Bounded W6-B mutations; the production checkout and databases stay untouched.

Each mutation weakens one verification gate of the BANK coe/macro_cap audit.
All of them must be KILLED by tests/test_w6b_bank_coe_macro_cap.py.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

AUDITOR = "scripts/audit_w6b_bank_coe_macro_cap.py"
TESTS = ["tests/test_w6b_bank_coe_macro_cap.py"]

MUTATIONS = [
    (
        "macro_cap_arithmetic_gate_removed",
        "        if recorded != expected.quantize(recorded):",
        "        if False:",
    ),
    (
        "lookahead_gate_removed",
        "        if publication > cutoff:",
        "        if False:",
    ),
    (
        "latest_eligible_vintage_gate_removed",
        "        elif eligible[-1] != cell[\"ovp\"]:",
        "        elif False:",
    ),
    (
        "unknown_cell_gate_removed",
        "        if source is None:\n            unknown_cell.append(key_fields(cell))",
        "        if False:\n            unknown_cell.append(key_fields(cell))",
    ),
    (
        "bank_family_gate_removed",
        '        elif source.get("historical_family") != "BANK":',
        "        elif False:",
    ),
    (
        "unknown_vintage_tolerated",
        '            raise W6BAuditError(f"MACRO_CAP_VINTAGE_NOT_IN_RECEIPT:{cell[\'ovp\']}")',
        '            vintage = {"gdp_last_billion_try": 1, "gdp_previous_billion_try": 1,\n'
        '                       "published_at": cell["ovp_published_at"], "url": "", "sha256": "",\n'
        '                       "size_bytes": 0}',
    ),
    (
        "macro_cap_status_hardcoded_resolved",
        '        "status": "RESOLVED_FROM_OFFICIAL_DATED_SOURCES" if clean else "VERIFICATION_FAILED",',
        '        "status": "RESOLVED_FROM_OFFICIAL_DATED_SOURCES",',
    ),
    (
        "coe_reason_code_hardcoded",
        '        "reason_code": coe["rejection"],',
        '        "reason_code": "COE_METHODOLOGY_UNVERSIONED_AND_INPUT_LINEAGE_INCOMPLETE",',
    ),
    (
        "priority_overlap_hardcoded_zero",
        '        "overlap_with_priority_cells": len(bank_keys & priority),',
        '        "overlap_with_priority_cells": 0,',
    ),
    (
        "priority_cohort_quoted_instead_of_recomputed",
        '        "priority_cells_total": len(priority),',
        '        "priority_cells_total": 3017,',
    ),
    (
        "raw_bytes_caveat_suppressed",
        '            "raw_source_bytes_committed": receipt.get("raw_source_bytes_committed", False),',
        '            "raw_source_bytes_committed": True,',
    ),
]


def _counts(report: Path) -> dict:
    suite = ET.parse(report).getroot()
    node = suite if suite.tag == "testsuite" else suite.find("testsuite")
    return {
        "tests": int(node.get("tests", 0)),
        "failures": int(node.get("failures", 0)),
        "errors": int(node.get("errors", 0)),
        "skipped": int(node.get("skipped", 0)),
    }


def run(root: Path) -> dict:
    original = (root / AUDITOR).read_text(encoding="utf-8")
    env = dict(os.environ)
    env.pop("TOTAL_RASYO_TEST_DSN", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    results = []
    with tempfile.TemporaryDirectory(prefix="rasyo-w6b-mutations-") as directory:
        checkout = Path(directory)
        shutil.copytree(root / "scripts", checkout / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (checkout / "tests").mkdir()
        for name in TESTS + ["pytest.ini"]:
            shutil.copyfile(root / name, checkout / name)
        for name in ("data", "src", "config"):
            (checkout / name).symlink_to(root / name, target_is_directory=True)
        for name, old, new in [("baseline", "", "")] + MUTATIONS:
            source = original
            if old:
                if old not in source:
                    raise RuntimeError(f"MUTATION_ANCHOR_NOT_FOUND:{name}")
                source = source.replace(old, new, 1)
            (checkout / AUDITOR).write_text(source, encoding="utf-8")
            report = checkout / f"{name}.xml"
            subprocess.run(
                [sys.executable, "-m", "pytest", "-q", *TESTS, f"--junitxml={report}"],
                cwd=checkout, env=env, capture_output=True, text=True, check=False,
            )
            counts = _counts(report)
            failed = counts["failures"] + counts["errors"] > 0
            results.append({
                "name": name,
                **counts,
                "status": ("PASS" if not failed else "UNEXPECTED_FAILURE")
                if name == "baseline"
                else ("KILLED" if failed else "SURVIVED"),
            })
    if (root / AUDITOR).read_text(encoding="utf-8") != original:
        raise RuntimeError("W6B_PRODUCTION_CHECKOUT_MUTATED")
    killed = sum(1 for item in results if item["status"] == "KILLED")
    return {
        "contract": "W6B_BANK_COE_MACRO_CAP_MUTATION_AUDIT_V1",
        "database_access": False,
        "auditor": AUDITOR,
        "auditor_sha256": sha256(original.encode("utf-8")).hexdigest(),
        "mutations_total": len(MUTATIONS),
        "mutations_killed": killed,
        "all_killed": killed == len(MUTATIONS),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    payload = run(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    )
    print(json.dumps({k: payload[k] for k in ("mutations_total", "mutations_killed", "all_killed")}))
    if not payload["all_killed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
