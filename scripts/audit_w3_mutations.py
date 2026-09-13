"""Bounded W3 mutations; the production checkout and databases stay untouched.

Each mutation weakens one evidence gate in the W3 reconciliation auditor. A gate
that no test can distinguish from its weakened form is not a gate, so every
mutation below must be KILLED by tests/test_w3_price_populations.py.
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

AUDITOR = "scripts/audit_w3_price_populations.py"
TESTS = ["tests/test_w3_price_populations.py"]

MUTATIONS = [
    (
        "alias_without_official_lineage_accepted",
        """            if lineage is None:
                reason, evidence = "UNRESOLVED_BLOCKED", {
                    "detail": "alias-resolved price row without an official lineage row"
                }
            else:""",
        """            if False:
                reason, evidence = "UNRESOLVED_BLOCKED", {"detail": "unused"}
            else:
                lineage = lineage or {
                    "effective_date": "", "new_ticker": "",
                    "source_workbook_sha256": "", "event_sha256": "",
                }""",
    ),
    (
        "corporate_action_continuity_assumed_proven",
        """                # An empty action inventory is not proof that no action occurred.
                "verified": False,""",
        """                "verified": True,""",
    ),
    (
        "share_class_identity_assumed_proven",
        '"verified": identity_rows > 0,',
        '"verified": True,',
    ),
    (
        "module_union_consistency_gate_removed",
        "if stock_window != flat_stock_window:",
        "if False:",
    ),
    (
        "exact_ticker_row_reported_as_lineage",
        """        elif row is not None:
            reason, evidence = "OTHER_EVIDENCED", {""",
        """        elif row is not None:
            reason, evidence = "TICKER_LINEAGE", {""",
    ),
    (
        "signal_calendar_gate_removed",
        "elif signal_date not in signal_dates:",
        "elif False:",
    ),
    (
        "source_series_coverage_gate_removed",
        'elif signal_date < bounds["first"] or signal_date > bounds["last"]:',
        "elif False:",
    ),
    (
        "exhaustiveness_gate_removed",
        """    validate_execution_assignments(
        populations_keys["EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING"], assignments
    )""",
        "    pass",
    ),
    (
        "duplicate_cell_gate_removed",
        """        if key in seen:
            raise W3AuditError(f"EXECUTION_CELL_ASSIGNED_TWICE:{key[1]}@{key[0]}")""",
        "        pass",
    ),
    (
        "closed_taxonomy_gate_removed",
        "        if item[\"reason_code\"] not in EXECUTION_REASON_CODES:",
        "        if False:",
    ),
    (
        "evidence_requirement_removed",
        '        if not item.get("evidence"):',
        "        if False:",
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
    env.pop("TOTAL_RASYO_TEST_DSN", None)  # never run a mutant against a database
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    results = []
    with tempfile.TemporaryDirectory(prefix="rasyo-w3-mutations-") as directory:
        checkout = Path(directory)
        shutil.copytree(root / "scripts", checkout / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (checkout / "tests").mkdir()
        for name in TESTS + ["pytest.ini"]:
            shutil.copyfile(root / name, checkout / name)
        # Read-only inputs are shared, never copied or written by the auditor.
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
        raise RuntimeError("W3_PRODUCTION_CHECKOUT_MUTATED")
    killed = sum(1 for item in results if item["status"] == "KILLED")
    return {
        "contract": "W3_PRICE_POPULATION_MUTATION_AUDIT_V1",
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
