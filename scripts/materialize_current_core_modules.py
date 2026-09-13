from __future__ import annotations

"""Materialize current M1/Ek1 with the production CORE/RSC replay machinery."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.experimental_core_module_materializer import build_core_modules
from scripts.materialize_current_nonfin_valuation import ARCHIVE_NAMES, _mapped_reports


CONTRACT = "CURRENT_CORE_M1_EK1_V1"
DEFAULT_ARCHIVES = ROOT / "private/reconstructed_kap_archives"
DEFAULT_ROUTES = ROOT / "data/backtest_sources/m3_source_package/sector_routes.csv.gz"
DEFAULT_ROUTE_MANIFEST = ROOT / "data/backtest_sources/m3_source_package/manifest.json"
DEFAULT_OUTPUT = ROOT / "data/live/current_core_modules_v1"
NONFIN_INDICES = frozenset({"XUSIN", "XUHIZ", "XUTEK"})


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def materialize(*, archive_dir: Path, routes_path: Path, route_manifest_path: Path,
                output_dir: Path, analysis_at: datetime | None = None) -> dict:
    analysis = analysis_at or datetime.now(timezone.utc)
    if analysis.tzinfo is None or analysis.utcoffset() is None:
        raise ValueError("analysis_at timezone-aware olmali")
    routes = pd.read_csv(routes_path, dtype=str)
    routes["valid_from"] = pd.to_datetime(routes["valid_from"])
    routes["valid_to"] = pd.to_datetime(routes["valid_to"], errors="coerce")
    day = pd.Timestamp(analysis.date())
    active = routes.loc[
        routes.sector_index_code.isin(NONFIN_INDICES)
        & routes.valid_from.le(day)
        & (routes.valid_to.isna() | routes.valid_to.gt(day))
    ].copy()
    if active.duplicated("ticker").any():
        raise ValueError("current NONFIN route ambiguous")

    manifest = json.loads(route_manifest_path.read_text(encoding="utf-8"))
    route_source = next(
        row for row in manifest["raw_sources"]
        if row["source_id"] == "KAP_SEKTORLER_2026_08_24"
    )
    retrieved_at = datetime.fromisoformat(route_source["retrieved_at"].replace("Z", "+00:00"))
    if retrieved_at > analysis:
        raise ValueError("current economic route source is after analysis cutoff")
    raw_route_path = ROOT / route_source["raw_path"]
    if _sha(raw_route_path) != route_source["raw_sha256"]:
        raise ValueError("current economic route source hash mismatch")

    tickers = sorted(set(active.ticker.astype(str).str.upper()))
    reports, archive_hashes = _mapped_reports(archive_dir, set(tickers))
    report_by_entity = {row["report"]["source_entity_code"] for row in reports}
    result = build_core_modules(reports, analysis, tickers, family_routes=routes)

    module_rows: list[dict] = []
    rejection_rows: list[dict] = []
    for ticker in tickers:
        diag = result["per_ticker"][ticker]
        m1 = diag.get("m1") or []
        ek1 = diag.get("ek1") or []
        if m1 and ek1:
            module_rows.append({
                "ticker": ticker,
                "m1": m1[-1]["m1"],
                "ek1": ek1[-1]["ek1"],
                "good_count_ge8": m1[-1]["good_count_ge8"],
                "period_end": str(m1[-1]["period_end"]),
                "analysis_at": analysis.isoformat(),
                "route_source_id": active.loc[active.ticker.eq(ticker), "source_id"].iloc[0],
            })
        else:
            rejection_rows.append({
                "ticker": ticker,
                "reasons": diag.get("reasons") or ["CURRENT_CORE_MODULE_NOT_MATERIALIZED"],
                "report_entity_present": ticker in report_by_entity,
            })

    output_dir.mkdir(parents=True, exist_ok=True)
    modules_path = output_dir / "modules.jsonl"
    rejections_path = output_dir / "rejections.jsonl"
    modules_path.write_text("".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in module_rows
    ), encoding="utf-8", newline="\n")
    rejections_path.write_text("".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rejection_rows
    ), encoding="utf-8", newline="\n")
    reason_counts = Counter(reason for row in rejection_rows for reason in row["reasons"])
    receipt = {
        "contract": CONTRACT,
        "analysis_at": analysis.isoformat(),
        "mode": "CURRENT_ONLY_NO_HISTORICAL_FINANCIAL_FALLBACK",
        "economic_family_contract": "CURRENT_OPEN_ENDED_KAP_SECTOR_ROUTE_NONFIN_V1",
        "economic_route_retrieved_at": retrieved_at.isoformat(),
        "economic_route_raw_sha256": route_source["raw_sha256"],
        "route_count": len(tickers),
        "financial_report_count": len(reports),
        "m1_valid_count": len(module_rows),
        "ek1_valid_count": len(module_rows),
        "rejection_count": len(rejection_rows),
        "top_rejection_reasons": dict(reason_counts.most_common(20)),
        "production_engines": [
            "compute_company_core_ratios_from_frame", "run_historical_pit_rsc_replay",
            "run_historical_pit_m1_replay", "run_historical_pit_ek1_replay",
        ],
        "neutral_score_injection": False,
        "archive_hashes": archive_hashes,
        "outputs": {
            modules_path.name: _sha(modules_path), rejections_path.name: _sha(rejections_path),
        },
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVES)
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--route-manifest", type=Path, default=DEFAULT_ROUTE_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(materialize(
        archive_dir=args.archive_dir, routes_path=args.routes,
        route_manifest_path=args.route_manifest, output_dir=args.output_dir,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
