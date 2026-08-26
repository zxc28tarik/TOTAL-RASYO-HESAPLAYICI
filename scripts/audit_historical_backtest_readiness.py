from __future__ import annotations

"""Operator CLI for the V24-G report-only historical backtest readiness audit."""

import argparse
import json
import sys

from src.analytics.historical_backtest_db import HistoricalBacktestDatabaseError
from src.analytics.historical_backtest_readiness_cli import (
    TECHNICAL_ERROR_EXIT_CODE,
    render_readiness_json,
    run_readiness_command,
)
from src.app.cli import get_conn


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the locked historical backtest inputs without repairing data "
            "or starting the backtest."
        )
    )
    parser.add_argument("--wage-schedule-key", required=True)
    parser.add_argument("--cutoff-profile-key", required=True)
    parser.add_argument("--start-month", default="2021-08")
    parser.add_argument("--end-month", default="2026-07")
    parser.add_argument("--index-code", default="XU100")
    parser.add_argument("--expected-months", type=int, default=60)
    parser.add_argument("--json-out")
    parser.add_argument("--findings-csv")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    conn = None
    try:
        conn = get_conn()
        result = run_readiness_command(
            conn,
            wage_schedule_key=args.wage_schedule_key,
            cutoff_profile_key=args.cutoff_profile_key,
            start_month=args.start_month,
            end_month=args.end_month,
            index_code=args.index_code,
            expected_months=args.expected_months,
            json_out=args.json_out,
            findings_csv=args.findings_csv,
        )
        print(render_readiness_json(result.snapshot))
        return result.exit_code
    except HistoricalBacktestDatabaseError as exc:
        print(
            json.dumps(
                {"status": "ERROR", "error_type": type(exc).__name__, "detail": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return TECHNICAL_ERROR_EXIT_CODE
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
