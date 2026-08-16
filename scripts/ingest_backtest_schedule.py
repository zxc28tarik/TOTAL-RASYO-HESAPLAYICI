from __future__ import annotations

"""Load audited historical backtest schedule CSVs into append-only storage."""

import argparse

from src.app.cli import get_conn
from src.ingest.historical_backtest_schedules import (
    load_cutoff_schedule_csv,
    load_wage_schedule_csv,
    persist_cutoff_schedule_records,
    persist_wage_schedule_records,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load audited wage or PIT cutoff schedule CSV rows; no historical defaults are inferred."
    )
    parser.add_argument("--kind", required=True, choices=("wage", "cutoff"))
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    conn = get_conn()
    try:
        if args.kind == "wage":
            rows = load_wage_schedule_csv(args.file)
            inserted = persist_wage_schedule_records(conn, rows)
        else:
            rows = load_cutoff_schedule_csv(args.file)
            inserted = persist_cutoff_schedule_records(conn, rows)
    finally:
        conn.close()

    print(f"Loaded {inserted} new {args.kind} schedule rows ({len(rows)} validated).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
