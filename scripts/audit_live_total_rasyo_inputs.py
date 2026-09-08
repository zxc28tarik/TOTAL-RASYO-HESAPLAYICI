from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import gzip
import json
import os
from pathlib import Path
import re
import sys
from zipfile import ZipFile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.materialize_experimental_financial_facts import economic_family
from src.analytics.live_total_rasyo_readiness import build_live_readiness
from src.ingest.api.kap_public_universe import (
    KapPublicUniverseClient, KapUniverseError, write_universe_snapshot,
)
from src.ingest.kap_bulk_financial_export import parse_kap_bulk_export_report


CONTRACT = "LIVE_TOTAL_RASYO_INPUT_READINESS_V1"
ROLE_RE = re.compile(rb'class="([A-Za-z0-9_-]+)-row-[0-9]+(?: |")')
DEFAULT_ARCHIVE = ROOT / "private/reconstructed_kap_archives/KAP_2026_6A.zip"
DEFAULT_OUTPUT = ROOT / "data/live/current_total_rasyo_v1"
DEFAULT_SHARES = ROOT / "data/live/current_share_basis_v1/ticker_share_basis.jsonl.gz"
DEFAULT_PRICES = ROOT / "data/live/current_raw_close_v1/raw_close.csv.gz"
DEFAULT_MARKET_CAPS = ROOT / "data/live/current_price_level_basis_v1/market_caps.csv"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_archive(path: Path) -> tuple[list[dict], dict]:
    path = path.resolve()
    archive_sha = _sha(path)
    reports: list[dict] = []
    rejected = 0
    with ZipFile(path) as bundle:
        members = sorted(name for name in bundle.namelist() if name.endswith(".xls"))
        for name in members:
            raw = bundle.read(name)
            roles = sorted({value.decode("ascii") for value in ROLE_RE.findall(raw)})
            try:
                report = parse_kap_bulk_export_report(
                    archive_name=path.name, archive_sha256=archive_sha,
                    member_name=name, raw_html=raw,
                )
            except (ValueError, TypeError):
                rejected += 1
                continue
            family = economic_family(report.company_name, roles)
            schema = "GENERAL" if any(role.startswith("general_role_") for role in roles) else "SPECIALIST"
            reports.append({
                "source_entity_code": report.source_entity_code,
                "notification_id": report.notification_id,
                "published_at": report.published_at.isoformat(),
                "family": family,
                "schema": schema,
            })
    return reports, {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "sha256": archive_sha,
        "member_count": len(reports) + rejected,
        "parsed_report_count": len(reports),
        "parse_rejected_count": rejected,
        "source_url": "https://kap.org.tr/tr/api/financialTable/download/2026/2",
    }


def audit(*, output_dir: Path, archive: Path, shares: Path = DEFAULT_SHARES,
          prices: Path = DEFAULT_PRICES, market_caps: Path = DEFAULT_MARKET_CAPS,
          refresh_universe: bool = True) -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    universe_path = output_dir / "universe.csv"
    meta_path = output_dir / "universe.csv.meta.json"
    universe_fetch_error = None
    try:
        if not refresh_universe:
            raise KapUniverseError("offline run requested")
        snapshot = KapPublicUniverseClient(timeout_seconds=60, max_retries=3, minimum_rows=100).fetch()
        universe_path, meta_path = write_universe_snapshot(snapshot, universe_path)
        universe_frame = snapshot.frame
        universe_source_mode = "LIVE_KAP_FETCH"
    except KapUniverseError as exc:
        if not universe_path.exists() or not meta_path.exists():
            raise
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("csv_sha256") != _sha(universe_path):
            raise ValueError("cached current universe hash mismatch") from exc
        universe_frame = pd.read_csv(universe_path)
        if len(universe_frame) != meta.get("row_count"):
            raise ValueError("cached current universe row count mismatch") from exc
        universe_fetch_error = type(exc).__name__ + ":" + str(exc)
        universe_source_mode = (
            "CACHED_HASH_VERIFIED_OFFLINE"
            if not refresh_universe else "CACHED_HASH_VERIFIED_AFTER_LIVE_FETCH_FAILURE"
        )
    reports, archive_receipt = inspect_archive(archive)
    database_available = any(os.environ.get(key) for key in (
        "TOTAL_RASYO_TEST_DSN", "DATABASE_URL", "PGDATABASE",
    ))
    share_rows = [] if not shares.exists() else [
        json.loads(line) for line in gzip.decompress(shares.read_bytes()).splitlines()
    ]
    share_tickers = {
        row["ticker"] for row in share_rows if row.get("usable_for_single_ticker_market_cap") is True
    }
    price_frame = None if not prices.exists() else pd.read_csv(prices)
    price_tickers = set() if price_frame is None else set(price_frame["ticker"].astype(str))
    market_cap_frame = None if not market_caps.exists() else pd.read_csv(market_caps)
    market_cap_tickers = (
        set() if market_cap_frame is None else set(market_cap_frame["ticker"].astype(str))
    )
    readiness, rejections = build_live_readiness(
        universe_rows=universe_frame.to_dict("records"), report_rows=reports,
        database_available=database_available,
        share_basis_tickers=share_tickers, raw_close_tickers=price_tickers,
        market_cap_tickers=market_cap_tickers,
    )
    rejection_path = output_dir / "rejections.jsonl"
    rejection_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rejections),
        encoding="utf-8",
    )
    receipt = {
        "contract": CONTRACT,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "mode": "CURRENT_LIVE_SEPARATE_FROM_HISTORICAL_PIT",
        "universe_source_mode": universe_source_mode,
        "universe_fetch_error": universe_fetch_error,
        **readiness,
        "current_financial_archive": archive_receipt,
        "financial_report_family_counts": dict(sorted(Counter(
            row["family"] or ("GENERAL_UNRESOLVED" if row["schema"] == "GENERAL" else "SCHEMA_UNRESOLVED")
            for row in reports
        ).items())),
        "historical_p5_authorized": False,
        "current_share_basis_artifact": None if not shares.exists() else {
            "path": str(shares.resolve().relative_to(ROOT)).replace("\\", "/"), "sha256": _sha(shares),
        },
        "current_raw_close_artifact": None if not prices.exists() else {
            "path": str(prices.resolve().relative_to(ROOT)).replace("\\", "/"), "sha256": _sha(prices),
        },
        "current_market_cap_artifact": None if not market_caps.exists() else {
            "path": str(market_caps.resolve().relative_to(ROOT)).replace("\\", "/"),
            "sha256": _sha(market_caps),
        },
        "outputs": {
            universe_path.name: _sha(universe_path),
            meta_path.name: _sha(meta_path),
            rejection_path.name: _sha(rejection_path),
        },
    }
    receipt_path = output_dir / "receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--shares", type=Path, default=DEFAULT_SHARES)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--market-caps", type=Path, default=DEFAULT_MARKET_CAPS)
    parser.add_argument("--offline-universe", action="store_true")
    args = parser.parse_args()
    print(json.dumps(audit(
        output_dir=args.output_dir, archive=args.archive, shares=args.shares, prices=args.prices,
        market_caps=args.market_caps,
        refresh_universe=not args.offline_universe,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
