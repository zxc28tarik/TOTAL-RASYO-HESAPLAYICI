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
import collections
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

from scripts.experimental_core_module_materializer import _dated_nonfin_family

CONTRACT = "CURRENT_KAP_SECTOR_INDEX_MEMBERSHIP_V1"
SOURCE_URL = "https://www.kap.org.tr/tr/Endeksler"
SOURCE_ID = "KAP_ENDEKSLER_CURRENT"
SECTOR_URL = "https://www.kap.org.tr/tr/Sektorler"
SECTOR_SOURCE_ID = "KAP_SEKTORLER_CURRENT"
INHERITED_ROUTES = ROOT / "data/backtest_sources/m3_source_package/sector_routes.csv.gz"
OUTPUT = ROOT / "data/live/current_sector_routes_v1"
INDEX_CODES = ("XUSIN", "XUHIZ", "XUTEK", "XUMAL")
# A route only resolves a report's sector family when it is already in force at
# that report's period_end (see _dated_nonfin_family). Dating these rows at the
# capture date would therefore silently yield no family for every financial
# report, and CORE would reject the ticker for missing family evidence even
# though its KAP report is present. The current pipeline reads KAP archives from
# 2025Q1 onward, so membership is asserted from the start of that coverage --
# still far narrower than the inherited rows' 2020-07-27 backfill.
CURRENT_FINANCIAL_COVERAGE_START = date(2025, 1, 1)


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


def parse_sectors(raw_html: str) -> dict[str, str]:
    """Every KAP-classified operating company and the sector it is filed under."""
    unescaped = raw_html.replace('\\"', '"')
    pairs = re.findall(
        r'"sectorName":"([^"]{1,160})","sectorOid"[^}]{0,300}?"stockCode":"([A-Z0-9]{2,8})"',
        unescaped,
    )
    if not pairs:
        raise ValueError("official KAP sector classification empty")
    sector_of: dict[str, str] = {}
    for sector, ticker in pairs:
        if sector_of.setdefault(ticker, sector) != sector:
            raise ValueError(f"ticker filed under two KAP sectors: {ticker}")
    return sector_of


def build_routes(members: dict[str, list[str]], sector_of: dict[str, str], *,
                 valid_from: date) -> tuple[pd.DataFrame, int, int, list[str]]:
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
    # KAP classifies more operating companies than the four indices admit. A
    # company with a sector but no index membership still needs a benchmark,
    # so it takes the index its own sector's index-member peers belong to. The
    # mapping is derived from those peers, never hand-written, and a sector
    # whose members disagree is left unrouted rather than guessed.
    by_sector: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for ticker, sector in sector_of.items():
        if ticker in official:
            by_sector[sector][official[ticker]] += 1
    sector_index = {
        sector: next(iter(counts))
        for sector, counts in by_sector.items() if len(counts) == 1
    }
    ambiguous = sorted(sector for sector, counts in by_sector.items() if len(counts) > 1)
    placed = set(official) | known
    sector_added = [
        {"ticker": ticker, "valid_from": valid_from.isoformat(), "valid_to": None,
         "sector_index_code": sector_index[sector], "source_id": SECTOR_SOURCE_ID}
        for ticker, sector in sorted(sector_of.items())
        if ticker not in placed and sector in sector_index
    ]
    added += sector_added
    routes = pd.concat([inherited, pd.DataFrame(added, columns=inherited.columns)],
                       ignore_index=True)
    if routes.loc[routes.valid_to.isna()].duplicated("ticker").any():
        raise ValueError("current sector route ambiguous")
    routes = routes.sort_values(["ticker", "valid_from"]).reset_index(drop=True)
    # A route dated after the reports it must classify resolves no family at
    # all, which surfaces only as a CORE rejection much later. Fail here instead.
    unresolved = [
        row["ticker"] for row in added
        if _dated_nonfin_family(routes, row["ticker"], CURRENT_FINANCIAL_COVERAGE_START) is None
        and row["sector_index_code"] in {"XUSIN", "XUHIZ", "XUTEK"}
    ]
    if unresolved:
        raise ValueError(
            "added NONFIN route resolves no family at coverage start: "
            f"{unresolved[:5]} ({len(unresolved)} total)"
        )
    return routes, len(added), len(sector_added), ambiguous


def capture(*, output_dir: Path = OUTPUT, valid_from: date) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "TOTAL-RASYO current sector routes/1"}
    response = requests.get(SOURCE_URL, timeout=90, headers=headers)
    response.raise_for_status()
    raw = response.content
    members = parse_members(response.text)

    sector_response = requests.get(SECTOR_URL, timeout=90, headers=headers)
    sector_response.raise_for_status()
    sector_raw = sector_response.content
    sector_of = parse_sectors(sector_response.text)
    routes, added_count, sector_added_count, ambiguous = build_routes(
        members, sector_of, valid_from=valid_from)

    raw_path = output_dir / "kap_endeksler.html.gz"
    raw_path.write_bytes(gzip.compress(raw, mtime=0))
    sector_raw_path = output_dir / "kap_sektorler.html.gz"
    sector_raw_path.write_bytes(gzip.compress(sector_raw, mtime=0))
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
        }, {
            "source_id": SECTOR_SOURCE_ID,
            "publisher": "Kamuyu Aydinlatma Platformu (KAP)",
            "source_url": SECTOR_URL,
            "artifact_identity": "KAP Sektorler classification snapshot, deterministic gzip",
            "raw_path": str(sector_raw_path.relative_to(ROOT)).replace("\\", "/"),
            "retrieved_at": retrieved_at.isoformat(),
            "http_date": sector_response.headers.get("Date"),
            "raw_sha256": _sha(sector_raw_path.read_bytes()),
            "uncompressed_sha256": _sha(gzip.decompress(sector_raw_path.read_bytes())),
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
        "index_member_route_count": added_count - sector_added_count,
        "sector_classified_non_index_route_count": sector_added_count,
        "sector_classified_company_count": len(sector_of),
        "ambiguous_sectors_left_unrouted": ambiguous,
        "route_row_count": int(len(routes)),
        "historical_source_package_modified": False,
        "outputs": {
            path.name: _sha(path.read_bytes())
            for path in (raw_path, sector_raw_path, routes_path, output_dir / "manifest.json")
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
    parser.add_argument("--valid-from", type=date.fromisoformat,
                        default=CURRENT_FINANCIAL_COVERAGE_START)
    args = parser.parse_args()
    print(json.dumps(capture(output_dir=args.output_dir, valid_from=args.valid_from),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
