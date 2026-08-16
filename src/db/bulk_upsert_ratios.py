from __future__ import annotations
import io
from typing import Any, List, TYPE_CHECKING
import pandas as pd

if TYPE_CHECKING:
    from psycopg2.extensions import connection as PGConn
else:
    PGConn = Any


def _df_to_csv_buffer(df: pd.DataFrame) -> io.StringIO:
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)
    return buf


def _copy_into_temp(cur, temp_table: str, cols: List[str], df: pd.DataFrame) -> None:
    if df.empty:
        return
    buf = _df_to_csv_buffer(df)
    col_list = ", ".join(cols)
    cur.copy_expert(f"COPY {temp_table} ({col_list}) FROM STDIN WITH (FORMAT csv)", buf)


def upsert_ratios_copy(conn: PGConn, df: pd.DataFrame) -> None:
    if df.empty:
        return
    cols = ["ticker", "period_end", "version_tag", "ratio_name", "ratio_value", "is_na"]
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TEMP TABLE tmp_ratios (
                  ticker TEXT,
                  period_end DATE,
                  version_tag TEXT,
                  ratio_name TEXT,
                  ratio_value NUMERIC,
                  is_na BOOLEAN
                ) ON COMMIT DROP
            """)
            _copy_into_temp(cur, "tmp_ratios", cols, df[cols].copy())
            cur.execute("""
                INSERT INTO analytics.ratios_quarterly
                  (ticker, period_end, version_tag, ratio_name, ratio_value, is_na)
                SELECT ticker, period_end, version_tag, ratio_name, ratio_value, is_na
                FROM tmp_ratios
                ON CONFLICT (ticker, period_end, version_tag, ratio_name)
                DO UPDATE SET
                  ratio_value = EXCLUDED.ratio_value,
                  is_na = EXCLUDED.is_na
            """)
