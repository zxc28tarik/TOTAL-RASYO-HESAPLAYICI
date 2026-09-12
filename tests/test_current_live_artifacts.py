from __future__ import annotations

from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _receipt(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_borsa_quote_unit_artifact_keeps_legal_share_count_separate():
    receipt = _receipt("data/live/current_quote_unit_v1/receipt.json")
    raw = gzip.decompress(
        (ROOT / "data/live/current_quote_unit_v1/pay_piyasasi_proseduru.pdf.gz").read_bytes()
    )
    assert receipt["contract"] == "BORSA_PAY_PRICE_PER_1_TRY_NOMINAL_V1"
    assert receipt["quoted_nominal_unit_try"] == 1
    assert receipt["legal_share_count_equated_to_nominal_try"] is False
    assert hashlib.sha256(raw).hexdigest() == receipt["source_pdf_sha256"]


def test_current_market_artifact_cutoff_covers_every_official_source_timestamp():
    receipt = _receipt("data/live/current_market_modules_v1/receipt.json")
    captured_at = datetime.fromisoformat(receipt["captured_at"])
    for source in receipt["index_source_hashes"].values():
        source_at = datetime.strptime(source["http_date"], "%a, %d %b %Y %H:%M:%S GMT")
        assert captured_at.replace(tzinfo=None) >= source_at
    assert receipt["m3_valid_count"] > 0
    assert receipt["ek4_valid_count"] > 0


def test_current_total_artifact_uses_real_m2_and_keeps_incomplete_rows_fail_closed():
    m2_receipt = _receipt("data/live/current_nonfin_valuation_v1/receipt.json")
    receipt = _receipt("data/live/current_total_scores_v1/receipt.json")
    assert m2_receipt["usable_valuation_count"] == 49
    assert m2_receipt["follow_materialized_count"] == 48
    assert m2_receipt["m2_materialized_count"] == 48
    assert m2_receipt["neutral_follow_or_m2_materialized"] is False
    assert receipt["universe_count"] == 807
    assert receipt["total_valid_count"] == receipt["ranking_count"] == 46
    assert receipt["explicit_rejection_count"] == 761
    assert receipt["total_valid_count"] + receipt["explicit_rejection_count"] == receipt["universe_count"]
    assert receipt["missing_module_counts"]["M2"] == 759
    assert receipt["neutral_fill"] is False
    assert receipt["weight_redistribution"] is False

def test_current_ek9_repair_binds_real_dated_prices_and_complete_windows():
    import pandas as pd
    from scripts.repair_current_ek9_gaps import parse_mynet_rows, verify_outputs
    output = ROOT / "data/live/current_ek9_gap_repair_v1"
    receipt = _receipt("data/live/current_ek9_gap_repair_v1/receipt.json")
    verify_outputs(output, receipt)
    assert receipt["priority_count"] == receipt["priority_ek9_valid_after"] == 44
    assert receipt["ek9_valid_before"] == 11
    assert receipt["ek9_valid_after"] == 55
    stocks = pd.read_csv(ROOT / "data/live/current_market_modules_v1/stock_prices.csv.gz")
    indices = pd.read_csv(ROOT / "data/live/current_market_modules_v1/index_prices.csv.gz")
    window = sorted(indices.loc[indices.index_code.eq("XU100"), "trade_date"].unique())[-64:]
    audit = [json.loads(line) for line in (output / "ticker_audit.jsonl").read_text().splitlines()]
    assert len(audit) == 44
    for row in audit:
        assert row["missing_dates_before"] == ["2026-09-07"]
        assert row["missing_dates_after"] == []
        ticker = row["ticker"]
        prices = stocks.loc[stocks.ticker.eq(ticker) & stocks.trade_date.isin(window)]
        assert len(prices) == 64
        assert set(prices.trade_date) == set(window)
        source = parse_mynet_rows(gzip.decompress((output / f"mynet_{ticker}.html.gz").read_bytes()), ticker)
        real_close = source.loc[source.trade_date.eq("2026-09-07"), "close"].iloc[0]
        gap = prices.loc[prices.trade_date.eq("2026-09-07")].iloc[0]
        assert gap.close == real_close
        assert pd.isna(gap.adj_close)
    scores = pd.read_csv(output / "ek9_scores.csv")
    assert len(scores) == 55
    assert scores.observations.eq(63).all()


def test_current_upper_receipt_counts_and_child_hashes_follow_current_artifacts():
    receipt = _receipt("data/live/current_total_rasyo_run_v1/receipt.json")
    assert receipt["stage_counts"]["usable_nonfin_valuation"] == 49
    assert receipt["stage_counts"]["m2"] == 48
    assert receipt["stage_counts"]["ek9"] == 55
    assert receipt["stage_counts"]["total_rasyo"] == receipt["stage_counts"]["ranking"] == 46
    assert receipt["m2_blocker"] is None
    for row in receipt["receipts"].values():
        assert hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest() == row["sha256"]
