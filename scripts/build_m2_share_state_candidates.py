"""Extract source-bound capital observations without inventing share counts."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_DIR = ROOT / "data/backtest_sources/experimental_semantic_facts_v1"
PRIORITY = ROOT / "data/audit/m2_priority_matrix_v1/m2_priority_cells.jsonl.gz"
FILES = (
    ("semantic_reports.jsonl.gz", "semantic_receipt.json"),
    ("semantic_alias_reports.jsonl.gz", "semantic_alias_receipt.json"),
    ("semantic_entity_reports.jsonl.gz", "semantic_entity_receipt.json"),
)


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


def build(output_dir: Path, semantic_dir: Path = SEMANTIC_DIR,
          priority_path: Path = PRIORITY):
    semantic_dir = Path(semantic_dir); output_dir = Path(output_dir)
    facts = []; sources = {}
    for artifact_name, receipt_name in FILES:
        artifact = semantic_dir / artifact_name
        receipt_path = semantic_dir / receipt_name
        receipt = json.loads(receipt_path.read_bytes())
        digest = sha(artifact)
        if digest != receipt["artifact_sha256"]:
            raise ValueError("SEMANTIC_ARTIFACT_HASH_MISMATCH:" + artifact_name)
        reports = rows(artifact)
        if sum(len(report["facts"]) for report in reports) != receipt["fact_count"]:
            raise ValueError("SEMANTIC_FACT_COUNT_MISMATCH:" + artifact_name)
        facts.extend(fact for report in reports for fact in report["facts"])
        sources[artifact_name] = digest
    if len(facts) != 199969:
        raise ValueError(f"SEMANTIC_CORPUS_COUNT_MISMATCH:{len(facts)}")

    priority_tickers = {row["ticker"] for row in rows(priority_path)}
    observations = []
    for fact in facts:
        if fact.get("canonical_field") != "ISSUED_CAPITAL":
            continue
        dimensions = fact.get("dimensions", {})
        observations.append({
            "contract": "EXPERIMENTAL_CAPITAL_OBSERVATION_V1",
            "ticker": fact["ticker"],
            "observed_at": fact["period_end"],
            "published_at": fact["published_at"],
            "effective_from": None,
            "capital": fact["value"],
            "nominal_value": None,
            "derived_shares": None,
            "share_state_usable": False,
            "status": "CAPITAL_OBSERVED_NOMINAL_AND_SHARE_CLASS_PROOF_MISSING",
            "source_disclosure": fact.get("disclosure_id"),
            "source_archive": dimensions.get("archive_name"),
            "source_archive_sha256": dimensions.get("archive_sha256"),
            "source_member": dimensions.get("member_name"),
            "source_member_sha256": dimensions.get("member_sha256"),
            "source_entity_code": dimensions.get("source_entity_code"),
            "priority_3017_ticker": fact["ticker"] in priority_tickers,
            "composite_share_classes_reconciled": False,
            "action_completeness_provided": False,
        })
    observations.sort(key=lambda row: (row["ticker"], row["observed_at"],
                                       row["published_at"], row["source_member"] or ""))
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / "issued_capital_observations.jsonl.gz"
    artifact.write_bytes(gzip.compress(b"".join(encoded(row) for row in observations), mtime=0))
    per_ticker = Counter(row["ticker"] for row in observations)
    priority_rows = [row for row in observations if row["priority_3017_ticker"]]
    receipt = {
        "contract": "EXPERIMENTAL_M2_SHARE_STATE_CANDIDATES_V1",
        "semantic_fact_count_examined": len(facts),
        "issued_capital_observation_count": len(observations),
        "issued_capital_ticker_count": len(per_ticker),
        "priority_ticker_count_with_observations": len({row["ticker"] for row in priority_rows}),
        "priority_observation_count": len(priority_rows),
        "derived_share_count": 0,
        "usable_share_state_count": 0,
        "nominal_value_fact_count": 0,
        "direct_number_of_shares_fact_count": 0,
        "economic_conclusion": "ISSUED_CAPITAL_ALONE_IS_NOT_SHARE_COUNT",
        "required_next_evidence": [
            "dated nominal value per listed share class",
            "complete listed/nonlisted share-class aggregation",
            "effective corporate-action inventory and reconciled post-state",
        ],
        "source_hashes": sources,
        "priority_matrix_sha256": sha(priority_path),
        "output_sha256": sha(artifact),
        "per_ticker_observation_counts": dict(sorted(per_ticker.items())),
    }
    (output_dir / "receipt.json").write_bytes(encoded(receipt))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output_dir), ensure_ascii=False, indent=2))
