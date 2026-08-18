from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.analytics.historical_price_supplement import (
    EXPECTED_EXACT_KEYS,
    PRICE_RESOLUTION,
    SOURCE,
    HistoricalPriceSupplementError,
    fill_exact_signal_price_gaps,
    load_borsa_thb_exact_signal_prices,
)


ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / "data/backtest_sources/borsa_exact_price_gap_discovery/thb_schema_proof.json"
COVERAGE = ROOT / "data/backtest_sources/yahoo_resolved/monthly_member_signal_price_coverage.csv"
MISSING = ROOT / "data/backtest_sources/yahoo_resolved/monthly_member_missing_signal_prices.csv"


def _supplement() -> pd.DataFrame:
    return load_borsa_thb_exact_signal_prices(PROOF)


def test_official_thb_proof_parses_exact_12_signal_prices_by_named_bilingual_columns():
    frame = _supplement()

    assert len(frame) == 12
    assert {
        (row.ticker, pd.Timestamp(row.trade_date).date().isoformat())
        for row in frame.itertuples(index=False)
    } == EXPECTED_EXACT_KEYS
    assert frame["price_resolution"].eq(PRICE_RESOLUTION).all()
    assert frame["execution_source"].eq(SOURCE).all()
    assert frame["source_url"].str.startswith("https://borsaistanbul.com/data/thb/").all()
    assert frame["archive_sha256"].str.fullmatch(r"[0-9a-f]{64}").all()
    assert frame["member_sha256"].str.fullmatch(r"[0-9a-f]{64}").all()
    assert (frame["open"] > 0).all()
    assert (frame["close"] > 0).all()

    inves = frame.set_index(["ticker", "trade_date"]).loc[("INVES", pd.Timestamp("2022-07-01"))]
    assert float(inves["open"]) == pytest.approx(37.00)
    assert float(inves["close"]) == pytest.approx(37.14)


def test_official_thb_loader_fails_closed_if_bilingual_open_semantics_are_mutated(tmp_path: Path):
    payload = json.loads(PROOF.read_text(encoding="utf-8"))
    payload["rows"][0]["first_lines"][1] = payload["rows"][0]["first_lines"][1].replace(
        "OPENING PRICE", "BROKEN OPEN SEMANTIC", 1
    )
    mutated = tmp_path / "mutated.json"
    mutated.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(HistoricalPriceSupplementError, match="bilingual semantic mismatch"):
        load_borsa_thb_exact_signal_prices(mutated)


def test_exact_borsa_supplement_closes_all_6000_monthly_member_execution_prices_without_overwrite():
    base = pd.read_csv(COVERAGE)
    missing = pd.read_csv(MISSING)
    supplement = _supplement()

    assert len(base) == 60 * 100
    assert len(missing) == 12
    expected_missing = {
        (str(row.ticker).strip().upper(), str(row.signal_date))
        for row in missing.itertuples(index=False)
    }
    assert expected_missing == EXPECTED_EXACT_KEYS

    available_before = base[base["has_execution_price"].astype(str).str.lower().eq("true")].copy()
    filled, audit = fill_exact_signal_price_gaps(base, supplement)

    assert audit.missing_before == 12
    assert audit.supplemented == 12
    assert audit.missing_after == 0
    assert audit.total_rows == 6000
    assert len(filled) == 6000
    assert filled["has_execution_price"].astype(str).str.lower().eq("true").all()
    assert pd.to_numeric(filled["open"], errors="coerce").gt(0).all()
    assert pd.to_numeric(filled["close"], errors="coerce").gt(0).all()

    supplemented = filled[filled["price_resolution"].eq(PRICE_RESOLUTION)].copy()
    assert len(supplemented) == 12
    assert supplemented["execution_source"].eq(SOURCE).all()
    assert {
        (row.ticker, pd.Timestamp(row.signal_date).date().isoformat())
        for row in supplemented.itertuples(index=False)
    } == EXPECTED_EXACT_KEYS

    # All rows that already had Yahoo/lineage execution prices must retain their
    # original execution values and provenance.  Only the 12 audited holes move.
    original_cols = [
        "month", "signal_date", "index_code", "ticker", "trade_date", "open", "close",
        "price_source_ticker", "price_resolution", "has_execution_price",
    ]
    before = available_before[original_cols].copy()
    available_keys = available_before[["signal_date", "ticker"]].copy()
    available_keys["signal_date"] = pd.to_datetime(available_keys["signal_date"]).dt.normalize()
    after = filled.merge(
        available_keys,
        on=["signal_date", "ticker"],
        how="inner",
    )[original_cols].copy()

    for frame in (before, after):
        frame["signal_date"] = pd.to_datetime(frame["signal_date"]).dt.strftime("%Y-%m-%d")
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
        frame["has_execution_price"] = frame["has_execution_price"].astype(str).str.lower()
        frame.sort_values(["signal_date", "ticker"], inplace=True)
        frame.reset_index(drop=True, inplace=True)

    pd.testing.assert_frame_equal(before, after, check_dtype=False)


def test_supplement_refuses_to_overwrite_an_existing_execution_price():
    supplement = _supplement().iloc[[0]].copy()
    row = supplement.iloc[0]
    coverage = pd.DataFrame(
        [
            {
                "month": "2022-07",
                "signal_date": "2022-07-01",
                "index_code": "XU100",
                "ticker": row["ticker"],
                "trade_date": "2022-07-01",
                "open": 999.0,
                "close": 999.0,
                "price_source_ticker": row["ticker"],
                "price_resolution": "DIRECT_YAHOO",
                "has_execution_price": True,
            }
        ]
    )

    with pytest.raises(HistoricalPriceSupplementError, match="extra_or_overlap"):
        fill_exact_signal_price_gaps(coverage, supplement)
