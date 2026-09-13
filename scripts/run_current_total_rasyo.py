from __future__ import annotations

"""One-command, fail-closed current Total Rasyo evidence pipeline."""

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_live_total_rasyo_inputs import DEFAULT_ARCHIVE, DEFAULT_OUTPUT, audit
from scripts.capture_current_kap_share_basis import DEFAULT_OUTPUT as SHARE_OUTPUT, capture as capture_shares
from scripts.capture_current_yahoo_raw_close import DEFAULT_OUTPUT as PRICE_OUTPUT, capture as capture_prices
from scripts.materialize_current_nonfin_valuation import DEFAULT_OUTPUT as VALUATION_OUTPUT, materialize as materialize_valuation
from scripts.materialize_current_price_level_basis import DEFAULT_OUTPUT as BASIS_OUTPUT, materialize as materialize_basis
from scripts.materialize_current_core_modules import DEFAULT_OUTPUT as CORE_OUTPUT, materialize as materialize_core
from scripts.capture_borsa_quote_unit_contract import OUTPUT as QUOTE_OUTPUT, capture as capture_quote_unit
from scripts.materialize_current_market_modules import OUTPUT as MARKET_OUTPUT, materialize as materialize_market
from scripts.materialize_current_total_rasyo import OUTPUT as TOTAL_OUTPUT, materialize as materialize_total


CONTRACT = "CURRENT_TOTAL_RASYO_RUN_V1"
RUN_RECEIPT = ROOT / "data/live/current_total_rasyo_run_v1/receipt.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_current_artifact(path: Path, *, contract: str, run_date: date,
                              date_key: str | None = None) -> dict:
    if not path.exists():
        raise ValueError(f"required offline artifact missing: {path}")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("contract") != contract:
        raise ValueError(f"unexpected artifact contract: {path}")
    if date_key and receipt.get(date_key) != run_date.isoformat():
        raise ValueError(f"stale artifact {date_key}: {path}")
    return receipt


def run(*, run_date: date, offline: bool, share_workers: int = 4,
        reuse_current_core: bool = False, reuse_derived: bool = False) -> dict:
    # Universe first: a live fetch, or a hash/row-count verified cached snapshot.
    initial = audit(output_dir=DEFAULT_OUTPUT, archive=DEFAULT_ARCHIVE,
                    refresh_universe=not offline)
    universe_path = DEFAULT_OUTPUT / "universe.csv"

    if offline:
        share_receipt = _require_current_artifact(
            SHARE_OUTPUT / "receipt.json", contract="CURRENT_KAP_EXPLICIT_SHARE_BASIS_V1",
            run_date=run_date,
        )
        price_receipt = _require_current_artifact(
            PRICE_OUTPUT / "receipt.json", contract="CURRENT_YAHOO_RAW_CLOSE_V1",
            run_date=run_date, date_key="cutoff_date",
        )
        quote_receipt = _require_current_artifact(
            QUOTE_OUTPUT / "receipt.json", contract="BORSA_PAY_PRICE_PER_1_TRY_NOMINAL_V1",
            run_date=run_date,
        )
    else:
        share_receipt = capture_shares(
            universe_path=universe_path, output_dir=SHARE_OUTPUT,
            workers=share_workers, no_fetch=False,
        )
        price_receipt = capture_prices(
            universe_path=universe_path, output_dir=PRICE_OUTPUT, cutoff=run_date,
        )
        quote_receipt = capture_quote_unit(QUOTE_OUTPUT)

    if reuse_derived:
        basis_receipt = _require_current_artifact(
            BASIS_OUTPUT / "receipt.json", contract="CURRENT_PRICE_LEVEL_BASIS_MATERIALIZATION_V1",
            run_date=run_date,
        )
        valuation_receipt = _require_current_artifact(
            VALUATION_OUTPUT / "receipt.json", contract="CURRENT_NONFIN_RELATIVE_VALUATION_V1",
            run_date=run_date,
        )
    else:
        basis_receipt = materialize_basis(
            share_dir=SHARE_OUTPUT, prices_path=PRICE_OUTPUT / "raw_close.csv.gz",
            output_dir=BASIS_OUTPUT,
        )
        valuation_receipt = materialize_valuation(
            archive_dir=ROOT / "private/reconstructed_kap_archives",
            basis_dir=BASIS_OUTPUT,
            routes_path=ROOT / "data/backtest_sources/m3_source_package/sector_routes.csv.gz",
            output_dir=VALUATION_OUTPUT,
        )
    if reuse_current_core:
        core_receipt = _require_current_artifact(
            CORE_OUTPUT / "receipt.json", contract="CURRENT_CORE_M1_EK1_V1", run_date=run_date,
        )
    else:
        core_receipt = materialize_core(
            archive_dir=ROOT / "private/reconstructed_kap_archives",
            routes_path=ROOT / "data/backtest_sources/m3_source_package/sector_routes.csv.gz",
            route_manifest_path=ROOT / "data/backtest_sources/m3_source_package/manifest.json",
            output_dir=CORE_OUTPUT,
        )
    if offline:
        market_receipt = _require_current_artifact(
            MARKET_OUTPUT / "receipt.json", contract="CURRENT_MARKET_M3_EK4_EK9_V1",
            run_date=run_date, date_key="cutoff_date",
        )
    else:
        market_receipt = materialize_market(cutoff=run_date, output_dir=MARKET_OUTPUT)
    total_receipt = materialize_total(TOTAL_OUTPUT)
    final = audit(output_dir=DEFAULT_OUTPUT, archive=DEFAULT_ARCHIVE,
                  refresh_universe=False)

    receipt = {
        "contract": CONTRACT,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "run_date": run_date.isoformat(),
        "mode": "OFFLINE_HASH_VERIFIED" if offline else "LIVE_REFRESH_WITH_FAIL_CLOSED_GATES",
        "historical_pit_mixed_into_current": False,
        "neutral_score_injection": False,
        "input_capture_modes": {
            "universe": initial["universe_source_mode"],
            "shares": share_receipt.get("capture_scope"),
            "prices": price_receipt["contract"],
            "quote_unit": quote_receipt["contract"],
        },
        "online_issuer_attempt_count_this_capture": share_receipt.get("issuer_attempted_count", 0),
        "issuer_capture_success_count": share_receipt.get("issuer_fetch_success_count", 0),
        "issuer_not_attempted_count": share_receipt.get("issuer_not_attempted_count"),
        "stage_counts": {
            "universe": final["universe_count"],
            "explicit_share_basis": share_receipt["usable_single_ticker_share_basis_count"],
            "raw_close": price_receipt["raw_close_count"],
            "market_cap": basis_receipt["materialized_market_cap_count"],
            "nonfin_valuation": valuation_receipt["production_valuation_count"],
            "usable_nonfin_valuation": valuation_receipt["usable_valuation_count"],
            "m2": valuation_receipt["m2_materialized_count"],
            "m1": core_receipt["m1_valid_count"],
            "ek1": core_receipt["ek1_valid_count"],
            "m3": market_receipt["m3_valid_count"],
            "ek4": market_receipt["ek4_valid_count"],
            "ek9": market_receipt["ek9_valid_count"],
            "at_least_4_modules": final["at_least_4_modules_count"],
            "at_least_5_modules": final["at_least_5_modules_count"],
            "total_rasyo": total_receipt["total_valid_count"],
            "ranking": total_receipt["ranking_count"],
        },
        "m2_blocker": valuation_receipt["m2_blocker"],
        "database_available": final["database_available"],
        "receipts": {},
    }
    receipt_paths = {
        "readiness": DEFAULT_OUTPUT / "receipt.json",
        "shares": SHARE_OUTPUT / "receipt.json",
        "prices": PRICE_OUTPUT / "receipt.json",
        "price_level_basis": BASIS_OUTPUT / "receipt.json",
        "nonfin_valuation": VALUATION_OUTPUT / "receipt.json",
        "core_modules": CORE_OUTPUT / "receipt.json",
        "quote_unit": QUOTE_OUTPUT / "receipt.json",
        "market_modules": MARKET_OUTPUT / "receipt.json",
        "total_scores": TOTAL_OUTPUT / "receipt.json",
    }
    for name, path in receipt_paths.items():
        receipt["receipts"][name] = {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": _sha(path),
        }
    RUN_RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RUN_RECEIPT.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument(
        "--offline", action="store_true",
        help="do not call KAP/Yahoo; require same-date price and hash-verified universe artifacts",
    )
    parser.add_argument("--share-workers", type=int, default=4)
    parser.add_argument("--reuse-current-core", action="store_true")
    parser.add_argument("--reuse-derived", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(
        run_date=args.date, offline=args.offline, share_workers=args.share_workers,
        reuse_current_core=args.reuse_current_core,
        reuse_derived=args.reuse_derived,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
