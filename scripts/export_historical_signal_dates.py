from __future__ import annotations

"""Export monthly first-trading-day skeleton without inventing cutoff times."""

import argparse
import json
import sys
from pathlib import Path

from src.analytics.historical_backtest_db import HistoricalBacktestDatabaseError
from src.analytics.historical_backtest_signal_dates import build_historical_signal_date_manifest
from src.app.cli import get_conn


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export monthly first observed index trading dates. cutoff_at and "
            "execution_at intentionally remain empty until an explicit V24-F policy is sourced."
        )
    )
    parser.add_argument("--start-month", default="2021-08")
    parser.add_argument("--end-month", default="2026-07")
    parser.add_argument("--expected-months", type=int, default=60)
    parser.add_argument("--index-code", default="XU100")
    parser.add_argument("--csv-out", required=True)
    parser.add_argument("--json-out")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    conn = None
    try:
        conn = get_conn()
        manifest = build_historical_signal_date_manifest(
            conn,
            start_month=args.start_month,
            end_month=args.end_month,
            expected_months=args.expected_months,
            index_code=args.index_code,
        )
        csv_path = Path(args.csv_out)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        manifest.to_frame().to_csv(csv_path, index=False)
        payload = manifest.to_dict()
        if args.json_out:
            json_path = Path(args.json_out)
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if manifest.status == "COMPLETE_SIGNAL_DATES_POLICY_UNRESOLVED" else 3
    except HistoricalBacktestDatabaseError as exc:
        print(
            json.dumps(
                {"status": "ERROR", "error_type": type(exc).__name__, "detail": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
