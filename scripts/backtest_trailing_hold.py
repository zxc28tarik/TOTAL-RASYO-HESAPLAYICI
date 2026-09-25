from __future__ import annotations

"""Buy AL names with a favourable P/B, hold them, sell on price alone; compare to random.

The brief, in the investor's words: buy when the score says AL and P/B is
favourable, hold, and sell according to how the price rises or falls. No time
limit -- twenty days or ten months. Do it for a year and a half, however many
trades that turns out to be, and compare it with random stocks.

Rules, frozen in RULES and committed before the first run
---------------------------------------------------------
* Buy on any session: this month's decision is AL and P/B, priced at the
  previous session's close, sits in the cheaper half of the month's positive-P/B
  cohort. Highest score first when slots are short.
* Sell on price alone, with a 20% trailing stop: when the previous session's
  close is 20% or more below the highest close since purchase, sell today. A
  rising stock is held and its stop rises with it; a falling one is cut. There is
  no time limit and no exit on score or P/B.
  Why 20%: a single BIST stock swings roughly 12-15% in a normal month, so a
  tighter stop sells ordinary noise; 20% is about one and a half months of it.
* A name stopped out is not bought back in the same signal month, so a stop is
  not undone by the next session's buy.
* Ten slots of 10%, idle money in XU100, 0.2% per side on every trade.

The random comparison
---------------------
The same engine runs 1000 more times with one change: at every moment the
strategy finds k qualifying names, the random twin picks k names at random from
the same month's scored cohort instead (excluding what it holds and what it was
stopped out of this month). Same moments, same count, same stop, same costs.
Where the strategy lands in that distribution is the whole result.
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backtest_horizon_strategy import (
    PointInTime, build_panel, phantom_moves, priced_cohort, rescale_for_phantoms, thin_sessions,
)
from scripts.backtest_pb_two_stage import (
    CAPS, INDEX, P4, PRICES, ROUTES, XU100, current_nominal, load_equity, load_p4,
    load_xu100, raw_market_inputs,
)

CONTRACT = "TRAILING_HOLD_VS_RANDOM_V1"
OUTPUT = ROOT / "data/audit/trailing_hold_vs_random_v1"
SCRIPT = Path(__file__).resolve()

RULES = {
    "window_first_signal": "2025-02",
    "window_last_signal": "2026-07",
    "window_end": "2026-07-31",
    "buy_if": "decision == AL this month and P/B > 0 and P/B percentile <= 0.50, priced at the previous close",
    "check": "every session",
    "sell_if": "previous close <= 0.80 * highest close since purchase (20% trailing stop); nothing else",
    "trailing_stop": 0.20,
    "time_limit": None,
    "no_rebuy_after_stop": "same signal month",
    "max_positions": 10,
    "slot_size": "1/10 of portfolio value at purchase",
    "idle_money": "held in XU100",
    "cost_per_side": 0.002,
    "priority": "highest score_percentile first",
    "random_twin": "same moments, same count k, k names drawn uniformly from the month's scored cohort "
                   "excluding held and stopped-out names; same stop, same costs",
    "random_draws": 1000,
    "random_seed": 20250203,
    "data_rules": "phantom-move rescaling and thin-session filter as in BLIND_HORIZON_STRATEGY_V1",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Holding:
    units: float
    entry_date: pd.Timestamp
    entry_price: float
    peak: float
    month: str


def daily_menus(view: PointInTime, sessions: pd.DatetimeIndex) -> dict:
    """What the strategy may know each session: the priced cohort. Shared by every run."""
    menus = {}
    for day in sessions:
        cohort = priced_cohort(view, day)
        if cohort.empty:
            menus[day] = (None, [], [])
            continue
        qualified = cohort.loc[cohort.qualifies].sort_values("score_percentile", ascending=False)
        menus[day] = (view.month(day), list(qualified.index), list(cohort.index))
    return menus


def run(menus: dict, adj: pd.DataFrame, index: pd.Series, sessions: pd.DatetimeIndex, *,
        rng: np.random.Generator | None = None) -> dict:
    """One path. With ``rng`` the picks are random twins of the strategy's picks."""
    cost = RULES["cost_per_side"]
    slots = RULES["max_positions"]
    stop = 1.0 - RULES["trailing_stop"]
    filled = adj.ffill()
    sleeve = 1.0 / float(index.loc[sessions[0]])
    held: dict[str, Holding] = {}
    stopped: dict[str, str] = {}
    trades, values = [], []
    previous = None
    for day in sessions:
        prices = filled.loc[day]
        level = float(index.loc[day])
        month, qualified, cohort = menus[day]
        # ---- sell on price alone, judged on the previous close
        if previous is not None:
            for ticker in list(held):
                holding = held[ticker]
                last = float(filled.at[previous, ticker])
                holding.peak = max(holding.peak, last)
                if last <= stop * holding.peak:
                    proceeds = holding.units * prices[ticker]
                    net = proceeds * (1 - cost)
                    sleeve += net * (1 - cost) / level
                    trades.append({"ticker": ticker, "entry_date": str(holding.entry_date.date()),
                                   "exit_date": str(day.date()), "entry_price": holding.entry_price,
                                   "exit_price": float(prices[ticker]), "exit": "IZ_SUREN_STOP"})
                    stopped[ticker] = month
                    del held[ticker]
        # ---- buy
        free = slots - len(held)
        if free > 0 and qualified:
            eligible = [t for t in (qualified if rng is None else cohort)
                        if t not in held and stopped.get(t) != month
                        and t in adj.columns and pd.notna(adj.at[day, t])]
            if rng is not None:
                k = sum(1 for t in qualified if t not in held and stopped.get(t) != month
                        and t in adj.columns and pd.notna(adj.at[day, t]))
                eligible = list(rng.permutation(eligible)[:k]) if k else []
            for ticker in eligible[:free]:
                total = sleeve * level + sum(h.units * prices[t] for t, h in held.items())
                gross = min(total / slots, sleeve * level)
                if gross <= 0:
                    break
                sleeve -= gross / level
                invest = gross * (1 - cost) * (1 - cost)
                price = float(prices[ticker])
                held[ticker] = Holding(units=invest / price, entry_date=day, entry_price=price,
                                       peak=price, month=month)
        values.append(sleeve * level + sum(h.units * prices[t] for t, h in held.items()))
        previous = day
    last = sessions[-1]
    for ticker, holding in held.items():
        trades.append({"ticker": ticker, "entry_date": str(holding.entry_date.date()),
                       "exit_date": str(last.date()), "entry_price": holding.entry_price,
                       "exit_price": float(filled.at[last, ticker]), "exit": "ACIK"})
    curve = pd.Series(values, index=sessions)
    return {"curve": curve, "trades": trades}


def rules_commit() -> dict:
    relative = str(SCRIPT.relative_to(ROOT))
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT).returncode != 0
    commit = subprocess.run(["git", "log", "-1", "--format=%H %cI", "--", relative], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    return {"script": relative, "last_commit": commit, "modified_since_commit": dirty}


def build(*, output_dir: Path = OUTPUT) -> dict:
    provenance = rules_commit()
    if provenance["modified_since_commit"]:
        raise RuntimeError("BLIND_RUN_REFUSED: commit the rules before running them")

    months = [str(p) for p in pd.period_range(RULES["window_first_signal"], RULES["window_last_signal"], freq="M")]
    p4 = load_p4(months)
    raw = raw_market_inputs(p4)
    merged = p4.merge(raw, on=["month", "ticker"])
    for module, banded in (("M3", "m3"), ("Ek4", "ek4"), ("Ek9", "ek9")):
        if not (merged[f"p4_{module}"] - merged[banded].astype(float)).abs().max() < 1e-9:
            raise ValueError(f"P4_REPRODUCTION_DRIFT:{module}")

    bench = load_xu100()
    bench.index = pd.to_datetime(bench.index)
    prices = pd.read_csv(PRICES, low_memory=False)
    prices = prices.loc[prices.ticker.eq(prices.price_source_ticker)].copy()
    prices["trade_date"] = pd.to_datetime(prices.trade_date)
    first = pd.Timestamp(p4.signal_date.min())
    end = pd.Timestamp(RULES["window_end"])
    calendar = bench.loc[:end].index
    events = phantom_moves(prices.loc[prices.ticker.isin(p4.ticker)], calendar)
    adj = rescale_for_phantoms(prices.pivot_table(index="trade_date", columns="ticker", values="adj_close",
                                                  aggfunc="last").reindex(calendar), events)
    closes = rescale_for_phantoms(prices.pivot_table(index="trade_date", columns="ticker", values="close",
                                                     aggfunc="last").reindex(calendar), events)
    thin = thin_sessions(adj).loc[first:end]
    sessions = thin.index[~thin]
    index = bench.reindex(calendar).ffill()

    panel = build_panel(p4, raw, load_equity(p4), current_nominal())
    view = PointInTime(panel, closes)
    menus = daily_menus(view, sessions)

    strategy = run(menus, adj, index, sessions)
    rng = np.random.default_rng(RULES["random_seed"])
    twins = [run(menus, adj, index, sessions, rng=rng) for _ in range(RULES["random_draws"])]

    total = lambda curve: float(curve.iloc[-1] / curve.iloc[0] - 1.0)
    drawdown = lambda curve: float((curve / curve.cummax() - 1.0).min())
    random_returns = np.array([total(t["curve"]) for t in twins])
    strategy_return = total(strategy["curve"])
    bench_path = index.reindex(sessions)

    trades = pd.DataFrame(strategy["trades"])
    if not trades.empty:
        trades["return"] = trades.exit_price / trades.entry_price - 1.0
        trades["days_held"] = (pd.to_datetime(trades.exit_date) - pd.to_datetime(trades.entry_date)).dt.days
        trades["xu100_same_days"] = (index.reindex(pd.to_datetime(trades.exit_date)).to_numpy()
                                     / index.reindex(pd.to_datetime(trades.entry_date)).to_numpy() - 1.0)
    random_trade_counts = np.array([len(t["trades"]) for t in twins])
    random_trade_means = np.array([np.mean([x["exit_price"] / x["entry_price"] - 1.0 for x in t["trades"]])
                                   if t["trades"] else np.nan for t in twins])

    result = {
        "from": str(sessions[0].date()), "to": str(sessions[-1].date()),
        "strategy_return": strategy_return,
        "strategy_max_drawdown": drawdown(strategy["curve"]),
        "xu100_return": float(bench_path.iloc[-1] / bench_path.iloc[0] - 1.0),
        "xu100_max_drawdown": drawdown(bench_path),
        "random_median_return": float(np.median(random_returns)),
        "random_p05": float(np.quantile(random_returns, 0.05)),
        "random_p95": float(np.quantile(random_returns, 0.95)),
        "random_share_beating_xu100": float((random_returns > float(bench_path.iloc[-1] / bench_path.iloc[0] - 1.0)).mean()),
        "strategy_percentile_among_random": float((random_returns < strategy_return).mean()),
        "trades": int(len(trades)),
        "trades_closed": int((trades.exit == "IZ_SUREN_STOP").sum()) if not trades.empty else 0,
        "trades_open_at_end": int((trades.exit == "ACIK").sum()) if not trades.empty else 0,
        "mean_trade_return": float(trades["return"].mean()) if not trades.empty else None,
        "random_mean_trade_return_median": float(np.nanmedian(random_trade_means)),
        "strategy_trade_mean_percentile_among_random": float(
            (random_trade_means < trades["return"].mean()).mean()) if not trades.empty else None,
        "median_days_held": float(trades.days_held.median()) if not trades.empty else None,
        "random_trade_count_median": float(np.median(random_trade_counts)),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    trades_path = output_dir / "trades.csv"
    trades.to_csv(trades_path, index=False)
    receipt = {
        "contract": CONTRACT, "blind_run": provenance, "rules": RULES, "result": result,
        "phantom_moves_rescaled": int(len(events)),
        "sessions_dropped_for_thin_stock_feed": [str(d.date()) for d in thin.index[thin]],
        "random_returns_quantiles": {str(q): float(np.quantile(random_returns, q))
                                     for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)},
        "not_modelled": [
            "XU100 is a price index; stock returns include dividends",
            "P/B uses current nominal capital; a later rights issue overstates past P/B",
            "the 1.5-year window was already seen under earlier rules; these rules were fixed before this run",
            "nominal TL, inflation not removed",
        ],
        "source_sha256": {"prices": _sha(PRICES), "index": _sha(INDEX), "routes": _sha(ROUTES),
                          "xu100": _sha(XU100), "market_caps": _sha(CAPS),
                          "core_diagnostics": _sha(P4 / "core_diagnostics.jsonl.gz")},
        "outputs": {"trades.csv": _sha(trades_path)},
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8", newline="\n")
    return receipt


def main() -> None:
    argparse.ArgumentParser().parse_args()
    receipt = build()
    print(json.dumps(receipt["result"], indent=2))


if __name__ == "__main__":
    main()
