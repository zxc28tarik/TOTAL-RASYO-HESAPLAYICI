from __future__ import annotations
import pandas as pd


def validate_core(conn) -> dict:
    out: dict = {}
    for tbl in ["core.universe_stocks", "core.prices_daily", "core.index_prices_daily", "core.financials_quarterly"]:
        df = pd.read_sql(f"SELECT COUNT(*) AS n FROM {tbl}", conn)
        out[tbl] = int(df.iloc[0, 0])
    df = pd.read_sql("SELECT COUNT(*) AS n FROM core.index_prices_daily WHERE index_code='XU100'", conn)
    out["has_XU100_rows"] = int(df.iloc[0, 0])
    df = pd.read_sql("SELECT COUNT(*) AS n FROM core.financials_quarterly WHERE t0_date IS NULL", conn)
    out["financials_missing_t0_date"] = int(df.iloc[0, 0])
    df = pd.read_sql("""SELECT COALESCE(sector_code,'(null)') AS sector_code, COUNT(*) AS n
                     FROM core.universe_stocks GROUP BY 1 ORDER BY 2 DESC""", conn)
    out["universe_by_sector_code"] = {str(r.sector_code): int(r.n) for r in df.itertuples(index=False)}
    df = pd.read_sql("SELECT MIN(trade_date) AS dmin, MAX(trade_date) AS dmax FROM core.prices_daily", conn)
    out["prices_date_range"] = {"min": str(df.iloc[0, 0]) if df.iloc[0, 0] is not None else None,
                                "max": str(df.iloc[0, 1]) if df.iloc[0, 1] is not None else None}
    df = pd.read_sql("SELECT MIN(trade_date) AS dmin, MAX(trade_date) AS dmax FROM core.index_prices_daily", conn)
    out["index_date_range"] = {"min": str(df.iloc[0, 0]) if df.iloc[0, 0] is not None else None,
                               "max": str(df.iloc[0, 1]) if df.iloc[0, 1] is not None else None}
    return out
