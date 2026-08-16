from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.ingest.api.semantic_facts import SemanticFinancialFact, SemanticMappingConfig
from src.ingest.semantic_materialization import persist_semantic_facts


class Cursor:
    def __init__(self):
        self.executed = []
    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Conn:
    def __init__(self): self.cur = Cursor()
    def cursor(self): return self.cur
    def __enter__(self): return self
    def __exit__(self, *_): return False


def semantic_fact():
    return SemanticFinancialFact(
        source="MKK_KAP_API",
        disclosure_id="D1",
        ticker="GARAN",
        published_at=datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc),
        version_tag="RESTATED",
        version_sequence=2,
        sector_family="BANK",
        semantic_profile="BANK_CORE_TEST",
        semantic_version=1,
        canonical_field="TOTAL_EQUITY",
        nature="INSTANT",
        period_start=None,
        period_end=date(2026, 3, 31),
        currency="TRY",
        statement_scope="CONSOLIDATED",
        value=Decimal("1000000"),
        source_fact_code="EQ",
        source_fact_key="a" * 64,
        source_mapping_profile="PORTAL",
        source_mapping_version=1,
        dimensions={},
        lineage_sha256="b" * 64,
        mapped_at=datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc),
    )


def test_persistence_is_idempotent_and_updates_only_mapped_at():
    conn = Conn()
    assert persist_semantic_facts(conn, [semantic_fact()]) == 1
    sql, params = conn.cur.executed[0]
    assert "INSERT INTO core.semantic_financial_facts" in sql
    assert "DO UPDATE SET mapped_at" in sql
    assert "value = EXCLUDED" not in sql
    assert params[:6] == (
        "MKK_KAP_API", "D1", "BANK_CORE_TEST", 1, "TOTAL_EQUITY", "b" * 64
    )


def test_empty_persistence_is_noop():
    conn = Conn()
    assert persist_semantic_facts(conn, []) == 0
    assert conn.cur.executed == []


def test_migration_has_lineage_fk_immutability_and_deterministic_bank_tiebreak():
    sql = (Path(__file__).resolve().parents[1] / "sql" / "016_semantic_sector_materialization.sql").read_text().lower()
    for token in (
        "create table if not exists core.semantic_financial_facts",
        "references raw.kap_financial_facts",
        "reject_semantic_fact_mutation",
        "old.source <> new.source",
        "published_at <= mapped_at",
        "create table if not exists core.semantic_mapping_rejections",
        "add column if not exists lineage_sha256",
        "m.lineage_sha256 desc nulls last",
        "ck_bank_metrics_derived_lineage",
        "reject_derived_bank_metric_mutation",
    ):
        assert token in sql


def test_example_mapping_is_explicitly_placeholder_not_fake_portal_codes():
    path = Path(__file__).resolve().parents[1] / "config" / "kap_bank_semantic_mapping.example.json"
    config = SemanticMappingConfig.from_json_file(str(path))
    all_codes = {code for rule in config.fields for code in rule.source_codes}
    assert all("PORTAL_DOKUMAN" in code for code in all_codes)
