from __future__ import annotations

"""A forward, blind paper portfolio: decisions are made now, prices happen later.

The investor wanted discretionary decisions -- "like a human, no fixed rules" --
tested blind. On the past that is impossible for the decider here, who has
already seen the 2025-26 price paths. On the future it is not: nobody has seen
them. So this book starts on 2026-09-28 and only ever moves forward.

How blindness is guaranteed
---------------------------
* Every decision is an ORDER event appended to ``ledger.jsonl`` with a UTC
  timestamp and a written reason, and is committed and pushed before it can
  fill. The git history is the proof of when it was decided.
* An order fills at the close of the first session whose Istanbul date is
  strictly after the order's Istanbul date. Nothing decided on day D can see
  D's close, let alone later ones.

Accounting
----------
* 15,000 TL arrives on the start session and on the first session of every
  later month. It lands as cash; cash earns nothing (stated, not hidden).
* Whole shares only (BIST trades in lots of one share); leftover lira stays cash.
* 0.2% commission on every buy and sell.
* Prices are raw exchange closes. Cash dividends are credited on the ex-date and
  splits / bonus issues scale the share count, both from Yahoo's action feed.
* Every fill, payment, dividend and split is frozen into the ledger on the day it
  happens and the book only moves forward, like a broker statement: a later
  retroactive price revision by the vendor cannot rewrite a past fill.
* A session is processed only once its close is final (after 18:30 Istanbul).
* The XU100 book receives the same payments on the same sessions and buys the
  index at that session's close, paying the same 0.2%. XU100 is a price index,
  so the stock book's dividends are an advantage the index book does not get.

Commands
--------
  sheet                   fetch prices, print and save the decision sheet
  order BUY|SELL ...      record a decision (it fills later)
  daily                   fetch prices, apply payments, fills, actions; mark both books
  status                  print the current book
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIR = ROOT / "data/paper_portfolio_v1"
LEDGER = DIR / "ledger.jsonl"
PRICES = DIR / "prices.csv"
ACTIONS = DIR / "actions.csv"
MARKS = DIR / "marks.csv"
RANKING = ROOT / "data/live/rebuilt_total_scores_v1/ranking.jsonl"
VALUATIONS = ROOT / "data/live/current_nonfin_valuation_v1/valuations.jsonl"
ISTANBUL = ZoneInfo("Europe/Istanbul")
START = "2026-09-28"
MONTHLY = 15_000.0
COST = 0.002
INDEX = "XU100"


# ------------------------------------------------------------------ ledger I/O

def read_ledger(path: Path = LEDGER) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append(event: dict, path: Path = LEDGER) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def istanbul_date(stamp: str) -> str:
    return datetime.fromisoformat(stamp).astimezone(ISTANBUL).date().isoformat()


# ---------------------------------------------------------------- the book

STATE = DIR / "state.json"
CLOSE_FINAL_AFTER = (18, 30)          # Istanbul time after which a session's close is final


@dataclass
class Book:
    cash: float = 0.0
    shares: dict = field(default_factory=dict)
    cost_basis: dict = field(default_factory=dict)
    paid_in: float = 0.0
    index_units: float = 0.0
    last_session: str = ""
    done_orders: list = field(default_factory=list)

    def value(self, closes: dict) -> float:
        return self.cash + sum(n * closes[t] for t, n in self.shares.items() if n)


def load_book(path: Path = STATE) -> Book:
    return Book(**json.loads(path.read_text(encoding="utf-8"))) if path.exists() else Book()


def save_book(book: Book, path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(book.__dict__, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                    encoding="utf-8", newline="\n")


def can_fill(order_time: str, session: str) -> bool:
    """An order may fill only on a session strictly after its own Istanbul date."""
    return session > istanbul_date(order_time)


def final_sessions(dates: list[str], now: datetime) -> list[str]:
    """Sessions whose close is final: before today, or today once the close has settled."""
    local = now.astimezone(ISTANBUL)
    today = local.date().isoformat()
    settled = (local.hour, local.minute) >= CLOSE_FINAL_AFTER
    return [d for d in dates if d < today or (d == today and settled)]


def process_session(book: Book, day: str, row: pd.Series, day_actions: pd.DataFrame,
                    orders: list[dict], *, start: str = START) -> list[dict]:
    """Advance the book by one settled session; return the events it produced (to be frozen)."""
    events = []
    for act in day_actions.itertuples():
        held = book.shares.get(act.ticker, 0)
        if not held:
            continue
        if act.kind == "DIVIDEND":
            book.cash += held * float(act.value)
            events.append({"event": "DIVIDEND", "date": day, "ticker": act.ticker, "per_share": float(act.value),
                           "shares": held, "tl": round(held * float(act.value), 2)})
        elif act.kind == "SPLIT" and float(act.value) > 0:
            book.shares[act.ticker] = int(held * float(act.value))
            events.append({"event": "SPLIT", "date": day, "ticker": act.ticker, "ratio": float(act.value),
                           "shares_before": held, "shares_after": book.shares[act.ticker]})
    if day >= start and (not book.last_session or day[:7] != book.last_session[:7] or book.paid_in == 0):
        book.cash += MONTHLY
        book.paid_in += MONTHLY
        book.index_units += MONTHLY * (1 - COST) / float(row[INDEX])
        events.append({"event": "CONTRIBUTION", "date": day, "tl": MONTHLY, "xu100_close": float(row[INDEX])})
    for order in orders:
        if order["id"] in book.done_orders or not can_fill(order["time"], day):
            continue
        ticker, price = order["ticker"], row.get(order["ticker"])
        if price is None or pd.isna(price):
            continue                                   # no print today: the order waits for the next session
        price = float(price)
        fill = {"event": "FILL", "date": day, "order": order["id"], "side": order["side"], "ticker": ticker,
                "price": price}
        if order["side"] == "BUY":
            qty = int(min(float(order["tl"]), book.cash) / (price * (1 + COST)))
            if qty > 0:
                spend = qty * price * (1 + COST)
                book.cash -= spend
                book.shares[ticker] = book.shares.get(ticker, 0) + qty
                book.cost_basis[ticker] = book.cost_basis.get(ticker, 0.0) + spend
                fill.update(qty=qty, tl=round(spend, 2))
            else:
                fill.update(qty=0, status="NO_CASH")
        else:
            held = book.shares.get(ticker, 0)
            qty = held if float(order.get("fraction", 1.0)) >= 1.0 else int(held * float(order["fraction"]))
            if qty > 0:
                proceeds = qty * price * (1 - COST)
                basis = book.cost_basis.get(ticker, 0.0) * qty / held
                book.cash += proceeds
                book.shares[ticker] = held - qty
                book.cost_basis[ticker] = book.cost_basis.get(ticker, 0.0) - basis
                if book.shares[ticker] == 0:
                    del book.shares[ticker]
                    book.cost_basis.pop(ticker, None)
                fill.update(qty=qty, tl=round(proceeds, 2), realised_tl=round(proceeds - basis, 2))
            else:
                fill.update(qty=0, status="NOTHING_TO_SELL")
        book.done_orders.append(order["id"])
        events.append(fill)
    book.last_session = day
    return events


def mark(book: Book, day: str, row: pd.Series, carried: dict) -> dict:
    closes = {t: float(row[t]) if pd.notna(row.get(t)) else carried[t] for t in book.shares}
    total = book.value(closes)
    return {"date": day, "paid_in": book.paid_in, "cash": round(book.cash, 2), "stocks": round(total - book.cash, 2),
            "total": round(total, 2), "xu100_book": round(book.index_units * float(row[INDEX]), 2),
            "positions": len(book.shares)}


# ------------------------------------------------------------- market data

def fetch(tickers: list[str], *, period: str = "1y") -> tuple[pd.DataFrame, pd.DataFrame]:
    import yfinance as yf

    symbols = [f"{t}.IS" for t in tickers] + [f"{INDEX}.IS"]
    frame = yf.download(symbols, period=period, auto_adjust=False, actions=True, progress=False,
                        group_by="ticker", threads=True)
    closes, actions = {}, []
    for symbol in symbols:
        if symbol not in frame.columns.get_level_values(0):
            continue
        sub = frame[symbol]
        name = symbol[:-3]
        closes[name] = sub["Close"]
        for column, kind in (("Dividends", "DIVIDEND"), ("Stock Splits", "SPLIT")):
            if column in sub:
                for stamp, value in sub[column].items():
                    if pd.notna(value) and float(value) != 0.0:
                        actions.append({"date": pd.Timestamp(stamp).date().isoformat(), "ticker": name,
                                        "kind": kind, "value": float(value)})
    prices = pd.DataFrame(closes)
    prices.index = pd.to_datetime(prices.index).date.astype(str)
    prices = prices.loc[prices[INDEX].notna()]
    return prices.sort_index(), pd.DataFrame(actions, columns=["date", "ticker", "kind", "value"])


def universe() -> pd.DataFrame:
    ranking = pd.DataFrame([json.loads(l) for l in RANKING.read_text(encoding="utf-8").splitlines() if l.strip()])
    vals = {}
    for line in VALUATIONS.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        pb = row.get("diagnostics", {}).get("multiple_details", {}).get("PB") or {}
        if pb.get("usable") and pb.get("implied_mid"):
            vals[row["ticker"]] = {"pb_peer_mid": pb["peer_q_mid"], "pb_implied_mid_price": pb["implied_mid"],
                                   "pb_peer_count": pb["peer_count"], "valuation_anchor": row.get("anchor_period_end")}
    ranking["pb_peer_mid"] = ranking.ticker.map(lambda t: vals.get(t, {}).get("pb_peer_mid"))
    ranking["pb_implied_mid_price"] = ranking.ticker.map(lambda t: vals.get(t, {}).get("pb_implied_mid_price"))
    ranking["valuation_anchor"] = ranking.ticker.map(lambda t: vals.get(t, {}).get("valuation_anchor"))
    return ranking


def sheet(prices: pd.DataFrame, ranking: pd.DataFrame, holdings: dict) -> pd.DataFrame:
    """What a decider looks at: score, own P/B against its sector, and recent price path."""
    rows = []
    last = prices.index[-1]
    for r in ranking.itertuples():
        if r.decision not in ("AL", "IZLE") and r.ticker not in holdings:
            continue
        if r.ticker not in prices.columns:
            continue
        path = prices[r.ticker].dropna()
        if path.empty:
            continue
        price = float(path.iloc[-1])
        own_pb = (price / r.pb_implied_mid_price * r.pb_peer_mid) if pd.notna(r.pb_implied_mid_price) else None
        change = lambda n: float(path.iloc[-1] / path.iloc[-n - 1] - 1) if len(path) > n else None
        rows.append({
            "ticker": r.ticker, "decision": r.decision, "score": round(r.total_rasyo_100, 1),
            "price": round(price, 2), "pb": None if own_pb is None else round(own_pb, 2),
            "pb_sector_median": None if pd.isna(r.pb_peer_mid) else round(r.pb_peer_mid, 2),
            "pb_vs_sector": None if own_pb is None else round(own_pb / r.pb_peer_mid - 1, 3),
            "m2": r.m2_overlay, "chg_20d": change(20), "chg_63d": change(63),
            "off_52w_high": round(price / float(path.max()) - 1, 3),
            "held": holdings.get(r.ticker, 0), "asof": last,
        })
    return pd.DataFrame(rows).sort_values(["decision", "pb_vs_sector"], na_position="last")


# ------------------------------------------------------------------ commands

def refresh_market() -> tuple[pd.DataFrame, pd.DataFrame]:
    ranking = universe()
    ordered = {e["ticker"] for e in read_ledger() if e["event"] == "ORDER"}
    tickers = sorted(set(ranking.loc[ranking.decision.isin(["AL", "IZLE"]), "ticker"]) | ordered)
    prices, actions = fetch(tickers)
    DIR.mkdir(parents=True, exist_ok=True)
    prices.to_csv(PRICES)
    actions.to_csv(ACTIONS, index=False)
    return prices, actions


def cmd_sheet(_args) -> None:
    prices, _ = refresh_market()
    table = sheet(prices, universe(), load_book().shares)
    out = DIR / f"sheet_{prices.index[-1]}.csv"
    table.to_csv(out, index=False)
    pd.set_option("display.width", 220)
    print(table.to_string(index=False))
    print("saved", out.relative_to(ROOT))


def cmd_order(args) -> None:
    ledger = read_ledger()
    event = {"event": "ORDER", "id": f"O{len([e for e in ledger if e['event'] == 'ORDER']) + 1:04d}",
             "time": now_utc(), "side": args.side, "ticker": args.ticker.upper(), "why": args.why}
    if args.side == "BUY":
        event["tl"] = float(args.tl)
    else:
        event["fraction"] = float(args.fraction)
    print(json.dumps(append(event), ensure_ascii=False))


def cmd_daily(_args, *, now: datetime | None = None) -> None:
    prices, actions = refresh_market()
    book = load_book()
    orders = [e for e in read_ledger() if e["event"] == "ORDER"]
    todo = [d for d in final_sessions(list(prices.index), now or datetime.now(timezone.utc))
            if d >= START and d > book.last_session]
    carried = prices.ffill()
    for day in todo:
        for event in process_session(book, day, prices.loc[day], actions.loc[actions.date.eq(day)], orders):
            append(event)
        row = mark(book, day, prices.loc[day], carried.loc[day].to_dict())
        header = not MARKS.exists()
        pd.DataFrame([row]).to_csv(MARKS, mode="a", header=header, index=False)
    save_book(book)
    cmd_status(None)


def cmd_status(_args) -> None:
    book = load_book()
    last = pd.read_csv(MARKS).iloc[-1].to_dict() if MARKS.exists() else None
    fills = [e for e in read_ledger() if e["event"] == "FILL"][-10:]
    pending = [e for e in read_ledger() if e["event"] == "ORDER" and e["id"] not in book.done_orders]
    print(json.dumps({"last_mark": last, "cash": round(book.cash, 2), "shares": book.shares,
                      "pending_orders": pending, "recent_fills": fills}, ensure_ascii=False, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sheet").set_defaults(func=cmd_sheet)
    sub.add_parser("daily").set_defaults(func=cmd_daily)
    sub.add_parser("status").set_defaults(func=cmd_status)
    order = sub.add_parser("order")
    order.add_argument("side", choices=["BUY", "SELL"])
    order.add_argument("ticker")
    order.add_argument("--tl", type=float, default=0.0)
    order.add_argument("--fraction", type=float, default=1.0)
    order.add_argument("--why", required=True)
    order.set_defaults(func=cmd_order)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
