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


def test_gyo_migration_contract_and_no_duplicate_columns():
    sql = Path("sql/024_gyo_nav_valuation.sql").read_text()
    for table in ["core.gyo_nav_snapshots", "analytics.gyo_valuation_periods", "analytics.gyo_m2_scores"]:
        cols = _table_columns(sql, table)
        assert len(cols) == len(set(cols)), (table, cols)
    for required in ["TIMESTAMPTZ", "nav_source_method", "property_portfolio_value", "latest_gyo_m2_scores", "immutable"]:
        assert required in sql


def test_holding_migration_duplicate_share_basis_regression():
    sql = Path("sql/023_holding_nav_valuation.sql").read_text()
    core_cols = _table_columns(sql, "core.holding_nav_snapshots")
    valuation_cols = _table_columns(sql, "analytics.holding_valuation_periods")
    assert core_cols.count("share_basis") == 1
    assert valuation_cols.count("share_basis") == 1


def test_gyo_valuation_insert_column_count_matches_persist_tuple():
    import ast

    source = Path("src/analytics/gyo_batch_pipeline.py").read_text(encoding="utf-8")
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
    assert tuple_lengths == [35]

    match = re.search(
        r"INSERT INTO analytics\.gyo_valuation_periods\s*\((.*?)\)\s*VALUES %s",
        source,
        re.S,
    )
    assert match
    columns = [item.strip() for item in match.group(1).split(",") if item.strip()]
    assert len(columns) == tuple_lengths[0]
