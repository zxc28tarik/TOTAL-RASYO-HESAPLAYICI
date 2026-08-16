import ast
import re
from pathlib import Path


def _table_columns(sql: str, table: str):
    match = re.search(rf"CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\((.*?)\n\);", sql, re.S)
    assert match, table
    return re.findall(
        r"^\s{2}([a-z_][a-z0-9_]*)\s+(?:TEXT|DATE|TIMESTAMPTZ|INT|NUMERIC|BOOLEAN|JSONB)\b",
        match.group(1),
        re.M,
    )


def test_insurance_migration_contract_and_no_duplicate_columns():
    sql = Path("sql/025_insurance_valuation.sql").read_text(encoding="utf-8")
    tables = [
        "core.insurance_metrics_snapshots",
        "analytics.insurance_valuation_periods",
        "analytics.insurance_m2_scores",
    ]
    for table in tables:
        columns = _table_columns(sql, table)
        assert len(columns) == len(set(columns)), (table, columns)
    for required in [
        "TIMESTAMPTZ", "business_type", "accounting_profile", "technical_result_ttm",
        "combined_ratio", "latest_insurance_m2_scores", "immutable",
    ]:
        assert required in sql


def test_insurance_valuation_insert_column_count_matches_persist_tuple():
    source = Path("src/analytics/insurance_batch_pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    tuple_lengths = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "append" or not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id != "valuation_rows" or len(node.args) != 1:
            continue
        if isinstance(node.args[0], ast.Tuple):
            tuple_lengths.append(len(node.args[0].elts))
    assert tuple_lengths == [47]
    match = re.search(
        r"INSERT INTO analytics\.insurance_valuation_periods\s*\((.*?)\)\s*VALUES %s",
        source,
        re.S,
    )
    assert match
    columns = [item.strip() for item in match.group(1).split(",") if item.strip()]
    assert len(columns) == tuple_lengths[0]


def test_insurance_metrics_insert_column_count_matches_params():
    source = Path("src/ingest/insurance_metrics.py").read_text(encoding="utf-8")
    match = re.search(r"INSERT INTO core\.insurance_metrics_snapshots \((.*?)\) VALUES", source, re.S)
    assert match
    columns = [item.strip() for item in match.group(1).split(",") if item.strip()]
    assert len(columns) == 30
    assert source.count("row.canonical_sha256") >= 2


def test_insurance_immutability_trigger_allows_only_inserted_at_refresh():
    sql = Path("sql/025_insurance_valuation.sql").read_text(encoding="utf-8")
    assert "requested_inserted_at := NEW.inserted_at" in sql
    assert "NEW.inserted_at := OLD.inserted_at" in sql
    assert "RAISE EXCEPTION 'insurance_metrics_snapshots immutable'" in sql
