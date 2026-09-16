from __future__ import annotations

"""Capture a market-wide, gap-free corporate-action inventory from KAP.

The peer-certification gate found in W5/W6-B needs to prove a given ticker's
action interval (last known share-class observation -> knowledge cutoff) is
*empty* -- i.e. no capital increase/decrease was registered inside it. Two
routes were already tried and rejected before this one:

  * the KAP corporate-action *calendar* is forward-looking only (returns [] for
    a 2023 month, populated for the current month) -- not a historical record;
  * a single wide-range disclosure query (2009..cutoff) returns HTTP 500.

What *does* work, demonstrated in data/backtest_sources/p2_action_research_v1
and data/backtest_sources/p7_version_research_v1: a market-wide (empty member
list), date-bounded POST to the official disclosure/members/byCriteria
endpoint returns real historical rows, including disclosureType == "CA"
(capital increase/decrease registration) events with a genuine publishDate.

A first attempt at one request per calendar month found the endpoint caps its
response at exactly 2000 rows, newest first -- a plain one-request-per-month
capture silently drops the older half of a busy month. This version instead
walks the whole window in adaptive, non-overlapping day ranges: any window
whose response is not comfortably under the cap is bisected and re-queried
until every returned window is provably under the cap, so completeness is
demonstrated by construction rather than assumed. Every request/response pair
is kept on disk and hashed; nothing here is trusted without its own bytes.
"""

import argparse
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "data/backtest_sources/kap_monthly_ca_inventory_v1"
URL = "https://kap.org.tr/tr/api/disclosure/members/byCriteria"
CONTRACT = "KAP_ADAPTIVE_WINDOW_MARKET_WIDE_CA_INVENTORY_V1"

# The response caps out at exactly 2000 rows. Anything strictly under this is
# proven complete for its window; anything at or above it must be bisected.
RESPONSE_CAP = 2000
SAFE_MARGIN = 1900  # bisect proactively before hitting the exact cap

# The earliest usable KAP share-class observation in the repo is 2016-06-13;
# the last of the 60 monthly cutoffs is 2026-06-30. Cover a full month of
# slack on each side so no boundary window is ever half-covered.
WINDOW_START = date(2016, 5, 1)
WINDOW_END = date(2026, 7, 31)


def encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def request_payload(from_date: str, to_date: str) -> dict:
    return dict(
        fromDate=from_date, toDate=to_date, memberType="", mkkMemberOidList=[],
        inactiveMkkMemberOidList=[], disclosureClass="", subjectList=[], isLate="",
        mainSector="", sector="", subSector="", marketOid="", index="", bdkReview="",
        bdkMemberOidList=[], year="", term="", ruleType="", period="", fromSrc=False,
        srcCategory="", disclosureIndexList=[],
    )


def fetch_window(start: date, end: date, *, max_attempts: int = 4) -> dict:
    payload = request_payload(start.isoformat(), end.isoformat())
    request_bytes = encode(payload)
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                URL, data=request_bytes,
                headers={"Content-Type": "application/json", "Accept-Language": "tr"},
                timeout=60,
            )
            if response.status_code == 200:
                raw = response.content
                rows = json.loads(raw)
                if not isinstance(rows, list):
                    raise ValueError(f"UNEXPECTED_RESPONSE_SHAPE:{type(rows)}")
                return {
                    "status": "OK", "rows": rows, "raw": raw,
                    "request_bytes": request_bytes, "attempt": attempt,
                    "response_http_date": response.headers.get("Date"),
                }
            last_error = f"HTTP_{response.status_code}"
        except Exception as exc:  # noqa: BLE001 - surfaced in the manifest, not swallowed
            last_error = f"{type(exc).__name__}:{exc}"
        time.sleep(min(2 ** attempt, 20))
    return {"status": "FAILED", "error": last_error, "request_bytes": request_bytes}


def label(start: date, end: date) -> str:
    return f"{start.isoformat()}_{end.isoformat()}"


def capture_range(
    start: date, end: date, manifest: dict, manifest_path: Path, sleep_s: float, depth: int = 0
) -> None:
    """Fetch [start, end] inclusive; bisect and recurse if the cap is at risk."""
    key = label(start, end)
    if manifest["windows"].get(key, {}).get("status") == "OK":
        return

    result = fetch_window(start, end)
    time.sleep(sleep_s)

    if result["status"] != "OK":
        manifest["windows"][key] = {
            "start": start.isoformat(), "end": end.isoformat(), "status": "FAILED",
            "error": result["error"], "request_sha256": sha256(result["request_bytes"]).hexdigest(),
        }
        manifest_path.write_bytes(encode(manifest))
        print(f"{'  ' * depth}FAILED  {key}  {result['error']}")
        return

    rows = result["rows"]
    if len(rows) >= SAFE_MARGIN and start != end:
        mid = start + (end - start) // 2
        print(f"{'  ' * depth}bisect  {key}  rows={len(rows)} (>= {SAFE_MARGIN}) -> "
              f"{start}..{mid} + {mid + timedelta(days=1)}..{end}")
        capture_range(start, mid, manifest, manifest_path, sleep_s, depth + 1)
        capture_range(mid + timedelta(days=1), end, manifest, manifest_path, sleep_s, depth + 1)
        return

    if len(rows) >= RESPONSE_CAP:
        # A single day at the hard cap: cannot be bisected further and cannot
        # be proven complete. Record it honestly rather than silently accept it.
        manifest["windows"][key] = {
            "start": start.isoformat(), "end": end.isoformat(), "status": "AT_CAP_UNSPLITTABLE",
            "row_count": len(rows), "request_sha256": sha256(result["request_bytes"]).hexdigest(),
        }
        manifest_path.write_bytes(encode(manifest))
        print(f"{'  ' * depth}CAP-HIT single day {key} rows={len(rows)} -- cannot prove complete")
        return

    raw = result["raw"]
    (OUT / f"{key}.request.json").write_bytes(result["request_bytes"])
    (OUT / f"{key}.response.json").write_bytes(raw)
    manifest["windows"][key] = {
        "start": start.isoformat(), "end": end.isoformat(), "status": "OK",
        "row_count": len(rows),
        "ca_row_count": sum(1 for r in rows if r.get("disclosureType") == "CA"),
        "request_sha256": sha256(result["request_bytes"]).hexdigest(),
        "response_sha256": sha256(raw).hexdigest(),
        "response_http_date": result.get("response_http_date"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_bytes(encode(manifest))
    print(f"{'  ' * depth}OK      {key}  rows={len(rows)} ca={manifest['windows'][key]['ca_row_count']}")


def initial_chunks(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    out = []
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=chunk_days - 1), end)
        out.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return out


def verify_coverage(manifest: dict) -> dict:
    ok = [w for w in manifest["windows"].values() if w["status"] == "OK"]
    intervals = sorted(
        (date.fromisoformat(w["start"]), date.fromisoformat(w["end"])) for w in ok
    )
    gaps = []
    cursor = WINDOW_START
    for s, e in intervals:
        if s > cursor:
            gaps.append((cursor.isoformat(), (s - timedelta(days=1)).isoformat()))
        cursor = max(cursor, e + timedelta(days=1))
    if cursor <= WINDOW_END:
        gaps.append((cursor.isoformat(), WINDOW_END.isoformat()))
    return {
        "windows_ok": len(ok),
        "windows_failed": sum(1 for w in manifest["windows"].values() if w["status"] == "FAILED"),
        "windows_at_cap_unsplittable": sum(
            1 for w in manifest["windows"].values() if w["status"] == "AT_CAP_UNSPLITTABLE"
        ),
        "total_rows": sum(w["row_count"] for w in ok),
        "total_ca_rows": sum(w["ca_row_count"] for w in ok),
        "gaps": gaps,
        "complete": not gaps,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sleep", type=float, default=1.2, help="seconds between requests")
    parser.add_argument("--chunk-days", type=int, default=7, help="initial window width in days")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "capture_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else {"windows": {}}
    )

    if not args.verify_only:
        chunks = initial_chunks(WINDOW_START, WINDOW_END, args.chunk_days)
        print(f"Capturing {WINDOW_START}..{WINDOW_END} in {len(chunks)} initial "
              f"{args.chunk_days}-day chunks (bisected further if any hits the cap)")
        for i, (s, e) in enumerate(chunks, 1):
            print(f"[{i}/{len(chunks)}]", end=" ")
            capture_range(s, e, manifest, manifest_path, args.sleep)

    coverage = verify_coverage(manifest)
    manifest["contract"] = CONTRACT
    manifest["window_start"] = WINDOW_START.isoformat()
    manifest["window_end"] = WINDOW_END.isoformat()
    manifest["coverage"] = coverage
    manifest_path.write_bytes(encode(manifest))
    print(f"\nCoverage: {json.dumps(coverage, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
