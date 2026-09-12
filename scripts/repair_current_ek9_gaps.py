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
        if (path.suffix in (".csv", ".jsonl", ".json") and b"\r" not in raw
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
        required = values if field == "close" else values.dropna()
        if (field == "close" and values.isna().any()) or not np.isfinite(required).all() or not required.gt(0).all():
            return stocks, "SUPPLEMENT_PRICE_INVALID"
        candidate[field] = values
    for gap in candidate.loc[candidate.adj_close.isna(), "trade_date"]:
        if gap not in missing_days(stocks, ticker, window):
            return stocks, "SUPPLEMENT_PRICE_INVALID"
        before = candidate.loc[candidate.trade_date.lt(gap)].sort_values("trade_date").tail(1)
        after = candidate.loc[candidate.trade_date.gt(gap)].sort_values("trade_date").head(1)
        adjacent = pd.concat([before, after])
        if len(adjacent) != 2 or adjacent.adj_close.isna().any() or not np.allclose(
            adjacent.close, adjacent.adj_close, rtol=1e-8, atol=1e-6
        ):
            return stocks, "SUPPLEMENT_PRICE_INVALID"
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


MONTHS = {
    "Ocak": 1, "Şubat": 2, "Mart": 3, "Nisan": 4, "Mayıs": 5, "Haziran": 6,
    "Temmuz": 7, "Ağustos": 8, "Eylül": 9, "Ekim": 10, "Kasım": 11, "Aralık": 12,
}


def parse_mynet_rows(raw: bytes, ticker: str) -> pd.DataFrame:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, "html.parser")
    title = soup.title.get_text() if soup.title else ""
    if ticker not in title.upper() or "Tarihsel" not in title:
        raise ValueError("MYNET_TICKER_OR_HISTORICAL_PAGE_IDENTITY_INVALID")
    tables = [table for table in soup.find_all("table")
              if "Son Fiyat" in table.get_text(" ", strip=True)
              and "Tarih" in table.get_text(" ", strip=True)]
    if len(tables) != 1:
        raise ValueError("MYNET_HISTORICAL_TABLE_AMBIGUOUS")
    rows = []
    for row in tables[0].find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 5:
            continue
        text = cells[0].get_text(" ", strip=True).split()
        if len(text) != 3 or text[1] not in MONTHS:
            continue
        trade_date = date(int(text[2]), MONTHS[text[1]], int(text[0])).isoformat()
        close = float(cells[1].get_text(strip=True).replace(".", "").replace(",", "."))
        if not np.isfinite(close) or close <= 0:
            raise ValueError("MYNET_CLOSE_INVALID")
        rows.append({"ticker": ticker, "trade_date": trade_date, "close": close})
    result = pd.DataFrame(rows, columns=["ticker", "trade_date", "close"])
    if result.empty or result.trade_date.duplicated().any():
        raise ValueError("MYNET_DATED_PRICE_ROWS_INVALID")
    return result


def complete_with_mynet(candidate: pd.DataFrame, mynet: pd.DataFrame,
                        window: list[str]) -> tuple[pd.DataFrame, str]:
    gaps = sorted(set(window) - set(candidate.trade_date))
    overlap = candidate.merge(mynet, on=["ticker", "trade_date"], suffixes=("_yahoo", "_mynet"))
    if len(overlap) < 2:
        return candidate, "MYNET_OVERLAP_UNAVAILABLE"
    if not np.allclose(overlap.close_yahoo.round(2), overlap.close_mynet, rtol=0, atol=1e-9):
        return candidate, "MYNET_RAW_CLOSE_BASIS_MISMATCH"
    additions = []
    for gap in gaps:
        real_price = mynet.loc[mynet.trade_date.eq(gap)]
        before = candidate.loc[candidate.trade_date.lt(gap)].sort_values("trade_date").tail(1)
        after = candidate.loc[candidate.trade_date.gt(gap)].sort_values("trade_date").head(1)
        if len(real_price) != 1 or before.empty or after.empty:
            return candidate, "MYNET_MISSING_DATE_OR_BRACKETING_UNAVAILABLE"
        # Existing production COALESCE(adj_close, close) is used for a real raw
        # close only when bracketing Yahoo observations share the raw price basis.
        adjacent = pd.concat([before, after])
        if adjacent.adj_close.isna().any() or not np.allclose(
            adjacent.close, adjacent.adj_close, rtol=1e-8, atol=1e-6
        ):
            return candidate, "MYNET_RAW_FALLBACK_ADJUSTED_BASIS_UNSAFE"
        additions.append({
            "ticker": real_price.iloc[0].ticker, "trade_date": gap,
            "close": float(real_price.iloc[0].close), "adj_close": np.nan,
        })
    return pd.concat([candidate, pd.DataFrame(additions)], ignore_index=True), "MYNET_REAL_RAW_CLOSE_COALESCE"


def fetch_mynet(ticker: str, output_dir: Path, session, links: dict) -> tuple[pd.DataFrame, dict]:
    url = links.get(ticker)
    if not url:
        raise ValueError("MYNET_TICKER_LINK_NOT_RESOLVED")
    response = session.get(url + "tarihselveriler/", timeout=30)
    response.raise_for_status()
    raw = response.content
    path = output_dir / f"mynet_{ticker}.html.gz"
    path.write_bytes(gzip.compress(raw, mtime=0))
    return parse_mynet_rows(raw, ticker), {
        "provider": "MYNET_FORINVEST_HISTORICAL_REAL_RAW_CLOSE",
        "url": response.url, "http_date": response.headers.get("Date"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "snapshot_path": path.name, "snapshot_sha256": sha(path),
    }


def mynet_links(output_dir: Path):
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin, urlparse
    import re
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 TOTAL-RASYO current-Ek9/1"})
    response = session.get("https://finans.mynet.com/borsa/hisseler/", timeout=30)
    response.raise_for_status()
    (output_dir / "mynet_ticker_directory.html.gz").write_bytes(gzip.compress(response.content, mtime=0))
    links = {}
    for link in BeautifulSoup(response.content, "html.parser").find_all("a", href=True):
        url = urljoin(response.url, link["href"])
        parsed = urlparse(url)
        match = re.fullmatch(r"/borsa/hisseler/([a-zA-Z0-9]+)-[^/]+/", parsed.path)
        if parsed.hostname == "finans.mynet.com" and match:
            ticker = match.group(1).upper()
            if ticker in links and links[ticker] != url:
                raise ValueError(f"MYNET_TICKER_LINK_AMBIGUOUS:{ticker}")
            links[ticker] = url
    return session, links


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
    mynet_session, links = None, {}
    if capture:
        try:
            mynet_session, links = mynet_links(output_dir)
        except Exception as exc:
            captures.append({"provider": "MYNET_DIRECTORY", "disposition": str(exc)})
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
                if set(candidate.trade_date) != set(window) and mynet_session is not None:
                    try:
                        mynet, evidence = fetch_mynet(ticker, output_dir, mynet_session, links)
                        candidate, evidence["disposition"] = complete_with_mynet(candidate, mynet, window)
                        captures.append({"ticker": ticker, **evidence})
                    except Exception as exc:
                        captures.append({"ticker": ticker, "provider": "MYNET", "disposition": str(exc)})
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
        "mode": "TARGETED_FREE_YAHOO_AND_MYNET_CAPTURE" if capture else "EXISTING_ARTIFACT_AUDIT",
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


def combine_current(*, repair_dir: Path = OUTPUT) -> dict:
    """Combine production Total and refresh cached current module summaries."""
    from scripts.materialize_current_total_rasyo import materialize

    live = ROOT / "data/live"
    core_dir = live / "current_core_modules_v1"
    valuation_dir = live / "current_nonfin_valuation_v1"
    valuation = json.loads((valuation_dir / "receipt.json").read_text())
    core_receipt = json.loads((core_dir / "receipt.json").read_text())
    verify_outputs(valuation_dir, valuation)
    verify_outputs(core_dir, core_receipt)
    total = materialize()
    market = pd.read_csv(MARKET / "modules.csv")
    core = [json.loads(line) for line in (core_dir / "modules.jsonl").read_text().splitlines()]
    m2 = [json.loads(line) for line in (valuation_dir / "m2.jsonl").read_text().splitlines()]
    sets = {
        "M2": {row["ticker"] for row in m2},
        "M1": {row["ticker"] for row in core if row.get("m1") is not None},
        "Ek1": {row["ticker"] for row in core if row.get("ek1") is not None},
        **{key: set(market.loc[market[column].notna(), "ticker"])
           for key, column in (("M3", "m3"), ("Ek4", "ek4"), ("Ek9", "ek9"))},
    }
    readiness_dir = live / "current_total_rasyo_v1"
    readiness = json.loads((readiness_dir / "receipt.json").read_text())
    verify_outputs(readiness_dir, readiness)
    meta_path = readiness_dir / "universe.csv.meta.json"
    metadata = json.loads(meta_path.read_text())
    universe_digest = sha(readiness_dir / "universe.csv")
    if metadata["csv_sha256"] != universe_digest:
        raw = (readiness_dir / "universe.csv").read_bytes()
        if hashlib.sha256(raw.replace(b"\n", b"\r\n")).hexdigest() != metadata["csv_sha256"]:
            raise ValueError("cached current universe hash mismatch")
        metadata["original_capture_csv_sha256"] = metadata["csv_sha256"]
        metadata["csv_sha256"] = universe_digest
        metadata["line_ending_reconciliation"] = "VERIFIED_ORIGINAL_CRLF_TO_REPOSITORY_LF"
        meta_path.write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n")
    rejections_path = readiness_dir / "rejections.jsonl"
    rejections = [json.loads(line) for line in rejections_path.read_text().splitlines()]
    universe = pd.read_csv(readiness_dir / "universe.csv")
    module_counts = {ticker: sum(ticker in members for members in sets.values())
                     for ticker in set(universe.ticker)}
    totals = {json.loads(line)["ticker"] for line in
              (live / "current_total_scores_v1/totals.jsonl").read_text().splitlines()}
    module_reasons = {key: f"CURRENT_{key.upper()}_NOT_MATERIALIZED" for key in sets}
    for row in rejections:
        row["reasons"] = [reason for reason in row["reasons"]
                          if reason not in module_reasons.values()]
        row["reasons"].extend(reason for key, reason in module_reasons.items()
                              if row["ticker"] not in sets[key])
        row["total_materialized"] = row["ticker"] in totals
    rejections_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n"
                                       for row in rejections), encoding="utf-8")
    readiness.update({
        "module_coverage": {key: len(members) for key, members in sets.items()},
        "at_least_4_modules_count": sum(count >= 4 for count in module_counts.values()),
        "at_least_5_modules_count": sum(count >= 5 for count in module_counts.values()),
        "total_rasyo_count": total["total_valid_count"], "ranking_count": total["ranking_count"],
        "top_readiness_rejection_reasons": dict(Counter(
            reason for row in rejections for reason in row["reasons"]).most_common(20)),
        "summary_refreshed_at": datetime.now(timezone.utc).isoformat(),
        "summary_refresh_scope": "CURRENT_MODULE_AND_TOTAL_ARTIFACTS_NON_MODULE_EVIDENCE_PRESERVED",
        "current_market_modules_artifact": {
            "path": "data/live/current_market_modules_v1/modules.csv",
            "sha256": sha(MARKET / "modules.csv")},
        "current_total_scores_receipt": {
            "path": "data/live/current_total_scores_v1/receipt.json",
            "sha256": sha(live / "current_total_scores_v1/receipt.json")},
    })
    readiness["outputs"] = {name: sha(readiness_dir / name) for name in readiness["outputs"]}
    (readiness_dir / "receipt.json").write_text(json.dumps(readiness, sort_keys=True, indent=2) + "\n")
    run_path = live / "current_total_rasyo_run_v1/receipt.json"
    upper = json.loads(run_path.read_text())
    upper["completed_at"] = datetime.now(timezone.utc).isoformat()
    upper["mode"] = "CURRENT_EK9_TARGETED_REPAIR_WITH_REUSED_CURRENT_ARTIFACTS"
    upper["m2_blocker"] = valuation["m2_blocker"]
    upper["stage_counts"].update({
        "m2": valuation["m2_materialized_count"],
        "usable_nonfin_valuation": valuation["usable_valuation_count"],
        "nonfin_valuation": valuation["production_valuation_count"],
        "ek9": len(sets["Ek9"]), "total_rasyo": total["total_valid_count"],
        "ranking": total["ranking_count"],
        "at_least_4_modules": readiness["at_least_4_modules_count"],
        "at_least_5_modules": readiness["at_least_5_modules_count"],
    })
    for row in upper["receipts"].values():
        row["sha256"] = sha(ROOT / row["path"])
    upper["receipts"]["ek9_gap_repair"] = {
        "path": "data/live/current_ek9_gap_repair_v1/receipt.json",
        "sha256": sha(repair_dir / "receipt.json"),
    }
    run_path.write_text(json.dumps(upper, sort_keys=True, indent=2) + "\n")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--combine-current", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(capture=args.capture), indent=2))
    if args.combine_current:
        print(json.dumps(combine_current(), indent=2))
