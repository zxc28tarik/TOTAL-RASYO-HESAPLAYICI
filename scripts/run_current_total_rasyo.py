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


def run(*, run_date: date, offline: bool, share_workers: int = 4) -> dict:
    # Universe first: a live fetch, or a hash/row-count verified cached snapshot.
    initial = audit(output_dir=DEFAULT_OUTPUT, archive=DEFAULT_ARCHIVE,
                    refresh_universe=not offline)
    universe_path = DEFAULT_OUTPUT / "universe.csv"

    share_receipt = capture_shares(
        universe_path=universe_path, output_dir=SHARE_OUTPUT,
        workers=share_workers, no_fetch=offline,
    )
    if offline:
        price_receipt = _require_current_artifact(
            PRICE_OUTPUT / "receipt.json", contract="CURRENT_YAHOO_RAW_CLOSE_V1",
            run_date=run_date, date_key="cutoff_date",
        )
    else:
        price_receipt = capture_prices(
            universe_path=universe_path, output_dir=PRICE_OUTPUT, cutoff=run_date,
        )

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
    final = audit(output_dir=DEFAULT_OUTPUT, archive=DEFAULT_ARCHIVE,
                  refresh_universe=False)

    receipt = {
        "contract": CONTRACT,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "run_date": run_date.isoformat(),
        "mode": "OFFLINE_HASH_VERIFIED" if offline else "LIVE_REFRESH_WITH_FAIL_CLOSED_GATES",
        "historical_pit_mixed_into_current": False,
        "neutral_score_injection": False,
        "stage_counts": {
            "universe": final["universe_count"],
            "explicit_share_basis": share_receipt["usable_single_ticker_share_basis_count"],
            "raw_close": price_receipt["raw_close_count"],
            "market_cap": basis_receipt["materialized_market_cap_count"],
            "nonfin_valuation": valuation_receipt["production_valuation_count"],
            "usable_nonfin_valuation": valuation_receipt["usable_valuation_count"],
            "m2": valuation_receipt["m2_materialized_count"],
            "total_rasyo": final["total_rasyo_count"],
            "ranking": final["ranking_count"],
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
    args = parser.parse_args()
    print(json.dumps(run(
        run_date=args.date, offline=args.offline, share_workers=args.share_workers,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
