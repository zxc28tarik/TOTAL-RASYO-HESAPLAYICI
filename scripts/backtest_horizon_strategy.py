from __future__ import annotations

"""Blind horizon strategy: AL names with a good P/B, three holding targets.

The investor's brief: check regularly, decide how many names to hold and when to
buy and sell, draw only from names the score calls AL that have a good P/B, and
build it three times -- for a one-month, a three-month and a six-month target.
Run it blind.

What "blind" means here, concretely
-----------------------------------
1. Every rule is in RULES below and was committed before the first run; the
   receipt records the commit and refuses to run from a modified copy.
2. The decision code never sees the future. At each check it is handed the
   monthly panel rows whose signal date has passed and the closes of sessions
   strictly before today; it executes at today's price. A test perturbs every
   price after a date and asserts that no decision before it changes.
3. The author had already seen 2025-08..2026-07 under a different rule, so the
   run covers 2021-08..2026-07 and reports 2021-08..2025-07 -- which no version
   of this strategy has been run on -- separately from the year already seen.

Why these rules (decided from structure, not from any return)
-------------------------------------------------------------
* "Good" P/B is the cheaper half of the month's positive-P/B cohort. The earlier
  brief said "very good" and got the cheapest third; this one says "good". The
  cheapest third also left 1-2 names a month, which turned the last test into a
  bet on one or two stocks.
* Ten slots of 10% each. How many names to hold is decided by how many good
  ideas there are, not by a target count: each qualifying name gets one slot, so
  two ideas mean 20% in stocks, not 100% in two stocks. The last test put all of
  the money into 1-2 names and its result was indistinguishable from luck.
* Money not in a slot sits in XU100, not in 0%-cash. With no good idea the
  portfolio is the market, and every lira of difference from XU100 comes from a
  decision this strategy made.
* Checks are weekly, so a buy or sell can happen in any week, not only at month
  starts. The score refreshes monthly because its balance-sheet inputs do; P/B
  is re-priced every week.
* Exits: the target horizon has passed and the name no longer qualifies (if it
  still qualifies, the clock restarts rather than paying to sell and rebuy); the
  score falls into the bottom half (the thesis is broken); or P/B becomes
  expensive, in the top quarter (the value case is spent -- take the profit). The
  gap between "cheaper half" to buy and "top quarter" to sell is hysteresis, so
  a name hovering at the median does not churn.
"""

import argparse
from dataclasses import dataclass, field
from datetime import date
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

from src.analytics.cross_sectional_score import percentile_score
from src.analytics.rebuilt_total_score import RebuiltScoreConfig, rebuilt_scores
from scripts.backtest_pb_two_stage import (
    CAPS, INDEX, P4, PRICES, ROUTES, XU100, current_nominal, load_equity, load_p4,
    load_xu100, raw_market_inputs,
)

CONTRACT = "BLIND_HORIZON_STRATEGY_V1"
OUTPUT = ROOT / "data/audit/blind_horizon_strategy_v1"
SCRIPT = Path(__file__).resolve()
CORE_MODULES = ("M1", "M3", "Ek4", "Ek1", "Ek9")

RULES = {
    "window_first_signal": "2021-08",
    "window_last_signal": "2026-07",
    "window_end": "2026-07-31",
    "unseen_period": ["2021-08-01", "2025-07-31"],
    "seen_period": ["2025-08-01", "2026-07-31"],
    "horizons_months": [1, 3, 6],
    "score": "REBUILT_TOTAL_SCORE_V1: equal-weight rank of M1, M3, Ek4, Ek1, Ek9; AL = top 10%",
    "pb": "close(previous session) * current nominal capital / PIT total equity",
    "buy_if": "decision == AL (this month) and P/B > 0 and P/B percentile <= 0.50 (cheaper half), re-priced weekly",
    "sell_if": [
        "score_percentile <= 0.50 at a monthly refresh (thesis broken)",
        "P/B percentile > 0.75 at any check (value case spent, take profit)",
        "target horizon passed and the name no longer qualifies (still qualifying renews the clock)",
        "no price for more than 10 sessions",
    ],
    "check_schedule": "every monthly signal day and the first session of every ISO week",
    "max_positions": 10,
    "slot_size": "1/10 of portfolio value at purchase; positions are not re-trimmed",
    "idle_money": "held in XU100",
    "priority_when_slots_are_short": "highest score_percentile first",
    "cost_per_side": 0.002,
    "cost_applies_to": "every stock trade and every XU100 sleeve trade",
    "execution": "adj_close of the check session (total return incl. dividends)",
    "phantom_move_rule": ("a close-to-close move beyond what k sessions of +/-10% limits around the prior "
                          "weighted-average base allow -- up > 1.1^k/0.9 - 1 + 0.02, down < 0.9^k/1.1 - 1 - 0.02 -- "
                          "is an unadjusted corporate action; earlier prices are rescaled as for a split. A "
                          "closure longer than 5 calendar days counts its weekdays as sessions."),
    "placebo_draws": 5000,
    "placebo_seed": 20210802,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _months() -> list[str]:
    return [str(p) for p in pd.period_range(RULES["window_first_signal"], RULES["window_last_signal"], freq="M")]


# --------------------------------------------------------------------------- data

def phantom_moves(frame: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """Close-to-close moves that the price-limit mechanics cannot produce."""
    frame = frame.loc[frame.trade_date.isin(calendar)].sort_values(["ticker", "trade_date"]).copy()
    frame["prev"] = frame.groupby("ticker").trade_date.shift()
    frame["move"] = frame.groupby("ticker").adj_close.pct_change()
    frame = frame.dropna(subset=["prev", "move"])
    position = pd.Series(np.arange(len(calendar)), index=calendar)
    sessions = position.reindex(frame.trade_date).to_numpy() - position.reindex(frame.prev).to_numpy()
    gap = (frame.trade_date - frame.prev).dt.days.to_numpy()
    weekdays = np.array([np.busday_count(a.date(), b.date()) for a, b in zip(frame.prev, frame.trade_date)])
    frame["sessions"] = np.where((gap > 5) & (sessions == 1), weekdays, sessions)
    up = 1.1 ** frame.sessions / 0.9 - 1.0 + 0.02
    down = 0.9 ** frame.sessions / 1.1 - 1.0 - 0.02
    return frame.loc[(frame.move > up) | (frame.move < down),
                     ["ticker", "trade_date", "sessions", "move"]].reset_index(drop=True)


def rescale_for_phantoms(pivot: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Multiply prices before each phantom move by (1 + move), as a split adjustment would."""
    out = pivot.copy()
    for event in events.itertuples():
        if event.ticker in out.columns:
            before = out.index < event.trade_date
            out.loc[before, event.ticker] = out.loc[before, event.ticker] * (1.0 + event.move)
    return out


def build_panel(p4: pd.DataFrame, raw: pd.DataFrame, equity: pd.DataFrame, nominal: pd.Series) -> pd.DataFrame:
    frame = p4.merge(raw, on=["month", "ticker"], how="left")
    frame["M1"] = frame.p4_M1
    frame["M3"] = frame.alpha_trailing.astype(float)
    frame["Ek4"] = frame.excess_return_20d.astype(float)
    frame["Ek1"] = frame.good_count.astype(float)
    frame["Ek9"] = -frame.volatility.astype(float)
    scored = rebuilt_scores(frame, config=RebuiltScoreConfig(modules=list(CORE_MODULES)))
    scored = scored.loc[scored.score_percentile.notna()].copy()
    scored = scored.merge(equity, on=["signal_date", "ticker"], how="left")
    scored["nominal"] = scored.ticker.map(nominal)
    scored["signal_ts"] = pd.to_datetime(scored.signal_date)
    return scored[["month", "signal_date", "signal_ts", "ticker", "score_percentile", "decision",
                   "equity", "equity_period_end", "equity_published_at", "nominal", "pit_shares_out"]]


# ------------------------------------------------------------------ point in time

class PointInTime:
    """The only door through which the strategy sees data.

    ``cohort(day)`` returns the latest month whose signal date is on or before
    ``day``; ``closes_before(day)`` returns the last close of every ticker from
    sessions strictly before ``day``. Nothing else is exposed to decisions.
    """

    def __init__(self, panel: pd.DataFrame, closes: pd.DataFrame):
        self._panel = panel
        self._signals = panel.groupby("month").signal_ts.first().sort_values()
        self._closes = closes.ffill()

    def month(self, day: pd.Timestamp) -> str | None:
        known = self._signals.loc[self._signals <= day]
        return None if known.empty else known.index[-1]

    def cohort(self, day: pd.Timestamp) -> pd.DataFrame:
        month = self.month(day)
        return self._panel.iloc[0:0] if month is None else self._panel.loc[self._panel.month.eq(month)]

    def closes_before(self, day: pd.Timestamp) -> pd.Series:
        earlier = self._closes.loc[self._closes.index < day]
        return earlier.iloc[-1] if len(earlier) else pd.Series(dtype=float)


def priced_cohort(view: PointInTime, day: pd.Timestamp) -> pd.DataFrame:
    cohort = view.cohort(day).copy()
    if cohort.empty:
        return cohort
    closes = view.closes_before(day)
    cohort["pb_now"] = cohort.ticker.map(closes) * cohort.nominal / cohort.equity
    cohort["pb_pct_now"] = percentile_score(cohort.pb_now.where(cohort.pb_now > 0))
    cohort["qualifies"] = (cohort.decision.eq("AL") & cohort.pb_now.gt(0)
                           & cohort.pb_pct_now.le(0.50)).fillna(False).astype(bool)
    return cohort.set_index("ticker")


# ------------------------------------------------------------------------- engine

def sessions_since_price(adj: pd.DataFrame) -> pd.DataFrame:
    position = np.arange(len(adj))[:, None]
    seen = np.where(adj.notna().to_numpy(), position, np.nan)
    last = pd.DataFrame(seen).ffill().to_numpy()
    return pd.DataFrame(np.nan_to_num(position - last, nan=0.0), index=adj.index, columns=adj.columns)


@dataclass
class Position:
    units: float
    entry_date: pd.Timestamp
    entry_price: float
    clock: pd.Timestamp
    month: str
    renewals: int = 0


@dataclass
class Book:
    sleeve_units: float
    positions: dict = field(default_factory=dict)

    def stock_value(self, prices: pd.Series) -> float:
        return sum(p.units * prices[t] for t, p in self.positions.items())

    def value(self, prices: pd.Series, index_level: float) -> float:
        return self.sleeve_units * index_level + self.stock_value(prices)


def run_strategy(view: PointInTime, adj: pd.DataFrame, index: pd.Series, *,
                 horizon_months: int, sessions: pd.DatetimeIndex) -> dict:
    cost = RULES["cost_per_side"]
    slots = RULES["max_positions"]
    filled = adj.ffill()
    age = sessions_since_price(adj)
    signal_days = set(view._signals.values)
    book = Book(sleeve_units=1.0 / float(index.loc[sessions[0]]))
    trades, curve, seen_weeks, events = [], [], set(), []
    fees = 0.0

    for day in sessions:
        prices = filled.loc[day]
        level = float(index.loc[day])
        week = day.isocalendar()[:2]
        is_signal = day in signal_days
        is_check = is_signal or week not in seen_weeks
        seen_weeks.add(week)
        if is_check:
            cohort = priced_cohort(view, day)
            month = view.month(day)
            # ---- sells
            for ticker in list(book.positions):
                position = book.positions[ticker]
                row = cohort.loc[ticker] if ticker in cohort.index else None
                reason = None
                if age.at[day, ticker] > 10:
                    reason = "FIYAT_YOK"
                elif is_signal and row is not None and row.score_percentile <= 0.50:
                    reason = "SKOR_ZAYIFLADI"
                elif row is not None and pd.notna(row.pb_pct_now) and row.pb_pct_now > 0.75:
                    reason = "PDDD_PAHALANDI"
                elif day >= position.clock + pd.DateOffset(months=horizon_months):
                    if row is not None and bool(row.qualifies):
                        position.clock = day
                        position.renewals += 1
                        events.append({"date": str(day.date()), "ticker": ticker, "action": "YENILENDI"})
                    else:
                        reason = "HEDEF_SURE_DOLDU"
                if reason:
                    proceeds = position.units * prices[ticker]
                    fee = cost * proceeds
                    net = proceeds - fee
                    fee_in = cost * net
                    book.sleeve_units += (net - fee_in) / level
                    fees += fee + fee_in
                    del book.positions[ticker]
                    trades.append({"ticker": ticker, "entry_date": str(position.entry_date.date()),
                                   "exit_date": str(day.date()), "entry_price": position.entry_price,
                                   "exit_price": float(prices[ticker]), "reason": reason,
                                   "renewals": position.renewals, "entry_month": position.month})
                    events.append({"date": str(day.date()), "ticker": ticker, "action": "SAT", "reason": reason})
            # ---- buys
            free = slots - len(book.positions)
            if free > 0 and not cohort.empty:
                candidates = cohort.loc[cohort.qualifies & ~cohort.index.isin(list(book.positions))]
                candidates = candidates.loc[[t in adj.columns and pd.notna(adj.at[day, t]) for t in candidates.index]]
                candidates = candidates.sort_values("score_percentile", ascending=False).head(free)
                for ticker in candidates.index:
                    total = book.value(prices, level)
                    slot_value = total / slots
                    gross_from_sleeve = min(slot_value, book.sleeve_units * level)
                    fee_out = cost * gross_from_sleeve
                    invest = gross_from_sleeve - fee_out
                    fee_in = cost * invest
                    if invest <= 0:
                        break
                    book.sleeve_units -= gross_from_sleeve / level
                    units = (invest - fee_in) / prices[ticker]
                    fees += fee_out + fee_in
                    book.positions[ticker] = Position(units=units, entry_date=day,
                                                      entry_price=float(prices[ticker]), clock=day,
                                                      month=month)
                    events.append({"date": str(day.date()), "ticker": ticker, "action": "AL",
                                   "month": month, "score": float(cohort.at[ticker, "score_percentile"]),
                                   "pb_pct": float(cohort.at[ticker, "pb_pct_now"])})
        total = book.value(prices, level)
        curve.append({"date": day, "value": total, "positions": len(book.positions),
                      "stock_weight": book.stock_value(prices) / total})

    # Mark open positions at the end so every trade has an outcome.
    last = sessions[-1]
    for ticker, position in book.positions.items():
        trades.append({"ticker": ticker, "entry_date": str(position.entry_date.date()),
                       "exit_date": str(last.date()), "entry_price": position.entry_price,
                       "exit_price": float(filled.at[last, ticker]), "reason": "ACIK_POZISYON",
                       "renewals": position.renewals, "entry_month": position.month})
    return {"curve": pd.DataFrame(curve).set_index("date"), "trades": trades, "events": events, "fees": fees}


# ------------------------------------------------------------------------ measure

def period_metrics(curve: pd.DataFrame, index: pd.Series, first: str, last: str) -> dict:
    value = curve["value"]
    inside = value.loc[first:last]
    before = value.loc[:pd.Timestamp(first) - pd.Timedelta(days=1)]
    base_day = before.index[-1] if len(before) else inside.index[0]
    path = value.loc[base_day:last]
    bench = index.reindex(path.index)
    return {
        "from": str(base_day.date()), "to": str(path.index[-1].date()),
        "return": float(path.iloc[-1] / path.iloc[0] - 1.0),
        "xu100_return": float(bench.iloc[-1] / bench.iloc[0] - 1.0),
        "excess_vs_xu100": float(path.iloc[-1] / path.iloc[0] - bench.iloc[-1] / bench.iloc[0]),
        "max_drawdown": float((path / path.cummax() - 1.0).min()),
        "xu100_max_drawdown": float((bench / bench.cummax() - 1.0).min()),
        "average_positions": float(curve["positions"].loc[first:last].mean()),
        "average_stock_weight": float(curve["stock_weight"].loc[first:last].mean()),
    }


def trade_table(trades: list[dict], index: pd.Series) -> pd.DataFrame:
    table = pd.DataFrame(trades)
    if table.empty:
        return table
    entry = pd.to_datetime(table.entry_date)
    exit_ = pd.to_datetime(table.exit_date)
    table["return"] = table.exit_price / table.entry_price - 1.0
    table["xu100_return"] = index.reindex(exit_).to_numpy() / index.reindex(entry).to_numpy() - 1.0
    table["excess"] = table["return"] - table["xu100_return"]
    table["days_held"] = (exit_ - entry).dt.days
    return table


def trade_placebo(table: pd.DataFrame, panel: pd.DataFrame, adj: pd.DataFrame, index: pd.Series) -> dict:
    """Swap every trade's stock for a random name from the same month's cohort, same dates."""
    if table.empty:
        return {"trades": 0}
    rng = np.random.default_rng(RULES["placebo_seed"])
    draws = RULES["placebo_draws"]
    filled = adj.ffill()
    sums = np.zeros(draws)
    for trade in table.itertuples():
        entry, exit_ = pd.Timestamp(trade.entry_date), pd.Timestamp(trade.exit_date)
        names = panel.loc[panel.month.eq(trade.entry_month), "ticker"]
        names = [t for t in names if t in adj.columns and pd.notna(adj.at[entry, t])]
        returns = (filled.loc[exit_, names] / filled.loc[entry, names]).to_numpy(dtype=float) - 1.0
        sums += returns[rng.integers(0, len(names), size=draws)] - trade.xu100_return
    placebo = sums / len(table)
    actual = float(table.excess.mean())
    return {
        "trades": int(len(table)),
        "mean_excess": actual,
        "placebo_mean_excess_median": float(np.median(placebo)),
        "placebo_p05": float(np.quantile(placebo, 0.05)),
        "placebo_p95": float(np.quantile(placebo, 0.95)),
        "percentile_among_placebo": float((placebo < actual).mean()),
    }


def trade_summary(table: pd.DataFrame) -> dict:
    if table.empty:
        return {"trades": 0}
    return {
        "trades": int(len(table)),
        "mean_return": float(table["return"].mean()),
        "mean_excess_vs_xu100": float(table.excess.mean()),
        "median_excess_vs_xu100": float(table.excess.median()),
        "hit_rate_vs_xu100": float((table.excess > 0).mean()),
        "median_days_held": float(table.days_held.median()),
        "exit_reasons": {k: int(v) for k, v in table.reason.value_counts().items()},
    }


def cohort_reference(panel: pd.DataFrame, adj: pd.DataFrame, end: pd.Timestamp) -> pd.Series:
    """Equal weight in every scored name, rebalanced monthly, gross: what the pool itself did."""
    filled = adj.ffill()
    signals = panel.groupby("month").signal_ts.first().sort_values()
    bounds = list(signals.values) + [end]
    level, path = 1.0, {}
    for month, start, stop in zip(signals.index, bounds[:-1], bounds[1:]):
        names = [t for t in panel.loc[panel.month.eq(month), "ticker"]
                 if t in adj.columns and pd.notna(adj.at[pd.Timestamp(start), t])]
        window = filled.loc[pd.Timestamp(start):pd.Timestamp(stop), names]
        growth = (window / window.iloc[0]).mean(axis=1)
        for day, g in growth.items():
            path[day] = level * g
        level = level * float(growth.iloc[-1])
    return pd.Series(path).sort_index()


# -------------------------------------------------------------------------- build

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

    months = _months()
    p4 = load_p4(months)
    raw = raw_market_inputs(p4)
    merged = p4.merge(raw, on=["month", "ticker"])
    for module, banded in (("M3", "m3"), ("Ek4", "ek4"), ("Ek9", "ek9")):
        drift = (merged[f"p4_{module}"] - merged[banded].astype(float)).abs().max()
        if not drift < 1e-9:
            raise ValueError(f"P4_REPRODUCTION_DRIFT:{module}:{drift}")

    bench = load_xu100()
    bench.index = pd.to_datetime(bench.index)
    prices = pd.read_csv(PRICES, low_memory=False)
    prices = prices.loc[prices.ticker.eq(prices.price_source_ticker)].copy()
    prices["trade_date"] = pd.to_datetime(prices.trade_date)
    first = pd.Timestamp(p4.signal_date.min())
    end = pd.Timestamp(RULES["window_end"])
    calendar = bench.loc[:end].index

    events = phantom_moves(prices.loc[prices.ticker.isin(p4.ticker)], calendar)
    adj = prices.pivot_table(index="trade_date", columns="ticker", values="adj_close", aggfunc="last")
    closes = prices.pivot_table(index="trade_date", columns="ticker", values="close", aggfunc="last")
    adj = rescale_for_phantoms(adj.reindex(calendar), events)
    closes = rescale_for_phantoms(closes.reindex(calendar), events)
    breadth = adj.loc[first:end].notna().mean(axis=1)
    sessions = breadth.index[breadth >= 0.9]
    dropped = [str(d.date()) for d in breadth.index[breadth < 0.9]]

    equity = load_equity(p4)
    panel = build_panel(p4, raw, equity, current_nominal())
    view = PointInTime(panel, closes)
    index = bench.reindex(calendar).ffill()

    periods = {"gorulmemis_2021_08__2025_07": RULES["unseen_period"],
               "gorulmus_2025_08__2026_07": RULES["seen_period"],
               "tum_donem": [str(sessions[0].date()), RULES["window_end"]]}
    results, logs = {}, {}
    for horizon in RULES["horizons_months"]:
        run = run_strategy(view, adj, index, horizon_months=horizon, sessions=sessions)
        table = trade_table(run["trades"], index)
        name = f"hedef_{horizon}_ay"
        results[name] = {"fees_paid": run["fees"]}
        for label, (a, b) in periods.items():
            chosen = table.loc[pd.to_datetime(table.entry_date).between(a, b)] if not table.empty else table
            results[name][label] = {
                **period_metrics(run["curve"], index, a, b),
                "trades": trade_summary(chosen),
                "placebo": trade_placebo(chosen, panel, adj, index),
            }
        logs[name] = {"events": run["events"], "trades": table.to_dict(orient="records")}

    reference = cohort_reference(panel, adj, sessions[-1])
    reference_periods = {}
    for label, (a, b) in periods.items():
        path = reference.loc[:b]
        before = path.loc[:pd.Timestamp(a) - pd.Timedelta(days=1)]
        base = before.iloc[-1] if len(before) else path.loc[a:].iloc[0]
        reference_periods[label] = float(path.iloc[-1] / base - 1.0)

    candidates = {}
    for month, cohort in panel.groupby("month"):
        priced = priced_cohort(view, pd.Timestamp(cohort.signal_ts.iloc[0]))
        candidates[month] = {"cohort": int(len(priced)), "al": int(priced.decision.eq("AL").sum()),
                             "al_and_good_pb": int(priced.qualifies.sum()),
                             "al_without_pb": int((priced.decision.eq("AL") & priced.pb_now.isna()).sum())}

    output_dir.mkdir(parents=True, exist_ok=True)
    logs_path = output_dir / "trade_logs.json"
    logs_path.write_text(json.dumps(logs, ensure_ascii=False, sort_keys=True, indent=1, default=str) + "\n",
                         encoding="utf-8", newline="\n")
    receipt = {
        "contract": CONTRACT,
        "blind_run": provenance,
        "rules": RULES,
        "p4_reproduction": f"{len(merged)}/{len(merged)} banded M3/Ek4/Ek9 values reproduced exactly",
        "phantom_moves_rescaled": [{"ticker": e.ticker, "date": str(e.trade_date.date()),
                                    "sessions": int(e.sessions), "move": round(float(e.move), 4)}
                                   for e in events.itertuples()],
        "sessions_dropped_for_thin_stock_feed": dropped,
        "candidates_per_month": candidates,
        "results": results,
        "reference_whole_cohort_equal_weight_gross": reference_periods,
        "holdout_status": "fully consumed: 2024-08..2025-07 is used here for the first time",
        "not_modelled": [
            "XU100 is a price index while stock returns include dividends, so each stock trade's "
            "excess is flattered by roughly its dividend yield over the holding period",
            "P/B uses CURRENT nominal capital; a rights issue after the signal overstates past P/B",
            "a bonus issue small enough to stay inside the price-limit band is undetectable",
            "scores are the production scores, computed on unrescaled prices",
            "returns are nominal TL; inflation is not removed",
        ],
        "source_sha256": {"prices": _sha(PRICES), "index": _sha(INDEX), "routes": _sha(ROUTES),
                          "xu100": _sha(XU100), "market_caps": _sha(CAPS),
                          "core_diagnostics": _sha(P4 / "core_diagnostics.jsonl.gz")},
        "outputs": {"trade_logs.json": _sha(logs_path)},
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8", newline="\n")
    return receipt


def main() -> None:
    argparse.ArgumentParser().parse_args()
    receipt = build()
    for name, result in receipt["results"].items():
        for label in ("gorulmemis_2021_08__2025_07", "gorulmus_2025_08__2026_07", "tum_donem"):
            r = result[label]
            t, pl = r["trades"], r["placebo"]
            print(f"{name:10s} {label:28s} getiri {r['return']:+8.1%}  XU100 {r['xu100_return']:+8.1%}  "
                  f"fark {r['excess_vs_xu100']:+7.1%}  maxDD {r['max_drawdown']:+6.1%}  "
                  f"hisse {r['average_positions']:4.1f}  islem {t.get('trades', 0):3d}  "
                  f"isabet {t.get('hit_rate_vs_xu100', float('nan')):.0%}  "
                  f"plasebo-yuzdelik {pl.get('percentile_among_placebo', float('nan')):.0%}")
    print("evren esit agirlik (brut):", receipt["reference_whole_cohort_equal_weight_gross"])


if __name__ == "__main__":
    main()
