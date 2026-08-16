from __future__ import annotations
import ast
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd
from src.db.bulk_upsert_ratios import upsert_ratios_copy
from src.utils.calendar import get_trading_days, next_trading_day

OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b, ast.USub: lambda a: -a}
CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Is: lambda a, b: a is b, ast.IsNot: lambda a, b: a is not b}
ALLOWED_FUNCS = {"avg", "lag", "lag4q", "sum4q", "ttm", "abs", "coalesce", "nz", "days_in_period", "log", "log1p", "max", "min"}


def coalesce(*args):
    for a in args:
        if a is not None:
            return a
    return None


def nz(x, default=0):
    return default if x is None else x


def _is_finite(x):
    try:
        return x is not None and math.isfinite(float(x))
    except Exception:
        return False


def _quarter_end(value: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("period_end date olmali")
    quarter = (value.month - 1) // 3
    next_month = quarter * 3 + 4
    year = value.year
    if next_month > 12:
        next_month -= 12
        year += 1
    return date(year, next_month, 1) - timedelta(days=1)


def _shift_quarter_end(value: date, offset: int) -> date:
    anchor = _quarter_end(value)
    q_index = anchor.year * 4 + ((anchor.month - 1) // 3) + offset
    year, quarter = divmod(q_index, 4)
    month = quarter * 3 + 3
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _row_version_key(row: dict) -> tuple:
    # Legacy core.financials_quarterly has no published_at. Use its available
    # report/t0 trace deterministically instead of physical row order.
    return (
        row.get("t0_date") or row.get("report_date") or date.min,
        str(row.get("version_tag") or ""),
    )


class QuarterSeries:
    def __init__(self, rows: List[dict]):
        selected: dict[date, dict] = {}
        for row in rows:
            pe = row.get("period_end")
            if not isinstance(pe, date) or isinstance(pe, datetime):
                raise ValueError("QuarterSeries period_end date olmali")
            pe = _quarter_end(pe)
            previous = selected.get(pe)
            if previous is None or _row_version_key(row) > _row_version_key(previous):
                selected[pe] = row
        self.by_period = selected
        self.rows = [selected[pe] for pe in sorted(selected)]

    def get(self, pe: date, field: str):
        row = self.by_period.get(_quarter_end(pe))
        return None if row is None else row.get(field)

    def lag(self, pe: date, field: str, n: int):
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            return None
        return self.get(_shift_quarter_end(pe, -n), field)

    def avg(self, pe: date, field: str):
        x0 = self.get(pe, field)
        x1 = self.lag(pe, field, 1)
        if x0 is None and x1 is None:
            return None
        if x0 is None:
            return x1
        if x1 is None:
            return x0
        return (x0 + x1) / 2.0

    def sum4q(self, pe: date, field: str):
        vals = []
        for offset in (-3, -2, -1, 0):
            v = self.get(_shift_quarter_end(pe, offset), field)
            if v is None:
                return None
            vals.append(float(v))
        return float(sum(vals))

    def days_in_period(self, pe: date) -> int:
        current = _quarter_end(pe)
        previous = _shift_quarter_end(current, -1)
        if previous not in self.by_period:
            return 91
        d = (current - previous).days
        return int(d) if 60 <= d <= 120 else 91


def safe_eval_expr(expr: str, env: dict, qs: QuarterSeries, pe: date):
    tree = ast.parse(expr, mode="eval")

    def eval_node(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
            v = eval_node(node.operand)
            return None if v is None else OPS[type(node.op)](v)
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            a, b = eval_node(node.left), eval_node(node.right)
            if a is None or b is None:
                return None
            if isinstance(node.op, ast.Div) and float(b) == 0.0:
                return None
            return OPS[type(node.op)](a, b)
        if isinstance(node, ast.Call):
            fn = node.func.id if isinstance(node.func, ast.Name) else None
            if fn not in ALLOWED_FUNCS:
                raise ValueError(f"Function not allowed: {fn}")
            if fn in {"avg", "lag", "lag4q", "sum4q", "ttm"}:
                if len(node.args) < 1:
                    return None
                a0 = node.args[0]
                field = a0.id if isinstance(a0, ast.Name) else (a0.value if isinstance(a0, ast.Constant) and isinstance(a0.value, str) else None)
                if not field:
                    return None
                if fn == "avg":
                    return qs.avg(pe, field)
                if fn == "lag":
                    if len(node.args) < 2:
                        return None
                    n = eval_node(node.args[1])
                    return None if n is None else qs.lag(pe, field, int(n))
                if fn == "lag4q":
                    return qs.lag(pe, field, 4)
                return qs.sum4q(pe, field)
            args = [eval_node(a) for a in node.args]
            if fn == "abs":
                return None if args[0] is None else abs(float(args[0]))
            if fn == "coalesce":
                return coalesce(*args)
            if fn == "nz":
                return nz(args[0], 0 if len(args) == 1 else args[1])
            if fn == "days_in_period":
                return qs.days_in_period(pe)
            if fn == "log":
                return None if args[0] is None or args[0] <= 0 else math.log(float(args[0]))
            if fn == "log1p":
                return None if args[0] is None else math.log1p(float(args[0]))
            if fn == "max":
                aa = [a for a in args if a is not None]
                return None if not aa else float(max(aa))
            if fn == "min":
                aa = [a for a in args if a is not None]
                return None if not aa else float(min(aa))
        raise ValueError(f"Unsupported expression: {ast.dump(node)}")

    return eval_node(tree.body)


def safe_eval_condition(cond: str, env: dict, qs: QuarterSeries, pe: date) -> bool:
    s = cond.strip().replace(" is not null", " is not None").replace(" is null", " is None")
    tree = ast.parse(s, mode="eval")

    def eval_node(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not bool(eval_node(node.operand))
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return all(bool(eval_node(v)) for v in node.values)
            if isinstance(node.op, ast.Or):
                return any(bool(eval_node(v)) for v in node.values)
        if isinstance(node, ast.Compare):
            left = eval_node(node.left)
            for op_node, comp in zip(node.ops, node.comparators):
                right = eval_node(comp)
                op_type = type(op_node)
                if op_type in (ast.Is, ast.IsNot):
                    ok = CMP[op_type](left, right)
                else:
                    ok = False if left is None or right is None else CMP[op_type](left, right)
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, (ast.Call, ast.BinOp, ast.UnaryOp)):
            return safe_eval_expr(ast.unparse(node), env, qs, pe)
        return bool(eval_node(node))

    return bool(eval_node(tree.body))


@dataclass(frozen=True)
class RatioSpec:
    ratio_name: str
    pillar: str
    core_or_val: str
    ratio_type: str
    formula: str
    na_if: List[str]


def load_ratio_specs(ratios_json_path: str | Path) -> Dict[str, RatioSpec]:
    cfg = json.loads(Path(ratios_json_path).read_text(encoding="utf-8"))
    out = {}
    for rn, s in cfg["ratios"].items():
        out[rn] = RatioSpec(rn, s["pillar"], s["core_or_val"], s["type"], s.get("formula", ""), s.get("na_if", []))
    return out

FIN_NUM_FIELDS = ["revenue", "cogs", "gross_profit", "ebit", "net_income", "interest_exp", "total_assets", "total_equity", "current_assets", "current_liabilities", "cash_and_eq", "st_investments", "receivables", "inventory", "debt_st", "debt_lt", "cfo", "capex", "shares_out", "shares_diluted"]


def fill_missing_t0_dates(conn) -> None:
    df = pd.read_sql("""
      SELECT ticker, period_end, version_tag, report_date, t0_date
      FROM core.financials_quarterly
      WHERE t0_date IS NULL
    """, conn)
    if df.empty:
        return
    trading = get_trading_days(conn)
    updates = []
    for r in df.itertuples(index=False):
        pe = pd.to_datetime(r.period_end).date()
        if r.report_date is not None:
            rd = pd.to_datetime(r.report_date).date()
            src = "reported"
        else:
            rd = pe + timedelta(days=45)
            src = "estimated"
        t0 = next_trading_day(rd, trading)
        updates.append((rd, t0, src, r.ticker, pe, str(r.version_tag)))
    with conn:
        with conn.cursor() as cur:
            cur.executemany("""
              UPDATE core.financials_quarterly
              SET report_date = COALESCE(report_date, %s),
                  t0_date = %s,
                  t0_source = %s
              WHERE ticker = %s AND period_end = %s AND version_tag = %s
            """, updates)


def fetch_financials(conn, since_period_end: Optional[str] = None) -> pd.DataFrame:
    q = "SELECT * FROM core.financials_quarterly WHERE 1=1"
    params = {}
    if since_period_end:
        q += " AND period_end >= %(since)s"
        params["since"] = since_period_end
    q += " ORDER BY ticker ASC, period_end ASC"
    df = pd.read_sql(q, conn, params=params)
    if df.empty:
        return df
    df["period_end"] = pd.to_datetime(df["period_end"]).dt.date
    df["t0_date"] = pd.to_datetime(df["t0_date"]).dt.date
    df["version_tag"] = df["version_tag"].astype(str)
    df["unit_scale"] = df["unit_scale"].fillna(1).astype(int)
    return df


def apply_unit_scale(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    for col in FIN_NUM_FIELDS:
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce") * d["unit_scale"].astype(float)
            d[col] = d[col].where(pd.notna(d[col]), None)
    return d


def derive_fields_per_ticker(rows: List[dict]) -> List[dict]:
    for r in rows:
        if r.get("gross_profit") is None:
            rev, cogs = r.get("revenue"), r.get("cogs")
            if rev is not None and cogs is not None:
                r["gross_profit"] = rev - cogs
    return rows


def fetch_prices_for_pairs(conn, pairs: List[Tuple[str, date]]) -> Dict[Tuple[str, date], float]:
    if not pairs:
        return {}
    with conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE tmp_pairs (ticker TEXT, t0_date DATE) ON COMMIT DROP")
            from psycopg2.extras import execute_values
            execute_values(cur, "INSERT INTO tmp_pairs (ticker, t0_date) VALUES %s", pairs, page_size=5000)
            # t0_date can fall on a weekend/holiday (e.g. a Friday-evening KAP
            # disclosure makes t0 a Saturday). Use the last available price on
            # or before t0, limited to a short lookback so stale prices from a
            # long halt cannot leak into valuation ratios. Same convention as
            # the expected-band builder (p0 = last price <= t0).
            cur.execute("""
              SELECT p.ticker, p.t0_date, x.px
              FROM tmp_pairs p
              JOIN LATERAL (
                SELECT COALESCE(pr.adj_close, pr.close) AS px
                FROM core.prices_daily pr
                WHERE pr.ticker=p.ticker
                  AND pr.trade_date <= p.t0_date
                  AND pr.trade_date >= p.t0_date - INTERVAL '10 days'
                ORDER BY pr.trade_date DESC
                LIMIT 1
              ) x ON true
            """)
            rows = cur.fetchall()
    return {(t, d): float(px) for (t, d, px) in rows if px is not None}


def compute_ratios_for_ticker(ticker: str, df_t: pd.DataFrame, ratio_specs: Dict[str, RatioSpec], price_map: Dict[Tuple[str, date], float]) -> pd.DataFrame:
    rows = [dict(r._asdict()) for r in df_t.itertuples(index=False)]
    for rec in rows:
        for k, v in list(rec.items()):
            if pd.isna(v):
                rec[k] = None
    rows = derive_fields_per_ticker(rows)
    qs = QuarterSeries(rows)
    out = []
    for rec in rows:
        pe, vt = rec["period_end"], str(rec["version_tag"])
        t0 = rec.get("t0_date")
        env = dict(rec)
        env["price"] = price_map.get((ticker, t0)) if t0 is not None else None
        for rn, spec in ratio_specs.items():
            if not spec.formula:
                env[rn] = None
                out.append((ticker, pe, vt, rn, None, True))
                continue
            is_na = False
            for rule in spec.na_if:
                try:
                    if safe_eval_condition(rule, env, qs, pe):
                        is_na = True
                        break
                except Exception:
                    is_na = True
                    break
            if is_na:
                env[rn] = None
                out.append((ticker, pe, vt, rn, None, True))
                continue
            try:
                val = safe_eval_expr(spec.formula, env, qs, pe)
            except Exception:
                val = None
            if not _is_finite(val):
                env[rn] = None
                out.append((ticker, pe, vt, rn, None, True))
            else:
                env[rn] = float(val)
                out.append((ticker, pe, vt, rn, float(val), False))
    return pd.DataFrame(out, columns=["ticker", "period_end", "version_tag", "ratio_name", "ratio_value", "is_na"])


def compute_all_ratios(conn, ratios_json_path: str, since_period_end: Optional[str] = None) -> pd.DataFrame:
    ratio_specs = load_ratio_specs(ratios_json_path)
    fill_missing_t0_dates(conn)
    fin = fetch_financials(conn, since_period_end=since_period_end)
    if fin.empty:
        return pd.DataFrame(columns=["ticker", "period_end", "version_tag", "ratio_name", "ratio_value", "is_na"])
    fin = apply_unit_scale(fin)
    pairs = [(str(r.ticker), r.t0_date) for r in fin[["ticker", "t0_date"]].dropna().drop_duplicates().itertuples(index=False)]
    price_map = fetch_prices_for_pairs(conn, pairs)
    parts = [compute_ratios_for_ticker(str(t), g, ratio_specs, price_map) for t, g in fin.groupby("ticker", sort=False)]
    return pd.concat(parts, axis=0) if parts else pd.DataFrame()


def run_ratios_calc(conn, ratios_json_path: str, since_period_end: Optional[str] = None) -> None:
    df = compute_all_ratios(conn, ratios_json_path, since_period_end=since_period_end)
    if not df.empty:
        upsert_ratios_copy(conn, df)
