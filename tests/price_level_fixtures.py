"""Explicit synthetic evidence for adapter unit tests, never real data builds."""
from datetime import datetime
from hashlib import sha256
import json

import pandas as pd

from src.analytics.price_level_action_evidence import CONTRACT, SOURCE_SHARE_BASIS, PriceLevelActionEvidence
from src.analytics.price_level_adapter import PriceLevelActionBundle
from src.analytics.price_level_valuation_basis import PRICE_LEVEL_BASIS, SHARE_BASIS


def certify_frames(source, prices, analysis_at, date_column):
    source["share_basis"] = SOURCE_SHARE_BASIS
    prices["price_basis"] = PRICE_LEVEL_BASIS
    bundles = []
    for price in prices.to_dict("records"):
        rows = source[source["ticker"] == price["ticker"]].sort_values(date_column)
        if rows.empty:
            bundles.append(None)
            continue
        row = rows.iloc[-1]
        raw = json.dumps({"synthetic_test_only": True, "ticker": price["ticker"],
                          "shares_out": float(row["shares_out"]), "date": str(row[date_column])}).encode()
        ref = "test:" + price["ticker"]
        source_sha = sha256(raw).hexdigest()
        payload = {"contract": CONTRACT, "ticker": price["ticker"],
                   "source_share_basis": SOURCE_SHARE_BASIS, "source_shares_out": float(row["shares_out"]),
                   "shares_basis_date": str(pd.Timestamp(row[date_column]).date()),
                   "complete_through": str(pd.Timestamp(price["price_trade_date"]).date()),
                   "enumeration_complete": True, "completeness_source_ref": ref, "share_source_ref": ref,
                   "sources": [{"source_ref": ref, "source_sha256": source_sha,
                                "published_at": analysis_at.isoformat()}], "events": []}
        manifest = json.dumps(payload, sort_keys=True).encode()
        bundles.append(PriceLevelActionBundle(PriceLevelActionEvidence(
            manifest, sha256(manifest).hexdigest(), {ref: raw}), ()))
    prices["action_bundle"] = bundles
