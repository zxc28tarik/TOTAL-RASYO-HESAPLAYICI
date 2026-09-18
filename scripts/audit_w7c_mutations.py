"""Bounded W7-C mutations; the production checkout and databases stay untouched.

Each mutation weakens one gate or measurement of the real-M2-score audit. All
of them must be KILLED by a targeted subset of tests/test_w7c_real_m2_score.py
(selected per mutation to keep this harness's runtime bounded -- the audit's
own derive() extracts text from hundreds of archived PDFs across two tickers'
multi-year windows and calls the real production replay, so it is not cheap
to re-run for every mutation against the full test file).
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

AUDITOR = "scripts/audit_w7c_real_m2_score.py"
TESTS = "tests/test_w7c_real_m2_score.py"

MUTATIONS = [
    (
        "bulletin_hash_mismatch_check_removed",
        "        if sha_file(path) != entry[\"sha256\"]:\n"
        "            raise W7CAuditError(f\"BULLETIN_HASH_MISMATCH:{entry['filename']}\")",
        "        if False:\n"
        "            raise W7CAuditError(f\"BULLETIN_HASH_MISMATCH:{entry['filename']}\")",
        ["test_archive_detects_tampered_pdf_bytes"],
    ),
    (
        "bulletin_numbering_gap_check_removed",
        "        missing = sorted(set(span) - nums)\n"
        "        if missing:\n"
        "            raise W7CAuditError(f\"BULLETIN_NUMBERING_GAP:{year}:{missing}\")",
        "        missing = sorted(set(span) - nums)\n"
        "        if False:\n"
        "            raise W7CAuditError(f\"BULLETIN_NUMBERING_GAP:{year}:{missing}\")",
        ["test_archive_detects_numbering_gap"],
    ),
    (
        "duplicate_bulletin_number_check_removed",
        "        if key in seen_files:\n"
        "            raise W7CAuditError(f\"DUPLICATE_BULLETIN_NUMBER:{key}\")",
        "        if False:\n"
        "            raise W7CAuditError(f\"DUPLICATE_BULLETIN_NUMBER:{key}\")",
        ["test_archive_detects_duplicate_bulletin_number"],
    ),
    (
        "unexplained_mention_check_removed",
        "                if key not in KNOWN_NON_ACTION_MENTIONS:\n"
        "                    raise W7CAuditError(",
        "                if False:\n"
        "                    raise W7CAuditError(",
        ["test_unreviewed_mention_would_abort"],
    ),
    (
        "empty_bulletin_window_check_removed",
        "    if not window_dates:\n"
        "        raise W7CAuditError(f\"EMPTY_BULLETIN_WINDOW:{ticker}\")",
        "    if False:\n"
        "        raise W7CAuditError(f\"EMPTY_BULLETIN_WINDOW:{ticker}\")",
        ["test_empty_bulletin_window_rejected"],
    ),
    (
        "sha_raw_reverted_to_crlf_stripping_bug",
        "def sha_raw(payload: bytes) -> str:\n"
        "    \"\"\"Plain byte hash for binary/arbitrary content (a PDF, a source blob\n"
        "    handed to PriceLevelActionEvidence). Matches exactly what verify() itself\n"
        "    computes (sha256 of the exact bytes, no normalization).\"\"\"\n"
        "    return sha256(payload).hexdigest()",
        "def sha_raw(payload: bytes) -> str:\n"
        "    return sha256(payload.replace(b\"\\r\\n\", b\"\\n\")).hexdigest()",
        ["test_sha_raw_is_not_crlf_normalized"],
    ),
    (
        "decimal_bug_correction_guard_removed",
        "    corrected = {t for t, a in anchors.items() if a[\"decimal_bug_corrected\"]}\n"
        "    if corrected != EXPECTED_DECIMAL_BUG_CORRECTED:\n"
        "        raise W7CAuditError(",
        "    corrected = {t for t, a in anchors.items() if a[\"decimal_bug_corrected\"]}\n"
        "    if False:\n"
        "        raise W7CAuditError(",
        ["test_census_guard_fires_if_correction_set_changes"],
    ),
    (
        "unexpected_rejections_guard_removed",
        "    if batch_replay[\"rejection_count\"] != 0:\n"
        "        raise W7CAuditError(",
        "    if False:\n"
        "        raise W7CAuditError(",
        ["test_unexpected_rejection_would_raise_not_pass_silently"],
    ),
    (
        "m2_score_count_guard_removed",
        "    if batch_replay[\"m2_score_count\"] != len(TICKERS):\n"
        "        raise W7CAuditError(\n"
        "            f\"M2_SCORE_COUNT_MISMATCH:{batch_replay['m2_score_count']} != {len(TICKERS)}\"\n"
        "        )",
        "    if False:\n"
        "        raise W7CAuditError(\n"
        "            f\"M2_SCORE_COUNT_MISMATCH:{batch_replay['m2_score_count']} != {len(TICKERS)}\"\n"
        "        )",
        ["test_m2_score_count_mismatch_would_raise_not_pass_silently"],
    ),
    (
        "expected_multiple_usable_guard_removed",
        "            if not details.get(multiple, {}).get(\"usable\"):\n"
        "                raise W7CAuditError(f\"EXPECTED_MULTIPLE_NOT_USABLE:{ticker}:{multiple}\")",
        "            if False:\n"
        "                raise W7CAuditError(f\"EXPECTED_MULTIPLE_NOT_USABLE:{ticker}:{multiple}\")",
        ["test_expected_multiple_not_usable_would_raise_not_pass_silently"],
    ),
    (
        "peer_count_guard_removed",
        "            if details[multiple][\"peer_count\"] != len(TICKERS) - 1:\n"
        "                raise W7CAuditError(\n"
        "                    f\"UNEXPECTED_PEER_COUNT:{ticker}:{multiple}:{details[multiple]['peer_count']}\"\n"
        "                )",
        "            if False:\n"
        "                raise W7CAuditError(\n"
        "                    f\"UNEXPECTED_PEER_COUNT:{ticker}:{multiple}:{details[multiple]['peer_count']}\"\n"
        "                )",
        ["test_unexpected_peer_count_would_raise_not_pass_silently"],
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
    with tempfile.TemporaryDirectory(prefix="rasyo-w7c-mutations-") as directory:
        checkout = Path(directory)
        (checkout / "scripts").mkdir()
        shutil.copyfile(root / AUDITOR, checkout / AUDITOR)
        (checkout / "tests").mkdir()
        for name in [TESTS, "pytest.ini"]:
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

        def run_tests(name: str, node_ids: list[str]) -> dict:
            report = checkout / f"{name}.xml"
            args = [sys.executable, "-m", "pytest", "-q"]
            args += [f"{TESTS}::{node}" for node in node_ids] if node_ids else [TESTS]
            args += [f"--junitxml={report}"]
            subprocess.run(args, cwd=checkout, env=env, capture_output=True, text=True, check=False)
            return _counts(report)

        baseline_counts = run_tests("baseline", [])
        results.append({
            "name": "baseline", **baseline_counts,
            "status": "PASS" if baseline_counts["failures"] + baseline_counts["errors"] == 0
            else "UNEXPECTED_FAILURE",
        })

        for name, old, new, node_ids in MUTATIONS:
            if old not in original:
                raise RuntimeError(f"MUTATION_ANCHOR_NOT_FOUND:{name}")
            source = original.replace(old, new, 1)
            (checkout / AUDITOR).write_text(source, encoding="utf-8")
            counts = run_tests(name, node_ids)
            failed = counts["failures"] + counts["errors"] > 0
            results.append({"name": name, **counts, "status": "KILLED" if failed else "SURVIVED"})
        (checkout / AUDITOR).write_text(original, encoding="utf-8")

    if (root / AUDITOR).read_text(encoding="utf-8") != original:
        raise RuntimeError("W7C_PRODUCTION_CHECKOUT_MUTATED")
    killed = sum(1 for item in results if item["status"] == "KILLED")
    return {
        "contract": "W7C_REAL_M2_SCORE_MUTATION_AUDIT_V1",
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
