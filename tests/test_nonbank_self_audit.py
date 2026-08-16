from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_nonbank_self_audit_runs_directly_from_repository_root():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/self_audit_nonbank_core.py"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "PASS"
    assert payload["scenario_count"] == 12000
    assert payload["uncontrolled_or_silent"] == 0


def test_makefile_includes_nonbank_migration_and_targets():
    text = Path("Makefile").read_text(encoding="utf-8")
    assert "sql/021_company_semantic_materialization.sql" in text
    assert "materialize-company-facts:" in text
    assert "calc-company-ratios:" in text
    assert "self-audit-nonbank-core:" in text
