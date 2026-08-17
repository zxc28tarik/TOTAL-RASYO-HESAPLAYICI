from __future__ import annotations

"""Report-only guard for historical raw-price corporate-action continuity."""

import argparse
import json
import sys
from pathlib import Path

from src.analytics.historical_backtest_corporate_actions import (
    audit_corporate_action_price_continuity,
)
from src.analytics.historical_backtest_db import HistoricalBacktestDatabaseError
from src.app.cli import get_conn


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Detect monthly adj_close/close factor changes before claiming real "
            "performance from raw OPEN/CLOSE. No data is repaired or mutated."
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
        result = audit_corporate_action_price_continuity(
            conn,
            start_month=args.start_month,
            end_month=args.end_month,
            expected_months=args.expected_months,
            index_code=args.index_code,
        )
        rendered = json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        if args.json_out:
            path = Path(args.json_out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0 if result.status == "CLEAR_NO_ADJUSTMENT_CHANGE" else 3
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
