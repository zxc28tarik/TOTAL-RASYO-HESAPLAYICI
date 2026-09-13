"""Repeatable real P2 gate. No synthetic evidence and no adjusted-price fallback."""
from pathlib import Path
from hashlib import sha256
import argparse
import json
from src.analytics.historical_valuation_price_supplement import CATALOG, materialize_historical_price_level_v2
from src.analytics.price_level_adapter import load_action_bundles, valuation_basis_receipt
from datetime import date

ROOT = Path(__file__).resolve().parents[1]

def audit(bundle_root=None):
    rows = []
    for evidence in CATALOG:
        archive = ROOT / "data/backtest_sources/p2_raw_close_pit_v2" / Path(evidence.archive_url).name
        bundle = None
        if bundle_root is not None:
            index = Path(bundle_root) / str(evidence.signal_date) / "index.json"
            if index.is_file():
                bundle = load_action_bundles(index).get(evidence.ticker)
        row = {"ticker": evidence.ticker, "signal_date": str(evidence.signal_date),
               "trade_date": str(evidence.trade_date), "raw_close": evidence.raw_close,
               "archive_sha256": evidence.archive_sha256, "member_sha256": evidence.member_sha256}
        try:
            manifest = json.loads(bundle.evidence.manifest_bytes) if bundle else {}
            value = materialize_historical_price_level_v2(evidence=evidence,
                analysis_at=evidence.cutoff_local, raw_archive_bytes=archive.read_bytes(),
                shares_out=manifest.get("source_shares_out"),
                shares_basis_date=date.fromisoformat(manifest["shares_basis_date"]) if bundle else None,
                action_bundle=bundle)
            row.update(status="MATERIALIZED", receipt=valuation_basis_receipt(value))
        except (ValueError, TypeError, OSError) as exc:
            row.update(status="REJECTED", reason=str(exc))
        rows.append(row)
    return {"contract": "P2_RAW_CLOSE_PIT_AUDIT_V2", "profile": "EXPERIMENTAL_RISK_ACCEPTED_5Y",
            "status": "PASS" if all(r["status"] == "MATERIALIZED" for r in rows) else "BLOCKED",
            "rows": rows, "row_count": len(rows),
            "proxy_is_canonical_nav": False, "candidate_993_recomputed": False}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path)
    args = parser.parse_args()
    first = json.dumps(audit(args.bundle_root), indent=2, sort_keys=True, default=str) + "\n"
    second = json.dumps(audit(args.bundle_root), indent=2, sort_keys=True, default=str) + "\n"
    if first != second:
        raise RuntimeError("independent P2 materializations disagree")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(first.encode())
    print(json.loads(first)["status"], "12/12 accounted", sha256(first.encode()).hexdigest())
