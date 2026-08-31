#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "data/backtest_sources/yahoo_resolved/monthly_member_signal_price_coverage.csv"
OUT = ROOT / "artifacts" / "bilancoveri_3y_coverage"
BASE = "https://bilancoveri.com/api/v1/hisse/{ticker}.json"
START_MONTH = "2023-08"
END_MONTH = "2026-07"
EXPECTED_MONTHS = 36
EXPECTED_CELLS = 3600
EXPECTED_UNIQUE_TICKERS = 171
REQUEST_DELAY_SECONDS = 0.45
MAX_ATTEMPTS = 3

STATUS_FOUND = "FOUND_API"
STATUS_NOT_FOUND = "NOT_IN_API"
STATUS_SCHEMA_REJECTED = "SCHEMA_REJECTED"
STATUS_TRANSPORT_REJECTED = "TRANSPORT_REJECTED"


def closed_scope() -> tuple[list[str], dict[str, list[str]]]:
    month_members: dict[str, set[str]] = {}
    ticker_months: dict[str, list[str]] = {}
    rows = 0
    with MATRIX.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            month = str(row["month"])
            if not (START_MONTH <= month <= END_MONTH):
                continue
            ticker = str(row["ticker"]).strip().upper()
            rows += 1
            month_members.setdefault(month, set()).add(ticker)
            ticker_months.setdefault(ticker, []).append(month)
    months = sorted(month_members)
    if len(months) != EXPECTED_MONTHS:
        raise RuntimeError(f"expected {EXPECTED_MONTHS} months, got {len(months)}")
    if rows != EXPECTED_CELLS:
        raise RuntimeError(f"expected {EXPECTED_CELLS} cells, got {rows}")
    bad_months = {month: len(members) for month, members in month_members.items() if len(members) != 100}
    if bad_months:
        raise RuntimeError(f"month member-count drift: {bad_months}")
    tickers = sorted(ticker_months)
    if len(tickers) != EXPECTED_UNIQUE_TICKERS:
        raise RuntimeError(f"expected {EXPECTED_UNIQUE_TICKERS} unique tickers, got {len(tickers)}")
    return tickers, ticker_months


def fetch_one(ticker: str) -> tuple[int | None, bytes | None, str | None]:
    url = BASE.format(ticker=ticker.lower())
    last_error: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "TOTAL-RASYO-HESAPLAYICI/BilancoVeri-research-coverage-probe",
            },
        )
        try:
            with urlopen(req, timeout=30) as response:
                raw = response.read()
                status = int(getattr(response, "status", 200))
            return status, raw, None
        except HTTPError as exc:
            if exc.code == 404:
                return 404, None, "HTTP 404"
            last_error = f"HTTP {exc.code}"
        except (URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < MAX_ATTEMPTS:
            time.sleep(1.5 * attempt)
    return None, None, last_error or "unknown transport error"


def period_sort_key(key: str) -> tuple[int, int]:
    try:
        year, period = key.split("/", 1)
        return int(year), int(period)
    except Exception:
        return (9999, 99)


def main() -> None:
    tickers, ticker_months = closed_scope()
    OUT.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []

    for index, ticker in enumerate(tickers, start=1):
        status_code, raw, transport_error = fetch_one(ticker)
        record: dict[str, object] = {
            "ticker": ticker,
            "membership_first_month": min(ticker_months[ticker]),
            "membership_last_month": max(ticker_months[ticker]),
            "membership_month_count": len(ticker_months[ticker]),
            "source_url": BASE.format(ticker=ticker.lower()),
        }
        if status_code == 404:
            record.update({"status": STATUS_NOT_FOUND, "reason": "BilancoVeri ticker endpoint returned 404"})
        elif raw is None:
            record.update({"status": STATUS_TRANSPORT_REJECTED, "reason": transport_error})
        else:
            try:
                payload = json.loads(raw.decode("utf-8-sig"))
                company = payload.get("company")
                periods = payload.get("periods")
                labels = payload.get("labels")
                if not isinstance(company, dict) or not isinstance(periods, dict) or not isinstance(labels, dict):
                    raise ValueError("company/periods/labels schema missing")
                api_ticker = str(company.get("ticker", "")).upper()
                if api_ticker != ticker:
                    raise ValueError(f"ticker mismatch {api_ticker!r} != {ticker!r}")
                ordered_periods = sorted(periods, key=period_sort_key)
                if not ordered_periods:
                    raise ValueError("empty periods")
                raw_path = OUT / f"{ticker}.raw.json"
                raw_path.write_bytes(raw)
                record.update(
                    {
                        "status": STATUS_FOUND,
                        "financial_group": company.get("fin_group"),
                        "company_name": company.get("name"),
                        "generated_at": payload.get("generated_at"),
                        "period_count": len(periods),
                        "earliest_period": ordered_periods[0],
                        "latest_period": ordered_periods[-1],
                        "label_count": len(labels),
                        "raw_size_bytes": len(raw),
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
            except Exception as exc:
                record.update({"status": STATUS_SCHEMA_REJECTED, "reason": str(exc)})
        records.append(record)
        print(f"[{index:03d}/{len(tickers)}] {ticker} {record['status']}")
        if index < len(tickers):
            time.sleep(REQUEST_DELAY_SECONDS)

    counts: dict[str, int] = {}
    for row in records:
        counts[str(row["status"])] = counts.get(str(row["status"]), 0) + 1
    financial_groups: dict[str, int] = {}
    for row in records:
        if row.get("status") != STATUS_FOUND:
            continue
        group = str(row.get("financial_group") or "UNKNOWN")
        financial_groups[group] = financial_groups.get(group, 0) + 1

    summary = {
        "contract": "BILANCOVERI_3Y_UNIVERSE_COVERAGE_PROBE_V1",
        "source": "BilancoVeri open keyless JSON API",
        "api_docs_url": "https://bilancoveri.com/api/",
        "legal_url": "https://bilancoveri.com/yasal-uyari/",
        "performance_window": {"start_month": START_MONTH, "end_month": END_MONTH, "months": EXPECTED_MONTHS},
        "closed_month_ticker_cells": EXPECTED_CELLS,
        "unique_historical_tickers": len(tickers),
        "request_delay_seconds": REQUEST_DELAY_SECONDS,
        "status_counts": counts,
        "financial_group_counts": financial_groups,
        "pit_authorized": False,
        "note": "Coverage only. BilancoVeri exposes one current representation per period and does not by itself prove publication-version timing; PIT revision ambiguity remains a separate gate.",
        "records": records,
    }
    (OUT / "coverage.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("BILANCOVERI_3Y_COVERAGE_DONE " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("FINANCIAL_GROUPS " + " ".join(f"{k}={v}" for k, v in sorted(financial_groups.items())))


if __name__ == "__main__":
    main()
