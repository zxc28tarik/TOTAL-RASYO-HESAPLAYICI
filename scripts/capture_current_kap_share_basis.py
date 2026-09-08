from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.capture_kap_share_class_history import (
    HISTORY_URL, MEMBERS_URL, parse_bist_members, validate_history_row,
)


CONTRACT = "CURRENT_KAP_EXPLICIT_SHARE_BASIS_V1"
DEFAULT_UNIVERSE = ROOT / "data/live/current_total_rasyo_v1/universe.csv"
DEFAULT_OUTPUT = ROOT / "data/live/current_share_basis_v1"


def _encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def capture(*, universe_path: Path, output_dir: Path, workers: int = 8,
            no_fetch: bool = False) -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(universe_path, dtype=str)
    tickers = sorted(set(universe["ticker"].astype(str).str.strip().str.upper()))
    session = requests.Session()
    headers = {"Accept-Language": "tr", "User-Agent": "TOTAL-RASYO current share sync/1"}
    roster_path = output_dir / "bist_member_roster.html.gz"
    if roster_path.exists():
        roster_bytes = gzip.decompress(roster_path.read_bytes())
    else:
        roster = session.get(MEMBERS_URL, headers=headers, timeout=60)
        roster.raise_for_status()
        roster_bytes = roster.content
    member_map = parse_bist_members(roster_bytes)
    by_oid: dict[str, list[str]] = defaultdict(list)
    for ticker in tickers:
        oid = member_map.get(ticker)
        if oid:
            by_oid[oid].append(ticker)

    cache_path = output_dir / "issuer_responses.jsonl.gz"
    cached: dict[str, dict] = {}
    if cache_path.exists():
        cached = {
            row["mkk_member_oid"]: row
            for row in (json.loads(line) for line in gzip.decompress(cache_path.read_bytes()).splitlines())
        }

    def fetch(oid: str) -> tuple[str, dict | None, str | None]:
        if oid in cached:
            return oid, cached[oid], None
        url = HISTORY_URL.format(mkk_member_oid=oid)
        for attempt in range(2):
            try:
                response = requests.get(url, headers=headers, timeout=20)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("NOT_ARRAY")
                return oid, {
                    "mkk_member_oid": oid, "url": url,
                    "http_date": response.headers.get("Date"),
                    "response_sha256": _sha(response.content), "response": payload,
                }, None
            except (requests.RequestException, ValueError) as exc:
                if attempt < 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return oid, None, f"{type(exc).__name__}:{exc}"
        raise AssertionError("retry loop exhausted")

    pending = [] if no_fetch else sorted(oid for oid in by_oid if oid not in cached)
    errors: dict[str, str] = {}
    def checkpoint() -> None:
        raw_rows = [cached[oid] for oid in sorted(cached) if oid in by_oid]
        cache_path.write_bytes(gzip.compress(b"".join(_encoded(row) for row in raw_rows), mtime=0))

    attempted: set[str] = set()
    stopped_for_rate_limit = False
    for offset in range(0, len(pending), 50):
        batch = pending[offset:offset + 50]
        batch_success = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for oid, row, error in pool.map(fetch, batch):
                attempted.add(oid)
                if row is None:
                    errors[oid] = error or "UNKNOWN_ERROR"
                else:
                    cached[oid] = row
                    batch_success += 1
        checkpoint()
        print(
            f"issuers {min(offset + len(batch), len(pending))}/{len(pending)} "
            f"captured={sum(oid in cached for oid in by_oid)} failed={len(errors)}",
            flush=True,
        )
        if batch_success == 0 and batch and all("429" in errors.get(oid, "") for oid in batch):
            stopped_for_rate_limit = True
            print("global KAP rate limit detected; remaining issuers left unattempted", flush=True)
            break

    checkpoint()
    roster_path.write_bytes(gzip.compress(roster_bytes, mtime=0))

    observations: list[dict] = []
    for ticker in tickers:
        oid = member_map.get(ticker)
        issuer_tickers = sorted(by_oid.get(oid, [])) if oid else []
        row = cached.get(oid) if oid else None
        base = {
            "ticker": ticker, "mkk_member_oid": oid,
            "issuer_tickers": issuer_tickers, "multi_ticker_issuer": len(issuer_tickers) != 1,
            "snapshot_captured_at": datetime.now(timezone.utc).isoformat(),
            "usable_for_single_ticker_market_cap": False,
        }
        if oid is None:
            observations.append({**base, "status": "MKK_MEMBER_OID_MISSING"})
            continue
        if row is None:
            if oid not in attempted:
                status = "NOT_ATTEMPTED_NO_NETWORK" if no_fetch else "NOT_ATTEMPTED_AFTER_GLOBAL_RATE_LIMIT"
                observations.append({**base, "status": status})
            else:
                observations.append({**base, "status": "HISTORY_FETCH_FAILED", "reason": errors.get(oid)})
            continue
        validated = [validate_history_row(ticker, item) for item in row["response"]]
        usable = [item for item in validated if item["usable"]]
        usable.sort(key=lambda item: datetime.strptime(item["published_at_local"], "%d/%m/%Y %H:%M:%S"))
        if not usable:
            observations.append({**base, "status": "NO_USABLE_EXPLICIT_CLASS_NOMINAL_STATE"})
            continue
        latest = usable[-1]
        if len(issuer_tickers) != 1:
            observations.append({
                **base, "status": "MULTI_TICKER_ISSUER_MARKET_CAP_AMBIGUOUS",
                "latest_explicit_state": latest,
            })
            continue
        observations.append({
            **base, "status": "CURRENT_EXPLICIT_CLASS_NOMINALS_RECONCILED",
            "usable_for_single_ticker_market_cap": True,
            "latest_explicit_state": latest,
        })

    observation_path = output_dir / "ticker_share_basis.jsonl.gz"
    observation_path.write_bytes(gzip.compress(
        b"".join(_encoded(row) for row in observations), mtime=0,
    ))
    status_counts = pd.Series([row["status"] for row in observations]).value_counts().sort_index().to_dict()
    source_http_dates = sorted(
        str(row["http_date"]) for oid, row in cached.items()
        if oid in by_oid and row.get("http_date")
    )
    receipt = {
        "contract": CONTRACT,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "universe_count": len(tickers),
        "roster_matched_ticker_count": sum(ticker in member_map for ticker in tickers),
        "unique_issuer_count": len(by_oid),
        "issuer_fetch_success_count": sum(oid in cached for oid in by_oid),
        "issuer_fetch_failure_count": sum(oid not in cached for oid in by_oid),
        "issuer_attempted_count": len(attempted),
        "stopped_for_global_rate_limit": stopped_for_rate_limit,
        "source_http_date_min": source_http_dates[0] if source_http_dates else None,
        "source_http_date_max": source_http_dates[-1] if source_http_dates else None,
        "usable_single_ticker_share_basis_count": sum(
            row["usable_for_single_ticker_market_cap"] for row in observations
        ),
        "status_counts": {key: int(value) for key, value in status_counts.items()},
        "economic_rule": "SUM(class_nominal_value / explicit_class_nominal_value_per_share)",
        "issued_capital_over_assumed_nominal_allowed": False,
        "multi_ticker_issuer_single_price_market_cap_allowed": False,
        "outputs": {
            cache_path.name: _sha(cache_path.read_bytes()),
            roster_path.name: _sha(roster_path.read_bytes()),
            observation_path.name: _sha(observation_path.read_bytes()),
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
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args()
    print(json.dumps(capture(
        universe_path=args.universe, output_dir=args.output_dir, workers=args.workers,
        no_fetch=args.no_fetch,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
