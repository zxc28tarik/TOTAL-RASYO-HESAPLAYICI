from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.analytics.kap_bank_batch_io import run_batch_preview_from_files
from src.analytics.kap_bank_batch_persistence import (
    KapBankBatchPersistenceError,
    persist_kap_bank_batch_report,
)

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = datetime(2026, 5, 15, 20, 0, tzinfo=timezone(timedelta(hours=3)))
ANCHOR = date(2026, 3, 31)


@pytest.fixture(scope="module")
def complete_report():
    return run_batch_preview_from_files(
        disclosures_path=ROOT / "test_fixtures/kap_bank_batch_e2e/disclosures.jsonl",
        contexts_path=ROOT / "test_fixtures/kap_bank_batch_e2e/contexts.json",
        analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR,
        fact_config_path=ROOT / "config/mkk_kap_financial_facts_mapping.example.json",
        semantic_config_path=ROOT / "config/kap_bank_semantic_mapping.official_v1.json",
        derivation_config_path=ROOT / "config/bank_fact_derivation.official_v1.json",
    )


class Cursor:
    def __init__(self, *, fail_pattern: str | None = None):
        self.calls = []
        self.fail_pattern = fail_pattern

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, params))
        if self.fail_pattern and self.fail_pattern in normalized:
            raise RuntimeError("injected database failure")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Conn:
    def __init__(self, *, fail_pattern: str | None = None):
        self.cur = Cursor(fail_pattern=fail_pattern)
        self.enter_count = 0
        self.exit_errors = []

    def cursor(self):
        return self.cur

    def __enter__(self):
        self.enter_count += 1
        return self

    def __exit__(self, exc_type, *_):
        self.exit_errors.append(exc_type)
        return False


def _sql_calls(conn: Conn, fragment: str):
    return [call for call in conn.cur.calls if fragment in call[0]]


def test_complete_batch_is_persisted_atomically_with_ranking_and_module_scores(complete_report):
    conn = Conn()
    saved = persist_kap_bank_batch_report(conn, complete_report)

    assert saved.status == "COMPLETE"
    assert saved.results_written == 3
    assert saved.ranking_written == 3
    assert saved.module_scores_written == 3
    assert saved.rejections_written == 0
    assert conn.enter_count == 1
    assert conn.exit_errors == [None]

    assert len(_sql_calls(conn, "INSERT INTO analytics.bank_valuation_periods")) == 3
    assert len(_sql_calls(conn, "INSERT INTO analytics.bank_m2_scores")) == 3
    module_calls = _sql_calls(conn, "INSERT INTO analytics.module_scores")
    assert len(module_calls) == 3
    assert all(call[1]["m2_source"] == "KAP_BANK_E2E_V47" for call in module_calls)
    assert all(call[1]["source_run_key"] == saved.run_key for call in module_calls)

    ranks = _sql_calls(conn, "INSERT INTO analytics.kap_bank_batch_rankings")
    assert [call[1]["rank"] for call in ranks] == [2, 3, 1]  # ticker yazim sirasi: AKBNK, GARAN, YKBNK
    assert {call[1]["ticker"] for call in ranks} == {"AKBNK", "GARAN", "YKBNK"}
    assert len(_sql_calls(conn, "DELETE FROM analytics.bank_valuation_periods")) == 1
    assert len(_sql_calls(conn, "DELETE FROM analytics.bank_m2_scores")) == 1


def test_rerun_replaces_stale_success_with_rejection_in_same_transaction(complete_report):
    report = deepcopy(complete_report)
    report["results"] = [row for row in report["results"] if row["ticker"] != "YKBNK"]
    report["ranking"] = [row for row in report["ranking"] if row["ticker"] != "YKBNK"]
    for idx, row in enumerate(report["ranking"], start=1):
        row["rank"] = idx
    report["rejections"] = [{"ticker": "YKBNK", "reason": "BOZUK_PAYLOAD"}]
    report["status"] = "PARTIAL"
    report["result_count"] = 2
    report["rejected_count"] = 1
    report["valuation_ok_count"] = 2

    conn = Conn()
    saved = persist_kap_bank_batch_report(conn, report)
    assert saved.results_written == 2
    assert saved.rejections_written == 1
    assert len(_sql_calls(conn, "INSERT INTO analytics.bank_valuation_periods")) == 2
    rejection = _sql_calls(conn, "INSERT INTO analytics.kap_bank_batch_rejections")[0]
    assert rejection[1]["ticker"] == "YKBNK"
    cleanup = _sql_calls(conn, "DELETE FROM analytics.bank_valuation_periods")[0][1]
    assert cleanup["tickers"] == ["AKBNK", "GARAN", "YKBNK"]


def test_same_run_identity_is_stable_but_report_hash_changes_with_payload(complete_report):
    first_conn = Conn()
    first = persist_kap_bank_batch_report(first_conn, complete_report)
    changed = deepcopy(complete_report)
    changed["results"][0]["raw_facts_extracted"] += 1
    second_conn = Conn()
    second = persist_kap_bank_batch_report(second_conn, changed)
    assert first.run_key == second.run_key
    first_run = _sql_calls(first_conn, "INSERT INTO analytics.kap_bank_batch_runs")[0][1]
    second_run = _sql_calls(second_conn, "INSERT INTO analytics.kap_bank_batch_runs")[0][1]
    assert first_run["report_sha256"] != second_run["report_sha256"]


def test_invalid_ranking_or_numpy_bool_fails_before_database_touch(complete_report):
    bad = deepcopy(complete_report)
    bad["ranking"][0]["rank"] = 2
    conn = Conn()
    with pytest.raises(KapBankBatchPersistenceError, match="kesintisiz"):
        persist_kap_bank_batch_report(conn, bad)
    assert conn.enter_count == 0
    assert not conn.cur.calls

    np = pytest.importorskip("numpy")
    bad_bool = deepcopy(complete_report)
    bad_bool["results"][0]["total_rasyo"]["module_scores"]["M1"] = np.bool_(False)
    with pytest.raises(KapBankBatchPersistenceError, match="bool"):
        persist_kap_bank_batch_report(Conn(), bad_bool)


def test_database_error_rolls_back_the_single_outer_transaction(complete_report):
    conn = Conn(fail_pattern="INSERT INTO analytics.module_scores")
    with pytest.raises(RuntimeError, match="injected"):
        persist_kap_bank_batch_report(conn, complete_report)
    assert conn.enter_count == 1
    assert conn.exit_errors == [RuntimeError]


def test_migration_contains_run_ranking_rejection_and_intraday_traceability():
    sql = (ROOT / "sql/017_kap_bank_batch_persistence.sql").read_text().lower()
    assert "analytics.kap_bank_batch_runs" in sql
    assert "analytics.kap_bank_batch_rankings" in sql
    assert "analytics.kap_bank_batch_rejections" in sql
    assert "source_run_key" in sql
    assert "analysis_at timestamptz" in sql
    assert "on delete cascade" in sql
    assert "result_count + rejected_count = requested_count" in sql
    assert "asof_date = (analysis_at at time zone 'europe/istanbul')::date" in sql


def test_timezone_equivalent_analysis_instants_share_one_run_key(complete_report):
    first = persist_kap_bank_batch_report(Conn(), complete_report)
    equivalent = deepcopy(complete_report)
    utc = equivalent["analysis_at"].astimezone(timezone.utc)
    equivalent["analysis_at"] = utc
    for row in equivalent["results"]:
        row["analysis_at"] = utc
        row["canonical"]["analysis_at"] = utc
        row["valuation"]["analysis_at"] = utc
        row["m2"]["analysis_at"] = utc
    second = persist_kap_bank_batch_report(Conn(), equivalent)
    assert first.run_key == second.run_key


def test_module_score_upsert_and_cleanup_cannot_overwrite_or_delete_later_intraday_run(complete_report):
    conn = Conn()
    persist_kap_bank_batch_report(conn, complete_report)
    module_sql = _sql_calls(conn, "INSERT INTO analytics.module_scores")[0][0]
    assert "EXCLUDED.analysis_at >= analytics.module_scores.analysis_at" in module_sql
    cleanup_sql = _sql_calls(conn, "DELETE FROM analytics.module_scores")[0][0]
    assert "analysis_at <= %(analysis_at)s" in cleanup_sql
    assert "source_run_key IS NOT NULL" in cleanup_sql


class ReadCursor:
    def __init__(self):
        self.sql = None
        self.params = None
        self.description = [
            ("analysis_at",), ("asof_date",), ("anchor_period_end",), ("horizon_days",),
            ("pipeline_version",), ("source",), ("status",), ("rank",), ("ticker",),
            ("total_rasyo_100",), ("decision",), ("m2_score",), ("v_conf",),
            ("valuation_status",),
        ]

    def execute(self, sql, params):
        self.sql = " ".join(sql.split())
        self.params = params

    def fetchall(self):
        return [(ANALYSIS, ANALYSIS.date(), ANCHOR, 63, "V7", "KAP", "COMPLETE", 1,
                 "YKBNK", 70.66, "AL", 0.74, 0.8, "OK")]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class ReadConn:
    def __init__(self):
        self.cur = ReadCursor()

    def cursor(self):
        return self.cur


def test_latest_ranking_reader_uses_view_and_bounded_parameters():
    from src.analytics.kap_bank_batch_persistence import fetch_latest_kap_bank_ranking
    conn = ReadConn()
    rows = fetch_latest_kap_bank_ranking(
        conn, asof_date=date(2026, 5, 15), horizon_days=63, limit=20,
    )
    assert rows[0]["ticker"] == "YKBNK"
    assert rows[0]["rank"] == 1
    assert "analytics.latest_kap_bank_batch_rankings" in conn.cur.sql
    assert conn.cur.params == {
        "asof_date": date(2026, 5, 15), "horizon_days": 63, "limit": 20,
    }
