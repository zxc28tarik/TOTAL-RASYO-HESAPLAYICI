from __future__ import annotations

"""Report-only inventory for the real 2021-08 .. 2026-07 backtest inputs."""

import argparse
import json
import sys
from pathlib import Path

from src.analytics.historical_backtest_db import HistoricalBacktestDatabaseError
from src.analytics.historical_backtest_inventory import inventory_backtest_database
from src.app.cli import get_conn


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Discover historical backtest input coverage and candidate audited "
            "schedule/profile keys without mutating data or starting a backtest."
        )
    )
    parser.add_argument("--start-month", default="2021-08")
    parser.add_argument("--end-month", default="2026-07")
    parser.add_argument("--expected-months", type=int, default=60)
    parser.add_argument("--index-code", default="XU100")
    parser.add_argument("--json-out")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    conn = None
    try:
        conn = get_conn()
        inventory = inventory_backtest_database(
            conn,
            start_month=args.start_month,
            end_month=args.end_month,
            expected_months=args.expected_months,
            index_code=args.index_code,
        )
        payload = inventory.to_dict()
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        if args.json_out:
            path = Path(args.json_out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0
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
