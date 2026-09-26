"""Bounded W6-C mutations; the production checkout and databases stay untouched.

Each mutation weakens one gate or measurement of the report-corroborated-basis
audit. All of them must be KILLED by tests/test_w6c_report_corroborated_basis.py.
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

AUDITOR = "scripts/audit_w6c_report_corroborated_basis.py"
TESTS = ["tests/test_w6c_report_corroborated_basis.py"]

MUTATIONS = [
    (
        "future_report_admitted",
        "                pub = parse_published_at(quarter.get(\"published_at\"))\n"
        "                if pub is None or pub > cutoff:\n"
        "                    continue",
        "                pub = parse_published_at(quarter.get(\"published_at\"))\n"
        "                if pub is None:\n"
        "                    continue",
    ),
    (
        "mismatch_does_not_stop_the_chain",
        "        if abs(shares - anchor_shares) > max(1.0, abs(anchor_shares) * 1e-9):\n"
        "            break",
        "        if False:\n"
        "            break",
    ),
    (
        "anchor_boundary_made_inclusive",
        "        (pub, pe, shares) for pe, pub, shares in reports if anchor_date < pub <= cutoff_date",
        "        (pub, pe, shares) for pe, pub, shares in reports if anchor_date <= pub <= cutoff_date",
    ),
    (
        "shrunk_flag_hardcoded_true",
        '                "shrunk": gap_after_published < gap_before,',
        '                "shrunk": True,',
    ),
    (
        "cells_shrunk_count_ignores_shrunk_flag",
        '        "cells_shrunk": len(shrunk),',
        '        "cells_shrunk": len(with_anchor),',
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
        "anchor_shares_lookup_failure_silenced",
        "            if anchor_shares is None:\n"
        '                raise W6CAuditError(f"ANCHOR_SHARES_LOOKUP_FAILED:{ticker}@{anchor_stamp}")',
        "            if anchor_shares is None:\n"
        "                anchor_shares = 0.0",
    ),
    (
        "gap_before_computed_from_wrong_date",
        "            gap_before = (cutoff_date - anchor_date).days",
        "            gap_before = (cutoff_date - cutoff_date).days",
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
    with tempfile.TemporaryDirectory(prefix="rasyo-w6c-mutations-") as directory:
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
            except OSError as exc:
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
        raise RuntimeError("W6C_PRODUCTION_CHECKOUT_MUTATED")
    killed = sum(1 for item in results if item["status"] == "KILLED")
    return {
        "contract": "W6C_REPORT_CORROBORATED_BASIS_MUTATION_AUDIT_V1",
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
