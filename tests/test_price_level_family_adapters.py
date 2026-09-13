from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import importlib
import json

import pandas as pd
import pytest

from src.analytics.historical_backtest_corporate_action_events import HistoricalCorporateAction
from src.analytics.price_level_action_evidence import PriceLevelActionEvidence
from src.analytics.price_level_adapter import PriceLevelActionBundle, load_action_bundles, BUNDLE_CONTRACT
from src.analytics.price_level_valuation_basis import SHARE_BASIS

FAMILIES = ("nonfin", "holding", "gyo", "insurance", "financial_institution")


def case(family):
    fixture = importlib.import_module(f"test_{family}_batch_pipeline")
    universe, source, prices, *_ = fixture.frames()
    pipeline = importlib.import_module(f"src.analytics.{family}_batch_pipeline")
    kwargs = {"universe": universe, "prices": prices, "analysis_at": fixture.ANALYSIS}
    kwargs["financials" if family == "nonfin" else "navs" if family in ("holding", "gyo") else "metrics"] = source
    if family == "nonfin":
        kwargs["anchor_period_end"] = None
    return getattr(pipeline, f"build_{family}_snapshots_from_frames"), kwargs, source


@pytest.mark.parametrize("family", FAMILIES)
def test_raw_price_and_matching_split_share_transform(family):
    build, args, _ = case(family)
    before, rejects = build(**args)
    assert not rejects
    price = args["prices"].iloc[0]
    original = price["action_bundle"]
    manifest = json.loads(original.evidence.manifest_bytes)
    source = manifest["sources"][0]
    split = HistoricalCorporateAction.build(ticker=price.ticker, action_type="SHARE_MULTIPLIER",
                ex_date=price.price_trade_date, share_multiplier=2, source_ref=source["source_ref"],
                source_sha256=source["source_sha256"])
    manifest["events"] = [{"action_id": split.action_id, "economic_kind": "SPLIT"}]
    raw = json.dumps(manifest).encode()
    args["prices"].at[0, "action_bundle"] = PriceLevelActionBundle(
        PriceLevelActionEvidence(raw, sha256(raw).hexdigest(), original.evidence.source_bytes), (split,))
    args["prices"].at[0, "current_price"] = float(price.current_price) / 2
    receipts = {}
    after, rejects = build(**args, basis_receipts=receipts)
    assert not rejects
    first = next(s for s in before if s.ticker == price.ticker)
    second = next(s for s in after if s.ticker == price.ticker)
    assert second.shares_out == first.shares_out * 2
    assert second.current_price * second.shares_out == first.current_price * first.shares_out
    assert receipts[price.ticker]["share_basis"] == SHARE_BASIS
    assert receipts[price.ticker]["applied_share_action_ids"] == (split.action_id,)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("mutation", ["missing", "hash", "future", "ticker", "basis", "source_date", "price_date", "shares"])
def test_all_families_fail_closed_on_bad_evidence(family, mutation):
    build, args, _ = case(family)
    prices = args["prices"]
    ticker = prices.iloc[0].ticker
    original = prices.iloc[0].action_bundle
    manifest = json.loads(original.evidence.manifest_bytes)
    if mutation == "missing":
        prices.at[0, "action_bundle"] = None
    elif mutation == "basis":
        prices.at[0, "price_basis"] = "ADJUSTED_PRICE_SERIES_V1"
    elif mutation == "hash":
        prices.at[0, "action_bundle"] = replace(original, evidence=replace(original.evidence, expected_sha256="0" * 64))
    else:
        if mutation == "future":
            manifest["sources"][0]["published_at"] = (args["analysis_at"] + timedelta(seconds=1)).isoformat()
        elif mutation == "ticker":
            manifest["ticker"] = "WRONG"
        elif mutation == "source_date":
            manifest["shares_basis_date"] = "2000-01-01"
        elif mutation == "price_date":
            manifest["complete_through"] = "2099-01-01"
        elif mutation == "shares":
            manifest["source_shares_out"] *= 2
        raw = json.dumps(manifest).encode()
        prices.at[0, "action_bundle"] = replace(original, evidence=replace(
            original.evidence, manifest_bytes=raw, expected_sha256=sha256(raw).hexdigest()))
    snapshots, rejected = build(**args)
    assert ticker not in {row.ticker for row in snapshots}
    assert [row["ticker"] for row in rejected] == [ticker]
    assert len(snapshots) + len(rejected) == len(args["universe"])


@pytest.mark.parametrize("family", FAMILIES)
def test_price_sql_fetches_raw_close_only(family, monkeypatch):
    pipeline = importlib.import_module(f"src.analytics.{family}_batch_pipeline")
    fixture = importlib.import_module(f"test_{family}_batch_pipeline")
    queries = []
    def read_sql(sql, *args, **kwargs):
        queries.append(sql)
        return pd.DataFrame()
    monkeypatch.setattr(pd, "read_sql", read_sql)
    getattr(pipeline, f"fetch_{family}_prices")(object(), tickers=["AAA"], analysis_at=fixture.ANALYSIS)
    assert len(queries) == 1
    assert "close AS current_price" in queries[0]
    assert "adj_close" not in queries[0]


def test_loader_requires_hash_locked_source_bytes(tmp_path):
    build, args, _ = case("holding")
    bundle = args["prices"].iloc[0].action_bundle
    (tmp_path / "manifest.json").write_bytes(bundle.evidence.manifest_bytes)
    (tmp_path / "events.json").write_bytes(b"[]")
    ref, raw = next(iter(bundle.evidence.source_bytes.items()))
    (tmp_path / "source.bin").write_bytes(raw)
    entry = {"ticker": "AAA", "manifest_path": "manifest.json", "manifest_sha256": bundle.evidence.expected_sha256,
             "events_path": "events.json", "events_sha256": sha256(b"[]").hexdigest(),
             "sources": [{"source_ref": ref, "path": "source.bin", "sha256": sha256(raw).hexdigest()}]}
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"contract": BUNDLE_CONTRACT, "entries": [entry]}))
    assert load_action_bundles(path)["AAA"] == bundle
    (tmp_path / "source.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA256"):
        load_action_bundles(path)
