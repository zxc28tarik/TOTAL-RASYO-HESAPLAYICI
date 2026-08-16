import re
from pathlib import Path

COLUMN_RE = re.compile(
    r"^\s{2}([a-z_][a-z0-9_]*)\s+(?:TEXT|DATE|TIMESTAMPTZ|TIMESTAMP|INT|INTEGER|BIGINT|SMALLINT|NUMERIC|REAL|DOUBLE|BOOLEAN|JSONB|JSON|BYTEA|UUID)\b",
    re.M,
)
TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+([a-z_][a-z0-9_.]*)\s*\((.*?)\n\);", re.S | re.I)


def test_all_sql_create_tables_have_unique_column_names():
    failures = []
    for path in sorted(Path("sql").glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for table, body in TABLE_RE.findall(text):
            cols = COLUMN_RE.findall(body)
            duplicates = sorted({col for col in cols if cols.count(col) > 1})
            if duplicates:
                failures.append((path.name, table, duplicates))
    assert failures == []
