#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://bilancoveri.com/api/v1/hisse/{ticker}.json"
PLAN_ROOT = Path("artifacts/bilancoveri_5y_plan")
OUT_ROOT = Path("artifacts/bilancoveri_5y_batches")


def _fetch(ticker: str) -> tuple[str, bytes]:
    url = BASE.format(ticker=ticker.lower())
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "TOTAL-RASYO-HESAPLAYICI/5Y-rate-limited-source-acquisition"})
    with urlopen(req, timeout=30) as response:
        raw = response.read()
        status = getattr(response, "status", 200)
        final_url = response.geturl()
    if status != 200 or not raw:
        raise RuntimeError(f"HTTP={status} bytes={len(raw)}")
    return final_url, raw


def _period_sort_key(period: str) -> tuple[int, int]:
    year, p = period.split("/", 1)
    return int(year), int(p)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, required=True, choices=range(1, 7))
    parser.add_argument("--delay", type=float, default=0.75)
    args = parser.parse_args()

    batch_name = f"batch_{args.batch:02d}"
    batch_csv = PLAN_ROOT / "batches" / f"{batch_name}.csv"
    if not batch_csv.exists():
        raise RuntimeError(f"missing frozen batch plan: {batch_csv}")

    with batch_csv.open(newline="", encoding="utf-8") as f:
        tickers = [str(r["ticker"]).strip().upper() for r in csv.DictReader(f)]
    if not tickers or len(tickers) != len(set(tickers)):
        raise RuntimeError("batch ticker list empty or duplicated")

    out = OUT_ROOT / batch_name
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, object]] = []
    status_counts: dict[str, int] = {}
    for idx, ticker in enumerate(tickers, start=1):
        record: dict[str, object] = {"ticker": ticker}
        try:
            final_url, raw = _fetch(ticker)
            payload = json.loads(raw.decode("utf-8-sig"))
            company = payload.get("company") if isinstance(payload, dict) else None
            periods = payload.get("periods") if isinstance(payload, dict) else None
            labels = payload.get("labels") if isinstance(payload, dict) else None
            if not isinstance(company, dict) or not isinstance(periods, dict) or not isinstance(labels, dict):
                raise ValueError("schema_missing_company_periods_labels")
            actual_ticker = str(company.get("ticker", "")).strip().upper()
            if actual_ticker != ticker:
                raise ValueError(f"ticker_mismatch:{actual_ticker}")
            period_keys = sorted((str(p) for p in periods), key=_period_sort_key)
            if not period_keys:
                raise ValueError("zero_periods")
            raw_path = raw_dir / f"{ticker}.json"
            raw_path.write_bytes(raw)
            record.update(
                {
                    "status": "FOUND_API",
                    "source_url": final_url,
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    "raw_size_bytes": len(raw),
                    "generated_at": payload.get("generated_at"),
                    "financial_group": str(company.get("fin_group", "")),
                    "period_count": len(period_keys),
                    "first_period": period_keys[0],
                    "last_period": period_keys[-1],
                    "has_2019_or_earlier": _period_sort_key(period_keys[0]) <= (2019, 12),
                }
            )
        except HTTPError as exc:
            record.update({"status": "NOT_IN_API" if exc.code == 404 else "HTTP_ERROR", "error": f"HTTP {exc.code}"})
        except URLError as exc:
            record.update({"status": "HTTP_ERROR", "error": str(exc.reason)})
        except Exception as exc:
            record.update({"status": "PARSING_REJECTED", "error": f"{type(exc).__name__}:{exc}"})

        status = str(record["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
        records.append(record)
        print(f"[{idx:03d}/{len(tickers):03d}] {ticker} {status}", flush=True)
        if idx != len(tickers):
            time.sleep(args.delay)

    if len(records) != len(tickers) or {str(r["ticker"]) for r in records} != set(tickers):
        raise RuntimeError("batch output is not exhaustive")

    manifest = {
        "contract": "BILANCOVERI_ALL_BIST_5Y_BATCH_V1",
        "batch": args.batch,
        "batch_name": batch_name,
        "requested_tickers": len(tickers),
        "status_counts": dict(sorted(status_counts.items())),
        "performance_window": {"start_month": "2021-08", "end_month": "2026-07", "months": 60},
        "source_history_policy": "full available company history captured; PIT filtering not authorized here",
        "pit_authorized": False,
        "real_total_rasyo_scoring_authorized": False,
        "records": records,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (out / "coverage.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["ticker", "status", "financial_group", "period_count", "first_period", "last_period", "raw_sha256", "raw_size_bytes", "generated_at", "source_url", "error"]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            writer.writerow(row)

    print(f"BILANCOVERI_5Y_BATCH_DONE batch={args.batch} requested={len(tickers)} statuses={json.dumps(status_counts, sort_keys=True)}")


if __name__ == "__main__":
    main()
