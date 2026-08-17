from __future__ import annotations

from hashlib import sha256
import os
from uuid import uuid4

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.historical_backtest_inventory import inventory_backtest_database


DSN = os.getenv("TOTAL_RASYO_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TOTAL_RASYO_TEST_DSN yok")


def _sha(suffix: str, label: str) -> str:
    return sha256(f"{suffix}:{label}".encode("utf-8")).hexdigest()


def _insert_result(cur, *, run_id: str, analysis_at: str, ticker: str) -> None:
    cur.execute(
        """
        INSERT INTO analytics.company_total_rasyo_result (
          analysis_at, ticker, routed_engine, engine_status, engine_reason,
          m2_score, m2_source, m2_source_at, m2_source_type, m2_missing,
          valuation_confidence, m1_score, m1_source_at, m1_missing,
          m3_score, m3_source_at, m3_missing,
          ek4_score, ek4_source_at, ek4_missing,
          ek1_score, ek1_source_at, ek1_missing,
          ek9_score, ek9_source_at, ek9_missing,
          module_source_type, good_count_ge8, good_count_missing,
          base_score, final_score, total_rasyo_100, veto_flag, decision,
          weights_profile, total_rasyo_status, rejection_reason,
          insufficiency_reason, missing_modules, data_confidence, diagnostics, run_id
        ) VALUES (
          %s::timestamptz, %s, 'BANK', 'OK', NULL,
          0.5, 'V24_REAL_DATA_INVENTORY_FIXTURE', %s::timestamptz,
          'SECTOR_ENGINE', false,
          1.0, 0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false,
          'ANALYTICS_MODULE_SCORES', 9, false,
          0.8, 0.8, 80.0, false, 'AL',
          'TOTAL_RASYO_SCORE_V1', 'OK', NULL, NULL, NULL, 1.0, '{}'::jsonb, %s
        )
        """,
        (
            analysis_at,
            ticker,
            analysis_at,
            analysis_at,
            analysis_at,
            analysis_at,
            analysis_at,
            analysis_at,
            run_id,
        ),
    )


def test_postgres_inventory_discovers_complete_two_month_candidate_without_writes():
    suffix = uuid4().hex[:8].upper()
    ticker = "I" + suffix[:6]
    index_code = "XI" + suffix[:6]
    wage_key = "WI_" + suffix
    cutoff_key = "CI_" + suffix
    run_id = "INV-" + suffix
    analysis_at = "2017-01-01T18:00:00Z"

    conn = psycopg2.connect(DSN)
    try:
        # This fixture must never leak into the shared CI database. Historical
        # tables are append-only, so test isolation is transaction rollback,
        # not DELETE/TRUNCATE cleanup.
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
                ) VALUES (%s,'2017-01-01','2017-03-01',true,%s,'XTEST','TEST',
                          'V24_INVENTORY_TEST','membership',%s,%s)
                """,
                (
                    ticker,
                    ticker + " TEST",
                    _sha(suffix, "membership-source"),
                    _sha(suffix, "membership-row"),
                ),
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
                ) VALUES (%s,'2017-01-01','2017-03-01',1000,
                          'V24_INVENTORY_TEST','wage',%s,%s)
                """,
                (
                    wage_key,
                    _sha(suffix, "wage-source"),
                    _sha(suffix, "wage-row"),
                ),
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
                    (
                        cutoff_key,
                        "2017-01-02",
                        "2017-01-01T19:00:00Z",
                        "2017-01-02T07:00:00Z",
                        "c1",
                        _sha(suffix, "cutoff-1-source"),
                        _sha(suffix, "cutoff-1-row"),
                    ),
                    (
                        cutoff_key,
                        "2017-02-01",
                        "2017-01-31T19:00:00Z",
                        "2017-02-01T07:00:00Z",
                        "c2",
                        _sha(suffix, "cutoff-2-source"),
                        _sha(suffix, "cutoff-2-row"),
                    ),
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
                  %s, %s::timestamptz, %s, %s::timestamptz, %s::timestamptz,
                  'COMPLETE', 'OK', 'FULL_UNIVERSE', 1, 'OVERWRITE',
                  0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                  'TOTAL_RASYO_SCORE_V1', '{}'::jsonb
                )
                """,
                (
                    run_id,
                    analysis_at,
                    _sha(suffix, "run-payload"),
                    analysis_at,
                    analysis_at,
                ),
            )
            _insert_result(cur, run_id=run_id, analysis_at=analysis_at, ticker=ticker)

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
        conn.rollback()
        conn.close()
