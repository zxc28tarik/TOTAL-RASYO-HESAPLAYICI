"""Bounded W6 mutations; the production checkout and databases stay untouched.

Each mutation weakens one gate or measurement of the KAP corporate-action
reachability audit. All of them must be KILLED by
tests/test_w6_ca_gate_reachability.py -- a 60/60 gate-opened finding this
large needs every one of its measurements to be provably falsifiable.
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

AUDITOR = "scripts/audit_w6_ca_gate_reachability.py"
TESTS = ["tests/test_w6_ca_gate_reachability.py"]

MUTATIONS = [
    (
        "action_subject_markers_emptied",
        "ACTION_SUBJECT_MARKERS = (\n"
        '    "sermaye artır", "sermaye azalt", "sermaye artırımı", "sermaye azaltımı",\n'
        '    "birleşme", "bölünme", "pay grubu",\n'
        ")",
        "ACTION_SUBJECT_MARKERS = ()",
    ),
    (
        "coverage_completeness_gate_removed",
        '    if not coverage.get("complete"):\n'
        '        raise W6AuditError(f"KAP_CA_INVENTORY_INCOMPLETE:gaps={coverage.get(\'gaps\')}")',
        "    pass",
    ),
    (
        "window_coverage_check_bypassed",
        "    if not window_fully_covered(window_start, cutoff, covered):\n"
        '        return {"ticker": ticker, "certifiable": False, "reason": "INVENTORY_COVERAGE_GAP"}',
        "    pass",
    ),
    (
        "interval_open_end_made_inclusive_at_start",
        "        e for e in events.get(ticker, [])\n"
        "        if window_start < e[0] <= cutoff",
        "        e for e in events.get(ticker, [])\n"
        "        if window_start <= e[0] <= cutoff",
    ),
    (
        "interval_closed_end_made_exclusive",
        "        e for e in events.get(ticker, [])\n"
        "        if window_start < e[0] <= cutoff",
        "        e for e in events.get(ticker, [])\n"
        "        if window_start < e[0] < cutoff",
    ),
    (
        "action_hits_ignored",
        "    if hits:\n"
        "        return {\n"
        '            "ticker": ticker, "certifiable": False, "reason": "ACTION_DETECTED_IN_WINDOW",',
        "    if False:\n"
        "        return {\n"
        '            "ticker": ticker, "certifiable": False, "reason": "ACTION_DETECTED_IN_WINDOW",',
    ),
    (
        "gate_reachability_off_by_one",
        '            "gate_reachable": len(certifiable) >= required + 1,',
        '            "gate_reachable": len(certifiable) >= required,',
    ),
    (
        "non_certifiable_rows_counted",
        '    for cutoff, rows in sorted(by_cutoff.items()):\n'
        '        certifiable = sorted(r["ticker"] for r in rows if r["certifiable"])',
        '    for cutoff, rows in sorted(by_cutoff.items()):\n'
        '        certifiable = sorted(r["ticker"] for r in rows)',
    ),
    (
        "verdict_status_hardcoded_opened",
        '        "status": "GATE_OPENED" if systemic["gate_reachable_cutoffs"] > 0 else "STILL_BLOCKED",',
        '        "status": "GATE_OPENED",',
    ),
    (
        "m2_materialized_flag_forced_true",
        '            "neutral_fill": False, "production_code_changed": False,\n'
        '            "m2_materialized": False,\n'
        "        },",
        '            "neutral_fill": False, "production_code_changed": False,\n'
        '            "m2_materialized": True,\n'
        "        },",
    ),
    (
        "day_covered_binary_search_short_circuited",
        "    lo, hi = 0, len(covered) - 1\n"
        "    while lo <= hi:",
        "    lo, hi = 0, len(covered) - 1\n"
        "    while False:",
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
    with tempfile.TemporaryDirectory(prefix="rasyo-w6-mutations-") as directory:
        checkout = Path(directory)
        shutil.copytree(
            root / "scripts", checkout / "scripts", ignore=shutil.ignore_patterns("__pycache__")
        )
        (checkout / "tests").mkdir()
        for name in TESTS + ["pytest.ini"]:
            shutil.copyfile(root / name, checkout / name)
        for name in ("data", "src", "config"):
            try:
                (checkout / name).symlink_to(root / name, target_is_directory=True)
            except OSError as exc:  # Windows needs developer mode for symlinks.
                raise RuntimeError(
                    "MUTATION_HARNESS_REQUIRES_SYMLINK_SUPPORT: the read-only inputs are "
                    "shared by symlink rather than copied, so this runs on Linux/macOS or on "
                    "Windows with developer mode enabled. The audit --check and the targeted "
                    "tests have no such requirement."
                ) from exc
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
                cwd=checkout,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            counts = _counts(report)
            failed = counts["failures"] + counts["errors"] > 0
            results.append(
                {
                    "name": name,
                    **counts,
                    "status": ("PASS" if not failed else "UNEXPECTED_FAILURE")
                    if name == "baseline"
                    else ("KILLED" if failed else "SURVIVED"),
                }
            )
    if (root / AUDITOR).read_text(encoding="utf-8") != original:
        raise RuntimeError("W6_PRODUCTION_CHECKOUT_MUTATED")
    killed = sum(1 for item in results if item["status"] == "KILLED")
    return {
        "contract": "W6_CA_GATE_REACHABILITY_MUTATION_AUDIT_V1",
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
