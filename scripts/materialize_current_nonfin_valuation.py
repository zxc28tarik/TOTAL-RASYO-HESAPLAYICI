from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.experimental_core_module_materializer import _fact
from scripts.materialize_experimental_financial_facts import map_report
from src.analytics.nonfin_batch_pipeline import build_nonfin_snapshots_from_frames
from src.analytics.nonfin_valuation import NonfinValuationConfig, value_nonfin_snapshot
from src.analytics.price_level_adapter import load_action_bundles
from src.ingest.company_fact_materializer import derive_company_quarters
from src.ingest.kap_bulk_exact_semantic_mapping import build_bulk_exact_company_derivation_config
from src.ingest.kap_bulk_financial_export import parse_kap_bulk_export_report


CONTRACT = "CURRENT_NONFIN_RELATIVE_VALUATION_V1"
ARCHIVE_NAMES = (
    "KAP_2025_3A.zip", "KAP_2025_6A.zip", "KAP_2025_9A.zip",
    "KAP_2025_Y.zip", "KAP_2026_3A.zip", "KAP_2026_6A.zip",
)
DEFAULT_ARCHIVES = ROOT / "private/reconstructed_kap_archives"
DEFAULT_BASIS = ROOT / "data/live/current_price_level_basis_v1"
DEFAULT_ROUTES = ROOT / "data/backtest_sources/m3_source_package/sector_routes.csv.gz"
DEFAULT_OUTPUT = ROOT / "data/live/current_nonfin_valuation_v1"
CONFIG = ROOT / "config/nonfin_valuation.kap_bulk_exact_v1.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mapped_reports(archive_dir: Path, tickers: set[str]) -> tuple[list[dict], dict[str, str]]:
    output: list[dict] = []
    hashes: dict[str, str] = {}
    for name in ARCHIVE_NAMES:
        path = archive_dir / name
        digest = _sha(path)
        hashes[name] = digest
        with ZipFile(path) as bundle:
            for member in bundle.namelist():
                entity = member.split("_", 1)[0]
                if entity not in tickers:
                    continue
                raw = bundle.read(member)
                report = parse_kap_bulk_export_report(
                    archive_name=name, archive_sha256=digest, member_name=member, raw_html=raw,
                )
                row = {
                    "member_name": member, "member_sha256": report.member_sha256,
                    "archive_name": name, "archive_sha256": digest,
                }
                output.append(map_report((str(path), row)))
    return output, hashes


def materialize(*, archive_dir: Path, basis_dir: Path, routes_path: Path,
                output_dir: Path) -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    caps = pd.read_csv(basis_dir / "market_caps.csv", dtype={"ticker": str, "trade_date": str})
    routes = pd.read_csv(routes_path, dtype=str)
    routes["valid_from"] = pd.to_datetime(routes["valid_from"])
    routes["valid_to"] = pd.to_datetime(routes["valid_to"], errors="coerce")
    analysis_at = datetime.now(timezone.utc)
    analysis_day = pd.Timestamp(analysis_at.date())
    active = routes.loc[
        routes.ticker.isin(caps.ticker)
        & routes.sector_index_code.eq("XUSIN")
        & routes.valid_from.le(analysis_day)
        & (routes.valid_to.isna() | routes.valid_to.gt(analysis_day))
    ].copy()
    if active.duplicated("ticker").any():
        raise ValueError("current XUSIN route ambiguous")
    tickers = set(active.ticker)
    reports, archive_hashes = _mapped_reports(archive_dir, tickers)
    by_ticker: dict[str, list[dict]] = {ticker: [] for ticker in tickers}
    for report in reports:
        entity = report["report"]["source_entity_code"]
        if entity in by_ticker:
            by_ticker[entity].append(report)

    derivation = build_bulk_exact_company_derivation_config("NONFIN")
    financial_rows: list[dict] = []
    derivation_rejections: list[dict] = []
    cap_by_ticker = caps.set_index("ticker").to_dict("index")
    for ticker in sorted(tickers):
        facts = []
        for report in by_ticker[ticker]:
            facts.extend(
                _fact(raw) for raw in report.get("facts", [])
                if raw.get("sector_family") == "NONFIN"
                and raw.get("ticker") == ticker
            )
        try:
            quarters = derive_company_quarters(
                facts, config=derivation, ticker=ticker, analysis_at=analysis_at,
                anchor_period_end=max(fact.period_end for fact in facts),
            )
        except (ValueError, TypeError) as exc:
            derivation_rejections.append({"ticker": ticker, "reason": type(exc).__name__ + ":" + str(exc)})
            continue
        for index, quarter in enumerate(quarters):
            values = dict(quarter.values)
            # The exact financial derivation's issued-capital/1 output is never
            # allowed across this boundary.  Only the latest row receives the
            # independently certified current class-nominal share basis.
            values["shares_out"] = None
            values["shares_basis_date"] = quarter.period_end
            if index == len(quarters) - 1:
                values["shares_out"] = int(cap_by_ticker[ticker]["shares_out"])
                values["shares_basis_date"] = cap_by_ticker[ticker]["trade_date"]
            financial_rows.append({
                "ticker": ticker, "period_end": quarter.period_end,
                "published_at": quarter.published_at,
                "derivation_profile": quarter.derivation_profile,
                "derivation_version": quarter.derivation_version,
                **values,
            })

    bundles = load_action_bundles(basis_dir / "action_bundle_index.json") or {}
    price_rows = []
    for ticker in sorted(tickers):
        cap = cap_by_ticker[ticker]
        price_rows.append({
            "ticker": ticker, "price_trade_date": cap["trade_date"],
            "current_price": cap["raw_close"], "price_basis": cap["price_basis"],
            "action_bundle": bundles.get(ticker),
        })
    universe = pd.DataFrame([{
        "ticker": ticker, "peer_group": "XUSIN", "sector_family": "NONFIN",
    } for ticker in sorted(tickers)], columns=("ticker", "peer_group", "sector_family"))
    if tickers:
        snapshots, snapshot_rejections = build_nonfin_snapshots_from_frames(
            universe=universe, financials=pd.DataFrame(financial_rows),
            prices=pd.DataFrame(price_rows), analysis_at=analysis_at,
            anchor_period_end=None, basis_receipts={},
        )
    else:
        snapshots, snapshot_rejections = [], []
    config = NonfinValuationConfig.from_json_file(CONFIG)
    valuations = []
    for target in snapshots:
        peers = [
            peer for peer in snapshots
            if peer.ticker != target.ticker
            and peer.anchor_period_end == target.anchor_period_end
            and peer.sector_code == target.sector_code
        ]
        valuations.append(value_nonfin_snapshot(target, peers, config))
    valuation_path = output_dir / "valuations.jsonl"
    valuation_path.write_text("".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        for row in sorted(valuations, key=lambda row: row["ticker"])
    ), encoding="utf-8")
    receipt = {
        "contract": CONTRACT, "analysis_at": analysis_at.isoformat(),
        "peer_group": "XUSIN", "route_source_sha256": _sha(routes_path),
        "routed_candidate_count": len(tickers), "routed_tickers": sorted(tickers),
        "financial_report_count": len(reports),
        "derived_financial_ticker_count": len({row["ticker"] for row in financial_rows}),
        "snapshot_count": len(snapshots),
        "production_valuation_count": len(valuations),
        "usable_valuation_count": sum(row["status"] == "OK" for row in valuations),
        "m2_materialized_count": 0,
        "m2_blocker": (
            "CURRENT_FOLLOW_AXIS_NOT_MATERIALIZED"
            if valuations else "CURRENT_NONFIN_CANDIDATE_SET_EMPTY_AFTER_SAFE_MARKET_CAP_GATE"
        ),
        "neutral_follow_or_m2_materialized": False,
        "unsafe_issued_capital_share_fields_scrubbed": True,
        "derivation_rejections": derivation_rejections,
        "snapshot_rejections": snapshot_rejections,
        "archive_hashes": archive_hashes,
        "outputs": {valuation_path.name: _sha(valuation_path)},
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVES)
    parser.add_argument("--basis-dir", type=Path, default=DEFAULT_BASIS)
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(materialize(
        archive_dir=args.archive_dir, basis_dir=args.basis_dir,
        routes_path=args.routes, output_dir=args.output_dir,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
