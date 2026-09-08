from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import gzip
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.price_level_action_evidence import (
    CONTRACT as ACTION_CONTRACT, SOURCE_QUOTED_NOMINAL_BASIS, PriceLevelActionEvidence,
)
from src.analytics.price_level_adapter import BUNDLE_CONTRACT, PriceLevelActionBundle, load_action_bundles
from src.analytics.price_level_valuation_basis import (
    build_price_level_observation, materialize_price_level_market_cap,
)


CONTRACT = "CURRENT_PRICE_LEVEL_BASIS_MATERIALIZATION_V1"
DEFAULT_SHARES = ROOT / "data/live/current_share_basis_v1"
DEFAULT_PRICES = ROOT / "data/live/current_raw_close_v1/raw_close.csv.gz"
DEFAULT_OUTPUT = ROOT / "data/live/current_price_level_basis_v1"
DEFAULT_QUOTE_UNIT = ROOT / "data/live/current_quote_unit_v1"
QUOTE_CONTRACT = "BORSA_PAY_PRICE_PER_1_TRY_NOMINAL_V1"


def _encoded(value: object, *, indent: int | None = None) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=None if indent else (",", ":"), indent=indent,
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def materialize(*, share_dir: Path, prices_path: Path, output_dir: Path,
                quote_unit_dir: Path = DEFAULT_QUOTE_UNIT) -> dict:
    share_dir = share_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifests").mkdir(exist_ok=True)
    (output_dir / "sources").mkdir(exist_ok=True)
    (output_dir / "events").mkdir(exist_ok=True)

    share_receipt = json.loads((share_dir / "receipt.json").read_text(encoding="utf-8"))
    captured_at = datetime.fromisoformat(share_receipt["captured_at"])
    analysis_at = max(datetime.now(timezone.utc), captured_at)
    quote_receipt = json.loads((quote_unit_dir / "receipt.json").read_text(encoding="utf-8"))
    if quote_receipt.get("contract") != QUOTE_CONTRACT:
        raise ValueError("unsupported Borsa quote-unit contract")
    quote_source = gzip.decompress((quote_unit_dir / "pay_piyasasi_proseduru.pdf.gz").read_bytes())
    if _sha(quote_source) != quote_receipt.get("source_pdf_sha256"):
        raise ValueError("Borsa quote-unit source hash mismatch")
    quoted_nominal_unit = Decimal(str(quote_receipt.get("quoted_nominal_unit_try")))
    if quoted_nominal_unit != Decimal("1"):
        raise ValueError("unsupported quoted nominal unit")
    share_rows = [
        json.loads(line)
        for line in gzip.decompress((share_dir / "ticker_share_basis.jsonl.gz").read_bytes()).splitlines()
    ]
    safe = {row["ticker"]: row for row in share_rows if row["usable_for_single_ticker_market_cap"]}
    issuers = {
        row["mkk_member_oid"]: row
        for row in (json.loads(line) for line in gzip.decompress(
            (share_dir / "issuer_responses.jsonl.gz").read_bytes()
        ).splitlines())
    }
    prices = pd.read_csv(prices_path, dtype={"ticker": str, "trade_date": str})
    if prices.duplicated("ticker").any():
        raise ValueError("current raw close duplicate ticker")
    price_by_ticker = prices.set_index("ticker").to_dict("index")

    entries: list[dict] = []
    caps: list[dict] = []
    rejected: list[dict] = []
    for ticker in sorted(safe):
        share = safe[ticker]
        price = price_by_ticker.get(ticker)
        if price is None:
            rejected.append({"ticker": ticker, "reason": "CURRENT_RAW_CLOSE_NOT_CAPTURED"})
            continue
        trade_date = date.fromisoformat(price["trade_date"])
        raw_shares = Decimal(share["latest_explicit_state"]["derived_shares"])
        if raw_shares <= 0 or raw_shares != raw_shares.to_integral_value():
            rejected.append({"ticker": ticker, "reason": "EXPLICIT_SHARE_COUNT_NOT_POSITIVE_INTEGER"})
            continue
        classes = share["latest_explicit_state"].get("classes") or []
        total_nominal_try = Decimal(share["latest_explicit_state"]["total_nominal_value_try"])
        if total_nominal_try <= 0:
            rejected.append({"ticker": ticker, "reason": "TOTAL_EXPLICIT_NOMINAL_NOT_POSITIVE"})
            continue
        shares_out = int(raw_shares)
        quoted_units = total_nominal_try / quoted_nominal_unit
        issuer = issuers[share["mkk_member_oid"]]
        http_date = issuer.get("http_date")
        try:
            source_available_at = parsedate_to_datetime(http_date).astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            rejected.append({"ticker": ticker, "reason": "KAP_SOURCE_HTTP_DATE_UNPROVEN"})
            continue
        if source_available_at.date() < trade_date:
            rejected.append({"ticker": ticker, "reason": "KAP_CURRENT_SNAPSHOT_PREDATES_PRICE"})
            continue
        if source_available_at > analysis_at:
            rejected.append({"ticker": ticker, "reason": "KAP_SOURCE_AFTER_ANALYSIS_CUTOFF"})
            continue
        source_ref = f"CURRENT_KAP_SHARE_HISTORY:{share['mkk_member_oid']}"
        source_bytes = _encoded({
            "mkk_member_oid": issuer["mkk_member_oid"],
            "url": issuer["url"], "http_date": issuer.get("http_date"),
            "response_sha256": issuer["response_sha256"], "response": issuer["response"],
        })
        source_rel = f"sources/{share['mkk_member_oid']}.json"
        source_path = output_dir / source_rel
        source_path.write_bytes(source_bytes)
        manifest = {
            "contract": ACTION_CONTRACT, "ticker": ticker,
            "source_share_basis": SOURCE_QUOTED_NOMINAL_BASIS,
            "source_shares_out": float(quoted_units),
            "shares_basis_date": trade_date.isoformat(),
            "complete_through": trade_date.isoformat(),
            "enumeration_complete": True,
            "completeness_scope": f"CURRENT_SNAPSHOT_EMPTY_INTERVAL_({trade_date},{trade_date}]",
            "sources": [{
                "source_ref": source_ref, "source_sha256": _sha(source_bytes),
                # A current endpoint response cannot be known before the server's
                # HTTP Date.  The local/offline receipt build time is not source
                # availability evidence.
                "published_at": source_available_at.isoformat(),
            }],
            "completeness_source_ref": source_ref, "share_source_ref": source_ref,
            "events": [],
        }
        manifest_bytes = _encoded(manifest)
        manifest_rel = f"manifests/{ticker}.json"
        manifest_path = output_dir / manifest_rel
        manifest_path.write_bytes(manifest_bytes)
        events_bytes = _encoded([])
        events_rel = f"events/{ticker}.json"
        events_path = output_dir / events_rel
        events_path.write_bytes(events_bytes)
        evidence = PriceLevelActionEvidence(manifest_bytes, _sha(manifest_bytes), {source_ref: source_bytes})
        observation = build_price_level_observation(
            ticker=ticker, trade_date=trade_date, close=float(price["raw_close"]),
            adjusted_close=float(price["adjusted_close_diagnostic"]),
        )
        basis = materialize_price_level_market_cap(
            price=observation, shares_out=float(quoted_units), shares_basis_date=trade_date,
            corporate_actions=(), events_complete_through=trade_date,
            evidence=evidence, cutoff=analysis_at,
        )
        caps.append({
            "ticker": ticker, "trade_date": trade_date.isoformat(),
            "raw_close": basis.raw_close, "shares_out": float(quoted_units),
            "legal_shares_out": shares_out,
            "normalized_shares_out": basis.normalized_shares_out,
            "quoted_nominal_units_out": float(quoted_units),
            "total_nominal_value_try": str(total_nominal_try),
            "market_cap": basis.market_cap,
            "price_basis": basis.price_basis,
            "price_quote_unit_basis": "BORSA_CLOSE_PER_1_TRY_NOMINAL_V1",
            "share_basis": basis.share_basis,
            "action_evidence_sha256": basis.action_evidence_sha256,
        })
        entries.append({
            "ticker": ticker, "manifest_path": manifest_rel,
            "manifest_sha256": _sha(manifest_bytes), "events_path": events_rel,
            "events_sha256": _sha(events_bytes),
            "sources": [{"source_ref": source_ref, "path": source_rel, "sha256": _sha(source_bytes)}],
        })

    index_path = output_dir / "action_bundle_index.json"
    index_path.write_bytes(_encoded({"contract": BUNDLE_CONTRACT, "entries": entries}, indent=2))
    # Exercise the production loader against every emitted bundle.
    loaded = load_action_bundles(index_path)
    if loaded is None or set(loaded) != {row["ticker"] for row in entries}:
        raise ValueError("current action bundle index verification failed")
    caps_path = output_dir / "market_caps.csv"
    cap_columns = (
        "ticker", "trade_date", "raw_close", "shares_out", "legal_shares_out",
        "normalized_shares_out",
        "quoted_nominal_units_out", "total_nominal_value_try", "market_cap",
        "price_basis", "price_quote_unit_basis", "share_basis", "action_evidence_sha256",
    )
    pd.DataFrame(caps, columns=cap_columns).sort_values("ticker").to_csv(caps_path, index=False)
    live_paths = {
        output_dir / row["manifest_path"] for row in entries
    } | {
        output_dir / row["events_path"] for row in entries
    } | {
        output_dir / source["path"]
        for row in entries for source in row["sources"]
    }
    for folder in (output_dir / "manifests", output_dir / "events", output_dir / "sources"):
        for stale in folder.glob("*.json"):
            if stale not in live_paths:
                stale.unlink()
    receipt = {
        "contract": CONTRACT, "analysis_at": analysis_at.isoformat(),
        "share_candidate_count": len(safe), "materialized_market_cap_count": len(caps),
        "rejection_count": len(rejected), "rejections": rejected,
        "nominal_value_per_share_assumed": False,
        "legal_share_count_equated_to_nominal_try": False,
        "quote_unit_contract": QUOTE_CONTRACT,
        "quote_unit_source_pdf_sha256": quote_receipt["source_pdf_sha256"],
        "market_cap_formula": quote_receipt["market_cap_formula"],
        "adjusted_close_used_for_market_cap": False,
        "empty_same_day_action_interval_only": True,
        "source_availability_basis": "KAP_RESPONSE_HTTP_DATE",
        "production_action_bundle_loader_verified": True,
        "outputs": {
            index_path.name: _sha(index_path.read_bytes()),
            caps_path.name: _sha(caps_path.read_bytes()),
        },
    }
    (output_dir / "receipt.json").write_bytes(_encoded(receipt, indent=2))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--share-dir", type=Path, default=DEFAULT_SHARES)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--quote-unit-dir", type=Path, default=DEFAULT_QUOTE_UNIT)
    args = parser.parse_args()
    print(json.dumps(materialize(
        share_dir=args.share_dir, prices_path=args.prices, output_dir=args.output_dir,
        quote_unit_dir=args.quote_unit_dir,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
