from __future__ import annotations

import os

import pandas as pd
import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.historical_backtest_db import run_monthly_backtest_from_database


DSN = os.getenv("TOTAL_RASYO_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TOTAL_RASYO_TEST_DSN yok")


def _insert_result(cur, *, run_id: str, analysis_at: str, ticker: str,
                   score: float, decision: str) -> None:
    cur.execute(
        """
        INSERT INTO analytics.company_total_rasyo_result (
          analysis_at, ticker, routed_engine, engine_status, engine_reason,
          m2_score, m2_source, m2_source_at, m2_source_type, m2_missing,
          valuation_confidence,
          m1_score, m1_source_at, m1_missing,
          m3_score, m3_source_at, m3_missing,
          ek4_score, ek4_source_at, ek4_missing,
          ek1_score, ek1_source_at, ek1_missing,
          ek9_score, ek9_source_at, ek9_missing,
          module_source_type, good_count_ge8, good_count_missing,
          base_score, final_score, total_rasyo_100, veto_flag, decision,
          weights_profile, total_rasyo_status, rejection_reason,
          insufficiency_reason, missing_modules, data_confidence,
          diagnostics, run_id
        ) VALUES (
          %s::timestamptz, %s, 'BANK', 'OK', NULL,
          0.5, 'V24E_FIXTURE', %s::timestamptz, 'SECTOR_ENGINE', false,
          1.0,
          0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false,
          'ANALYTICS_MODULE_SCORES', 9, false,
          %s, %s, %s, false, %s,
          'TOTAL_RASYO_SCORE_V1', 'OK', NULL,
          NULL, NULL, 1.0,
          '{}'::jsonb, %s
        )
        """,
        (
            analysis_at, ticker,
            analysis_at, analysis_at, analysis_at, analysis_at, analysis_at, analysis_at,
            score, score, score * 100.0, decision, run_id,
        ),
    )


def _insert_run(cur, *, run_id: str, analysis_at: str, scope: str,
                company_count: int, universe_count: int,
                status: str = "COMPLETE", persistence: str = "OK") -> None:
    successful = company_count if status in {"COMPLETE", "PARTIAL"} else 0
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
          %s, %s::timestamptz, %s,
          %s::timestamptz, %s::timestamptz,
          %s, %s, %s, %s, 'OVERWRITE',
          0, %s, %s, 0, 0, 0, 0,
          0, 0, 0, 0, 0, 0, 0,
          'TOTAL_RASYO_SCORE_V1', '{}'::jsonb
        )
        """,
        (
            run_id, analysis_at, ("a" if run_id.endswith("1") else "b" if run_id.endswith("2") else "c") * 64,
            analysis_at, analysis_at, status, persistence, scope, universe_count,
            company_count, successful,
        ),
    )


def test_database_bridge_uses_only_authoritative_full_universe_runs_and_executes_v24b():
    conn = psycopg2.connect(DSN)
    try:
        # CI uses a fresh PostgreSQL database.  All writes stay inside this
        # transaction and are rolled back in finally; no cleanup relies on
        # DELETE/TRUNCATE because historical membership is append-only.
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO core.index_prices_daily(index_code, trade_date, open, close) VALUES (%s,%s,%s,%s)",
                [
                    ("XU100", "2022-01-03", 100, 101),
                    ("XU100", "2022-02-02", 110, 111),
                ],
            )
            cur.executemany(
                """
                INSERT INTO core.universe_membership_history(
                  ticker, valid_from, valid_to, is_tradable, company_name,
                  sector_index_code, sector_code, source, source_ref,
                  source_sha256, row_sha256
                ) VALUES (%s,%s,NULL,true,%s,'XBANK','BANK','V24E_TEST',%s,%s,%s)
                """,
                [
                    ("V24EAAA", "2020-01-01", "AAA TEST", "fixture-a", "d"*64, "e"*64),
                    ("V24EBBB", "2020-01-01", "BBB TEST", "fixture-b", "f"*64, "1"*64),
                ],
            )
            cur.executemany(
                "INSERT INTO core.prices_daily(ticker, trade_date, open, close) VALUES (%s,%s,%s,%s)",
                [
                    ("V24EAAA", "2022-01-03", 10, 11),
                    ("V24EBBB", "2022-01-03", 20, 21),
                    ("V24EAAA", "2022-02-02", 12, 13),
                    ("V24EBBB", "2022-02-02", 22, 23),
                ],
            )
            _insert_run(cur, run_id="V24E-FULL-1", analysis_at="2022-01-02T19:00:00Z", scope="FULL_UNIVERSE", company_count=2, universe_count=2)
            _insert_result(cur, run_id="V24E-FULL-1", analysis_at="2022-01-02T19:00:00Z", ticker="V24EAAA", score=0.9, decision="AL")
            _insert_result(cur, run_id="V24E-FULL-1", analysis_at="2022-01-02T19:00:00Z", ticker="V24EBBB", score=0.4, decision="UZAK")

            _insert_run(cur, run_id="V24E-FULL-2", analysis_at="2022-02-01T19:00:00Z", scope="FULL_UNIVERSE", company_count=2, universe_count=2)
            _insert_result(cur, run_id="V24E-FULL-2", analysis_at="2022-02-01T19:00:00Z", ticker="V24EAAA", score=0.7, decision="IZLE")
            _insert_result(cur, run_id="V24E-FULL-2", analysis_at="2022-02-01T19:00:00Z", ticker="V24EBBB", score=0.8, decision="AL")

            # Later but TARGETED: must never replace the authoritative full run.
            _insert_run(cur, run_id="V24E-TARGET-X", analysis_at="2022-02-01T19:30:00Z", scope="TARGETED", company_count=1, universe_count=2)
            _insert_result(cur, run_id="V24E-TARGET-X", analysis_at="2022-02-01T19:30:00Z", ticker="V24EAAA", score=0.1, decision="UZAK")

        wages = pd.DataFrame([
            {"valid_from": "2020-01-01", "valid_to": None, "net_min_wage": 1000},
        ])
        cutoffs = pd.DataFrame([
            {"signal_date": "2022-01-03", "cutoff_at": "2022-01-02T20:00:00Z"},
            {"signal_date": "2022-02-02", "cutoff_at": "2022-02-01T20:00:00Z"},
        ])
        run = run_monthly_backtest_from_database(
            conn,
            minimum_wage_schedule=wages,
            cutoffs=cutoffs,
            start_month="2022-01",
            end_month="2022-02",
            expected_months=2,
        )

        feb = run.inputs.signals[run.inputs.signals["signal_date"] == pd.Timestamp("2022-02-02")]
        assert dict(zip(feb["ticker"], feb["decision"])) == {"V24EAAA": "IZLE", "V24EBBB": "AL"}
        assert set(feb["analysis_at"].astype(str)) == {"2022-02-01 19:00:00+00:00"}
        assert list(run.inputs.contributions["contribution"]) == [2000.0, 2000.0]
        assert list(run.trades["side"]) == ["BUY", "BUY"]
        assert list(run.trades["ticker"]) == ["V24EAAA", "V24EBBB"]
        assert len(run.monthly) == 2
        assert len(run.benchmark) == 2
        assert float(run.monthly.iloc[-1]["cash"]) == pytest.approx(20.0)
    finally:
        conn.rollback()
        conn.close()
