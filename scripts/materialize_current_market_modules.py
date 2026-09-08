from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import gzip
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
import yfinance as yf
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.historical_pit_ek4_replay import run_historical_pit_ek4_replay
from src.analytics.historical_pit_ek9_replay import run_historical_pit_ek9_replay
from src.analytics.historical_pit_m3_replay import run_historical_pit_m3_replay

CONTRACT = "CURRENT_MARKET_M3_EK4_EK9_V1"
OUTPUT = ROOT / "data/live/current_market_modules_v1"
ROUTES = ROOT / "data/backtest_sources/m3_source_package/sector_routes.csv.gz"
INDEX_SYMBOLS = {"XU100": "XU100.IS", "XUSIN": "XUSIN.IS", "XUHIZ": "XUHIZ.IS", "XUTEK": "XUTEK.IS"}
NONFIN_INDICES = frozenset({"XUSIN", "XUHIZ", "XUTEK"})


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def materialize(*, cutoff: date, output_dir: Path = OUTPUT, routes_path: Path = ROUTES) -> dict:
    routes = pd.read_csv(routes_path, dtype=str)
    routes["valid_from"] = pd.to_datetime(routes.valid_from)
    routes["valid_to"] = pd.to_datetime(routes.valid_to, errors="coerce")
    day = pd.Timestamp(cutoff)
    active = routes.loc[
        routes.sector_index_code.isin(NONFIN_INDICES) & routes.valid_from.le(day)
        & (routes.valid_to.isna() | routes.valid_to.gt(day)),
        ["ticker", "sector_index_code"],
    ].drop_duplicates().sort_values("ticker")
    if active.duplicated("ticker").any():
        raise ValueError("current market route ambiguous")
    tickers = active.ticker.tolist()
    symbols = [f"{ticker}.IS" for ticker in tickers]
    frame = yf.download(
        symbols, period="2y", auto_adjust=False, actions=False, repair=False,
        progress=False, threads=True, group_by="ticker",
    )
    stock_rows, index_rows = [], []
    for ticker in tickers:
        symbol = f"{ticker}.IS"
        if not isinstance(frame.columns, pd.MultiIndex) or symbol not in frame.columns.get_level_values(0):
            continue
        sub = frame[symbol]
        for stamp, row in sub.iterrows():
            trade_day = pd.Timestamp(stamp).date()
            if trade_day > cutoff or pd.isna(row.get("Close")):
                continue
            stock_rows.append({
                "ticker": ticker, "trade_date": trade_day,
                "close": float(row["Close"]),
                "adj_close": None if pd.isna(row.get("Adj Close")) else float(row["Adj Close"]),
            })
    # Same-day chunked capture is authoritative for the last raw/adjusted close;
    # supplementing avoids a bulk-Yahoo partial-day omission.
    current_prices = pd.read_csv(ROOT / "data/live/current_raw_close_v1/raw_close.csv.gz")
    current_prices = current_prices.loc[
        current_prices.ticker.isin(tickers) & current_prices.trade_date.eq(cutoff.isoformat())
    ]
    stocks = pd.DataFrame(stock_rows, columns=("ticker", "trade_date", "close", "adj_close"))
    stocks = stocks.loc[~(
        stocks.ticker.isin(set(current_prices.ticker)) & stocks.trade_date.eq(cutoff)
    )]
    stocks = pd.concat([stocks, pd.DataFrame({
        "ticker": current_prices.ticker,
        "trade_date": [cutoff] * len(current_prices),
        "close": current_prices.raw_close,
        "adj_close": current_prices.adjusted_close_diagnostic,
    })], ignore_index=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    index_source_hashes = {}
    for index_code in INDEX_SYMBOLS:
        url = f"https://www.borsaistanbul.com/graphic.php?veriTuru=endeks-graphic&indexCode={index_code}"
        response = requests.get(url, timeout=60, headers={"User-Agent": "TOTAL-RASYO current market/1"})
        response.raise_for_status()
        raw = response.content
        payload = response.json()
        if payload.get("status") != "success" or not isinstance(payload.get("data"), list):
            raise ValueError(f"official Borsa index response invalid: {index_code}")
        source_path = output_dir / f"borsa_{index_code}.json.gz"
        source_path.write_bytes(gzip.compress(raw, mtime=0))
        index_source_hashes[index_code] = {
            "url": url, "http_date": response.headers.get("Date"),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "gzip_sha256": _sha(source_path),
        }
        for row in payload["data"]:
            trade_day = date.fromisoformat(str(row["hisTs"])[:10])
            if trade_day <= cutoff:
                index_rows.append({
                    "index_code": index_code, "trade_date": trade_day, "close": float(row["clval"]),
                })
    indices = pd.DataFrame(index_rows, columns=("index_code", "trade_date", "close"))
    # The analysis cutoff is after every network response used by this run.
    source_dates = [
        parsedate_to_datetime(row["http_date"]).astimezone(timezone.utc)
        for row in index_source_hashes.values() if row.get("http_date")
    ]
    analysis = max([datetime.now(timezone.utc), *source_dates])
    latest = indices.groupby("index_code").trade_date.max().to_dict()
    missing_indices = sorted(set(INDEX_SYMBOLS) - set(latest))
    if missing_indices:
        raise ValueError(f"current Yahoo index series missing: {missing_indices}")
    market_asof = min(latest.values())
    calendar = pd.DataFrame({
        "trade_date": sorted(indices.loc[indices.index_code.eq("XU100"), "trade_date"].unique())
    })
    stocks = stocks.loc[stocks.trade_date.isin(set(calendar.trade_date))].copy()
    indices = indices.loc[indices.trade_date.isin(set(calendar.trade_date))].copy()
    m3 = run_historical_pit_m3_replay(
        analysis_at=analysis, asof_date=cutoff, market_asof_date=market_asof,
        universe=active, trading_calendar=calendar, stock_prices=stocks, index_prices=indices,
    )
    ek4 = run_historical_pit_ek4_replay(
        analysis_at=analysis, asof_date=cutoff, market_asof_date=market_asof,
        universe=active, trading_calendar=calendar, stock_prices=stocks, index_prices=indices,
    )
    ek9 = run_historical_pit_ek9_replay(
        analysis_at=analysis, asof_date=cutoff, market_asof_date=market_asof,
        universe=active, trading_calendar=calendar, stock_prices=stocks,
    )
    modules = active[["ticker"]].copy()
    for result, key, column in ((m3, "m3_scores", "m3"), (ek4, "ek4_scores", "ek4"), (ek9, "ek9_scores", "ek9")):
        modules = modules.merge(getattr(result, key)[["ticker", column]], on="ticker", how="left")
    rejection_rows = []
    for result, name in ((m3, "M3"), (ek4, "Ek4"), (ek9, "Ek9")):
        rejection_rows.extend({
            "ticker": row.ticker, "module": name, "reason": row.reason,
        } for row in result.rejections.itertuples())
    stocks_path, indices_path = output_dir / "stock_prices.csv.gz", output_dir / "index_prices.csv.gz"
    modules_path, rejection_path = output_dir / "modules.csv", output_dir / "rejections.jsonl"
    stocks.to_csv(stocks_path, index=False, compression="gzip")
    indices.to_csv(indices_path, index=False, compression="gzip")
    modules.to_csv(modules_path, index=False)
    rejection_path.write_text("".join(
        json.dumps(row, sort_keys=True) + "\n" for row in rejection_rows
    ), encoding="utf-8")
    receipt = {
        "contract": CONTRACT, "captured_at": analysis.isoformat(), "cutoff_date": cutoff.isoformat(),
        "market_asof_date": market_asof.isoformat(), "universe_count": len(tickers),
        "stock_price_row_count": len(stocks), "index_price_row_count": len(indices),
        "m3_valid_count": int(modules.m3.notna().sum()),
        "ek4_valid_count": int(modules.ek4.notna().sum()),
        "ek9_valid_count": int(modules.ek9.notna().sum()),
        "rejection_counts": dict(Counter(row["module"] + ":" + row["reason"] for row in rejection_rows)),
        "stock_source": "YAHOO_UNADJUSTED_AND_ADJUSTED_DAILY_CAPTURE_CURRENT_V1",
        "index_source": "OFFICIAL_BORSA_GRAPHIC_CURRENT_V1",
        "index_source_hashes": index_source_hashes,
        "production_engines": ["run_historical_pit_m3_replay", "run_historical_pit_ek4_replay", "run_historical_pit_ek9_replay"],
        "outputs": {p.name: _sha(p) for p in (stocks_path, indices_path, modules_path, rejection_path)},
    }
    (output_dir / "receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    print(json.dumps(materialize(cutoff=args.cutoff), indent=2))
