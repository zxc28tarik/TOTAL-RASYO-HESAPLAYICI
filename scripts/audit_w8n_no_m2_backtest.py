from __future__ import annotations

"""W8-N: the five-module (no-M2) 60-month diagnostic score/decision series.

Why this exists
---------------
The six-module historical Total Rasyo cannot be produced today: M2 needs dated
share-count evidence and carries a real score in 0 of 6000 historical cells.
Every other module is reachable -- M1/Ek1 from the archived statements, M3/Ek4/
Ek9 from price series alone -- so a five-module variant can be replayed across
all 60 months right now. It is a DIAGNOSTIC TRACK, not the model: it exists so
M2's contribution can eventually be measured against a real out-of-sample
ledger instead of argued about.

What it is not
--------------
This does not change the production model. Weights, the veto rule, the decision
bands and the universe are untouched. The six-module line keeps its own
artifacts and is not read or written here.

How M2 is excluded
------------------
compute_total_rasyo requires all six module keys, so M2 is passed with
**weight 0.0**. Its score therefore cannot reach the sum: the contribution is
exactly 0.0 whatever value is supplied, and a test asserts that. This is an
exclusion, NOT a neutral fill -- a neutral fill would give M2 a mid-range score
at its real 0.40 weight and let it move the result. The remaining five weights
are the production weights rescaled by 1/0.60 so they still sum to 1.0, which
keeps the decision bands (0.70 / 0.55) on the same footing as the six-module
score. Their ratios to one another are exactly the production ratios.

Fail-closed
-----------
A cell is scored only when all five modules AND good_count are present. Missing
anything is an explicit rejection carrying a reason code; nothing is imputed.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.total_rasyo_score import compute_total_rasyo

CONTRACT = "W8N_NO_M2_FIVE_MODULE_SERIES_V1"
SOURCE_DIR = ROOT / "data/audit/experimental_materialization_v3"
OUTPUT = ROOT / "data/audit/w8n_no_m2_backtest_v1"
MONTH_FILE = re.compile(r"^p4_(\d{4}-\d{2})\.jsonl\.gz$")

SCORED_MODULES = ("M1", "M3", "Ek4", "Ek1", "Ek9")
EXCLUDED_MODULE = "M2"
# Production weights, rescaled by 1/0.60 so the five that remain sum to 1.0.
PRODUCTION_WEIGHTS = {"M2": 0.40, "M1": 0.18, "M3": 0.12, "Ek4": 0.16, "Ek1": 0.08, "Ek9": 0.06}
RETAINED_WEIGHT = sum(PRODUCTION_WEIGHTS[key] for key in SCORED_MODULES)
VARIANT_WEIGHTS = {
    **{key: PRODUCTION_WEIGHTS[key] / RETAINED_WEIGHT for key in SCORED_MODULES},
    EXCLUDED_MODULE: 0.0,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(rows: list[dict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def month_files(source_dir: Path) -> list[tuple[str, Path]]:
    found = []
    for path in sorted(source_dir.iterdir()):
        match = MONTH_FILE.match(path.name)
        if match:
            found.append((match.group(1), path))
    if not found:
        raise ValueError(f"W8N_SOURCE_MONTHS_ABSENT: {source_dir}")
    return found


def score_cell(cell: dict) -> tuple[dict | None, dict | None]:
    """Score one P4 cell, or explain why it cannot be scored."""
    values = cell.get("module_values") or {}
    missing = [key for key in SCORED_MODULES if values.get(key) is None]
    if missing:
        return None, {
            "month": cell["month"], "ticker": cell["ticker"],
            "reason": "MODULE_MISSING", "missing_modules": missing,
        }
    if cell.get("good_count") is None:
        return None, {
            "month": cell["month"], "ticker": cell["ticker"],
            "reason": "GOOD_COUNT_MISSING", "missing_modules": ["good_count_ge8"],
        }
    scored = compute_total_rasyo(
        # The excluded module carries weight 0.0, so this placeholder cannot
        # reach the weighted sum; the assertion below holds it to that.
        {**{key: values[key] for key in SCORED_MODULES}, EXCLUDED_MODULE: 0.0},
        good_count_ge8=cell["good_count"],
        weights=VARIANT_WEIGHTS,
    )
    if scored["contributions"][EXCLUDED_MODULE] != 0.0:
        raise ValueError("W8N_EXCLUDED_MODULE_REACHED_THE_SUM")
    return {
        "month": cell["month"], "signal_date": cell["signal_date"],
        "knowledge_cutoff_at": cell["knowledge_cutoff_at"], "ticker": cell["ticker"],
        "final_score": scored["final_score"], "total_rasyo_100": scored["total_rasyo_100"],
        "decision": scored["decision"], "veto_flag": scored["veto_flag"],
        "good_count_ge8": scored["good_count_ge8"],
        "module_scores": {key: scored["module_scores"][key] for key in SCORED_MODULES},
        "p3_cell_sha256": cell["p3_cell_sha256"],
    }, None


def build(*, source_dir: Path = SOURCE_DIR, output_dir: Path = OUTPUT) -> dict:
    months = month_files(source_dir)
    source_hashes = {month: _sha(path) for month, path in months}
    series: list[dict] = []
    rejections: list[dict] = []
    monthly: list[dict] = []

    for month, path in months:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            cells = [json.loads(line) for line in handle if line.strip()]
        scored_month: list[dict] = []
        for cell in cells:
            scored, rejected = score_cell(cell)
            (scored_month if scored else rejections).append(scored or rejected)
        # Production ranking order: final_score DESC, ticker ASC.
        scored_month.sort(key=lambda row: (-row["final_score"], row["ticker"]))
        for index, row in enumerate(scored_month, start=1):
            row["rank"] = index
        series.extend(scored_month)
        decisions = Counter(row["decision"] for row in scored_month)
        monthly.append({
            "month": month, "cell_count": len(cells), "scored_count": len(scored_month),
            "rejected_count": len(cells) - len(scored_month),
            "decision_counts": {key: decisions.get(key, 0) for key in ("AL", "IZLE", "UZAK")},
            "veto_count": sum(1 for row in scored_month if row["veto_flag"]),
            "top_score": scored_month[0]["total_rasyo_100"] if scored_month else None,
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    series_path = output_dir / "series.jsonl"
    rejection_path = output_dir / "rejections.jsonl"
    monthly_path = output_dir / "monthly.json"
    series_path.write_text(_canonical(series), encoding="utf-8", newline="\n")
    rejection_path.write_text(_canonical(sorted(
        rejections, key=lambda row: (row["month"], row["ticker"])
    )), encoding="utf-8", newline="\n")
    monthly_path.write_text(
        json.dumps({"contract": CONTRACT, "months": monthly}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    decisions = Counter(row["decision"] for row in series)
    receipt = {
        "contract": CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "DIAGNOSTIC_ONLY_NOT_THE_PRODUCTION_MODEL",
        "excluded_module": EXCLUDED_MODULE,
        "excluded_module_weight": VARIANT_WEIGHTS[EXCLUDED_MODULE],
        "excluded_module_is_neutral_fill": False,
        "production_weights": PRODUCTION_WEIGHTS,
        "variant_weights": VARIANT_WEIGHTS,
        "retained_production_weight": RETAINED_WEIGHT,
        "veto_threshold": 5, "veto_factor": 0.60,
        "decision_bands": {"AL": 0.70, "IZLE": 0.55},
        "production_engine": "src.analytics.total_rasyo_score.compute_total_rasyo",
        "month_count": len(months),
        "cell_count": sum(row["cell_count"] for row in monthly),
        "scored_count": len(series),
        "rejected_count": len(rejections),
        "rejection_reasons": dict(Counter(row["reason"] for row in rejections)),
        "decision_counts": {key: decisions.get(key, 0) for key in ("AL", "IZLE", "UZAK")},
        "veto_count": sum(1 for row in series if row["veto_flag"]),
        "months_with_at_least_six_scored": sum(1 for row in monthly if row["scored_count"] >= 6),
        "neutral_fill": False,
        "weight_redistribution": "EXPLICIT_RESCALE_OF_RETAINED_MODULES_ONLY",
        "source_dir": str(source_dir.relative_to(ROOT)),
        "source_sha256": source_hashes,
        "outputs": {
            path.name: _sha(path) for path in (series_path, rejection_path, monthly_path)
        },
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return receipt


def check(*, source_dir: Path = SOURCE_DIR, output_dir: Path = OUTPUT) -> dict:
    recorded = json.loads((output_dir / "receipt.json").read_text(encoding="utf-8"))
    for name, digest in recorded["outputs"].items():
        if _sha(output_dir / name) != digest:
            raise ValueError(f"W8N_OUTPUT_HASH_MISMATCH:{name}")
    for month, digest in recorded["source_sha256"].items():
        if _sha(source_dir / f"p4_{month}.jsonl.gz") != digest:
            raise ValueError(f"W8N_SOURCE_HASH_MISMATCH:{month}")
    return {"status": "W8N_CHECK_PASS", "scored_count": recorded["scored_count"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--check", action="store_true")
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.check:
        print(json.dumps(check(source_dir=args.source_dir, output_dir=args.output_dir)))
    else:
        print(json.dumps(build(source_dir=args.source_dir, output_dir=args.output_dir),
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
