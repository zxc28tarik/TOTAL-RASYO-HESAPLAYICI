from __future__ import annotations

"""Audit and repair current Ek9 gaps with dated prices, preserving production math."""

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.historical_pit_ek9_replay import run_historical_pit_ek9_replay

MARKET = ROOT / "data/live/current_market_modules_v1"
OUTPUT = ROOT / "data/live/current_ek9_gap_repair_v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_outputs(directory: Path, receipt: dict) -> list[dict]:
    reconciled = []
    for name, digest in receipt["outputs"].items():
        path = directory / name
        actual = sha(path)
        if actual == digest:
            continue
        raw = path.read_bytes()
        # Git eol=lf converted these pre-existing Windows-generated text outputs.
        # Reconstruct only CRLF bytes, and still require the exact recorded hash.
        if (path.suffix in (".csv", ".jsonl") and b"\r" not in raw
                and hashlib.sha256(raw.replace(b"\n", b"\r\n")).hexdigest() == digest):
            reconciled.append({"path": str(path.relative_to(ROOT)),
                               "recorded_crlf_sha256": digest,
                               "repository_lf_sha256": actual,
                               "transformation": "GIT_EOL_LF_FROM_VERIFIED_CRLF"})
            continue
        raise ValueError(f"artifact hash mismatch: {path}")
    return reconciled


def missing_days(stocks: pd.DataFrame, ticker: str, window: list[str]) -> list[str]:
    return sorted(set(window) - set(stocks.loc[stocks.ticker.eq(ticker), "trade_date"]))


def merge_verified(stocks: pd.DataFrame, ticker: str, window: list[str],
                   supplement: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Require exact dates and consistent overlapping raw/adjusted price bases."""
    columns = ["ticker", "trade_date", "close", "adj_close"]
    candidate = supplement.loc[:, columns].copy()
    if not candidate.ticker.eq(ticker).all() or candidate.trade_date.duplicated().any():
        return stocks, "SUPPLEMENT_IDENTITY_OR_DUPLICATE_INVALID"
    candidate = candidate.loc[candidate.trade_date.isin(window)]
    if set(candidate.trade_date) != set(window):
        return stocks, "SUPPLEMENT_WINDOW_INCOMPLETE"
    for field in ("close", "adj_close"):
        values = pd.to_numeric(candidate[field], errors="coerce")
        if values.isna().any() or not np.isfinite(values).all() or not values.gt(0).all():
            return stocks, "SUPPLEMENT_PRICE_INVALID"
        candidate[field] = values
    old = stocks.loc[stocks.ticker.eq(ticker) & stocks.trade_date.isin(window), columns]
    overlap = old.merge(candidate, on=["ticker", "trade_date"], suffixes=("_old", "_new"))
    if overlap.empty:
        return stocks, "SUPPLEMENT_OVERLAP_UNAVAILABLE"
    for field in ("close", "adj_close"):
        previous = pd.to_numeric(overlap[field + "_old"], errors="coerce")
        if previous.isna().any() or not np.allclose(
            previous, overlap[field + "_new"], rtol=1e-8, atol=1e-6
        ):
            return stocks, "SUPPLEMENT_PRICE_BASIS_MISMATCH"
    added = candidate.loc[candidate.trade_date.isin(missing_days(stocks, ticker, window))]
    return pd.concat([stocks, added], ignore_index=True), "VERIFIED_DATED_PRICE_GAP_REPAIRED"


def run(*, capture: bool = False, market_dir: Path = MARKET,
        output_dir: Path = OUTPUT) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    market_receipt = json.loads((market_dir / "receipt.json").read_text())
    reconciliations = verify_outputs(market_dir, market_receipt)
    cutoff = date.fromisoformat(market_receipt["cutoff_date"])
    market_asof = date.fromisoformat(market_receipt["market_asof_date"])
    stocks = pd.read_csv(market_dir / "stock_prices.csv.gz")
    indices = pd.read_csv(market_dir / "index_prices.csv.gz")
    modules = pd.read_csv(market_dir / "modules.csv")
    calendar_days = sorted(indices.loc[
        indices.index_code.eq("XU100") & indices.trade_date.le(market_asof.isoformat()),
        "trade_date"
    ].unique())
    window = calendar_days[-64:]
    if len(window) != 64:
        raise ValueError("production Ek9 64-price calendar window unavailable")
    total_dir = ROOT / "data/live/current_total_scores_v1"
    total_receipt = json.loads((total_dir / "receipt.json").read_text())
    reconciliations += verify_outputs(total_dir, total_receipt)
    rejected = [json.loads(line) for line in (total_dir / "rejections.jsonl").read_text().splitlines()]
    priority = sorted(row["ticker"] for row in rejected
                      if row["missing_modules"] == ["Ek9"] and not row["good_count_missing"])
    input_hashes = {
        str(path.relative_to(ROOT)): sha(path) for path in (
            market_dir / "receipt.json", market_dir / "stock_prices.csv.gz",
            market_dir / "index_prices.csv.gz", market_dir / "modules.csv",
            total_dir / "receipt.json", total_dir / "rejections.jsonl",
        )
    }
    audit_rows = []
    captures = []
    repaired = stocks.copy()
    for ticker in priority:
        gaps = missing_days(stocks, ticker, window)
        disposition = "EXISTING_ARTIFACT_PRICE_GAP"
        if capture and gaps:
            import yfinance as yf
            captured_frame = pd.DataFrame()
            failure = None
            for attempt in range(3):
                try:
                    captured_frame = yf.Ticker(f"{ticker}.IS").history(
                        start=window[0],
                        end=(market_asof + timedelta(days=1)).isoformat(),
                        auto_adjust=False, actions=True, repair=False,
                        timeout=20,
                    )
                    if not captured_frame.empty:
                        break
                    failure = "YAHOO_EMPTY_WINDOW"
                except Exception as exc:
                    failure = type(exc).__name__
                if attempt < 2:
                    time.sleep(1 + attempt)
            captured_at = datetime.now(timezone.utc).isoformat()
            if captured_frame.empty:
                disposition = failure or "YAHOO_EMPTY_WINDOW"
                captures.append({"ticker": ticker, "captured_at": captured_at,
                                 "disposition": disposition})
            else:
                candidate = pd.DataFrame({
                    "ticker": ticker,
                    "trade_date": [pd.Timestamp(stamp).date().isoformat() for stamp in captured_frame.index],
                    "close": captured_frame["Close"].to_numpy(),
                    "adj_close": captured_frame.get("Adj Close", pd.Series(
                        np.nan, index=captured_frame.index)).to_numpy(),
                })
                source_path = output_dir / f"yahoo_{ticker}_window.csv"
                candidate.to_csv(source_path, index=False)
                repaired, disposition = merge_verified(repaired, ticker, window, candidate)
                captures.append({
                    "ticker": ticker, "symbol": f"{ticker}.IS", "captured_at": captured_at,
                    "endpoint": f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}.IS",
                    "start": window[0], "end_exclusive": (market_asof + timedelta(days=1)).isoformat(),
                    "auto_adjust": False, "repair": False, "actions": True,
                    "snapshot_sha256": sha(source_path), "snapshot_path": source_path.name,
                    "disposition": disposition,
                })
            time.sleep(0.25)
        audit_rows.append({
            "ticker": ticker, "missing_dates_before": gaps,
            "missing_dates_after": missing_days(repaired, ticker, window),
            "start_date": window[0], "end_date": window[-1],
            "required_price_observations": 64, "required_return_observations": 63,
            "benchmark_role": "XU100_CALENDAR_ONLY",
            "disposition": disposition,
        })
    analysis = datetime.now(timezone.utc)
    result = run_historical_pit_ek9_replay(
        analysis_at=analysis, asof_date=cutoff, market_asof_date=market_asof,
        universe=modules[["ticker"]],
        trading_calendar=pd.DataFrame({"trade_date": calendar_days}),
        stock_prices=repaired,
    )
    scores = result.ek9_scores.set_index("ticker").ek9
    previous = modules.set_index("ticker").ek9.dropna()
    if not np.allclose(previous, scores.reindex(previous.index), rtol=0, atol=1e-14):
        raise ValueError("existing valid Ek9 score changed")
    result.ek9_scores.to_csv(output_dir / "ek9_scores.csv", index=False)
    (output_dir / "ticker_audit.jsonl").write_text("".join(
        json.dumps(row, sort_keys=True) + "\n" for row in audit_rows), encoding="utf-8")
    (output_dir / "captures.json").write_text(json.dumps(captures, sort_keys=True, indent=2) + "\n")
    receipt = {
        "contract": "CURRENT_EK9_GAP_REPAIR_V1",
        "analysis_at": analysis.isoformat(), "cutoff_date": cutoff.isoformat(),
        "market_asof_date": market_asof.isoformat(),
        "mode": "TARGETED_FREE_YAHOO_CAPTURE" if capture else "EXISTING_ARTIFACT_AUDIT",
        "priority_count": len(priority),
        "priority_ek9_valid_before": sum(t in set(previous.index) for t in priority),
        "priority_ek9_valid_after": sum(t in set(scores.index) for t in priority),
        "ek9_valid_before": len(previous), "ek9_valid_after": len(scores),
        "window_start": window[0], "window_end": window[-1],
        "missing_day_counts": dict(Counter(d for row in audit_rows for d in row["missing_dates_before"])),
        "input_line_ending_reconciliations": reconciliations,
        "inputs": input_hashes, "production_engine": "run_historical_pit_ek9_replay",
        "neutral_fill": False, "weight_redistribution": False, "threshold_relaxation": False,
    }
    if capture and len(scores) > len(previous):
        modules["ek9"] = modules.ticker.map(scores)
        modules.to_csv(market_dir / "modules.csv", index=False)
        (market_dir / "stock_prices.csv.gz").write_bytes(gzip.compress(
            repaired.to_csv(index=False).encode(), mtime=0))
        old_rejections = [json.loads(line) for line in (market_dir / "rejections.jsonl").read_text().splitlines()]
        current_rejections = [row for row in old_rejections if row["module"] != "Ek9"]
        current_rejections += [{"ticker": row.ticker, "module": "Ek9", "reason": row.reason}
                               for row in result.rejections.itertuples()]
        (market_dir / "rejections.jsonl").write_text("".join(
            json.dumps(row, sort_keys=True) + "\n" for row in current_rejections))
        market_receipt.update({
            "captured_at": analysis.isoformat(), "ek9_valid_count": len(scores),
            "stock_price_row_count": len(repaired),
            "rejection_counts": dict(Counter(row["module"] + ":" + row["reason"] for row in current_rejections)),
            "ek9_gap_repair": {"path": "data/live/current_ek9_gap_repair_v1/receipt.json"},
        })
        market_receipt["outputs"] = {name: sha(market_dir / name)
                                     for name in market_receipt["outputs"]}
        (market_dir / "receipt.json").write_text(json.dumps(market_receipt, sort_keys=True, indent=2) + "\n")
    receipt["outputs"] = {path.name: sha(path) for path in sorted(output_dir.iterdir())
                           if path.is_file() and path.name != "receipt.json"}
    (output_dir / "receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(capture=args.capture), indent=2))
