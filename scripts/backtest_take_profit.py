from __future__ import annotations

"""The same AL + favourable P/B buys, now with profit-taking decided up front.

The investor asked why bought names were never sold, and handed the selling
decision over: "you can sell at 70% or at 30%, you decide". The previous rule
sold only on a 20% fall from the peak, so a name that kept making highs -- AKSEN
ran +134.6% -- was never sold; it was still open when the window ended.

The decision (primary rule, taken from the investor's own two numbers rather
than from any trade already seen):
* at +30% from the entry price, sell half and lock the gain in;
* at +70%, sell the rest;
* in between, and before either, the 20% trailing stop still guards the
  position; after the half is sold it guards the remaining half.

Because this window's trades have already been seen, picking one threshold and
presenting it alone would be fitting. So every threshold named here is declared
before the run and reported whatever it shows: all out at +30%, +50%, +70% or
+100%, and the old no-profit-taking rule, which must reproduce the previous
published result exactly (+30.4%, 22 trades) or the run stops.

Each variant has its own 1000 random twins: the same buy sessions and counts,
the same exit rule, the same costs, random names from the same cohort. The
twin, not XU100, is the judge of whether the picks add anything.
"""

import argparse
from dataclasses import dataclass, field
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
    PointInTime, build_panel, phantom_moves, rescale_for_phantoms, thin_sessions,
)
from scripts.backtest_pb_two_stage import (
    CAPS, INDEX, P4, PRICES, ROUTES, XU100, current_nominal, load_equity, load_p4,
    load_xu100, raw_market_inputs,
)
from scripts.backtest_trailing_hold import RULES as HOLD_RULES, daily_menus

CONTRACT = "TAKE_PROFIT_VS_RANDOM_V1"
OUTPUT = ROOT / "data/audit/take_profit_vs_random_v1"
SCRIPT = Path(__file__).resolve()
PREVIOUS = ROOT / "data/audit/trailing_hold_vs_random_v1/receipt.json"

VARIANTS = {
    "A_yarisi_30_kalani_70": {"half_at": 0.30, "all_at": 0.70},
    "B_tamami_30": {"half_at": None, "all_at": 0.30},
    "C_tamami_50": {"half_at": None, "all_at": 0.50},
    "D_tamami_70": {"half_at": None, "all_at": 0.70},
    "E_tamami_100": {"half_at": None, "all_at": 1.00},
    "F_kar_almadan": {"half_at": None, "all_at": None},
}

RULES = {
    "primary_variant": "A_yarisi_30_kalani_70",
    "primary_reason": "the investor named 30% and 70%; half at the first locks a gain, the rest at the second",
    "variants": VARIANTS,
    "every_variant_reported": True,
    "buy": HOLD_RULES["buy_if"],
    "trailing_stop": 0.20,
    "trigger_price": "previous session's close against the entry price; the sale fills at today's close",
    "check_order": "full take-profit, then half take-profit, then trailing stop",
    "no_rebuy_after_full_exit": "same signal month",
    "window": [HOLD_RULES["window_first_signal"], HOLD_RULES["window_last_signal"], HOLD_RULES["window_end"]],
    "max_positions": 10, "slot_size": "1/10 of portfolio value at purchase", "idle_money": "held in XU100",
    "cost_per_side": 0.002,
    "random_twin": "per variant: the variant's own buy sessions and counts, same exit rule, random names",
    "random_draws": 1000,
    "random_seed": 20250204,
    "consistency_gate": "F_kar_almadan must reproduce TRAILING_HOLD_VS_RANDOM_V1: +30.4% and 22 trades",
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
    initial_units: float
    half_taken: bool = False
    closed: bool = False
    fills: list = field(default_factory=list)      # (units sold, price, reason, date)


def run(menus: dict, adj: pd.DataFrame, index: pd.Series, sessions: pd.DatetimeIndex, *,
        half_at: float | None, all_at: float | None,
        rng: np.random.Generator | None = None, buys: dict | None = None) -> dict:
    cost = RULES["cost_per_side"]
    slots = RULES["max_positions"]
    stop = 1.0 - RULES["trailing_stop"]
    filled = adj.ffill()
    sleeve = 1.0 / float(index.loc[sessions[0]])
    held: dict[str, Holding] = {}
    exited: dict[str, str] = {}
    positions, values, bought = [], [], {}
    previous = None

    def sell(ticker: str, fraction_of_original: float, price: float, reason: str, day) -> None:
        nonlocal sleeve
        holding = held[ticker]
        # A full exit sells whatever is left; a half take sells half the original lot.
        units = holding.units if fraction_of_original >= 1.0 else holding.initial_units * 0.5
        proceeds = units * price
        sleeve += proceeds * (1 - cost) * (1 - cost) / float(index.loc[day])
        holding.units -= units
        holding.fills.append((units, price, reason, str(day.date())))
        # Closed means a full exit happened, not that the lot is small: a buy
        # funded by a rounding residue is a real, if tiny, holding.
        holding.closed = fraction_of_original >= 1.0

    for day in sessions:
        prices = filled.loc[day]
        level = float(index.loc[day])
        month, qualified, cohort = menus[day]
        if previous is not None:
            for ticker in list(held):
                holding = held[ticker]
                last = float(filled.at[previous, ticker])
                holding.peak = max(holding.peak, last)
                gain = last / holding.entry_price - 1.0
                price = float(prices[ticker])
                if all_at is not None and gain >= all_at:
                    sell(ticker, 1.0, price, "KAR_AL_TAMAMI", day)
                elif half_at is not None and not holding.half_taken and gain >= half_at:
                    sell(ticker, 0.5, price, "KAR_AL_YARISI", day)
                    holding.half_taken = True
                elif last <= stop * holding.peak:
                    sell(ticker, 1.0, price, "IZ_SUREN_STOP", day)
                if holding.closed:
                    positions.append(_close(ticker, holding))
                    exited[ticker] = month
                    del held[ticker]
        free = slots - len(held)
        wanted = free if rng is None else min(free, buys.get(day, 0))
        if wanted > 0 and (qualified if rng is None else cohort):
            eligible = [t for t in (qualified if rng is None else cohort)
                        if t not in held and exited.get(t) != month
                        and t in adj.columns and pd.notna(adj.at[day, t])]
            if rng is not None:
                eligible = list(rng.permutation(eligible))
            for ticker in eligible[:wanted]:
                total = sleeve * level + sum(h.units * prices[t] for t, h in held.items())
                gross = min(total / slots, sleeve * level)
                if gross <= 0:
                    break
                sleeve -= gross / level
                price = float(prices[ticker])
                units = gross * (1 - cost) * (1 - cost) / price
                held[ticker] = Holding(units=units, entry_date=day, entry_price=price, peak=price,
                                       month=month, initial_units=units)
                bought[day] = bought.get(day, 0) + 1
        values.append(sleeve * level + sum(h.units * prices[t] for t, h in held.items()))
        previous = day
    last_day = sessions[-1]
    for ticker, holding in held.items():
        holding.fills.append((holding.units, float(filled.at[last_day, ticker]), "ACIK", str(last_day.date())))
        positions.append(_close(ticker, holding))
    return {"curve": pd.Series(values, index=sessions), "positions": positions, "buys": bought}


def _close(ticker: str, holding: Holding) -> dict:
    initial = holding.initial_units
    realised = sum(units * price for units, price, _, _ in holding.fills)
    return {
        "ticker": ticker, "entry_date": str(holding.entry_date.date()),
        "exit_date": holding.fills[-1][3], "entry_price": holding.entry_price,
        "return": realised / (initial * holding.entry_price) - 1.0,
        "exits": [{"reason": r, "date": d, "share": round(u / initial, 6), "price": p}
                  for u, p, r, d in holding.fills],
        "final_exit": holding.fills[-1][2],
    }


def rules_commit() -> dict:
    relative = str(SCRIPT.relative_to(ROOT))
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT).returncode != 0
    commit = subprocess.run(["git", "log", "-1", "--format=%H %cI", "--", relative], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    return {"script": relative, "last_commit": commit, "modified_since_commit": dirty}


def prepare() -> dict:
    """The same data the trailing-hold run used, rebuilt the same way."""
    months = [str(p) for p in pd.period_range(HOLD_RULES["window_first_signal"],
                                              HOLD_RULES["window_last_signal"], freq="M")]
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
    end = pd.Timestamp(HOLD_RULES["window_end"])
    calendar = bench.loc[:end].index
    events = phantom_moves(prices.loc[prices.ticker.isin(p4.ticker)], calendar)
    pivot = lambda column: rescale_for_phantoms(prices.pivot_table(
        index="trade_date", columns="ticker", values=column, aggfunc="last").reindex(calendar), events)
    adj, closes = pivot("adj_close"), pivot("close")
    thin = thin_sessions(adj).loc[first:end]
    sessions = thin.index[~thin]
    index = bench.reindex(calendar).ffill()
    panel = build_panel(p4, raw, load_equity(p4), current_nominal())
    menus = daily_menus(PointInTime(panel, closes), sessions)
    return {"adj": adj, "index": index, "sessions": sessions, "menus": menus}


def summarise(strategy: dict, twins: list[dict], index: pd.Series, sessions) -> dict:
    total = lambda curve: float(curve.iloc[-1] / curve.iloc[0] - 1.0)
    drawdown = lambda curve: float((curve / curve.cummax() - 1.0).min())
    randoms = np.array([total(t["curve"]) for t in twins])
    bench = index.reindex(sessions)
    own = total(strategy["curve"])
    table = pd.DataFrame(strategy["positions"])
    twin_means = np.array([np.mean([p["return"] for p in t["positions"]]) if t["positions"] else np.nan
                           for t in twins])
    days = (pd.to_datetime(table.exit_date) - pd.to_datetime(table.entry_date)).dt.days
    return {
        "strategy_return": own,
        "strategy_max_drawdown": drawdown(strategy["curve"]),
        "xu100_return": float(bench.iloc[-1] / bench.iloc[0] - 1.0),
        "random_median_return": float(np.median(randoms)),
        "random_p05": float(np.quantile(randoms, 0.05)),
        "random_p95": float(np.quantile(randoms, 0.95)),
        "strategy_percentile_among_random": float((randoms < own).mean()),
        "positions": int(len(table)),
        "positions_open_at_end": int((table.final_exit == "ACIK").sum()),
        "final_exit_reasons": {k: int(v) for k, v in table.final_exit.value_counts().items()},
        "half_takes": int(sum(any(e["reason"] == "KAR_AL_YARISI" for e in p["exits"]) for p in strategy["positions"])),
        "mean_position_return": float(table["return"].mean()),
        "random_mean_position_return_median": float(np.nanmedian(twin_means)),
        "position_mean_percentile_among_random": float((twin_means < table["return"].mean()).mean()),
        "median_days_held": float(days.median()),
        "random_position_count_median": float(np.median([len(t["positions"]) for t in twins])),
    }


def build(*, output_dir: Path = OUTPUT) -> dict:
    provenance = rules_commit()
    if provenance["modified_since_commit"]:
        raise RuntimeError("BLIND_RUN_REFUSED: commit the rules before running them")
    data = prepare()
    adj, index, sessions, menus = data["adj"], data["index"], data["sessions"], data["menus"]

    results, positions = {}, {}
    for offset, (name, policy) in enumerate(VARIANTS.items()):
        strategy = run(menus, adj, index, sessions, **policy)
        rng = np.random.default_rng(RULES["random_seed"] + offset)
        twins = [run(menus, adj, index, sessions, **policy, rng=rng, buys=strategy["buys"])
                 for _ in range(RULES["random_draws"])]
        results[name] = summarise(strategy, twins, index, sessions)
        positions[name] = strategy["positions"]
        print(name, json.dumps({k: results[name][k] for k in (
            "strategy_return", "xu100_return", "random_median_return", "strategy_percentile_among_random",
            "positions")}), flush=True)

    previous = json.loads(PREVIOUS.read_text(encoding="utf-8"))["result"]
    gate = results["F_kar_almadan"]
    if abs(gate["strategy_return"] - previous["strategy_return"]) > 1e-9 or gate["positions"] != previous["trades"]:
        raise ValueError(f"CONSISTENCY_GATE_FAILED: {gate['strategy_return']} / {gate['positions']} "
                         f"vs {previous['strategy_return']} / {previous['trades']}")

    output_dir.mkdir(parents=True, exist_ok=True)
    positions_path = output_dir / "positions.json"
    positions_path.write_text(json.dumps(positions, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                              encoding="utf-8", newline="\n")
    receipt = {
        "contract": CONTRACT, "blind_run": provenance, "rules": RULES, "results": results,
        "consistency_gate": "PASSED: no-profit-taking variant reproduces the previous run exactly",
        "not_modelled": [
            "this window's trades were seen before these rules were written; every declared variant is "
            "reported and each is judged against its own random twin",
            "XU100 is a price index; stock returns include dividends",
            "P/B uses current nominal capital",
        ],
        "source_sha256": {"prices": _sha(PRICES), "index": _sha(INDEX), "routes": _sha(ROUTES),
                          "xu100": _sha(XU100), "market_caps": _sha(CAPS),
                          "core_diagnostics": _sha(P4 / "core_diagnostics.jsonl.gz")},
        "outputs": {"positions.json": _sha(positions_path)},
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8", newline="\n")
    return receipt


def main() -> None:
    argparse.ArgumentParser().parse_args()
    build()


if __name__ == "__main__":
    main()
