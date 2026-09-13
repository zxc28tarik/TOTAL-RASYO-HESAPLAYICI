"""Bounded W10 mutations; the production checkout and databases stay untouched.

Each mutation weakens one measurement or gate of the P7 enumeration audit. All
of them must be KILLED by tests/test_w10_p7_enumeration.py, because a number
nobody can falsify is an assertion, not a measurement.
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

AUDITOR = "scripts/audit_w10_p7_enumeration.py"
TESTS = ["tests/test_w10_p7_enumeration.py"]

MUTATIONS = [
    (
        "version_identifier_check_assumed_met",
        '    versioned = sum(\n'
        '        1\n'
        '        for _, report in selected\n'
        '        if any(field in report for field in VERSION_METADATA_FIELDS)\n'
        '        or report.get("historical_version_enumeration_complete") is True\n'
        '    )',
        "    versioned = len(selected)",
    ),
    (
        "lookahead_count_suppressed",
        '    lookahead = sum(\n'
        '        1\n'
        '        for cell, report in selected\n'
        '        if datetime.fromisoformat(report["published_at"])\n'
        '        > datetime.fromisoformat(cell["knowledge_cutoff_at"])\n'
        '    )',
        "    lookahead = 0",
    ),
    (
        "unresolved_sector_family_ignored",
        '            "met": families.get("None", 0) == 0,',
        '            "met": True,',
    ),
    (
        "issue24_verdict_hardcoded",
        '        "verdict": "BLOCKED" if unmet else "CLOSURE_CRITERIA_SATISFIED",',
        '        "verdict": "BLOCKED",',
    ),
    (
        "fixture_source_check_removed",
        '    fixture = sum(\n'
        '        1\n'
        '        for _, report in selected\n'
        '        if "example" in str(report.get("source_url", "")).lower()\n'
        '        or "example" in str(report.get("archive_name", "")).lower()\n'
        '        or "fixture" in str(report.get("archive_name", "")).lower()\n'
        '    )',
        "    fixture = 0",
    ),
    (
        "supersession_rate_counts_non_financial_rows",
        '    financial = [row for row in query_rows if row.get("disclosureCategory") == "FR"]\n'
        '    superseded = [row for row in financial if row.get("modifyStatus") == SUPERSEDED_MARKER]\n'
        '    revised = [row for row in financial if row.get("modifyStatus") == REVISION_MARKER]',
        '    financial = list(query_rows)\n'
        '    superseded = [row for row in financial if row.get("modifyStatus") == SUPERSEDED_MARKER]\n'
        '    revised = [row for row in financial if row.get("modifyStatus") == REVISION_MARKER]',
    ),
    (
        "authoritative_label_gate_removed",
        '    if gap["authoritative_label_allowed"]:\n'
        '        raise W10AuditError("AUTHORITATIVE_LABEL_MUST_STAY_DISALLOWED_WHILE_BLOCKED")',
        "    pass",
    ),
    (
        "contamination_flag_hardcoded_clean",
        '        "contamination_detected": bool(touching),',
        '        "contamination_detected": False,',
    ),
    (
        "probe_ignores_selected_reports",
        '        "corrections_selected_by_a_cell": len(touching),',
        '        "corrections_selected_by_a_cell": 0,',
    ),
    (
        "superseded_rows_evidence_suppressed",
        '                "superseded_rows_returned": len(superseded),',
        '                "superseded_rows_returned": 0,',
    ),
    (
        "distinct_report_count_hardcoded",
        '        "distinct_selected_reports": len(distinct_reports),',
        '        "distinct_selected_reports": 2115,',
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
    with tempfile.TemporaryDirectory(prefix="rasyo-w10-mutations-") as directory:
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
        raise RuntimeError("W10_PRODUCTION_CHECKOUT_MUTATED")
    killed = sum(1 for item in results if item["status"] == "KILLED")
    return {
        "contract": "W10_P7_ENUMERATION_MUTATION_AUDIT_V1",
        "database_access": False,
        "network_access": False,
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
