from __future__ import annotations

"""Start from nothing, pay in 15,000 TL a month: XU100 against the strategy, fully invested.

The investor's framing: no lump sum, a fixed payment every month, two books side
by side -- and no 10%-per-position rule. Both books receive the same payment on
the same session (the first session of each month) and pay the same 0.2% to put
it to work.

XU100 book: every payment buys the index. Nothing else happens.

Strategy book (rules fixed before the run):
* Money to invest is every payment plus every sale's proceeds.
* It goes, in full and split equally, into the names that qualify at that moment
  and are not already held: decision AL this month, P/B at the previous close in
  the cheaper half of the month's positive-P/B cohort. No cap on position count
  or size.
* If nothing new qualifies, the money waits in XU100 -- not in 0% cash -- and all
  of it moves into the next names that qualify.
* Selling is the primary take-profit rule, unchanged: half at +30%, the rest at
  +70%, a 20% trailing stop throughout; a name fully sold is not bought back in
  the same signal month.

Reported: total paid in, ending value, profit, and the money-weighted annual
return (XIRR), the right yardstick when money arrives over time.
"""

import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backtest_take_profit import prepare

OUTPUT = ROOT / "data/audit/monthly_contribution_v1"
MONTHLY = 15_000.0
COST = 0.002
HALF_AT, ALL_AT, STOP = 0.30, 0.70, 0.20


def first_sessions_of_month(sessions: pd.DatetimeIndex) -> list[pd.Timestamp]:
    return list(pd.Series(sessions, index=sessions).groupby(sessions.to_period("M")).first())


def xirr(flows: list[tuple[pd.Timestamp, float]]) -> float:
    """Annual rate r with sum(cf / (1+r)^(days/365)) = 0, by bisection."""
    start = flows[0][0]
    value = lambda r: sum(cf / (1 + r) ** ((d - start).days / 365.0) for d, cf in flows)
    lo, hi = -0.99, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if value(mid) > 0 else (lo, mid)
    return (lo + hi) / 2


def index_book(index: pd.Series, sessions: pd.DatetimeIndex, pays: dict) -> pd.Series:
    units, values = 0.0, []
    for day in sessions:
        level = float(index.loc[day])
        units += pays.get(day, 0.0) * (1 - COST) / level
        values.append(units * level)
    return pd.Series(values, index=sessions)


def strategy_book(menus: dict, adj: pd.DataFrame, index: pd.Series, sessions: pd.DatetimeIndex,
                  pays: dict) -> dict:
    filled = adj.ffill()
    sleeve = 0.0                       # XU100 units holding money that waits
    held: dict[str, dict] = {}
    exited: dict[str, str] = {}
    values, trades, previous = [], [], None
    for day in sessions:
        prices = filled.loc[day]
        level = float(index.loc[day])
        month, qualified, _ = menus[day]
        sleeve += pays.get(day, 0.0) * (1 - COST) / level
        # ---- sell, judged on the previous close
        if previous is not None:
            for ticker in list(held):
                h = held[ticker]
                last = float(filled.at[previous, ticker])
                h["peak"] = max(h["peak"], last)
                gain = last / h["entry"] - 1.0
                if gain >= ALL_AT:
                    reason, units = "KAR_AL_TAMAMI", h["units"]
                elif not h["half"] and gain >= HALF_AT:
                    reason, units = "KAR_AL_YARISI", h["units0"] * 0.5
                elif last <= (1 - STOP) * h["peak"]:
                    reason, units = "IZ_SUREN_STOP", h["units"]
                else:
                    continue
                proceeds = units * float(prices[ticker])
                sleeve += proceeds * (1 - COST) * (1 - COST) / level
                h["units"] -= units
                h["out"] += proceeds * (1 - COST)
                trades.append({"hisse": ticker, "islem": "SAT", "tarih": str(day.date()), "neden": reason,
                               "tl": round(proceeds, 2), "fiyat": float(prices[ticker])})
                if reason == "KAR_AL_YARISI":
                    h["half"] = True
                else:
                    exited[ticker] = month
                    del held[ticker]
        # ---- buy: everything waiting, split equally over the new qualifiers
        new = [t for t in qualified if t not in held and exited.get(t) != month
               and t in adj.columns and pd.notna(adj.at[day, t])]
        waiting = sleeve * level
        if new and waiting > 0:
            sleeve = 0.0
            each = waiting / len(new)
            for ticker in new:
                price = float(prices[ticker])
                units = each * (1 - COST) * (1 - COST) / price
                held[ticker] = {"units": units, "units0": units, "entry": price, "peak": price,
                                "half": False, "in": each, "out": 0.0}
                trades.append({"hisse": ticker, "islem": "AL", "tarih": str(day.date()), "neden": month,
                               "tl": round(each, 2), "fiyat": price})
        values.append(sleeve * level + sum(h["units"] * float(prices[t]) for t, h in held.items()))
        previous = day
    return {"curve": pd.Series(values, index=sessions), "trades": trades,
            "open": {t: round(h["units"] * float(filled.at[sessions[-1], t]), 2) for t, h in held.items()}}


def build(*, output_dir: Path = OUTPUT) -> dict:
    data = prepare()
    adj, index, sessions, menus = data["adj"], data["index"], data["sessions"], data["menus"]
    pay_days = first_sessions_of_month(sessions)
    pays = {day: MONTHLY for day in pay_days}
    strategy = strategy_book(menus, adj, index, sessions, pays)
    xu100 = index_book(index, sessions, pays)
    last = sessions[-1]
    paid = MONTHLY * len(pay_days)

    def summary(curve: pd.Series) -> dict:
        end = float(curve.iloc[-1])
        flows = [(d, -MONTHLY) for d in pay_days] + [(last, end)]
        return {"paid_in_tl": paid, "ending_value_tl": round(end, 2), "profit_tl": round(end - paid, 2),
                "profit_on_paid_in": end / paid - 1.0, "money_weighted_annual_return": xirr(flows)}

    path = pd.DataFrame({"yatirilan_toplam_tl": [MONTHLY * sum(d <= day for d in pay_days) for day in sessions],
                         "xu100_tl": xu100.round(0), "strateji_tl": strategy["curve"].round(0)}, index=sessions)
    month_end = path.groupby(path.index.to_period("M")).last()
    month_end.index = month_end.index.astype(str)
    result = {
        "rules": {"monthly_payment_tl": MONTHLY, "cost_per_side": COST, "position_cap": None,
                  "buy": "AL and P/B in the cheaper half, not held; all waiting money split equally",
                  "waiting_money": "XU100", "sell": "half at +30%, rest at +70%, 20% trailing stop"},
        "payments": len(pay_days), "first_payment": str(pay_days[0].date()),
        "last_payment": str(pay_days[-1].date()), "valued_at": str(last.date()),
        "xu100": summary(xu100), "strategy": summary(strategy["curve"]),
        "strategy_open_positions_tl": strategy["open"],
        "month_end": month_end.to_dict(orient="index"),
        "trades": strategy["trades"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8", newline="\n")
    return result


if __name__ == "__main__":
    out = build()
    print(json.dumps({k: out[k] for k in ("payments", "xu100", "strategy", "strategy_open_positions_tl")},
                     indent=2, ensure_ascii=False))
    for month, row in out["month_end"].items():
        print(month, row)
    for t in out["trades"]:
        print(t)
