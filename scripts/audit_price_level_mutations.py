"""Run bounded production-code mutations in isolated temporary checkouts."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

EVIDENCE = "src/analytics/price_level_action_evidence.py"
BASIS = "src/analytics/price_level_valuation_basis.py"
FAMILIES = ("nonfin", "holding", "gyo", "insurance", "financial_institution")
ADAPTERS = [f"src/analytics/{family}_batch_pipeline.py" for family in FAMILIES]
TESTS = ["tests/test_price_level_action_evidence.py", "tests/test_price_level_valuation_basis.py",
         "tests/test_price_level_family_adapters.py"]
MUTATIONS = [
    ("manifest_hash_bypass", EVIDENCE, "if digest != self.expected_sha256:", "if False:"),
    ("source_hash_bypass", EVIDENCE, 'if sha256(raw).hexdigest() != source.get("source_sha256"):', "if False:"),
    ("future_publication_bypass", EVIDENCE, "if published > cutoff:", "if False:"),
    ("wrong_ticker_bypass", EVIDENCE, 'if payload.get("ticker") != ticker:', "if False:"),
    ("wrong_share_basis_bypass", EVIDENCE, 'if payload.get("source_share_basis") != SOURCE_SHARE_BASIS:', "if False:"),
    ("completeness_bypass", EVIDENCE, 'if payload.get("enumeration_complete") is not True:', "if False:"),
    ("wrong_source_date_bypass", EVIDENCE, 'if _day(payload.get("shares_basis_date")) != shares_basis_date:', "if False:"),
    ("wrong_price_date_bypass", EVIDENCE, 'if _day(payload.get("complete_through")) != price_trade_date:', "if False:"),
    ("event_set_bypass", EVIDENCE, "if set(inventory) != {event.action_id for event in checked}:", "if False:"),
    ("event_identity_bypass", EVIDENCE, "if rebuilt != event:", "if False:"),
    ("event_date_bypass", EVIDENCE, "if not shares_basis_date < event.ex_date <= price_trade_date:", "if False:"),
    ("rights_as_split", EVIDENCE, 'if kind not in {"SPLIT", "BONUS", "REVERSE_SPLIT"}:', "if False:"),
    ("price_cutoff_bypass", BASIS, "if available_at > cutoff:", "if False:"),
    ("dividend_changes_shares", BASIS, "if event.action_type == ACTION_CASH_DIVIDEND:\n            continue", "if event.action_type == ACTION_CASH_DIVIDEND:\n            normalized *= 2\n            continue"),
    ("split_not_applied", BASIS, 'normalized *= _positive(event.share_multiplier, "share_multiplier")', "normalized *= 1"),
    ("adjusted_price_for_market_cap", BASIS, 'close=_positive(close, "close"),', 'close=_positive(adjusted_close if adjusted_close is not None else close, "close"),'),
]

for family, path in zip(FAMILIES, ADAPTERS):
    MUTATIONS.append((f"{family}_adjusted_query", path,
        "close AS current_price, 'POINT_IN_TIME_MARKET_CLOSE_V1' AS price_basis",
        "COALESCE(adj_close, close) AS current_price, 'POINT_IN_TIME_MARKET_CLOSE_V1' AS price_basis"))
    if family == "nonfin":
        MUTATIONS.append((f"{family}_split_not_applied", path,
            'normalized_group.loc[normalized_group.index[-1], "shares_out"] = basis.normalized_shares_out',
            'normalized_group.loc[normalized_group.index[-1], "shares_out"] = latest["shares_out"]'))
    else:
        source = "nav" if family in {"holding", "gyo"} else "metric"
        MUTATIONS.append((f"{family}_split_not_applied", path,
            'shares_out=basis.normalized_shares_out,', f'shares_out={source}["shares_out"],'))


def run(root: Path) -> dict:
    sources = {name: (root / name).read_text(encoding="utf-8") for name in (EVIDENCE, BASIS, *ADAPTERS)}
    results = []
    with tempfile.TemporaryDirectory(prefix="rasyo-mutations-") as directory:
        checkout = Path(directory)
        shutil.copytree(root / "src", checkout / "src", ignore=shutil.ignore_patterns("__pycache__"))
        (checkout / "tests").mkdir()
        shutil.copytree(root / "config", checkout / "config")
        support = ["tests/price_level_fixtures.py"] + [f"tests/test_{family}_batch_pipeline.py" for family in FAMILIES]
        for name in TESTS + support + ["pytest.ini"]:
            shutil.copyfile(root / name, checkout / name)
        for name, path, old, new in [("baseline", BASIS, "", "")] + MUTATIONS:
            for source_path, content in sources.items():
                (checkout / source_path).write_text(content, encoding="utf-8")
            if name != "baseline":
                if sources[path].count(old) != 1:
                    raise RuntimeError(f"mutation anchor is not unique: {name}")
                (checkout / path).write_text(sources[path].replace(old, new), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-B", "-m", "pytest", "-q", *TESTS, "--junitxml=result.xml"],
                cwd=checkout, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            suites = ET.parse(checkout / "result.xml").getroot()
            failures = sum(int(suite.get("failures", 0)) for suite in suites.iter("testsuite"))
            errors = sum(int(suite.get("errors", 0)) for suite in suites.iter("testsuite"))
            passed = proc.returncode == 0 if name == "baseline" else proc.returncode == 1 and failures > 0 and errors == 0
            if not passed:
                raise RuntimeError(f"mutation audit failed: {name}\n{proc.stdout}\n{proc.stderr}")
            results.append({"name": name, "status": "PASS" if name == "baseline" else "KILLED", "failures": failures})
    return {"contract": "PRICE_LEVEL_MUTATION_AUDIT_V1", "results": results,
            "source_sha256": {name: sha256((root / name).read_bytes()).hexdigest() for name in sources},
            "tests_sha256": {name: sha256((root / name).read_bytes()).hexdigest() for name in TESTS}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(Path(__file__).resolve().parents[1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(f"{len(MUTATIONS)}/{len(MUTATIONS)} mutations killed; baseline PASS")
