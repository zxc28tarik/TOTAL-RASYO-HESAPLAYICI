from __future__ import annotations

import json
import os
import uuid
from datetime import date
from itertools import combinations
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.utils.calendar import get_trading_days, add_trading_days

MODULES = ["m2", "m1", "m3", "ek4", "ek1", "ek9"]
MODULE_KEYS = {"m2": "M2", "m1": "M1", "m3": "M3", "ek4": "Ek4", "ek1": "Ek1", "ek9": "Ek9"}


def _weight_grid(step: float = 0.10, min_m2: float = 0.15) -> np.ndarray:
    """All weight combinations on the 6-module simplex with the given step.

    min_m2 keeps the search inside the project's design philosophy (M2 is the
    main axis); set it to 0.0 to search the full simplex.
    """
    k = int(round(1.0 / step))
    combos: List[Tuple[float, ...]] = []
    # Stars and bars over 6 dimensions summing to k.
    for bars in combinations(range(k + 5), 5):
        parts = []
        prev = -1
        for b in bars:
            parts.append(b - prev - 1)
            prev = b
        parts.append(k + 5 - prev - 1)
        w = tuple(p / k for p in parts)
        if w[0] + 1e-9 >= min_m2:
            combos.append(w)
    return np.array(combos, dtype=float)


def _rank_along_axis0(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, axis=0, kind="stable")
    ranks = np.empty_like(order, dtype=float)
    n = a.shape[0]
    idx = np.arange(n, dtype=float)
    for j in range(a.shape[1]):
        ranks[order[:, j], j] = idx
    return ranks


def _spearman_vs_target(comp: np.ndarray, target_rank: np.ndarray) -> np.ndarray:
    """Spearman correlation of each composite column against the target ranks."""
    cr = _rank_along_axis0(comp)
    cr = cr - cr.mean(axis=0, keepdims=True)
    t = target_rank - target_rank.mean()
    denom = np.sqrt((cr ** 2).sum(axis=0)) * np.sqrt((t ** 2).sum())
    with np.errstate(divide="ignore", invalid="ignore"):
        ic = (cr * t[:, None]).sum(axis=0) / denom
    return np.where(np.isfinite(ic), ic, np.nan)


def _forward_excess_returns(
    conn,
    asof_dates: List[date],
    hold_days: int,
    trading: List[date],
    bench_index: str = "XU100",
) -> Dict[date, pd.Series]:
    """ticker -> forward excess return (stock - benchmark) for each asof date."""
    hold_end = {d: add_trading_days(d, hold_days, trading) for d in asof_dates}
    needed = sorted(set(asof_dates) | set(hold_end.values()))
    if not needed:
        return {}

    px = pd.read_sql(
        """
        SELECT ticker, trade_date, COALESCE(adj_close, close) AS px
        FROM core.prices_daily
        WHERE trade_date = ANY(%(d)s)
        """,
        conn,
        params={"d": needed},
    )
    if px.empty:
        return {}
    px["trade_date"] = pd.to_datetime(px["trade_date"]).dt.date
    piv = px.pivot_table(index="trade_date", columns="ticker", values="px", aggfunc="last")

    bx = pd.read_sql(
        """
        SELECT trade_date, close AS px
        FROM core.index_prices_daily
        WHERE index_code=%(i)s AND trade_date = ANY(%(d)s)
        """,
        conn,
        params={"i": bench_index, "d": needed},
    )
    bmap = {}
    if not bx.empty:
        bx["trade_date"] = pd.to_datetime(bx["trade_date"]).dt.date
        bmap = dict(zip(bx["trade_date"], pd.to_numeric(bx["px"], errors="coerce")))

    out: Dict[date, pd.Series] = {}
    for d in asof_dates:
        e = hold_end[d]
        if d not in piv.index or e not in piv.index:
            continue
        p0 = piv.loc[d]
        p1 = piv.loc[e]
        ret = (p1 / p0 - 1.0).replace([np.inf, -np.inf], np.nan)
        b0, b1 = bmap.get(d), bmap.get(e)
        bench_ret = (float(b1) / float(b0) - 1.0) if b0 and b1 and float(b0) > 0 else 0.0
        out[d] = (ret - bench_ret).dropna()
    return out


def optimize_weights(
    conn,
    start_date: str,
    end_date: str,
    hold_days: int = 20,
    step: float = 0.10,
    objective: str = "ic",
    min_m2: float = 0.15,
    top_quantile: float = 0.20,
    min_tickers: int = 10,
    out_dir: str = "outputs",
) -> dict:
    """Grid-search module weights against realised forward excess returns.

    Uses the analytics.module_scores history in [start_date, end_date]:
      - objective "ic": average Spearman rank-IC between the composite score
        and the forward `hold_days`-day excess return over XU100.
      - objective "topq": average forward excess return of the top quantile
        of the composite score.

    Writes a full result CSV plus a weights JSON compatible with --weights.
    Overfitting warning: with few rebalance dates the best combo is noise;
    prefer wide date ranges and read the per-module ICs first.
    """
    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()

    ms = pd.read_sql(
        """
        SELECT ticker, asof_date, m1, m2, m3, ek1, ek4, ek9
        FROM analytics.module_scores
        WHERE asof_date >= %(s)s AND asof_date <= %(e)s AND horizon_days=63
        """,
        conn,
        params={"s": start, "e": end},
    )
    if ms.empty:
        raise ValueError("No module_scores rows in the given range. Run run-daily/backtest first.")
    ms["asof_date"] = pd.to_datetime(ms["asof_date"]).dt.date
    for c in MODULES:
        ms[c] = pd.to_numeric(ms[c], errors="coerce")

    trading = get_trading_days(conn)
    asof_dates = sorted(ms["asof_date"].unique().tolist())
    fwd = _forward_excess_returns(conn, asof_dates, hold_days, trading)

    W = _weight_grid(step=step, min_m2=min_m2)  # (K, 6) in MODULES order
    K = W.shape[0]

    obj_sum = np.zeros(K, dtype=float)
    obj_cnt = np.zeros(K, dtype=float)
    module_ic_sum = np.zeros(len(MODULES), dtype=float)
    module_ic_cnt = 0
    used_dates = 0

    for d in asof_dates:
        f = fwd.get(d)
        if f is None or f.empty:
            continue
        g = ms[ms["asof_date"] == d].dropna(subset=MODULES)
        g = g[g["ticker"].isin(f.index)]
        if g.shape[0] < min_tickers:
            continue
        M = g[MODULES].to_numpy(dtype=float)          # (N, 6)
        y = f.reindex(g["ticker"]).to_numpy(dtype=float)
        target_rank = pd.Series(y).rank(method="average").to_numpy(dtype=float)

        comp = M @ W.T                                # (N, K)
        if objective == "topq":
            n = M.shape[0]
            q = max(int(np.ceil(n * top_quantile)), 1)
            order = np.argsort(-comp, axis=0, kind="stable")[:q, :]
            vals = y[order].mean(axis=0)
        else:
            vals = _spearman_vs_target(comp, target_rank)

        good = np.isfinite(vals)
        obj_sum[good] += vals[good]
        obj_cnt[good] += 1.0

        m_ic = _spearman_vs_target(M, target_rank)
        if np.all(np.isfinite(m_ic)):
            module_ic_sum += m_ic
            module_ic_cnt += 1
        used_dates += 1

    if used_dates == 0:
        raise ValueError("No usable dates (need module scores + forward prices for at least one date).")

    with np.errstate(divide="ignore", invalid="ignore"):
        obj_mean = np.where(obj_cnt > 0, obj_sum / np.maximum(obj_cnt, 1.0), np.nan)

    res = pd.DataFrame(W, columns=MODULES)
    res["objective_mean"] = obj_mean
    res["n_dates"] = obj_cnt.astype(int)
    res = res.sort_values("objective_mean", ascending=False).reset_index(drop=True)

    module_ics = (module_ic_sum / module_ic_cnt) if module_ic_cnt > 0 else np.full(len(MODULES), np.nan)
    module_ic_map = {MODULE_KEYS[m]: (None if not np.isfinite(v) else float(v)) for m, v in zip(MODULES, module_ics)}

    run_id = str(uuid.uuid4())[:8]
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, f"weight_opt_{run_id}.csv")
    res.to_csv(csv_path, index=False)

    best = res.iloc[0]
    best_weights = {MODULE_KEYS[m]: float(best[m]) for m in MODULES}
    json_path = os.path.join(out_dir, f"weights_optimized_{run_id}.json")
    with open(json_path, "w", encoding="utf-8") as fjson:
        json.dump({
            "meta": {
                "source": "optimize-weights",
                "run_id": run_id,
                "start": str(start), "end": str(end),
                "hold_days": int(hold_days), "step": float(step),
                "objective": objective, "n_dates_used": int(used_dates),
                "objective_mean_best": None if not np.isfinite(best["objective_mean"]) else float(best["objective_mean"]),
                "module_ic": module_ic_map,
            },
            "base_weights": best_weights,
        }, fjson, ensure_ascii=False, indent=2)

    return {
        "run_id": run_id,
        "best_weights": best_weights,
        "objective_mean_best": None if not np.isfinite(best["objective_mean"]) else float(best["objective_mean"]),
        "module_ic": module_ic_map,
        "n_dates_used": int(used_dates),
        "n_combos": int(K),
        "csv_path": csv_path,
        "json_path": json_path,
    }
