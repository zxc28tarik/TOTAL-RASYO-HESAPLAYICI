"""Build the cell-level M2 priority and procurement dependency matrix."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.analytics.verified_yahoo_raw_close import (
    direct_yahoo_candidate_from_source, verify_yahoo_raw_close,
)

DEFAULT_INPUT = ROOT / "data/audit/experimental_materialization_v2"


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows(path):
    return [json.loads(line) for line in gzip.decompress(Path(path).read_bytes()).splitlines()]


def write_rows(path, values):
    Path(path).write_bytes(gzip.compress(b"".join(encoded(row) for row in values), mtime=0))


def _issued_capital(cell):
    facts = [fact for fact in cell.get("own_period_semantic_facts", [])
             if fact.get("canonical_field") == "ISSUED_CAPITAL"]
    if len(facts) > 1:
        raise ValueError("MULTIPLE_ISSUED_CAPITAL_FACTS_IN_CELL")
    if not facts:
        return None
    fact = facts[0]
    dimensions = fact.get("dimensions", {})
    return {
        "capital": fact["value"],
        "observed_at": fact["period_end"],
        "published_at": fact["published_at"],
        "effective_from": None,
        "nominal_value": None,
        "derived_shares": None,
        "derivation_status": "NOMINAL_VALUE_AND_SHARE_CLASS_PROOF_MISSING",
        "source_disclosure": fact.get("disclosure_id"),
        "source_archive": dimensions.get("archive_name"),
        "source_archive_sha256": dimensions.get("archive_sha256"),
        "source_member": dimensions.get("member_name"),
        "source_member_sha256": dimensions.get("member_sha256"),
    }


def _package_years(capital, price_date):
    price_year = int(price_date[:4])
    if capital is None:
        return [price_year]
    start_year = int(capital["observed_at"][:4])
    if start_year > price_year:
        raise ValueError("SHARE_BASIS_AFTER_PRICE")
    return list(range(start_year, price_year + 1))


def _raw_close_status(cell):
    gate = cell.get("m2_source_gate", {})
    if gate.get("raw_close_basis_verified"):
        return True, gate.get("price_source"), "PREEXISTING_VERIFIED_THB"
    price = cell.get("price")
    if not price:
        return False, None, "PRICE_MISSING"
    candidate = dict(price)
    if "yahoo_symbol" not in candidate or "adj_close" not in candidate:
        source = direct_yahoo_candidate_from_source(
            ticker=cell["ticker"], trade_date=candidate["trade_date"])
        # Retain the legacy artifact's independently audited value/lineage and
        # enrich only fields that V2 did not persist.
        candidate = {**source, **candidate}
    receipt = verify_yahoo_raw_close(
        ticker=cell["ticker"], candidate=candidate,
        analysis_at=datetime.fromisoformat(cell["knowledge_cutoff_at"]))
    return True, receipt, "VERIFIED_YAHOO_RAW_CLOSE_V1"


def build(input_dir: Path, output_dir: Path):
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    receipt = json.loads((input_dir / "receipt.json").read_bytes())
    p3_path = input_dir / "p3_cells.jsonl.gz"
    p4_path = input_dir / "p4_cells.jsonl.gz"
    if sha(p3_path) != receipt["outputs"]["p3_cells.jsonl.gz"]:
        raise ValueError("P3_SOURCE_HASH_MISMATCH")
    if sha(p4_path) != receipt["outputs"]["p4_cells.jsonl.gz"]:
        raise ValueError("P4_SOURCE_HASH_MISMATCH")
    p3 = rows(p3_path)
    p4 = rows(p4_path)
    if len(p3) != len(p4) or len(p3) != 6000:
        raise ValueError("MATERIALIZATION_CELL_COUNT_MISMATCH")

    target = []
    package_cells = defaultdict(set)
    tickers = defaultdict(list)
    month_counts = Counter()
    family_counts = Counter()
    raw_counts = Counter()
    for cell, scored in zip(p3, p4):
        if cell["ticker"] != scored["ticker"] or cell["month"] != scored["month"]:
            raise ValueError("P3_P4_ALIGNMENT_MISMATCH")
        if scored.get("module_count") != 5 or "M2" not in scored.get("missing_modules", []):
            continue
        capital = _issued_capital(cell)
        price_date = (cell.get("price") or {}).get("trade_date")
        if not price_date:
            raise ValueError("FIVE_MODULE_TARGET_WITHOUT_PRICE")
        verified, price_receipt, raw_route = _raw_close_status(cell)
        years = _package_years(capital, price_date)
        for year in years:
            package_cells[year].add((cell["ticker"], cell["month"]))
        binding = cell.get("financial_entity_binding") or {}
        prerequisites = ["DATED_UNADJUSTED_NOMINAL_SHARES", "ACTION_COMPLETENESS_THROUGH_PRICE_DATE"]
        if binding.get("composite_entity"):
            prerequisites.append("COMPOSITE_SHARE_CLASS_RECONCILIATION")
        row = {
            "contract": "EXPERIMENTAL_M2_PRIORITY_CELL_V1",
            "ticker": cell["ticker"], "month": cell["month"],
            "signal_date": cell["signal_date"],
            "knowledge_cutoff_at": cell["knowledge_cutoff_at"],
            "historical_family": cell.get("historical_family"),
            "module_count_before_m2": scored["module_count"],
            "other_module_values": {key: value for key, value in scored["module_values"].items() if key != "M2"},
            "price_trade_date": price_date,
            "raw_close_verified": verified,
            "raw_close_evidence_route": raw_route,
            "raw_close_receipt": price_receipt,
            "issued_capital_observation": capital,
            "composite_financial_entity": bool(binding.get("composite_entity")),
            "required_serart_years": years,
            "required_prerequisites": prerequisites,
            "baseline_m2_reasons": cell.get("m2_source_gate", {}).get("reasons", []),
            "m2_executed": False,
            "total_score_ready": False,
        }
        target.append(row)
        tickers[cell["ticker"]].append(cell["month"])
        month_counts[cell["month"]] += 1
        family_counts[str(cell.get("historical_family"))] += 1
        raw_counts[raw_route] += 1

    if len(target) != 3017:
        raise ValueError(f"FIVE_MODULE_TARGET_COUNT_MISMATCH:{len(target)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    target.sort(key=lambda row: (row["month"], row["ticker"]))
    cell_path = output_dir / "m2_priority_cells.jsonl.gz"
    write_rows(cell_path, target)
    csv_buffer = io.StringIO(newline="")
    writer = csv.writer(csv_buffer, lineterminator="\n")
    writer.writerow(["ticker", "cell_count", "first_month", "last_month", "months"])
    for ticker, months in sorted(tickers.items()):
        ordered = sorted(months)
        writer.writerow([ticker, len(ordered), ordered[0], ordered[-1], "|".join(ordered)])
    ticker_path = output_dir / "m2_priority_ticker_months.csv"
    ticker_path.write_text(csv_buffer.getvalue(), encoding="utf-8", newline="")
    summary = {
        "contract": "EXPERIMENTAL_M2_PRIORITY_MATRIX_V1",
        "source_artifact_dir": str(input_dir.relative_to(ROOT)).replace("\\", "/"),
        "source_p3_sha256": sha(p3_path), "source_p4_sha256": sha(p4_path),
        "target_definition": "P4 module_count=5 and missing_modules contains M2",
        "target_cells": len(target), "target_tickers": len(tickers),
        "target_months": len(month_counts),
        "family_counts": dict(sorted(family_counts.items())),
        "monthly_counts": dict(sorted(month_counts.items())),
        "raw_close_evidence_routes": dict(sorted(raw_counts.items())),
        "raw_close_verified_cells_after_wiring": sum(row["raw_close_verified"] for row in target),
        "issued_capital_observation_cells": sum(row["issued_capital_observation"] is not None for row in target),
        "issued_capital_without_nominal_share_proof_cells": sum(row["issued_capital_observation"] is not None and row["issued_capital_observation"]["derived_shares"] is None for row in target),
        "composite_financial_entity_cells": sum(row["composite_financial_entity"] for row in target),
        "package_dependency_cells": {str(year): len(cells) for year, cells in sorted(package_cells.items())},
        "minimum_complete_year_set": sorted(package_cells),
        "raw_close_only_unlocks_m2_cells": 0,
        "raw_close_only_unlocks_total_cells": 0,
        "m2_valid_cells": 0, "total_valid_cells": 0,
        "outputs": {},
    }
    summary["outputs"] = {
        cell_path.name: sha(cell_path), ticker_path.name: sha(ticker_path),
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_bytes(encoded(summary))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.input_dir, args.output_dir), ensure_ascii=False, indent=2))
