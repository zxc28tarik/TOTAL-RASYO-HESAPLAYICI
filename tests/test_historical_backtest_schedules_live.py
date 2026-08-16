from __future__ import annotations

import os
from uuid import uuid4

import pandas as pd
import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.historical_backtest_schedule_db import (
    fetch_registered_backtest_schedules,
    run_monthly_backtest_with_registered_schedules,
)
from src.ingest.historical_backtest_schedules import (
    CutoffScheduleRecord,
    HistoricalBacktestScheduleError,
    WageScheduleRecord,
    persist_cutoff_schedule_records,
    persist_wage_schedule_records,
)


DSN = os.getenv("TOTAL_RASYO_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TOTAL_RASYO_TEST_DSN yok")
SHA_A = "a" * 64
SHA_B = "b" * 64


def _suffix() -> str:
    return uuid4().hex[:10].upper()


def _wage(key: str, *, start="2022-01-01", end=None, value="1000", ref="w1"):
    return WageScheduleRecord.from_mapping({
        "schedule_key": key,
        "valid_from": start,
        "valid_to": end,
        "net_min_wage": value,
        "source": "V24F_TEST",
        "source_ref": ref,
        "source_sha256": SHA_A,
    })


def _cutoff(profile: str, signal_date: str, cutoff_at: str, execution_at: str, ref: str):
    return CutoffScheduleRecord.from_mapping({
        "profile_key": profile,
        "signal_date": signal_date,
        "cutoff_at": cutoff_at,
        "execution_at": execution_at,
        "source": "V24F_TEST",
        "source_ref": ref,
        "source_sha256": SHA_B,
    })


def test_wage_exact_replay_is_idempotent_and_conflict_fails_closed():
    key = "WAGE_" + _suffix()
    row = _wage(key)
    conn = psycopg2.connect(DSN)
    try:
        assert persist_wage_schedule_records(conn, [row]) == 1
        assert persist_wage_schedule_records(conn, [row]) == 0
        conflict = _wage(key, value="1001", ref="w2")
        with pytest.raises(HistoricalBacktestScheduleError, match="farkli icerik"):
            persist_wage_schedule_records(conn, [conflict])
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), min(row_sha256) FROM core.backtest_minimum_wage_schedule WHERE schedule_key=%s",
                (key,),
            )
            count, row_sha = cur.fetchone()
        assert count == 1
        assert str(row_sha).strip() == row.row_sha256
    finally:
        conn.close()


def test_wage_database_rejects_overlap_against_existing_history():
    key = "WAGE_" + _suffix()
    first = _wage(key, start="2022-01-01", end="2022-07-01")
    overlap = _wage(key, start="2022-06-30", end="2023-01-01", ref="overlap")
    conn = psycopg2.connect(DSN)
    try:
        assert persist_wage_schedule_records(conn, [first]) == 1
        with pytest.raises(psycopg2.Error, match="overlapping interval"):
            persist_wage_schedule_records(conn, [overlap])
    finally:
        conn.close()


def test_cutoff_exact_replay_is_idempotent_and_conflict_fails_closed():
    profile = "CUT_" + _suffix()
    row = _cutoff(
        profile, "2022-01-03",
        "2022-01-02T20:00:00+03:00", "2022-01-03T10:00:00+03:00", "c1",
    )
    conn = psycopg2.connect(DSN)
    try:
        assert persist_cutoff_schedule_records(conn, [row]) == 1
        assert persist_cutoff_schedule_records(conn, [row]) == 0
        conflict = _cutoff(
            profile, "2022-01-03",
            "2022-01-02T19:00:00+03:00", "2022-01-03T10:00:00+03:00", "c2",
        )
        with pytest.raises(HistoricalBacktestScheduleError, match="farkli icerik"):
            persist_cutoff_schedule_records(conn, [conflict])
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), min(row_sha256) FROM analytics.backtest_signal_cutoff_schedule WHERE profile_key=%s",
                (profile,),
            )
            count, row_sha = cur.fetchone()
        assert count == 1
        assert str(row_sha).strip() == row.row_sha256
    finally:
        conn.close()


def test_schedule_tables_reject_update_delete_and_truncate():
    key = "WAGE_" + _suffix()
    profile = "CUT_" + _suffix()
    conn = psycopg2.connect(DSN)
    try:
        assert persist_wage_schedule_records(conn, [_wage(key)]) == 1
        assert persist_cutoff_schedule_records(conn, [
            _cutoff(profile, "2022-01-03", "2022-01-02T20:00:00+03:00", "2022-01-03T10:00:00+03:00", "c")
        ]) == 1
        statements = [
            ("UPDATE core.backtest_minimum_wage_schedule SET net_min_wage=999 WHERE schedule_key=%s", (key,)),
            ("DELETE FROM core.backtest_minimum_wage_schedule WHERE schedule_key=%s", (key,)),
            ("TRUNCATE core.backtest_minimum_wage_schedule", ()),
            ("UPDATE analytics.backtest_signal_cutoff_schedule SET source='HACK' WHERE profile_key=%s", (profile,)),
            ("DELETE FROM analytics.backtest_signal_cutoff_schedule WHERE profile_key=%s", (profile,)),
            ("TRUNCATE analytics.backtest_signal_cutoff_schedule", ()),
        ]
        for sql, params in statements:
            with pytest.raises(psycopg2.Error, match="degistirilemez"):
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(sql, params)
    finally:
        conn.close()


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
          'COMPLETE', 'OK', 'FULL_UNIVERSE', 2, 'OVERWRITE',
          0, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
          'TOTAL_RASYO_SCORE_V1', '{}'::jsonb
        )
        """,
        (run_id, analysis_at, ("1" if run_id.endswith("1") else "2") * 64, analysis_at, analysis_at),
    )


def _insert_result(cur, *, run_id: str, analysis_at: str, ticker: str, score: float, decision: str) -> None:
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
          0.5, 'V24F_FIXTURE', %s::timestamptz, 'SECTOR_ENGINE', false,
          1.0, 0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false, 0.5, %s::timestamptz, false,
          0.5, %s::timestamptz, false, 0.5, %s::timestamptz, false,
          'ANALYTICS_MODULE_SCORES', 9, false,
          %s, %s, %s, false, %s,
          'TOTAL_RASYO_SCORE_V1', 'OK', NULL, NULL, NULL, 1.0, '{}'::jsonb, %s
        )
        """,
        (
            analysis_at, ticker, analysis_at, analysis_at, analysis_at,
            analysis_at, analysis_at, analysis_at,
            score, score, score * 100.0, decision, run_id,
        ),
    )


def test_registered_schedules_drive_full_database_backtest_and_preserve_provenance():
    suffix = _suffix()
    wage_key = "WAGE_" + suffix
    profile = "CUT_" + suffix
    aaa = "F" + suffix[:5] + "A"
    bbb = "F" + suffix[:5] + "B"
    conn = psycopg2.connect(DSN)
    try:
        # Schedule registry writes are intentionally committed by persist_*.
        assert persist_wage_schedule_records(conn, [_wage(wage_key)]) == 1
        cut_rows = [
            _cutoff(profile, "2022-01-03", "2022-01-02T20:00:00+03:00", "2022-01-03T10:00:00+03:00", "jan"),
            _cutoff(profile, "2022-02-02", "2022-02-01T20:00:00+03:00", "2022-02-02T10:00:00+03:00", "feb"),
        ]
        assert persist_cutoff_schedule_records(conn, cut_rows) == 2

        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO core.index_prices_daily(index_code, trade_date, open, close) VALUES (%s,%s,%s,%s)",
                    [("XU100", "2022-01-03", 100, 101), ("XU100", "2022-02-02", 110, 111)],
                )
                cur.executemany(
                    """
                    INSERT INTO core.universe_membership_history(
                      ticker, valid_from, valid_to, is_tradable, company_name,
                      sector_index_code, sector_code, source, source_ref,
                      source_sha256, row_sha256
                    ) VALUES (%s,'2020-01-01',NULL,true,%s,'XTEST','TEST','V24F_TEST',%s,%s,%s)
                    """,
                    [
                        (aaa, "AAA TEST", "u-a", "3"*64, "4"*64),
                        (bbb, "BBB TEST", "u-b", "5"*64, "6"*64),
                    ],
                )
                cur.executemany(
                    "INSERT INTO core.prices_daily(ticker, trade_date, open, close) VALUES (%s,%s,%s,%s)",
                    [
                        (aaa, "2022-01-03", 10, 11), (bbb, "2022-01-03", 20, 21),
                        (aaa, "2022-02-02", 12, 13), (bbb, "2022-02-02", 22, 23),
                    ],
                )
                _insert_run(cur, run_id="V24F-RUN-1", analysis_at="2022-01-02T19:00:00Z")
                _insert_run(cur, run_id="V24F-RUN-2", analysis_at="2022-02-01T19:00:00Z")
                _insert_result(cur, run_id="V24F-RUN-1", analysis_at="2022-01-02T19:00:00Z", ticker=aaa, score=.9, decision="AL")
                _insert_result(cur, run_id="V24F-RUN-1", analysis_at="2022-01-02T19:00:00Z", ticker=bbb, score=.4, decision="UZAK")
                _insert_result(cur, run_id="V24F-RUN-2", analysis_at="2022-02-01T19:00:00Z", ticker=aaa, score=.7, decision="IZLE")
                _insert_result(cur, run_id="V24F-RUN-2", analysis_at="2022-02-01T19:00:00Z", ticker=bbb, score=.8, decision="AL")

        schedules = fetch_registered_backtest_schedules(
            conn,
            wage_schedule_key=wage_key,
            cutoff_profile_key=profile,
            start_month="2022-01", end_month="2022-02", expected_months=2,
        )
        assert set(schedules.minimum_wage["schedule_key"]) == {wage_key}
        assert set(schedules.cutoffs["profile_key"]) == {profile}
        assert set(schedules.cutoffs["source_ref"]) == {"jan", "feb"}

        result = run_monthly_backtest_with_registered_schedules(
            conn,
            wage_schedule_key=wage_key,
            cutoff_profile_key=profile,
            start_month="2022-01", end_month="2022-02", expected_months=2,
        )
        assert list(result.run.inputs.contributions["contribution"]) == [2000.0, 2000.0]
        assert list(result.run.trades["ticker"]) == [aaa, bbb]
        assert list(result.run.trades["side"]) == ["BUY", "BUY"]
        assert len(result.run.monthly) == 2
        assert len(result.run.benchmark) == 2
        assert set(result.schedules.minimum_wage["source"]) == {"V24F_TEST"}
        assert set(result.schedules.cutoffs["source"]) == {"V24F_TEST"}
    finally:
        conn.close()
