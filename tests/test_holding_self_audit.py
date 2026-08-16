from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_holding_self_audit_runs_directly_from_repository_root():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/self_audit_holding_valuation.py"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "PASS"
    assert payload["scenario_count"] == 15000
    assert payload["uncontrolled_exception"] == 0
    assert payload["silent_bad_acceptance"] == 0
