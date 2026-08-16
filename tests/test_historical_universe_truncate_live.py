from __future__ import annotations

import os

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DSN = os.getenv("TOTAL_RASYO_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TOTAL_RASYO_TEST_DSN yok")


def test_historical_universe_rejects_truncate():
    conn = psycopg2.connect(DSN)
    try:
        with pytest.raises(psycopg2.Error, match="TRUNCATE denendi"):
            with conn:
                with conn.cursor() as cur:
                    cur.execute("TRUNCATE core.universe_membership_history")
    finally:
        conn.close()
