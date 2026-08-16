from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.analytics.kap_bank_batch_io import load_batch_contexts_json, load_disclosures_jsonl
from src.analytics.kap_bank_batch_persistence import PersistedKapBankBatch
from src.analytics.kap_bank_db_workflow import (
    KapBankDatabaseWorkflowError,
    build_database_bank_contexts,
    fetch_bank_module_contexts,
    fetch_kap_bank_disclosures,
    resolve_kap_bank_anchor_period_end,
    run_kap_bank_database_batch,
)
from src.analytics.kap_bank_end_to_end import evaluate_kap_bank_batch_end_to_end
from src.ingest.api.kap_financial_facts import KapFinancialFactConfig
from src.ingest.api.semantic_facts import SemanticMappingConfig
from src.ingest.bank_fact_materializer import BankDerivationConfig

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test_fixtures/kap_bank_batch_e2e"
ANALYSIS = datetime(2026, 5, 15, 20, 0, tzinfo=timezone(timedelta(hours=3)))
ANCHOR = date(2026, 3, 31)
FACT_CONFIG = KapFinancialFactConfig.from_json_file(
    ROOT / "config/mkk_kap_financial_facts_mapping.example.json"
)
SEMANTIC_CONFIG = SemanticMappingConfig.from_json_file(
    ROOT / "config/kap_bank_semantic_mapping.official_v1.json"
)
DERIVATION_CONFIG = BankDerivationConfig.from_json_file(
    ROOT / "config/bank_fact_derivation.official_v1.json"
)


class StaticCursor:
    def __init__(self, rows, names):
        self.rows = rows
        self.description = [(name,) for name in names]
        self.sql = ""
        self.params = None

    def execute(self, sql, params=None):
        self.sql = " ".join(str(sql).split())
        self.params = params

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class StaticConn:
    def __init__(self, cursor):
        self.cur = cursor

    def cursor(self):
        return self.cur


def _raw_db_rows():
    rows = []
    for envelope in load_disclosures_jsonl(FIXTURE / "disclosures.jsonl"):
        rows.append((
            envelope.source,
            envelope.disclosure_id,
            envelope.published_at,
            envelope.ticker,
            envelope.company_id,
            envelope.notification_type,
            envelope.subject,
            envelope.source_url,
            json.dumps(envelope.payload),
            envelope.payload_sha256,
            envelope.fetched_at,
        ))
    return rows


def test_raw_disclosures_are_loaded_point_in_time_and_canonicalized():
    names = [
        "source", "disclosure_id", "published_at", "ticker", "company_id",
        "notification_type", "subject", "source_url", "payload",
        "payload_sha256", "fetched_at",
    ]
    cur = StaticCursor(_raw_db_rows(), names)
    rows = fetch_kap_bank_disclosures(
        StaticConn(cur),
        tickers=["garan", "akbnk", "ykbnk"],
        analysis_at=ANALYSIS,
    )
    assert len(rows) == 37
    assert {row.ticker for row in rows} == {"AKBNK", "GARAN", "YKBNK"}
    assert all(row.published_at <= ANALYSIS for row in rows)
    assert "published_at <= %(analysis_at)s" in cur.sql
    assert cur.params["tickers"] == ["AKBNK", "GARAN", "YKBNK"]


def test_raw_disclosure_query_rejects_unexpected_ticker_and_bad_json():
    names = [
        "source", "disclosure_id", "published_at", "ticker", "company_id",
        "notification_type", "subject", "source_url", "payload",
        "payload_sha256", "fetched_at",
    ]
    row = list(_raw_db_rows()[0])
    row[3] = "THYAO"
    with pytest.raises(KapBankDatabaseWorkflowError, match="beklenmeyen ticker"):
        fetch_kap_bank_disclosures(
            StaticConn(StaticCursor([tuple(row)], names)),
            tickers=["AKBNK"],
            analysis_at=ANALYSIS,
        )
    row[3] = "AKBNK"
    row[8] = "{bozuk"
    with pytest.raises(KapBankDatabaseWorkflowError, match="gecersiz JSON"):
        fetch_kap_bank_disclosures(
            StaticConn(StaticCursor([tuple(row)], names)),
            tickers=["AKBNK"],
            analysis_at=ANALYSIS,
        )


def test_anchor_is_resolved_from_matching_point_in_time_fact_profile():
    cur = StaticCursor([(ANCHOR,)], ["max"])
    result = resolve_kap_bank_anchor_period_end(
        StaticConn(cur),
        tickers=["AKBNK", "GARAN"],
        analysis_at=ANALYSIS,
        fact_config=FACT_CONFIG,
    )
    assert result == ANCHOR
    assert "mapping_profile = %(mapping_profile)s" in cur.sql
    assert cur.params["mapping_profile"] == FACT_CONFIG.mapping_profile
    assert cur.params["mapping_version"] == FACT_CONFIG.mapping_version


def test_module_context_requires_exact_market_cutoff_and_rejects_invalid_score():
    names = [
        "ticker", "asof_date", "analysis_at", "m1", "m3", "ek4", "ek1", "ek9",
        "good_count_ge8",
    ]
    rows = [
        ("AKBNK", date(2026, 5, 15), ANALYSIS, 0.62, 0.60, 0.55, 0.80, 0.65, 8),
        ("GARAN", date(2026, 5, 15), ANALYSIS, "bozuk", 0.60, 0.55, 0.80, 0.65, 8),
    ]
    cur = StaticCursor(rows, names)
    contexts, rejected = fetch_bank_module_contexts(
        StaticConn(cur),
        tickers=["AKBNK", "GARAN", "YKBNK"],
        analysis_at=ANALYSIS,
        horizon_days=63,
    )
    assert contexts["AKBNK"].other_module_scores["M1"] == pytest.approx(0.62)
    assert "INVALID" in rejected["GARAN"]
    assert "MISSING" in rejected["YKBNK"]
    assert "ms.asof_date <= %(context_asof)s" in cur.sql
    assert "ms.asof_date >= %(context_asof)s - %(max_context_age_days)s" in cur.sql
    assert "ms.analysis_at <= %(analysis_at)s" in cur.sql


def test_module_context_duplicate_ticker_is_hard_contract_failure():
    names = [
        "ticker", "asof_date", "analysis_at", "m1", "m3", "ek4", "ek1", "ek9",
        "good_count_ge8",
    ]
    row = ("AKBNK", date(2026, 5, 15), ANALYSIS, .62, .60, .55, .80, .65, 8)
    with pytest.raises(KapBankDatabaseWorkflowError, match="tekrarlanan ticker"):
        fetch_bank_module_contexts(
            StaticConn(StaticCursor([row, row], names)),
            tickers=["AKBNK"], analysis_at=ANALYSIS, horizon_days=63,
        )


def test_module_context_uses_latest_prior_trading_day_with_bounded_staleness():
    monday_noon = datetime(2026, 5, 18, 12, 0, tzinfo=ANALYSIS.tzinfo)
    friday = date(2026, 5, 15)
    names = [
        "ticker", "asof_date", "analysis_at", "m1", "m3", "ek4", "ek1", "ek9",
        "good_count_ge8",
    ]
    cur = StaticCursor([
        ("AKBNK", friday, datetime(2026, 5, 15, 20, 0, tzinfo=ANALYSIS.tzinfo),
         .62, .60, .55, .80, .65, 8)
    ], names)
    contexts, rejected = fetch_bank_module_contexts(
        StaticConn(cur), tickers=["AKBNK"], analysis_at=monday_noon,
        horizon_days=63, max_context_age_days=7,
    )
    assert not rejected
    assert contexts["AKBNK"].source_asof_date == friday
    assert cur.params["context_asof"] == date(2026, 5, 17)
    assert cur.params["max_context_age_days"] == 7


def test_module_context_rejects_row_older_than_configured_age_even_if_driver_returns_it():
    names = [
        "ticker", "asof_date", "analysis_at", "m1", "m3", "ek4", "ek1", "ek9",
        "good_count_ge8",
    ]
    cur = StaticCursor([
        ("AKBNK", date(2026, 5, 1), ANALYSIS, .62, .60, .55, .80, .65, 8)
    ], names)
    contexts, rejected = fetch_bank_module_contexts(
        StaticConn(cur), tickers=["AKBNK"], analysis_at=ANALYSIS,
        horizon_days=63, max_context_age_days=7,
    )
    assert contexts == {}
    assert "yasi gecersiz" in rejected["AKBNK"]


def test_requested_ticker_without_total_context_still_contributes_to_sector_floor():
    disclosures = load_disclosures_jsonl(FIXTURE / "disclosures.jsonl")
    contexts = load_batch_contexts_json(FIXTURE / "contexts.json")
    contexts.pop("GARAN")
    report = evaluate_kap_bank_batch_end_to_end(
        disclosures,
        analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR,
        fact_config=FACT_CONFIG,
        semantic_config=SEMANTIC_CONFIG,
        derivation_config=DERIVATION_CONFIG,
        contexts=contexts,
        requested_tickers=["AKBNK", "GARAN", "YKBNK"],
    )
    assert report["prepared_count"] == 3
    assert report["result_count"] == 2
    assert report["rejections"] == [
        {"ticker": "GARAN", "reason": "EVALUATION_CONTEXT_MISSING"}
    ]
    assert all(row["valuation"]["sector_sample_size"] == 2 for row in report["results"])


def test_database_workflow_merges_context_rejection_and_persists(monkeypatch):
    disclosures = load_disclosures_jsonl(FIXTURE / "disclosures.jsonl")
    contexts = load_batch_contexts_json(FIXTURE / "contexts.json")
    contexts.pop("GARAN")

    monkeypatch.setattr(
        "src.analytics.kap_bank_db_workflow.fetch_active_bank_tickers",
        lambda conn: ["AKBNK", "GARAN", "YKBNK"],
    )
    monkeypatch.setattr(
        "src.analytics.kap_bank_db_workflow.resolve_kap_bank_anchor_period_end",
        lambda *args, **kwargs: ANCHOR,
    )
    monkeypatch.setattr(
        "src.analytics.kap_bank_db_workflow.fetch_kap_bank_disclosures",
        lambda *args, **kwargs: disclosures,
    )
    monkeypatch.setattr(
        "src.analytics.kap_bank_db_workflow._build_database_bank_contexts_with_lineage",
        lambda *args, **kwargs: (
            contexts,
            {"GARAN": "NON_M2_MODULE_CONTEXT_MISSING"},
            {
                ticker: {
                    "assumption": {
                        "effective_at": datetime(2026, 1, 1, tzinfo=ANALYSIS.tzinfo),
                        "source": "TEST",
                        "coe": 0.15,
                        "macro_cap": 0.08,
                        "risk_free_rate": None,
                    }
                }
                for ticker in contexts
            },
        ),
    )
    monkeypatch.setattr(
        "src.analytics.kap_bank_db_workflow.persist_kap_bank_batch_report",
        lambda *args, **kwargs: PersistedKapBankBatch(
            run_key="a" * 64,
            status="PARTIAL",
            results_written=2,
            rejections_written=1,
            ranking_written=2,
            module_scores_written=2,
        ),
    )
    result = run_kap_bank_database_batch(
        object(),
        analysis_at=ANALYSIS,
        fact_config=FACT_CONFIG,
        semantic_config=SEMANTIC_CONFIG,
        derivation_config=DERIVATION_CONFIG,
    )
    assert result.report["status"] == "PARTIAL"
    assert result.report["requested_count"] == 3
    assert result.report["rejections"] == [
        {"ticker": "GARAN", "reason": "NON_M2_MODULE_CONTEXT_MISSING"}
    ]
    assert result.persistence is not None
    assert result.context_ready_count == 2
    assert result.disclosures_loaded == 37
    akbnk = next(row for row in result.report["results"] if row["ticker"] == "AKBNK")
    assert akbnk["valuation"]["assumption"]["source"] == "TEST"
    assert akbnk["valuation"]["sector_asof_cutoff"] == ANALYSIS


def test_database_context_builder_rejects_missing_assumption_without_defaults(monkeypatch):
    module_context = type("M", (), {
        "other_module_scores": {"M1": .6, "M3": .6, "Ek4": .5, "Ek1": .8, "Ek9": .6},
        "good_count_ge8": 8,
    })()
    monkeypatch.setattr(
        "src.analytics.kap_bank_db_workflow.resolve_bank_assumptions",
        lambda *args, **kwargs: ({}, ["AKBNK"]),
    )
    monkeypatch.setattr(
        "src.analytics.kap_bank_db_workflow.fetch_bank_m2_contexts",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "src.analytics.kap_bank_db_workflow.fetch_bank_module_contexts",
        lambda *args, **kwargs: ({"AKBNK": module_context}, {}),
    )
    contexts, rejected = build_database_bank_contexts(
        object(), tickers=["AKBNK"], analysis_at=ANALYSIS, horizon_days=63,
    )
    assert contexts == {}
    assert rejected == {"AKBNK": "POINT_IN_TIME_ASSUMPTION_MISSING"}


def test_numpy_bool_module_score_is_controlled_reject():
    np = pytest.importorskip("numpy")
    names = [
        "ticker", "asof_date", "analysis_at", "m1", "m3", "ek4", "ek1", "ek9",
        "good_count_ge8",
    ]
    cur = StaticCursor([
        ("AKBNK", date(2026, 5, 15), ANALYSIS, np.bool_(False), .6, .5, .8, .6, 8)
    ], names)
    contexts, rejected = fetch_bank_module_contexts(
        StaticConn(cur), tickers=["AKBNK"], analysis_at=ANALYSIS, horizon_days=63,
    )
    assert contexts == {}
    assert "bool" in rejected["AKBNK"]


def test_v8_migration_contains_point_in_time_workflow_indexes():
    sql = (ROOT / "sql/018_kap_bank_database_workflow.sql").read_text().lower()
    assert "upper(ticker)" in sql
    assert "source, upper(ticker), published_at" in sql
    assert "mapping_profile, mapping_version, upper(ticker), published_at" in sql
    assert "horizon_days, asof_date, upper(ticker), analysis_at desc" in sql


class WorkflowCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = []
        self.rows = []

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.conn.calls.append((normalized, params))
        contexts = load_batch_contexts_json(FIXTURE / "contexts.json")
        if "FROM core.universe_stocks" in normalized:
            self.description = [("ticker",)]
            self.rows = [(ticker,) for ticker in ("AKBNK", "GARAN", "YKBNK")]
        elif "SELECT max(period_end)" in normalized:
            self.description = [("max",)]
            self.rows = [(ANCHOR,)]
        elif "FROM raw.kap_disclosures" in normalized:
            self.description = [(name,) for name in [
                "source", "disclosure_id", "published_at", "ticker", "company_id",
                "notification_type", "subject", "source_url", "payload",
                "payload_sha256", "fetched_at",
            ]]
            self.rows = _raw_db_rows()
        elif "FROM analytics.bank_valuation_assumptions" in normalized:
            self.description = [(name,) for name in [
                "scope_type", "scope_code", "effective_at", "coe", "macro_cap",
                "risk_free_rate", "tier_cap", "payout_missing_factor",
                "band_width_shadow_mode", "max_halfwidth", "source", "metadata",
            ]]
            self.rows = [(
                "BANK", "BANK", datetime(2026, 1, 1, tzinfo=ANALYSIS.tzinfo),
                0.15, 0.08, 0.10, 0.80, 0.70, True, 0.80, "TEST_ASSUMPTION", {},
            )]
        elif "WITH requested AS" in normalized and "m2_follow_score" in normalized:
            self.description = [(name,) for name in [
                "ticker", "price_trade_date", "current_price", "m2_follow_score",
            ]]
            self.rows = [
                (ticker, context.price_trade_date, context.current_price, None)
                for ticker, context in sorted(contexts.items())
            ]
        elif "WITH candidates AS" in normalized and "good_count_ge8" in normalized:
            self.description = [(name,) for name in [
                "ticker", "asof_date", "analysis_at", "m1", "m3", "ek4", "ek1", "ek9",
                "good_count_ge8",
            ]]
            self.rows = [
                (
                    ticker,
                    date(2026, 5, 15),
                    ANALYSIS,
                    context.other_module_scores["M1"],
                    context.other_module_scores["M3"],
                    context.other_module_scores["Ek4"],
                    context.other_module_scores["Ek1"],
                    context.other_module_scores["Ek9"],
                    context.good_count_ge8,
                )
                for ticker, context in sorted(contexts.items())
            ]
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class WorkflowConn:
    def __init__(self):
        self.calls = []

    def cursor(self):
        return WorkflowCursor(self)


def test_full_database_read_path_produces_same_three_bank_ranking_without_files():
    conn = WorkflowConn()
    result = run_kap_bank_database_batch(
        conn,
        analysis_at=ANALYSIS,
        fact_config=FACT_CONFIG,
        semantic_config=SEMANTIC_CONFIG,
        derivation_config=DERIVATION_CONFIG,
        persist=False,
    )
    assert result.report["status"] == "COMPLETE"
    assert result.report["requested_count"] == 3
    assert result.disclosures_loaded == 37
    assert [row["ticker"] for row in result.report["ranking"]] == [
        "YKBNK", "AKBNK", "GARAN"
    ]
    assert [round(row["total_rasyo_100"], 2) for row in result.report["ranking"]] == [
        70.66, 66.70, 65.98
    ]
    assert all(
        row["valuation"]["assumption"]["source"] == "TEST_ASSUMPTION"
        for row in result.report["results"]
    )
    assert len(conn.calls) == 6


def test_database_workflow_self_audit_runs_directly_from_repository_root():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/self_audit_kap_bank_db_workflow.py"), "--smoke"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["scenario_count"] == 47
    assert report["validated_scenarios"] == 47
    assert report["uncontrolled_exceptions"] == 0
    assert report["silent_invalid_accepts"] == 0


def test_all_repository_self_audits_bootstrap_the_project_root():
    scripts = sorted((ROOT / "scripts").glob("self_audit_*.py"))
    assert scripts
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        if "from src." in text or "import src." in text:
            assert "ensure_repo_root" in text, script.name
