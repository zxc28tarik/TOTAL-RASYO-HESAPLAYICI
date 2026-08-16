from __future__ import annotations
from typing import Dict, Any, Optional
import pandas as pd

DEFAULT_MIN_COVERAGE = 0.95


def _date_range_counts(conn, table: str, key_cols: str, start: str, end: str, extra_where: str = "", params: Optional[dict] = None) -> pd.DataFrame:
    params = params or {}
    q = f"""
    SELECT {key_cols} AS key, COUNT(*) AS n,
           MIN(trade_date) AS dmin, MAX(trade_date) AS dmax
    FROM {table}
    WHERE trade_date >= %(start)s AND trade_date <= %(end)s
    {extra_where}
    GROUP BY 1
    ORDER BY 1
    """
    return pd.read_sql(q, conn, params={"start": start, "end": end, **params})


def doctor_report(conn, start: str, end: str, market_index: str = "XU100", min_coverage: float = DEFAULT_MIN_COVERAGE) -> Dict[str, Any]:
    trading = pd.read_sql(
        "SELECT COUNT(*) AS n FROM core.index_prices_daily WHERE index_code=%(i)s AND trade_date >= %(s)s AND trade_date <= %(e)s",
        conn, params={"i": market_index, "s": start, "e": end}
    )
    n_trading = int(trading.iloc[0, 0]) if not trading.empty else 0
    out: Dict[str, Any] = {"range": {"start": start, "end": end}, "market_index": market_index, "n_trading_days": n_trading}
    df_u = pd.read_sql("SELECT ticker, sector_index_code FROM core.universe_stocks WHERE is_active=true", conn)
    tickers = df_u["ticker"].astype(str).tolist() if not df_u.empty else []
    indices = sorted(set(["XU100"] + df_u["sector_index_code"].fillna("XU100").astype(str).tolist())) if not df_u.empty else ["XU100"]
    df_p = _date_range_counts(conn, "core.prices_daily", "ticker", start, end)
    missing = []
    for t in tickers:
        r = df_p[df_p["key"] == t]
        cov = 0.0 if r.empty else float(r["n"].iloc[0]) / max(n_trading, 1)
        if cov < min_coverage:
            missing.append(t)
    out["prices_coverage"] = {"min_coverage": min_coverage, "missing_or_low": missing[:500], "count": len(missing)}
    df_i = _date_range_counts(conn, "core.index_prices_daily", "index_code", start, end)
    missing_i = []
    for idx in indices:
        r = df_i[df_i["key"] == idx]
        cov = 0.0 if r.empty else float(r["n"].iloc[0]) / max(n_trading, 1)
        if cov < min_coverage:
            missing_i.append(idx)
    out["index_coverage"] = {"min_coverage": min_coverage, "missing_or_low": missing_i, "count": len(missing_i)}
    return out
