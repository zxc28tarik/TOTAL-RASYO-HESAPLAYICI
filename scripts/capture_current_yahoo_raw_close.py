from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "CURRENT_YAHOO_RAW_CLOSE_V1"
DEFAULT_UNIVERSE = ROOT / "data/live/current_total_rasyo_v1/universe.csv"
DEFAULT_OUTPUT = ROOT / "data/live/current_raw_close_v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_latest(frame: pd.DataFrame, ticker: str, *, cutoff: date) -> dict | None:
    symbol = f"{ticker}.IS"
    if frame.empty or not isinstance(frame.columns, pd.MultiIndex) or symbol not in frame.columns.get_level_values(0):
        return None
    rows = frame[symbol].copy()
    rows = rows.loc[pd.to_datetime(rows.index).date <= cutoff]
    rows = rows.loc[pd.to_numeric(rows.get("Close"), errors="coerce").notna()]
    if rows.empty:
        return None
    trade_date = pd.Timestamp(rows.index[-1]).date()
    row = rows.iloc[-1]
    close = float(row["Close"])
    adjusted = float(row["Adj Close"]) if pd.notna(row.get("Adj Close")) else close
    if not (close > 0 and close < float("inf")):
        return None
    return {
        "ticker": ticker, "yahoo_symbol": symbol, "trade_date": trade_date.isoformat(),
        "raw_close": close, "adjusted_close_diagnostic": adjusted,
        "price_basis": "UNADJUSTED_MARKET_CLOSE_V1", "auto_adjust": False,
        "close_field": "Close", "adjusted_close_used_for_market_cap": False,
    }


def capture(*, universe_path: Path, output_dir: Path, cutoff: date, chunk_size: int = 100) -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(universe_path, dtype=str)
    tickers = sorted(set(universe["ticker"].astype(str).str.strip().str.upper()))
    rows: list[dict] = []
    failures: list[dict] = []
    for offset in range(0, len(tickers), chunk_size):
        batch = tickers[offset:offset + chunk_size]
        symbols = [f"{ticker}.IS" for ticker in batch]
        try:
            frame = yf.download(
                symbols, period="10d", auto_adjust=False, actions=True, repair=False,
                progress=False, threads=True, group_by="ticker",
            )
        except Exception as exc:
            failures.extend({"ticker": ticker, "reason": type(exc).__name__} for ticker in batch)
            continue
        for ticker in batch:
            result = extract_latest(frame, ticker, cutoff=cutoff)
            if result is None:
                failures.append({"ticker": ticker, "reason": "RAW_CLOSE_NOT_RETURNED"})
            else:
                rows.append(result)
        print(f"prices {min(offset + len(batch), len(tickers))}/{len(tickers)} captured={len(rows)}", flush=True)

    prices_path = output_dir / "raw_close.csv.gz"
    pd.DataFrame(rows).sort_values("ticker").to_csv(prices_path, index=False, compression="gzip")
    rejection_path = output_dir / "rejections.json"
    rejection_path.write_text(json.dumps(
        sorted(failures, key=lambda row: row["ticker"]), ensure_ascii=False,
        sort_keys=True, indent=2,
    ) + "\n", encoding="utf-8")
    receipt = {
        "contract": CONTRACT,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "cutoff_date": cutoff.isoformat(),
        "universe_count": len(tickers),
        "raw_close_count": len(rows),
        "rejection_count": len(failures),
        "auto_adjust": False,
        "market_cap_price_field": "Close",
        "adjusted_close_diagnostic_only": True,
        "outputs": {
            prices_path.name: _sha(prices_path),
            rejection_path.name: _sha(rejection_path),
        },
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cutoff", type=date.fromisoformat, default=date.today())
    parser.add_argument("--chunk-size", type=int, default=100)
    args = parser.parse_args()
    print(json.dumps(capture(
        universe_path=args.universe, output_dir=args.output_dir,
        cutoff=args.cutoff, chunk_size=args.chunk_size,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

