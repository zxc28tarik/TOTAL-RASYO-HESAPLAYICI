from __future__ import annotations

"""Load an audited historical BIST membership CSV into append-only storage."""

import argparse

from src.app.cli import get_conn
from src.ingest.historical_universe import (
    load_historical_universe_csv,
    persist_historical_universe_records,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    rows = load_historical_universe_csv(args.file)
    conn = get_conn()
    try:
        inserted = persist_historical_universe_records(conn, rows)
    finally:
        conn.close()
    print(f"Loaded {inserted} new historical universe interval rows ({len(rows)} validated).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
