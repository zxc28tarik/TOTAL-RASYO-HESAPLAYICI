"""Certify the sole same-day KAP share state without inventing action history."""
from __future__ import annotations

from datetime import date, datetime
import gzip
import hashlib
import json
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.price_level_action_evidence import (
    CONTRACT, SOURCE_SHARE_BASIS, PriceLevelActionEvidence,
)
from src.analytics.price_level_valuation_basis import (
    build_price_level_observation, materialize_price_level_market_cap,
)


PRIORITY = ROOT / "data/audit/m2_priority_matrix_v1/m2_priority_cells.jsonl.gz"
BRIDGES = ROOT / "data/backtest_sources/borsa_thb_action_bridge_v1/cell_bridges.jsonl.gz"
KAP_RAW = ROOT / "data/backtest_sources/kap_share_class_history_v1/raw_responses.jsonl.gz"
OUT = ROOT / "data/backtest_sources/zero_interval_share_basis_v1"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]


def _encode(value: object, *, indent: int | None = None) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=None if indent else (",", ":"), indent=indent,
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build_manifest(*, source_sha256: str, shares_out: int) -> bytes:
    return _encode({
        "contract": CONTRACT, "ticker": "SMRTG",
        "source_share_basis": SOURCE_SHARE_BASIS,
        "source_shares_out": shares_out,
        "shares_basis_date": "2023-07-31", "complete_through": "2023-07-31",
        "enumeration_complete": True,
        "completeness_scope": "EMPTY_OPEN_CLOSED_DATE_INTERVAL_(2023-07-31,2023-07-31]",
        "sources": [{
            "source_ref": "KAP_SMRTG_SHARE_HISTORY",
            "source_sha256": source_sha256,
            "published_at": "2023-07-31T17:49:42+03:00",
        }],
        "completeness_source_ref": "KAP_SMRTG_SHARE_HISTORY",
        "share_source_ref": "KAP_SMRTG_SHARE_HISTORY", "events": [],
    })


def materialize(output_dir: Path = OUT) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    bridges = [row for row in _rows(BRIDGES)
               if row["status"] == "BRIDGE_COMPLETE_BEFORE_CUTOFF"]
    if len(bridges) != 1 or (bridges[0]["ticker"], bridges[0]["month"]) != ("SMRTG", "2023-08"):
        raise ValueError("ZERO_INTERVAL_CELL_SET_CHANGED")
    bridge = bridges[0]
    raw_meta = next(row for row in _rows(KAP_RAW) if row["ticker"] == "SMRTG")
    response = requests.get(raw_meta["url"], headers={
        "Accept-Language": "tr", "User-Agent": "TOTAL-RASYO evidence recapture/1",
    }, timeout=60)
    response.raise_for_status()
    source_bytes = response.content
    if _sha(source_bytes) != raw_meta["response_sha256"]:
        raise ValueError("KAP_HISTORY_RECAPTURE_BYTES_CHANGED")
    source_path = output_dir / "smrtg_kap_share_history.json"
    source_path.write_bytes(source_bytes)
    shares = int(bridge["shares_out"])
    manifest_bytes = build_manifest(source_sha256=_sha(source_bytes), shares_out=shares)
    manifest_path = output_dir / "action_coverage_manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    evidence = PriceLevelActionEvidence(
        manifest_bytes=manifest_bytes, expected_sha256=_sha(manifest_bytes),
        source_bytes={"KAP_SMRTG_SHARE_HISTORY": source_bytes},
    )
    cell = next(row for row in _rows(PRIORITY)
                if row["ticker"] == "SMRTG" and row["month"] == "2023-08")
    price_receipt = cell["raw_close_receipt"]
    price = build_price_level_observation(
        ticker="SMRTG", trade_date=date.fromisoformat(cell["price_trade_date"]),
        close=price_receipt["raw_close"],
        adjusted_close=price_receipt["adjusted_close_diagnostic"],
    )
    market_cap = materialize_price_level_market_cap(
        price=price, shares_out=shares, shares_basis_date=price.trade_date,
        corporate_actions=(), events_complete_through=price.trade_date,
        evidence=evidence, cutoff=datetime.fromisoformat(cell["knowledge_cutoff_at"]),
    )
    receipt = {
        "contract": "ZERO_INTERVAL_VERIFIED_SHARE_BASIS_V1",
        "ticker": "SMRTG", "month": "2023-08",
        "knowledge_cutoff_at": cell["knowledge_cutoff_at"],
        "share_state_published_at": "2023-07-31T17:49:42+03:00",
        "shares_out": shares, "nominal_value_per_share_assumed": False,
        "price_trade_date": price.trade_date.isoformat(), "raw_close": price.close,
        "normalized_shares_out": market_cap.normalized_shares_out,
        "market_cap": market_cap.market_cap,
        "applied_share_action_ids": list(market_cap.applied_share_action_ids),
        "empty_interval_only": True,
        "nonempty_action_completeness_claimed": False,
        "m2_materialized": False,
        "m2_blockers": [
            "VERIFIED_SHARE_PEER_COHORT_INSUFFICIENT",
            "SOURCE_DERIVATION_PROFILE_MISMATCH:artifact=KAP_BULK_GENERAL_HOLDING_CORE_EXACT_V1;config=KAP_NONBANK_CORE_EXAMPLE",
        ],
        "outputs": {
            source_path.name: _sha(source_path.read_bytes()),
            manifest_path.name: _sha(manifest_path.read_bytes()),
        },
    }
    (output_dir / "receipt.json").write_bytes(_encode(receipt, indent=2))
    return receipt


if __name__ == "__main__":
    print(json.dumps(materialize(), ensure_ascii=False, indent=2))
