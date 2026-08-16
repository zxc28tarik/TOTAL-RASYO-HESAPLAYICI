from __future__ import annotations

import os
from uuid import uuid4

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DSN = os.getenv("TOTAL_RASYO_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TOTAL_RASYO_TEST_DSN yok")


def _id(prefix: str) -> str:
    return prefix + uuid4().hex[:12].upper()


def test_database_rejects_nan_or_non_positive_wage_even_without_python_ingest():
    conn = psycopg2.connect(DSN)
    try:
        for value in ("NaN", "0", "-1"):
            with pytest.raises(psycopg2.Error):
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO core.backtest_minimum_wage_schedule(
                              schedule_key, valid_from, valid_to, net_min_wage,
                              source, source_sha256, row_sha256
                            ) VALUES (%s,'2022-01-01',NULL,%s::numeric,'DIRECT_TEST',%s,%s)
                            """,
                            (_id("W"), value, "a"*64, "b"*64),
                        )
    finally:
        conn.close()


def test_database_rejects_cutoff_at_or_after_execution_even_without_python_ingest():
    conn = psycopg2.connect(DSN)
    try:
        for cutoff in ("2022-01-03T10:00:00+03:00", "2022-01-03T11:00:00+03:00"):
            with pytest.raises(psycopg2.Error):
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO analytics.backtest_signal_cutoff_schedule(
                              profile_key, signal_date, cutoff_at, execution_at,
                              source, source_sha256, row_sha256
                            ) VALUES (%s,'2022-01-03',%s::timestamptz,
                                      '2022-01-03T10:00:00+03:00'::timestamptz,
                                      'DIRECT_TEST',%s,%s)
                            """,
                            (_id("C"), cutoff, "c"*64, "d"*64),
                        )
    finally:
        conn.close()


def test_database_rejects_execution_date_mismatch_in_istanbul():
    conn = psycopg2.connect(DSN)
    try:
        with pytest.raises(psycopg2.Error):
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO analytics.backtest_signal_cutoff_schedule(
                          profile_key, signal_date, cutoff_at, execution_at,
                          source, source_sha256, row_sha256
                        ) VALUES (%s,'2022-01-03',
                                  '2022-01-02T20:00:00+03:00'::timestamptz,
                                  '2022-01-04T00:01:00+03:00'::timestamptz,
                                  'DIRECT_TEST',%s,%s)
                        """,
                        (_id("C"), "e"*64, "f"*64),
                    )
    finally:
        conn.close()
