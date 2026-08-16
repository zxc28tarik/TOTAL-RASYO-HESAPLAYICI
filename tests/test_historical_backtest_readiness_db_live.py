from __future__ import annotations

import os
from uuid import uuid4

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.historical_backtest_readiness_db import (
    audit_backtest_readiness_from_database,
)


DSN = os.getenv("TOTAL_RASYO_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TOTAL_RASYO_TEST_DSN yok")


def _insert_run(cur, *, run_id: str, analysis_at: str) -> None:
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
        (run_id, analysis_at, "1" * 64, analysis_at, analysis_at),
    )


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
          0.5, 'V24G_DB_FIXTURE', %s::timestamptz, 'SECTOR_ENGINE', false,
          1.0, 0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false, 0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false, 0.5, %s::timestamptz, false,
          'ANALYTICS_MODULE_SCORES', 9, false,
          0.8, 0.8, 80.0, false, 'AL',
          'TOTAL_RASYO_SCORE_V1', 'OK', NULL, NULL, NULL, 1.0, '{}'::jsonb, %s
        )
        """,
        (
            analysis_at, ticker, analysis_at, analysis_at, analysis_at,
            analysis_at, analysis_at, analysis_at, run_id,
        ),
    )


def test_postgres_snapshot_feeds_readiness_and_missing_price_remains_report_only():
    suffix = uuid4().hex[:8].upper()
    ticker = "G" + suffix[:6]
    index_code = "XG" + suffix[:6]
    wage_key = "WG_" + suffix
    cutoff_key = "CG_" + suffix
    run1 = "V24G-" + suffix + "-1"
    run2 = "V24G-" + suffix + "-2"

    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO core.index_prices_daily(index_code, trade_date, open, close) VALUES (%s,%s,%s,%s)",
                [
                    (index_code, "2016-01-04", 100, 101),
                    (index_code, "2016-02-01", 110, 111),
                ],
            )
            cur.execute(
                """
                INSERT INTO core.universe_membership_history(
                  ticker, valid_from, valid_to, is_tradable, company_name,
                  sector_index_code, sector_code, source, source_ref,
                  source_sha256, row_sha256
                ) VALUES (%s,'2015-01-01',NULL,true,%s,'XTEST','TEST',
                          'V24G_DB_TEST','membership',%s,%s)
                """,
                (ticker, ticker + " TEST", "2" * 64, "3" * 64),
            )
            cur.executemany(
                "INSERT INTO core.prices_daily(ticker, trade_date, open, close) VALUES (%s,%s,%s,%s)",
                [
                    (ticker, "2016-01-04", 10, 11),
                    (ticker, "2016-02-01", 12, 13),
                ],
            )
            cur.execute(
                """
                INSERT INTO core.backtest_minimum_wage_schedule(
                  schedule_key, valid_from, valid_to, net_min_wage,
                  source, source_ref, source_sha256, row_sha256
                ) VALUES (%s,'2015-01-01',NULL,1000,'V24G_DB_TEST','wage',%s,%s)
                """,
                (wage_key, "4" * 64, "5" * 64),
            )
            cur.executemany(
                """
                INSERT INTO analytics.backtest_signal_cutoff_schedule(
                  profile_key, signal_date, cutoff_at, execution_at,
                  source, source_ref, source_sha256, row_sha256
                ) VALUES (%s,%s,%s::timestamptz,%s::timestamptz,
                          'V24G_DB_TEST',%s,%s,%s)
                """,
                [
                    (
                        cutoff_key, "2016-01-04",
                        "2016-01-03T20:00:00+03:00", "2016-01-04T10:00:00+03:00",
                        "jan", "6" * 64, "7" * 64,
                    ),
                    (
                        cutoff_key, "2016-02-01",
                        "2016-01-31T20:00:00+03:00", "2016-02-01T10:00:00+03:00",
                        "feb", "8" * 64, "9" * 64,
                    ),
                ],
            )
            _insert_run(cur, run_id=run1, analysis_at="2016-01-03T15:00:00Z")
            _insert_run(cur, run_id=run2, analysis_at="2016-01-31T15:00:00Z")
            _insert_result(cur, run_id=run1, analysis_at="2016-01-03T15:00:00Z", ticker=ticker)
            _insert_result(cur, run_id=run2, analysis_at="2016-01-31T15:00:00Z", ticker=ticker)

        snapshot = audit_backtest_readiness_from_database(
            conn,
            wage_schedule_key=wage_key,
            cutoff_profile_key=cutoff_key,
            start_month="2016-01",
            end_month="2016-02",
            index_code=index_code,
            expected_months=2,
        )
        assert snapshot.report.ready is True
        assert snapshot.report.checked_months == 2
        assert snapshot.report.findings.empty
        assert len(snapshot.index_prices) == 2
        assert len(snapshot.membership) == 1
        assert len(snapshot.prices_daily) == 2
        assert len(snapshot.wages) == 1
        assert len(snapshot.cutoffs) == 2
        assert len(snapshot.run_registry) == 2
        assert len(snapshot.total_rasyo_results) == 2

        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM core.prices_daily WHERE ticker=%s AND trade_date='2016-02-01'",
                (ticker,),
            )

        broken = audit_backtest_readiness_from_database(
            conn,
            wage_schedule_key=wage_key,
            cutoff_profile_key=cutoff_key,
            start_month="2016-01",
            end_month="2016-02",
            index_code=index_code,
            expected_months=2,
        )
        hit = broken.report.findings[
            (broken.report.findings["month"] == "2016-02")
            & (broken.report.findings["category"] == "PRICE")
        ]
        assert broken.report.ready is False
        assert broken.report.checked_months == 2
        assert "EXACT_DAY_COVERAGE" in set(hit["code"])
    finally:
        conn.rollback()
        conn.close()
