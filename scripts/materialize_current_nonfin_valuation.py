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
from src.analytics.current_nonfin_follow import materialize_current_nonfin_follow
from src.analytics.nonfin_batch_pipeline import build_nonfin_snapshots_from_frames
from src.analytics.nonfin_valuation import (
    NonfinValuationConfig, build_nonfin_snapshot, combine_nonfin_m2,
    value_nonfin_snapshot,
)
from src.analytics.price_level_adapter import load_action_bundles
from src.ingest.company_fact_materializer import derive_company_quarters
from src.ingest.kap_bulk_exact_semantic_mapping import build_bulk_exact_company_derivation_config
from src.ingest.kap_bulk_financial_export import parse_kap_bulk_export_report


CONTRACT = "CURRENT_NONFIN_RELATIVE_VALUATION_V1"
NONFIN_INDICES = frozenset({"XUSIN", "XUHIZ", "XUTEK"})
ARCHIVE_NAMES = (
    "KAP_2025_3A.zip", "KAP_2025_6A.zip", "KAP_2025_9A.zip",
    "KAP_2025_Y.zip", "KAP_2026_3A.zip", "KAP_2026_6A.zip",
)
DEFAULT_ARCHIVES = ROOT / "private/reconstructed_kap_archives"
DEFAULT_BASIS = ROOT / "data/live/current_price_level_basis_v1"
DEFAULT_ROUTES = ROOT / "data/backtest_sources/m3_source_package/sector_routes.csv.gz"
DEFAULT_OUTPUT = ROOT / "data/live/current_nonfin_valuation_v1"
DEFAULT_STOCK_PRICES = ROOT / "data/live/current_market_modules_v1/stock_prices.csv.gz"
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


def _previous_quarter_end(value):
    if value.month == 3:
        return value.replace(year=value.year - 1, month=12, day=31)
    days = {6: 31, 9: 30, 12: 30}
    return value.replace(month=value.month - 3, day=days[value.month])


def _previous_period_valuations(snapshots, financial_rows, config):
    frame = pd.DataFrame(financial_rows)
    output = []
    rejections = []
    prior_snapshots = []
    for current in snapshots:
        prior_anchor = _previous_quarter_end(current.anchor_period_end)
        group = frame.loc[
            (frame.ticker == current.ticker)
            & (pd.to_datetime(frame.period_end).dt.date <= prior_anchor)
        ].sort_values("period_end").tail(4).copy()
        try:
            if len(group) != 4 or pd.Timestamp(group.iloc[-1].period_end).date() != prior_anchor:
                raise ValueError("PREVIOUS_FOUR_QUARTERS_MISSING")
            group.loc[group.index[-1], "shares_out"] = current.shares_out
            prior_snapshots.append(build_nonfin_snapshot(
                ticker=current.ticker, analysis_at=current.analysis_at,
                sector_code=current.sector_code, current_price=current.current_price,
                price_trade_date=current.price_trade_date,
                quarters=group.to_dict("records"),
            ))
        except (ValueError, TypeError, OverflowError) as exc:
            rejections.append({"ticker": current.ticker, "reason": str(exc)})
    for target in prior_snapshots:
        peers = [
            peer for peer in prior_snapshots
            if peer.ticker != target.ticker
            and peer.anchor_period_end == target.anchor_period_end
            and peer.sector_code == target.sector_code
        ]
        output.append(value_nonfin_snapshot(target, peers, config))
    return output, rejections


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
        & routes.sector_index_code.isin(NONFIN_INDICES)
        & routes.valid_from.le(analysis_day)
        & (routes.valid_to.isna() | routes.valid_to.gt(analysis_day))
    ].copy()
    if active.duplicated("ticker").any():
        raise ValueError("current NONFIN route ambiguous")
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
                # The production snapshot's per-price-unit denominator must
                # match the Borsa quote unit. Legal share count is retained in
                # the price-basis artifact but must not be paired with a price
                # announced per 1 TRY nominal value.
                values["shares_out"] = float(cap_by_ticker[ticker]["quoted_nominal_units_out"])
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
    peer_group_by_ticker = active.set_index("ticker")["sector_index_code"].to_dict()
    universe = pd.DataFrame([{
        "ticker": ticker, "peer_group": peer_group_by_ticker[ticker], "sector_family": "NONFIN",
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
    previous_valuations, previous_rejections = _previous_period_valuations(
        snapshots, financial_rows, config
    )
    follow_rows, follow_rejections = materialize_current_nonfin_follow(
        current_valuations=valuations, previous_valuations=previous_valuations,
        adjusted_prices=pd.read_csv(DEFAULT_STOCK_PRICES),
    )
    follow_map = {row["ticker"]: row for row in follow_rows}
    m2_rows = [
        combine_nonfin_m2(
            row, follow_score=follow_map[row["ticker"]]["follow_score"],
            follow_active=True, config=config,
        )
        for row in valuations
        if row["status"] == "OK" and row["ticker"] in follow_map
    ]
    valuation_path = output_dir / "valuations.jsonl"
    valuation_path.write_text("".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        for row in sorted(valuations, key=lambda row: row["ticker"])
    ), encoding="utf-8")
    previous_path = output_dir / "previous_valuations.jsonl"
    follow_path = output_dir / "follow.jsonl"
    m2_path = output_dir / "m2.jsonl"
    for path, rows in (
        (previous_path, previous_valuations), (follow_path, follow_rows), (m2_path, m2_rows),
    ):
        path.write_text("".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n"
            for row in sorted(rows, key=lambda item: item["ticker"])
        ), encoding="utf-8")
    receipt = {
        "contract": CONTRACT, "analysis_at": analysis_at.isoformat(),
        "peer_groups": sorted(NONFIN_INDICES), "route_source_sha256": _sha(routes_path),
        "routed_candidate_counts_by_peer_group": {
            key: int(value) for key, value in active.sector_index_code.value_counts().sort_index().items()
        },
        "routed_candidate_count": len(tickers), "routed_tickers": sorted(tickers),
        "financial_report_count": len(reports),
        "derived_financial_ticker_count": len({row["ticker"] for row in financial_rows}),
        "snapshot_count": len(snapshots),
        "production_valuation_count": len(valuations),
        "usable_valuation_count": sum(row["status"] == "OK" for row in valuations),
        "previous_period_valuation_count": len(previous_valuations),
        "usable_previous_period_valuation_count": sum(row["status"] == "OK" for row in previous_valuations),
        "follow_materialized_count": len(follow_rows),
        "m2_materialized_count": len(m2_rows),
        "m2_blocker": (
            None
            if m2_rows
            else (
                "CURRENT_NONFIN_VALUATION_COVERAGE_INSUFFICIENT"
                if valuations else "CURRENT_NONFIN_CANDIDATE_SET_EMPTY_AFTER_SAFE_MARKET_CAP_GATE"
            )
        ),
        "neutral_follow_or_m2_materialized": False,
        "follow_contract": "CURRENT_NONFIN_FOLLOW_AXIS_V1",
        "follow_rejections": follow_rejections,
        "previous_period_rejections": previous_rejections,
        "unsafe_issued_capital_share_fields_scrubbed": True,
        "current_price_denominator_basis": "BORSA_QUOTED_NOMINAL_UNITS_OUT",
        "legal_share_count_used_as_price_denominator": False,
        "derivation_rejections": derivation_rejections,
        "snapshot_rejections": snapshot_rejections,
        "archive_hashes": archive_hashes,
        "outputs": {path.name: _sha(path) for path in (
            valuation_path, previous_path, follow_path, m2_path,
        )},
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
