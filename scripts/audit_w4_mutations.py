"""Bounded W4 mutations; the production checkout and databases stay untouched.

Every mutation weakens one gate of the Ek4 contract audit. A gate no test can
distinguish from its weakened form is not a gate, so all of them must be KILLED
by tests/test_w4_ek4_contract.py.
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

AUDITOR = "scripts/audit_w4_ek4_contract.py"
TESTS = ["tests/test_w4_ek4_contract.py"]

MUTATIONS = [
    (
        "recorded_return_reproduction_gate_removed",
        'if abs(reproduced - cell["stock_return_20d"]) > 1e-9:',
        "if False:",
    ),
    (
        "missing_endpoint_price_tolerated",
        "        if start_key not in prices or end_key not in prices:",
        "        if False:",
    ),
    (
        "materiality_band_collapsed_to_everything",
        "        if delta_excess > MATERIALITY:",
        "        if delta_excess > -1.0:",
    ),
    (
        "anomalous_negative_band_hidden",
        "        elif delta_excess < -MATERIALITY:",
        "        elif False:",
    ),
    (
        "direction_flag_hardcoded",
        '"no_inverted_adjustment_beyond_rounding": not anomalous,',
        '"no_inverted_adjustment_beyond_rounding": True,',
    ),
    (
        "contract_verdict_hardcoded_compliant",
        '"verdict": "COMPLIANT" if not violations else "VIOLATION_PROVEN",',
        '"verdict": "COMPLIANT",',
    ),
    (
        "shared_formula_check_assumed",
        """    shared_formula = (
        "from src.analytics.ek4_momentum import compute_ek4_momentum_point" in daily
        and "compute_ek4_momentum_point" in replay
    )""",
        "    shared_formula = True",
    ),
    (
        "violation_list_suppressed",
        'violations = sorted(name for name, value in checks.items() if not value["verified"])',
        "violations = []",
    ),
    (
        "live_fallback_finding_suppressed",
        '"live_fallback_marker_present": live_fallback_present,',
        '"live_fallback_marker_present": False,',
    ),
    (
        "sector_leg_rebased_to_adjusted",
        """        raw_point = compute_ek4_momentum_point(
            stock_start=close_start,
            stock_end=close_end,""",
        """        raw_point = compute_ek4_momentum_point(
            stock_start=adj_start,
            stock_end=adj_end,""",
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
    with tempfile.TemporaryDirectory(prefix="rasyo-w4-mutations-") as directory:
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
        raise RuntimeError("W4_PRODUCTION_CHECKOUT_MUTATED")
    killed = sum(1 for item in results if item["status"] == "KILLED")
    return {
        "contract": "W4_EK4_CONTRACT_MUTATION_AUDIT_V1",
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
