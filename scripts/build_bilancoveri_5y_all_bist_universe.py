#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

CURRENT_URL = "https://bilancoveri.com/api/v1/sirketler.json"
SEARCH_URL = "https://bilancoveri.com/api/v1/arama.json"
HISTORICAL_BIST100 = Path("data/backtest_sources/yahoo_resolved/monthly_member_signal_price_coverage.csv")
OUT = Path("artifacts/bilancoveri_5y_plan")
BATCH_COUNT = 6


def _fetch(url: str) -> bytes:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "TOTAL-RASYO-HESAPLAYICI/5Y-source-plan"})
    with urlopen(req, timeout=30) as response:
        raw = response.read()
        status = getattr(response, "status", 200)
    if status != 200 or not raw:
        raise RuntimeError(f"source list fetch failed HTTP={status} bytes={len(raw)} url={url}")
    return raw


def _ticker(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    if not value or len(value) > 10 or not value.replace(".", "").isalnum():
        return None
    return value


def _extract_tickers(payload: object) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        for key in ("companies", "items", "results", "data"):
            if key in payload:
                found |= _extract_tickers(payload[key])
        for key in ("ticker", "kod", "code", "symbol"):
            if key in payload:
                t = _ticker(payload[key])
                if t:
                    found.add(t)
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                found |= _extract_tickers(item)
            elif isinstance(item, (list, tuple)) and item:
                t = _ticker(item[0])
                if t:
                    found.add(t)
            else:
                t = _ticker(item)
                if t:
                    found.add(t)
    return found


def _historical_seed() -> set[str]:
    tickers: set[str] = set()
    months: set[str] = set()
    rows = 0
    with HISTORICAL_BIST100.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            month = str(row["month"])
            if "2021-08" <= month <= "2026-07":
                rows += 1
                months.add(month)
                tickers.add(str(row["ticker"]).strip().upper())
    if rows != 6000 or len(months) != 60:
        raise RuntimeError(f"closed historical BIST100 seed drift: rows={rows} months={len(months)}")
    return tickers


def _balanced_batches(tickers: list[str]) -> list[list[str]]:
    n = len(tickers)
    q, r = divmod(n, BATCH_COUNT)
    batches: list[list[str]] = []
    start = 0
    for i in range(BATCH_COUNT):
        size = q + (1 if i < r else 0)
        batch = tickers[start : start + size]
        if not batch:
            raise RuntimeError("empty batch")
        batches.append(batch)
        start += size
    if start != n or sorted(t for b in batches for t in b) != tickers:
        raise RuntimeError("batch partition is not exhaustive")
    return batches


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw_current = _fetch(CURRENT_URL)
    raw_search = _fetch(SEARCH_URL)
    (OUT / "sirketler.raw.json").write_bytes(raw_current)
    (OUT / "arama.raw.json").write_bytes(raw_search)

    current = _extract_tickers(json.loads(raw_current.decode("utf-8-sig")))
    search = _extract_tickers(json.loads(raw_search.decode("utf-8-sig")))
    historical_seed = _historical_seed()

    if not 500 <= len(current) <= 700:
        raise RuntimeError(f"unexpected current listed count: {len(current)}")
    if not search:
        raise RuntimeError("search index produced zero tickers")

    universe = sorted(current | search | historical_seed)
    batches = _balanced_batches(universe)

    batch_dir = OUT / "batches"
    batch_dir.mkdir(exist_ok=True)
    for idx, batch in enumerate(batches, start=1):
        with (batch_dir / f"batch_{idx:02d}.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ticker"])
            writer.writerows([[t] for t in batch])

    plan = {
        "contract": "BILANCOVERI_ALL_BIST_5Y_SOURCE_PLAN_V1",
        "performance_window": {"start_month": "2021-08", "end_month": "2026-07", "months": 60},
        "source_download_policy": "download full available history per ticker; do not truncate source-side",
        "pit_authorized": False,
        "current_listed_count": len(current),
        "search_index_count": len(search),
        "closed_historical_bist100_seed_count": len(historical_seed),
        "source_candidate_universe_count": len(universe),
        "batch_count": BATCH_COUNT,
        "batch_sizes": [len(b) for b in batches],
        "current_only_not_search": sorted(current - search),
        "search_only_not_current": sorted(search - current),
        "historical_seed_not_in_bilancoveri_indexes": sorted(historical_seed - (current | search)),
        "raw_sources": [
            {"url": CURRENT_URL, "sha256": hashlib.sha256(raw_current).hexdigest(), "bytes": len(raw_current)},
            {"url": SEARCH_URL, "sha256": hashlib.sha256(raw_search).hexdigest(), "bytes": len(raw_search)},
        ],
        "batches": {f"batch_{i:02d}": batch for i, batch in enumerate(batches, start=1)},
    }
    (OUT / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "ALL_BIST_5Y_PLAN_OK "
        f"current={len(current)} search={len(search)} historical_seed={len(historical_seed)} "
        f"universe={len(universe)} batches={','.join(map(str, plan['batch_sizes']))}"
    )
    print("HISTORICAL_SEED_NOT_IN_INDEXES=" + ",".join(plan["historical_seed_not_in_bilancoveri_indexes"]))


if __name__ == "__main__":
    main()
