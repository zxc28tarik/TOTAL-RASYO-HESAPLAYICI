from __future__ import annotations

"""Capture current BIST sector-index membership from KAP's official index page.

The historical M3 source package routes only 209 tickers, while KAP's own
index pages list 583 members across XUSIN/XUHIZ/XUTEK/XUMAL. Every one of those
583 is in the live universe, and the 209 existing rows agree with the official
lists (202 exact, 0 contradictions, 7 superseded ticker codes). The shortfall is
therefore an incomplete extraction, not a narrower index definition, and a
ticker with no route can never receive M3/Ek4.

This capture is current-only. data/backtest_sources/m3_source_package stays
byte-identical and hash-pinned so no historical replay changes.
"""

import argparse
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONTRACT = "CURRENT_KAP_SECTOR_INDEX_MEMBERSHIP_V1"
SOURCE_URL = "https://www.kap.org.tr/tr/Endeksler"
SOURCE_ID = "KAP_ENDEKSLER_CURRENT"
INHERITED_ROUTES = ROOT / "data/backtest_sources/m3_source_package/sector_routes.csv.gz"
OUTPUT = ROOT / "data/live/current_sector_routes_v1"
INDEX_CODES = ("XUSIN", "XUHIZ", "XUTEK", "XUMAL")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def parse_members(raw_html: str) -> dict[str, list[str]]:
    # KAP embeds the index payload as escaped JSON inside the Next.js bundle.
    unescaped = raw_html.replace('\\"', '"')
    members: dict[str, list[str]] = {}
    for code in INDEX_CODES:
        block = re.search(r'\{"code":"' + code + r'","content":\[(.*?)\]', unescaped, re.S)
        if block is None:
            raise ValueError(f"official KAP index membership missing: {code}")
        codes = re.findall(r'"stockCode":"([A-Z0-9]{2,8})"', block.group(1))
        if not codes:
            raise ValueError(f"official KAP index membership empty: {code}")
        members[code] = sorted(set(codes))
    overlap = [
        ticker for ticker in {t for lst in members.values() for t in lst}
        if sum(ticker in lst for lst in members.values()) > 1
    ]
    if overlap:
        raise ValueError(f"ticker in multiple sector indices: {sorted(overlap)}")
    return members


def build_routes(members: dict[str, list[str]], *, valid_from: date) -> tuple[pd.DataFrame, int]:
    inherited = pd.read_csv(INHERITED_ROUTES, dtype=str)
    official = {
        ticker: code for code, tickers in members.items() for ticker in tickers
    }
    conflicts = [
        (row.ticker, row.sector_index_code, official[row.ticker])
        for row in inherited.itertuples()
        if pd.isna(row.valid_to) and row.ticker in official
        and row.sector_index_code != official[row.ticker]
    ]
    if conflicts:
        raise ValueError(f"inherited route contradicts official membership: {conflicts}")
    known = set(inherited.loc[inherited.valid_to.isna(), "ticker"])
    added = [
        {"ticker": ticker, "valid_from": valid_from.isoformat(), "valid_to": None,
         "sector_index_code": code, "source_id": SOURCE_ID}
        for ticker, code in sorted(official.items()) if ticker not in known
    ]
    routes = pd.concat([inherited, pd.DataFrame(added, columns=inherited.columns)],
                       ignore_index=True)
    if routes.loc[routes.valid_to.isna()].duplicated("ticker").any():
        raise ValueError("current sector route ambiguous")
    return routes.sort_values(["ticker", "valid_from"]).reset_index(drop=True), len(added)


def capture(*, output_dir: Path = OUTPUT, valid_from: date) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    response = requests.get(SOURCE_URL, timeout=90, headers={
        "User-Agent": "TOTAL-RASYO current sector routes/1",
    })
    response.raise_for_status()
    raw = response.content
    members = parse_members(response.text)
    routes, added_count = build_routes(members, valid_from=valid_from)

    raw_path = output_dir / "kap_endeksler.html.gz"
    raw_path.write_bytes(gzip.compress(raw, mtime=0))
    routes_path = output_dir / "sector_routes.csv.gz"
    routes.to_csv(routes_path, index=False, compression="gzip")

    retrieved_at = datetime.now(timezone.utc)
    manifest = {
        "contract": CONTRACT,
        "canonical_files": {
            "sector_routes": {
                "path": str(routes_path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": _sha(routes_path.read_bytes()),
                "row_count": int(len(routes)),
                "source_id": SOURCE_ID,
            },
        },
        "raw_sources": [{
            "source_id": SOURCE_ID,
            "publisher": "Kamuyu Aydinlatma Platformu (KAP)",
            "source_url": SOURCE_URL,
            "artifact_identity": "KAP Endeksler index-membership snapshot, deterministic gzip",
            "raw_path": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
            "retrieved_at": retrieved_at.isoformat(),
            "http_date": response.headers.get("Date"),
            # The consumer hashes the stored artifact itself, so this is the
            # gzip file's digest, not the decompressed payload's.
            "raw_sha256": _sha(raw_path.read_bytes()),
            "uncompressed_sha256": _sha(gzip.decompress(raw_path.read_bytes())),
        }],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "contract": CONTRACT,
        "captured_at": retrieved_at.isoformat(),
        "source_url": SOURCE_URL,
        "http_date": response.headers.get("Date"),
        "official_member_counts": {code: len(lst) for code, lst in members.items()},
        "official_member_total": sum(len(lst) for lst in members.values()),
        "inherited_route_row_count": int(len(pd.read_csv(INHERITED_ROUTES, dtype=str))),
        "added_route_count": added_count,
        "route_row_count": int(len(routes)),
        "historical_source_package_modified": False,
        "outputs": {
            path.name: _sha(path.read_bytes())
            for path in (raw_path, routes_path, output_dir / "manifest.json")
        },
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--valid-from", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    print(json.dumps(capture(output_dir=args.output_dir, valid_from=args.valid_from),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
