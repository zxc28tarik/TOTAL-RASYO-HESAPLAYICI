from __future__ import annotations

import os
from uuid import uuid4

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.historical_backtest_inventory import inventory_backtest_database


DSN = os.getenv("TOTAL_RASYO_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TOTAL_RASYO_TEST_DSN yok")


def test_postgres_inventory_discovers_complete_two_month_candidate_without_writes():
    suffix = uuid4().hex[:8].upper()
    ticker = "I" + suffix[:6]
    index_code = "XI" + suffix[:6]
    wage_key = "WI_" + suffix
    cutoff_key = "CI_" + suffix
    run_id = "INV-" + suffix

    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO core.index_prices_daily(index_code, trade_date, open, close) VALUES (%s,%s,%s,%s)",
                [
                    (index_code, "2017-01-02", 100, 101),
                    (index_code, "2017-02-01", 110, 111),
                ],
            )
            cur.execute(
                """
                INSERT INTO core.universe_membership_history(
                  ticker, valid_from, valid_to, is_tradable, company_name,
                  sector_index_code, sector_code, source, source_ref,
                  source_sha256, row_sha256
                ) VALUES (%s,'2016-01-01','2017-03-01',true,%s,'XTEST','TEST',
                          'V24_INVENTORY_TEST','membership',%s,%s)
                """,
                (ticker, ticker + " TEST", "1" * 64, "2" * 64),
            )
            cur.executemany(
                "INSERT INTO core.prices_daily(ticker, trade_date, open, close) VALUES (%s,%s,%s,%s)",
                [
                    (ticker, "2017-01-02", 10, 11),
                    (ticker, "2017-02-01", 12, 13),
                ],
            )
            cur.execute(
                """
                INSERT INTO core.backtest_minimum_wage_schedule(
                  schedule_key, valid_from, valid_to, net_min_wage,
                  source, source_ref, source_sha256, row_sha256
                ) VALUES (%s,'2016-01-01','2017-03-01',1000,
                          'V24_INVENTORY_TEST','wage',%s,%s)
                """,
                (wage_key, "3" * 64, "4" * 64),
            )
            cur.executemany(
                """
                INSERT INTO analytics.backtest_signal_cutoff_schedule(
                  profile_key, signal_date, cutoff_at, execution_at,
                  source, source_ref, source_sha256, row_sha256
                ) VALUES (%s,%s,%s::timestamptz,%s::timestamptz,
                          'V24_INVENTORY_TEST',%s,%s,%s)
                """,
                [
                    (cutoff_key, "2017-01-02", "2017-01-01T19:00:00Z", "2017-01-02T07:00:00Z", "c1", "5" * 64, "6" * 64),
                    (cutoff_key, "2017-02-01", "2017-01-31T19:00:00Z", "2017-02-01T07:00:00Z", "c2", "7" * 64, "8" * 64),
                ],
            )
            cur.execute(
                """
                INSERT INTO analytics.total_rasyo_run (
                  run_id, analysis_at, payload_sha256, started_at, finished_at,
                  overall_status, persistence_status, run_scope, universe_company_count,
                  not_run_policy, engine_error_count, company_count,
                  successful_company_count, insufficient_data_count,
                  engine_failed_company_count, not_run_company_count,
                  routing_conflict_count, missing_m1_count, missing_m2_count,
                  missing_m3_count, missing_ek4_count, missing_ek1_count,
                  missing_ek9_count, missing_good_count, weights_profile, diagnostics
                ) VALUES (
                  %s, '2017-01-01T18:00:00Z'::timestamptz, %s,
                  '2017-01-01T18:00:00Z'::timestamptz, '2017-01-01T18:00:00Z'::timestamptz,
                  'COMPLETE', 'OK', 'FULL_UNIVERSE', 1, 'OVERWRITE',
                  0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                  'TOTAL_RASYO_SCORE_V1', '{}'::jsonb
                )
                """,
                (run_id, "9" * 64),
            )
        conn.commit()

        result = inventory_backtest_database(
            conn,
            start_month="2017-01",
            end_month="2017-02",
            expected_months=2,
            index_code=index_code,
        )

        assert result.status == "CANDIDATE_READY_FOR_V24G"
        assert result.hard_blockers == ()
        assert result.candidate_wage_schedule_keys == (wage_key,)
        assert result.candidate_cutoff_profile_keys == (cutoff_key,)
        assert result.prices["valid_execution_ticker_pairs"] == 2
        assert result.total_rasyo["authority_run_count_before_window_end"] >= 1
    finally:
        conn.close()
