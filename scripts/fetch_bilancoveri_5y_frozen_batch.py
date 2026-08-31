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
PLAN_ROOT = Path("data/backtest_sources/bilancoveri_5y_source_plan")
OUT_ROOT = Path("artifacts/bilancoveri_5y_frozen_batches")
EXPECTED_BATCH_SIZES = [100, 106, 106, 105, 105, 105]
EXPECTED_TOTAL = 627


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_batch(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = [str(row["ticker"]).strip().upper() for row in csv.DictReader(f)]
    if not rows or len(rows) != len(set(rows)):
        raise RuntimeError(f"batch list empty or duplicated: {path}")
    return rows


def _validate_frozen_plan() -> tuple[dict[str, object], list[list[str]]]:
    plan_path = PLAN_ROOT / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("contract") != "BILANCOVERI_ALL_BIST_5Y_FROZEN_SOURCE_PLAN_V2":
        raise RuntimeError("unexpected frozen plan contract")
    if plan.get("pit_authorized") is not False or plan.get("real_total_rasyo_scoring_authorized") is not False:
        raise RuntimeError("frozen source plan must not authorize PIT/scoring")
    if plan.get("source_candidate_universe_count") != EXPECTED_TOTAL:
        raise RuntimeError("frozen plan universe count drift")
    if plan.get("batch_sizes") != EXPECTED_BATCH_SIZES:
        raise RuntimeError("frozen plan batch sizes drift")

    expected_hashes = plan.get("batch_csv_sha256")
    if not isinstance(expected_hashes, dict):
        raise RuntimeError("missing batch hash manifest")

    batches: list[list[str]] = []
    for idx, expected_size in enumerate(EXPECTED_BATCH_SIZES, start=1):
        name = f"batch_{idx:02d}.csv"
        path = PLAN_ROOT / name
        actual_hash = _sha256(path)
        expected_hash = expected_hashes.get(name)
        if actual_hash != expected_hash:
            raise RuntimeError(f"frozen batch hash mismatch {name}: {actual_hash} != {expected_hash}")
        rows = _read_batch(path)
        if len(rows) != expected_size:
            raise RuntimeError(f"frozen batch size mismatch {name}: {len(rows)} != {expected_size}")
        batches.append(rows)

    flattened = [ticker for batch in batches for ticker in batch]
    if len(flattened) != EXPECTED_TOTAL or len(set(flattened)) != EXPECTED_TOTAL:
        raise RuntimeError("frozen six-batch plan is not exhaustive/unique")
    return plan, batches


def _fetch(ticker: str, retries: int = 2) -> tuple[str, bytes]:
    url = BASE.format(ticker=ticker.lower())
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "TOTAL-RASYO-HESAPLAYICI/5Y-frozen-rate-limited-source-acquisition",
            },
        )
        try:
            with urlopen(req, timeout=30) as response:
                raw = response.read()
                status = getattr(response, "status", 200)
                final_url = response.geturl()
            if status != 200 or not raw:
                raise RuntimeError(f"HTTP={status} bytes={len(raw)}")
            return final_url, raw
        except HTTPError as exc:
            if exc.code == 404:
                raise
            last_error = exc
            if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt >= retries:
                raise
        except URLError as exc:
            last_error = exc
            if attempt >= retries:
                raise
        time.sleep(2 ** attempt)
    assert last_error is not None
    raise last_error


def _period_sort_key(period: str) -> tuple[int, int]:
    year, p = period.split("/", 1)
    return int(year), int(p)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, required=True, choices=range(2, 7))
    parser.add_argument("--delay", type=float, default=0.75)
    args = parser.parse_args()

    plan, batches = _validate_frozen_plan()
    tickers = batches[args.batch - 1]
    batch_name = f"batch_{args.batch:02d}"
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
        "contract": "BILANCOVERI_ALL_BIST_5Y_FROZEN_BATCH_V1",
        "frozen_plan_contract": plan["contract"],
        "batch": args.batch,
        "batch_name": batch_name,
        "requested_tickers": len(tickers),
        "frozen_batch_csv_sha256": plan["batch_csv_sha256"][f"{batch_name}.csv"],
        "status_counts": dict(sorted(status_counts.items())),
        "performance_window": plan["performance_window"],
        "source_history_policy": plan["source_history_policy"],
        "pit_authorized": False,
        "real_total_rasyo_scoring_authorized": False,
        "records": records,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (out / "coverage.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "ticker", "status", "financial_group", "period_count", "first_period", "last_period",
            "raw_sha256", "raw_size_bytes", "generated_at", "source_url", "error",
        ]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            writer.writerow(row)

    print(
        f"BILANCOVERI_5Y_FROZEN_BATCH_DONE batch={args.batch} requested={len(tickers)} "
        f"statuses={json.dumps(status_counts, sort_keys=True)}"
    )


if __name__ == "__main__":
    main()
