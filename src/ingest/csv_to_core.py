from __future__ import annotations
import io
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Iterable, TYPE_CHECKING
import pandas as pd

if TYPE_CHECKING:
    from psycopg2.extensions import connection as PGConn
else:
    PGConn = Any


@dataclass(frozen=True)
class CopySpec:
    table: str
    columns: list[str]
    date_cols: list[str]
    timestamptz_cols: list[str] = field(default_factory=list)
    uppercase_cols: list[str] = field(default_factory=list)


def _normalize_df(df: pd.DataFrame, col_map: Optional[Dict[str, str]], spec: CopySpec) -> pd.DataFrame:
    d = df.copy()
    d.columns = [str(c).strip() for c in d.columns]
    if col_map:
        d = d.rename(columns=col_map)
    missing = [c for c in spec.columns if c not in d.columns]
    if missing:
        raise ValueError(f"CSV missing required columns for {spec.table}: {missing}")
    for c in spec.date_cols:
        d[c] = pd.to_datetime(d[c], errors="coerce").dt.date
    for c in spec.uppercase_cols:
        d[c] = d[c].map(lambda value: value if pd.isna(value) else str(value).strip().upper())
    for c in spec.timestamptz_cols:
        converted = []
        for idx, value in d[c].items():
            if pd.isna(value):
                converted.append(None)
                continue
            try:
                ts = pd.Timestamp(value)
            except Exception as exc:
                raise ValueError(f"{spec.table}.{c}[{idx}] gecersiz timestamp: {value!r}") from exc
            if ts.tzinfo is None or ts.utcoffset() is None:
                raise ValueError(
                    f"{spec.table}.{c}[{idx}] timezone icermeli; naive timestamp look-ahead riski yaratir"
                )
            converted.append(ts.to_pydatetime())
        d[c] = converted
    return d[spec.columns]


def _df_to_csv_buffer(df: pd.DataFrame) -> io.StringIO:
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)
    return buf


def copy_dataframe(conn: PGConn, spec: CopySpec, df: pd.DataFrame, upsert: bool, key_cols: list[str]) -> int:
    if df.empty:
        return 0
    cols = spec.columns
    col_list = ", ".join(cols)
    with conn:
        with conn.cursor() as cur:
            # LIKE ... INCLUDING DEFAULTS BIGSERIAL/sequence varsayilanini gecici
            # tabloya tasiyip kaynak sequence'ini gereksiz yere tuketiyordu. Yalniz
            # secilen sutunlarin tiplerini kopyala; default/identity/constraint alma.
            cur.execute(
                f"CREATE TEMP TABLE tmp_copy ON COMMIT DROP AS "
                f"SELECT {col_list} FROM {spec.table} WITH NO DATA"
            )
            cur.copy_expert(f"COPY tmp_copy ({col_list}) FROM STDIN WITH (FORMAT csv)", _df_to_csv_buffer(df))
            if not upsert:
                cur.execute(f"INSERT INTO {spec.table} ({col_list}) SELECT {col_list} FROM tmp_copy")
                return int(df.shape[0])
            if not key_cols:
                raise ValueError("key_cols required for upsert=True")
            set_cols = [c for c in cols if c not in key_cols]
            set_sql = ", ".join([f"{c}=EXCLUDED.{c}" for c in set_cols])
            cur.execute(f"""
                INSERT INTO {spec.table} ({col_list})
                SELECT {col_list} FROM tmp_copy
                ON CONFLICT ({", ".join(key_cols)})
                DO UPDATE SET {set_sql}
            """)
            return int(df.shape[0])


def _iter_csv_chunks(csv_path: str, chunksize: int) -> Iterable[pd.DataFrame]:
    for chunk in pd.read_csv(csv_path, chunksize=chunksize):
        yield chunk


def ingest_csv(conn: PGConn, csv_path: str, spec: CopySpec, col_map: Optional[Dict[str, str]] = None,
               upsert: bool = True, key_cols: Optional[list[str]] = None, chunksize: Optional[int] = None) -> int:
    key_cols = key_cols or []
    total = 0
    if chunksize is None:
        df = pd.read_csv(csv_path)
        df2 = _normalize_df(df, col_map, spec)
        return copy_dataframe(conn, spec, df2, upsert=upsert, key_cols=key_cols)
    for chunk in _iter_csv_chunks(csv_path, chunksize=int(chunksize)):
        df2 = _normalize_df(chunk, col_map, spec)
        total += copy_dataframe(conn, spec, df2, upsert=upsert, key_cols=key_cols)
    return total


UNIVERSE_SPEC = CopySpec(
    table="core.universe_stocks",
    columns=["ticker", "company_name", "sector_index_code", "sector_code", "is_active"],
    date_cols=[]
)

PRICES_SPEC = CopySpec(
    table="core.prices_daily",
    columns=["ticker", "trade_date", "open", "high", "low", "close", "adj_close", "volume", "currency"],
    date_cols=["trade_date"]
)

INDEX_SPEC = CopySpec(
    table="core.index_prices_daily",
    columns=["index_code", "trade_date", "open", "high", "low", "close", "volume", "currency"],
    date_cols=["trade_date"]
)

FIN_SPEC = CopySpec(
    table="core.financials_quarterly",
    columns=[
        "ticker", "period_end", "version_tag", "report_date", "t0_date", "t0_source", "unit_scale",
        "revenue", "cogs", "gross_profit", "ebit", "net_income", "interest_exp",
        "total_assets", "total_equity", "current_assets", "current_liabilities",
        "cash_and_eq", "st_investments", "receivables", "inventory", "debt_st", "debt_lt",
        "cfo", "capex", "shares_out", "shares_diluted"
    ],
    date_cols=["period_end", "report_date", "t0_date"]
)


BANK_METRICS_SPEC = CopySpec(
    table="core.bank_metrics_quarterly",
    columns=[
        "ticker", "period_end", "version_tag", "version_sequence",
        "published_at", "source_disclosure_id", "roe_ttm", "bvps", "payout_sus",
    ],
    date_cols=["period_end"],
    timestamptz_cols=["published_at"],
)


BANK_ASSUMPTIONS_SPEC = CopySpec(
    table="analytics.bank_valuation_assumptions",
    columns=[
        "scope_type", "scope_code", "effective_at", "coe", "macro_cap",
        "risk_free_rate", "tier_cap", "payout_missing_factor", "band_width_shadow_mode",
        "max_halfwidth", "source", "metadata",
    ],
    date_cols=[],
    timestamptz_cols=["effective_at"],
    uppercase_cols=["scope_type", "scope_code"],
)
