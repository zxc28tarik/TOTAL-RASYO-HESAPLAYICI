"""Bounded W5 mutations; the production checkout and databases stay untouched.

Each mutation weakens one gate or measurement of the SMRTG canary. All of them
must be KILLED by tests/test_w5_smrtg_canary.py, because a canary whose gates
cannot be made to fail proves nothing about the cell it is guarding.
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

AUDITOR = "scripts/audit_w5_smrtg_canary.py"
TESTS = ["tests/test_w5_smrtg_canary.py"]

MUTATIONS = [
    (
        "share_action_gate_assumed_passed",
        '        "share_action_gate_passed": all(check["verified"] for check in checks.values()),',
        '        "share_action_gate_passed": True,',
    ),
    (
        "post_cutoff_observation_admitted",
        '                    "at_or_before_cutoff": stamp.date() <= CUTOFF_DATE,',
        '                    "at_or_before_cutoff": True,',
    ),
    (
        "empty_interval_check_dropped",
        '    interval_is_empty = (\n'
        '        manifest.get("shares_basis_date") == CUTOFF_DATE.isoformat()\n'
        '        and manifest.get("events") == []\n'
        '        and manifest.get("enumeration_complete") is True\n'
        '    )',
        "    interval_is_empty = True",
    ),
    (
        "assumed_nominal_tolerated",
        '            "verified": receipt.get("nominal_value_per_share_assumed") is False,',
        '            "verified": True,',
    ),
    (
        "nonempty_completeness_claim_ignored",
        '            "verified": receipt.get("nonempty_action_completeness_claimed") is False,',
        '            "verified": True,',
    ),
    (
        "recorded_basis_not_compared_against_latest_known",
        '    pit_not_latest = selection_matches and (\n'
        '        naive_latest is selected\n'
        '        or float(recorded) != float(naive_latest["derived_shares"])\n'
        '    )',
        "    pit_not_latest = True",
    ),
    (
        "supersession_asserted_from_the_certificate_alone",
        '    supersedes = bool(\n'
        '        certification["share_action_gate_passed"]\n'
        '        and cap_follows_certified\n'
        '        and not cap_follows_artifact\n'
        '    )',
        '    supersedes = bool(certification["certification_contract"])',
    ),
    (
        "profile_origin_forced_to_config",
        "    if mismatch_with_default and exact_config_declares_artifact_profile:",
        "    if mismatch_with_default:",
    ),
    (
        "profile_version_ignored",
        '    exact_config_declares_artifact_profile = (\n'
        '        exact_config.get("source_derivation_profile") == artifact_profile\n'
        '        and exact_config.get("source_derivation_version")\n'
        '        == against_default["actual_derivation_version"]\n'
        '    )',
        '    exact_config_declares_artifact_profile = (\n'
        '        exact_config.get("source_derivation_profile") == artifact_profile\n'
        '    )',
    ),
    (
        "prior_reproduction_gate_removed",
        '    if not cell["prior_receipt_reproduced"]:\n'
        '        raise W5AuditError("PRIOR_CANARY_COHORT_NOT_REPRODUCED")',
        "    pass",
    ),
    (
        "scan_no_longer_bound_to_the_production_helper",
        '    if scanned["nonfin_candidates"] != cell["peer_cohort"]["financial_candidate_count"]:',
        "    if False:",
    ),
    (
        "cohort_admission_rule_relaxed",
        "        if not isinstance(quarters, list) or len(quarters) < 4:",
        "        if not isinstance(quarters, list) or len(quarters) < 1:",
    ),
    (
        "any_pre_cutoff_observation_treated_as_certifiable",
        "        elif gap == 0:",
        "        elif gap is not None:",
    ),
    (
        "gate_reachability_off_by_one",
        '                "gate_reachable": len(certifiable) >= required + 1,',
        '                "gate_reachable": len(certifiable) >= required,',
    ),
    (
        "unsafe_derivation_cells_counted_as_safe",
        "    safe_cells = sum(count for key, count in totals.items() if key in "
        "SAFE_SHARE_DERIVATIONS)",
        "    safe_cells = sum(totals.values())",
    ),
    (
        "peer_shortfall_blocker_suppressed",
        '    if not cohort["peer_gate_passed"]:',
        "    if False:",
    ),
    (
        "residual_share_blocker_ignored",
        '    residual_cleared = not residual or supersession["certified_basis_supersedes_artifact"]',
        "    residual_cleared = True",
    ),
    (
        "verdict_status_hardcoded_blocked",
        '        "status": "ADMISSIBLE_FOR_M2" if admissible else "BLOCKED",',
        '        "status": "BLOCKED",',
    ),
    (
        "verdict_self_consistency_gate_removed",
        '    if verdict["status"] == "BLOCKED" and not verdict["active_blockers"]:\n'
        '        raise W5AuditError("BLOCKED_VERDICT_WITHOUT_A_NAMED_BLOCKER")',
        "    pass",
    ),
    (
        "materialization_claim_gate_removed",
        '        if verdict[claim]:\n'
        '            raise W5AuditError(f"CANARY_CLAIMS_WORK_IT_DID_NOT_DO:{claim}")',
        "        pass",
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
    with tempfile.TemporaryDirectory(prefix="rasyo-w5-mutations-") as directory:
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
        raise RuntimeError("W5_PRODUCTION_CHECKOUT_MUTATED")
    killed = sum(1 for item in results if item["status"] == "KILLED")
    return {
        "contract": "W5_SMRTG_CANARY_MUTATION_AUDIT_V1",
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
