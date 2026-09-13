"""Common fail-closed boundary for all five price-level valuation families."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping

import pandas as pd

from src.analytics.historical_backtest_corporate_action_events import HistoricalCorporateAction
from src.analytics.price_level_action_evidence import PriceLevelActionEvidence, SOURCE_SHARE_BASIS
from src.analytics.price_level_valuation_basis import (
    PRICE_LEVEL_BASIS, SHARE_BASIS, PriceLevelValuationBasisError,
    build_price_level_observation, materialize_price_level_market_cap,
)

BUNDLE_CONTRACT = "PRICE_LEVEL_ACTION_BUNDLE_INDEX_V1"


@dataclass(frozen=True)
class PriceLevelActionBundle:
    evidence: PriceLevelActionEvidence
    events: tuple[HistoricalCorporateAction, ...]


def attach_action_bundles(prices: pd.DataFrame, bundles: Mapping[str, PriceLevelActionBundle] | None):
    if bundles is None:
        return prices
    result = prices.copy()
    result["action_bundle"] = result["ticker"].map(bundles)
    return result


def load_action_bundles(index_path: str | Path | None) -> dict[str, PriceLevelActionBundle] | None:
    """Read a reviewed, hash-pinned local bundle index; no network or fallback.

    One index is scoped to one batch/cutoff. Original event/source bytes are
    checked here and the requested ticker/date/publication scope at consumption.
    """
    if index_path is None:
        return None
    index_path = Path(index_path).resolve()
    index = json.loads(index_path.read_bytes())
    if index.get("contract") != BUNDLE_CONTRACT or not isinstance(index.get("entries"), list):
        raise PriceLevelValuationBasisError("invalid action bundle index")
    root = index_path.parent

    def read(name, expected):
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise PriceLevelValuationBasisError("action source path outside bundle root")
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != expected:
            raise PriceLevelValuationBasisError("action bundle source SHA256 mismatch")
        return raw

    bundles = {}
    for entry in index["entries"]:
        ticker = entry["ticker"]
        if not isinstance(ticker, str) or ticker != ticker.strip().upper() or not ticker or ticker in bundles:
            raise PriceLevelValuationBasisError("missing/noncanonical/duplicate action bundle ticker")
        manifest = read(entry["manifest_path"], entry["manifest_sha256"])
        event_rows = json.loads(read(entry["events_path"], entry["events_sha256"]))
        events = tuple(HistoricalCorporateAction.build(**row) for row in event_rows)
        sources = {}
        for source in entry["sources"]:
            if source["source_ref"] in sources:
                raise PriceLevelValuationBasisError("duplicate action bundle source")
            sources[source["source_ref"]] = read(source["path"], source["sha256"])
        bundles[ticker] = PriceLevelActionBundle(
            PriceLevelActionEvidence(manifest, entry["manifest_sha256"], sources), events)
    return bundles


def normalize_price_level_input(*, ticker: str, shares_out: object, source_date: date,
                                source_share_basis: str, price: Mapping, analysis_at: datetime):
    if source_share_basis != SOURCE_SHARE_BASIS:
        raise PriceLevelValuationBasisError("SOURCE_SHARE_BASIS_MISMATCH: dated unadjusted shares required")
    if price.get("price_basis") != PRICE_LEVEL_BASIS:
        raise PriceLevelValuationBasisError("PRICE_BASIS_MISMATCH: raw market close required")
    if str(price.get("ticker", "")).strip().upper() != ticker:
        raise PriceLevelValuationBasisError("PRICE_TICKER_MISMATCH")
    bundle = price.get("action_bundle")
    if not isinstance(bundle, PriceLevelActionBundle):
        raise PriceLevelValuationBasisError("ACTION_COMPLETENESS_EVIDENCE_MISSING")
    trade_date = pd.Timestamp(price["price_trade_date"]).date()
    return materialize_price_level_market_cap(
        price=build_price_level_observation(ticker=ticker, trade_date=trade_date, close=price["current_price"]),
        shares_out=shares_out, shares_basis_date=source_date, corporate_actions=bundle.events,
        events_complete_through=trade_date, evidence=bundle.evidence, cutoff=analysis_at)


def valuation_basis_receipt(value):
    return asdict(value)


def attach_basis_receipts(report, receipts):
    report["price_level_basis"] = {"price_basis": PRICE_LEVEL_BASIS, "share_basis": SHARE_BASIS,
                                   "receipts": receipts}
    for result in report.get("results", []):
        receipt = receipts.get(result["ticker"])
        if receipt is not None:
            result["valuation"].setdefault("diagnostics", {})["price_level_basis"] = receipt
            result["m2"].setdefault("score_inputs", {})["price_level_basis"] = receipt
