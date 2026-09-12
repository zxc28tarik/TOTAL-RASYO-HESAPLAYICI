"""Bounded W1 regression mutations; production checkout and databases stay untouched."""
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

DAILY = "src/analytics/run_daily_pipeline.py"
EK9 = "src/analytics/ek9_volatility.py"
TESTS = ["tests/test_w1_daily_score_safety.py", "tests/test_w1_ek9_safety.py",
         "tests/test_ek9_live_compatibility.py"]
MUTATIONS = [
    ("m2_missing_to_neutral", DAILY, 'df["m2"] = _validated_score_values(df["m2"])',
     'df["m2"] = pd.to_numeric(df["m2"], errors="coerce").fillna(0.5).clip(0, 1)'),
    ("m3_missing_to_neutral", DAILY, 'df["m3"] = _validated_score_values(df["m3"])',
     'df["m3"] = pd.to_numeric(df["m3"], errors="coerce").fillna(0.5).clip(0, 1)'),
    ("undefined_volatility_to_maximum_score", EK9, 'vol = vol.where(np.isfinite(vol))',
     'vol = vol.where(np.isfinite(vol)).fillna(0.0)'),
    ("minimum_window_relaxed", DAILY, 'if len(days) < lookback + 2:',
     'if len(days) < lookback + 1:'),
    ("calendar_disagreement_ignored", DAILY,
     'if not set(observed["trade_date"]).issubset(set(days)):', 'if False:'),
    ("persisted_rejections_not_updated", DAILY,
     'module_rejections=EXCLUDED.module_rejections,', ''),
]


def run(root: Path) -> dict:
    sources = {name: (root / name).read_text(encoding="utf-8") for name in (DAILY, EK9)}
    results = []
    env = dict(os.environ)
    env.pop("TOTAL_RASYO_TEST_DSN", None)  # never run a mutant against a database
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="rasyo-w1-mutations-") as directory:
        checkout = Path(directory)
        shutil.copytree(root / "src", checkout / "src", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(root / "config", checkout / "config")
        (checkout / "tests").mkdir()
        for name in TESTS + ["tests/test_daily_pipeline_fail_closed.py", "pytest.ini"]:
            shutil.copyfile(root / name, checkout / name)
        for name, path, old, new in [("baseline", DAILY, "", "")] + MUTATIONS:
            for source, content in sources.items():
                (checkout / source).write_text(content, encoding="utf-8")
            if name != "baseline":
                if sources[path].count(old) != 1:
                    raise RuntimeError(f"non-unique mutation anchor: {name}")
                (checkout / path).write_text(sources[path].replace(old, new), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-B", "-m", "pytest", "-q", *TESTS, "--junitxml=result.xml"],
                cwd=checkout, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=90)
            suites = ET.parse(checkout / "result.xml").getroot()
            counts = {key: sum(int(s.get(key, 0)) for s in suites.iter("testsuite"))
                      for key in ("tests", "failures", "errors", "skipped")}
            passed = proc.returncode == 0 if name == "baseline" else (
                proc.returncode == 1 and counts["failures"] > 0 and counts["errors"] == 0)
            if not passed:
                raise RuntimeError(f"W1 mutation audit failed: {name}\n{proc.stdout}\n{proc.stderr}")
            results.append({"name": name, "status": "PASS" if name == "baseline" else "KILLED", **counts})
    return {"contract": "W1_FAIL_CLOSED_MUTATION_AUDIT_V1", "results": results,
            "database_access": False,
            "source_sha256": {name: sha256((root / name).read_bytes()).hexdigest() for name in sources},
            "tests_sha256": {name: sha256((root / name).read_bytes()).hexdigest() for name in TESTS}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(Path(__file__).resolve().parents[1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(f"{len(MUTATIONS)}/{len(MUTATIONS)} W1 mutations killed; baseline PASS")
