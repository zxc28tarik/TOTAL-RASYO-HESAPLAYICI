"""Read-only audit of preserved KAP package bytes; never repair identity claims."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path


def audit(root: Path) -> dict:
    package = root / "data/backtest_sources/kap_bulk_financial_source_capture"
    checksum_file = package / "SHA256SUMS"
    rows = []
    seen = set()
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        expected, name = line.split()
        if name in seen or Path(name).name != name or len(expected) != 64:
            raise ValueError("invalid preserved checksum entry")
        seen.add(name)
        path = package / name
        observed = sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        rows.append({"path": path.relative_to(root).as_posix(), "expected_sha256": expected,
                     "observed_sha256": observed,
                     "status": "MISSING" if observed is None else "MATCH" if observed == expected else "MISMATCH"})
    return {"contract": "ASTRA_PRESERVED_KAP_SOURCE_AUDIT_V1",
            "profile": "EXPERIMENTAL_RISK_ACCEPTED_5Y",
            "status": "PASS" if all(row["status"] == "MATCH" for row in rows) else "BLOCKED",
            "checksum_file_sha256": sha256(checksum_file.read_bytes()).hexdigest(),
            "entries": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(Path(__file__).resolve().parents[1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(result["status"])
    raise SystemExit(0 if result["status"] == "PASS" else 2)
