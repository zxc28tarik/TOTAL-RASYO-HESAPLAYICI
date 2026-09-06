"""Hash-pinned evidence adapter for Yahoo's unadjusted historical Close.

This module proves only the price side of the price-level valuation contract.
It deliberately does not certify shares outstanding or corporate-action
completeness.  Alias rows are excluded because a successor's price history is
not automatically evidence for an old security's valuation identity.
"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Mapping

import pandas as pd

from src.analytics.price_level_valuation_basis import PRICE_LEVEL_BASIS

CONTRACT = "VERIFIED_YAHOO_RAW_CLOSE_EVIDENCE_V1"
RECEIPT_CONTRACT = "VERIFIED_YAHOO_RAW_CLOSE_RECEIPT_V1"
ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/backtest_sources/yahoo_raw_close_evidence_v1.json"


class YahooRawCloseEvidenceError(ValueError):
    pass


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _positive(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise YahooRawCloseEvidenceError(f"{name}_INVALID")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise YahooRawCloseEvidenceError(f"{name}_INVALID") from exc
    if not math.isfinite(result) or result <= 0:
        raise YahooRawCloseEvidenceError(f"{name}_INVALID")
    return result


def _one_row(frame: pd.DataFrame, *, ticker: str, trade_date: str, label: str) -> pd.Series:
    try:
        matched = frame.loc[(ticker, trade_date)]
    except KeyError as exc:
        raise YahooRawCloseEvidenceError(f"YAHOO_{label}_ROW_MISSING_OR_DUPLICATE") from exc
    if isinstance(matched, pd.DataFrame):
        raise YahooRawCloseEvidenceError(f"YAHOO_{label}_ROW_MISSING_OR_DUPLICATE")
    return matched


@lru_cache(maxsize=4)
def _load(root_text: str, manifest_text: str):
    root = Path(root_text)
    manifest_path = Path(manifest_text)
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("contract") != CONTRACT:
        raise YahooRawCloseEvidenceError("YAHOO_RAW_CLOSE_MANIFEST_CONTRACT_MISMATCH")

    checked = {}
    for key in ("acquisition_workflow", "discovery_summary", "discovery_prices",
                "resolution_summary", "resolved_prices"):
        path = (root / manifest[key]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise YahooRawCloseEvidenceError("YAHOO_RAW_CLOSE_PATH_OUTSIDE_REPO")
        digest = _sha(path)
        if digest != manifest[f"{key}_sha256"]:
            raise YahooRawCloseEvidenceError(f"YAHOO_{key.upper()}_HASH_MISMATCH")
        checked[key] = path

    workflow = checked["acquisition_workflow"].read_text(encoding="utf-8")
    required_fragments = (
        "auto_adjust=False", "actions=True", "repair=False",
        "h.get('Close')", "h.get('Adj Close')",
    )
    if any(fragment not in workflow for fragment in required_fragments):
        raise YahooRawCloseEvidenceError("YAHOO_ACQUISITION_SEMANTICS_MISMATCH")

    discovery_summary = json.loads(checked["discovery_summary"].read_bytes())
    resolution_summary = json.loads(checked["resolution_summary"].read_bytes())
    if (discovery_summary.get("contract") != "V24_HISTORICAL_MEMBER_YAHOO_DISCOVERY_V2"
            or discovery_summary.get("workflow_head_sha") != manifest["acquisition_workflow_head_sha"]
            or discovery_summary.get("price_row_count") != 270038):
        raise YahooRawCloseEvidenceError("YAHOO_DISCOVERY_SUMMARY_MISMATCH")
    if (resolution_summary.get("contract") != "V24_HISTORICAL_PRICE_RESOLUTION_V1"
            or resolution_summary.get("discovery_contract") != discovery_summary["contract"]
            or resolution_summary.get("discovery_summary_sha256") != manifest["discovery_summary_sha256"]):
        raise YahooRawCloseEvidenceError("YAHOO_RESOLUTION_SUMMARY_MISMATCH")

    columns = ["ticker", "yahoo_symbol", "trade_date", "close", "adj_close"]
    discovery = pd.read_csv(checked["discovery_prices"], usecols=columns, dtype={"ticker": str, "yahoo_symbol": str, "trade_date": str})
    resolved = pd.read_csv(checked["resolved_prices"], usecols=columns + ["price_source_ticker", "price_resolution"],
                           dtype={"ticker": str, "yahoo_symbol": str, "trade_date": str,
                                  "price_source_ticker": str, "price_resolution": str})
    if discovery.duplicated(["ticker", "trade_date"]).any() or resolved.duplicated(["ticker", "trade_date"]).any():
        raise YahooRawCloseEvidenceError("YAHOO_SOURCE_ROW_DUPLICATE")
    discovery = discovery.set_index(["ticker", "trade_date"]).sort_index()
    resolved = resolved.set_index(["ticker", "trade_date"]).sort_index()
    return manifest, discovery, resolved


def clear_yahoo_raw_close_cache():
    """Test hook; production callers normally retain the immutable source index."""
    _load.cache_clear()


def direct_yahoo_candidate_from_source(*, ticker: str, trade_date: str,
                                       root: Path = ROOT, manifest_path: Path = MANIFEST) -> dict:
    """Return one hash-bound direct row; primarily for legacy artifact migration."""
    manifest, _discovery, resolved = _load(
        str(Path(root).resolve()), str(Path(manifest_path).resolve()))
    ticker = str(ticker).strip().upper()
    row = _one_row(resolved, ticker=ticker, trade_date=str(trade_date), label="RESOLVED")
    return {
        "trade_date": str(trade_date), "close": _positive(row.close, "YAHOO_SOURCE_CLOSE"),
        "adj_close": _positive(row.adj_close, "YAHOO_SOURCE_ADJ_CLOSE"),
        "yahoo_symbol": str(row.yahoo_symbol),
        "source_ticker": str(row.price_source_ticker),
        "resolution": str(row.price_resolution),
        "file_sha256": manifest["resolved_prices_sha256"],
    }


def verify_yahoo_raw_close(*, ticker: str, candidate: Mapping,
                           analysis_at: datetime, root: Path = ROOT,
                           manifest_path: Path = MANIFEST) -> dict:
    if not isinstance(analysis_at, datetime) or analysis_at.tzinfo is None or analysis_at.utcoffset() is None:
        raise YahooRawCloseEvidenceError("YAHOO_ANALYSIS_CUTOFF_TIMEZONE_REQUIRED")
    ticker = str(ticker).strip().upper()
    trade_date = str(candidate.get("trade_date", candidate.get("price_trade_date", "")))
    try:
        day = date.fromisoformat(trade_date)
    except ValueError as exc:
        raise YahooRawCloseEvidenceError("YAHOO_TRADE_DATE_INVALID") from exc
    if day > analysis_at.date():
        raise YahooRawCloseEvidenceError("PRICE_AFTER_CUTOFF")

    manifest, discovery, resolved = _load(str(Path(root).resolve()), str(Path(manifest_path).resolve()))
    if candidate.get("file_sha256") != manifest["resolved_prices_sha256"]:
        raise YahooRawCloseEvidenceError("YAHOO_RESOLVED_FILE_HASH_MISMATCH")
    if candidate.get("resolution") != manifest["accepted_resolution"]:
        raise YahooRawCloseEvidenceError("YAHOO_PRICE_RESOLUTION_NOT_DIRECT")
    if str(candidate.get("source_ticker", "")).strip().upper() != ticker:
        raise YahooRawCloseEvidenceError("YAHOO_PRICE_SOURCE_TICKER_MISMATCH")
    expected_symbol = f"{ticker}.IS"
    if candidate.get("yahoo_symbol") != expected_symbol:
        raise YahooRawCloseEvidenceError("YAHOO_SYMBOL_MISMATCH")

    resolved_row = _one_row(resolved, ticker=ticker, trade_date=trade_date, label="RESOLVED")
    discovery_row = _one_row(discovery, ticker=ticker, trade_date=trade_date, label="DISCOVERY")
    for row in (resolved_row, discovery_row):
        if str(row.yahoo_symbol) != expected_symbol:
            raise YahooRawCloseEvidenceError("YAHOO_SOURCE_ROW_SYMBOL_MISMATCH")
        if not math.isclose(_positive(row.close, "YAHOO_SOURCE_CLOSE"),
                            _positive(candidate.get("close"), "YAHOO_CANDIDATE_CLOSE"),
                            rel_tol=1e-12, abs_tol=1e-12):
            raise YahooRawCloseEvidenceError("YAHOO_RAW_CLOSE_VALUE_MISMATCH")
        if not math.isclose(_positive(row.adj_close, "YAHOO_SOURCE_ADJ_CLOSE"),
                            _positive(candidate.get("adj_close"), "YAHOO_CANDIDATE_ADJ_CLOSE"),
                            rel_tol=1e-12, abs_tol=1e-12):
            raise YahooRawCloseEvidenceError("YAHOO_ADJ_CLOSE_DIAGNOSTIC_MISMATCH")
    if (str(resolved_row.price_source_ticker) != ticker
            or str(resolved_row.price_resolution) != manifest["accepted_resolution"]):
        raise YahooRawCloseEvidenceError("YAHOO_RESOLVED_ROW_NOT_DIRECT")

    canonical_row = {
        "ticker": ticker, "yahoo_symbol": expected_symbol, "trade_date": trade_date,
        "close": _positive(discovery_row.close, "YAHOO_SOURCE_CLOSE"),
        "adj_close": _positive(discovery_row.adj_close, "YAHOO_SOURCE_ADJ_CLOSE"),
    }
    row_hash = sha256(json.dumps(canonical_row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "contract": RECEIPT_CONTRACT,
        "ticker": ticker,
        "yahoo_symbol": expected_symbol,
        "trade_date": trade_date,
        "raw_close": canonical_row["close"],
        "adjusted_close_diagnostic": canonical_row["adj_close"],
        "price_basis": PRICE_LEVEL_BASIS,
        "price_resolution": manifest["accepted_resolution"],
        "source_row_sha256": row_hash,
        "discovery_prices_sha256": manifest["discovery_prices_sha256"],
        "resolved_prices_sha256": manifest["resolved_prices_sha256"],
        "discovery_summary_sha256": manifest["discovery_summary_sha256"],
        "resolution_summary_sha256": manifest["resolution_summary_sha256"],
        "acquisition_workflow_sha256": manifest["acquisition_workflow_sha256"],
        "acquisition_workflow_head_sha": manifest["acquisition_workflow_head_sha"],
        "auto_adjust": False,
        "close_field": "Close",
        "adjusted_close_field": "Adj Close",
        "corporate_action_evidence_provided": False,
        "share_count_evidence_provided": False,
    }


def price_level_input_from_yahoo_receipt(receipt: Mapping, action_bundle=None) -> dict:
    if receipt.get("contract") != RECEIPT_CONTRACT:
        raise YahooRawCloseEvidenceError("YAHOO_RAW_CLOSE_RECEIPT_CONTRACT_MISMATCH")
    return {
        "ticker": receipt["ticker"],
        "price_trade_date": receipt["trade_date"],
        "current_price": receipt["raw_close"],
        "adjusted_close": receipt["adjusted_close_diagnostic"],
        "price_basis": PRICE_LEVEL_BASIS,
        "action_bundle": action_bundle,
    }
