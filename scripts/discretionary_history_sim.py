from __future__ import annotations

"""Discretionary, human-style trading over the past on the whole BIST, day by day.

The investor asked for decisions made "like a human, no fixed rules", from the
names the score calls AL with a genuinely good P/B, on the whole routed BIST,
starting with 15,000 TL and adding 15,000 TL a month, sizing and selling at the
decider's discretion -- over the past, and told the decider to disregard what it
had already seen. It cannot un-see it: the result is recorded as a simulation by
a decider who had seen the period, and must not be read as a blind test.

What the tool does enforce
--------------------------
* ``sheet --date D`` shows only what was knowable on the evening of D: the panel
  month whose signal date is on or before D, and closes up to and including D.
* ``order --date D`` records a decision stamped on the evening of D; it fills at
  the close of the first session after D (the paper engine's own rule).
* ``advance --to D`` moves the book session by session with the paper engine:
  payments on the first session of each month, whole-share fills with 0.2%
  commission, cash dividends. Yahoo closes are already split-adjusted in one
  download, so split events are not applied again.
* A twin XU100 book receives the same payments.
"""

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.paper_portfolio import Book, INDEX, mark, process_session

BASE = ROOT / "data/full_bist_history_v1"
SIM = BASE / "sim"
LEDGER = SIM / "ledger.jsonl"
STATE = SIM / "state.json"
MARKS = SIM / "marks.csv"
START = "2025-04-01"


def _prices() -> tuple[pd.DataFrame, pd.DataFrame]:
    long = pd.read_csv(BASE / "prices.csv.gz")
    closes = long.pivot_table(index="trade_date", columns="ticker", values="close", aggfunc="last")
    closes = closes.loc[closes[INDEX].notna()]
    actions = pd.read_csv(BASE / "actions.csv")
    return closes, actions.loc[actions.kind.eq("DIVIDEND")].copy()


def _ledger() -> list[dict]:
    return [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if LEDGER.exists() else []


def _append(event: dict) -> None:
    SIM.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _book() -> Book:
    return Book(**json.loads(STATE.read_text(encoding="utf-8"))) if STATE.exists() else Book()


def _save(book: Book) -> None:
    SIM.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(book.__dict__, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                     encoding="utf-8", newline="\n")


def advance(to: str) -> dict:
    closes, dividends = _prices()
    book = _book()
    orders = [e for e in _ledger() if e["event"] == "ORDER"]
    carried = closes.ffill()
    for day in [d for d in closes.index if START <= d <= to and d > book.last_session]:
        for event in process_session(book, day, closes.loc[day], dividends.rename(columns={"date": "date"})
                                     .loc[dividends.date.eq(day)], orders, start=START):
            _append(event)
        row = mark(book, day, closes.loc[day], carried.loc[day].to_dict())
        pd.DataFrame([row]).to_csv(MARKS, mode="a", header=not MARKS.exists(), index=False)
    _save(book)
    return {"book": book, "last": pd.read_csv(MARKS).iloc[-1].to_dict() if MARKS.exists() else None}


def sheet(date: str, top: int = 25) -> str:
    closes, _ = _prices()
    known = closes.loc[closes.index <= date]
    panel = pd.read_csv(BASE / "panel.csv.gz")
    panel = panel.loc[panel.signal_date.notna() & (panel.signal_date <= date)]
    month = panel.signal_date.max()
    cohort = panel.loc[panel.signal_date.eq(month)].copy()
    last = known.iloc[-1]
    cohort["price"] = cohort.ticker.map(last)
    cohort["pb_now"] = cohort.price * cohort.nominal / cohort.equity
    positive = cohort.loc[cohort.pb_now > 0]
    cohort["pb_sector_median"] = cohort.sector_index_code.map(positive.groupby("sector_index_code").pb_now.median())
    cohort["pb_vs_sector"] = cohort.pb_now / cohort.pb_sector_median - 1
    hist = lambda t, n: (float(known[t].dropna().iloc[-1] / known[t].dropna().iloc[-n - 1] - 1)
                         if t in known and known[t].dropna().shape[0] > n else None)
    cohort["chg_20d"] = [hist(t, 20) for t in cohort.ticker]
    cohort["chg_63d"] = [hist(t, 63) for t in cohort.ticker]
    cohort["off_high"] = [float(known[t].dropna().iloc[-1] / known[t].dropna().tail(252).max() - 1)
                          if t in known and known[t].notna().any() else None for t in cohort.ticker]
    al = cohort.loc[cohort.decision.eq("AL") & cohort.pb_now.gt(0)].sort_values("pb_vs_sector").head(top)
    cols = ["ticker", "sector_index_code", "total_rasyo_100", "price", "pb_now", "pb_sector_median",
            "pb_vs_sector", "chg_20d", "chg_63d", "off_high"]
    book = _book()
    lines = [f"== {date} | skor ayi {month} | AL {int(cohort.decision.eq('AL').sum())} / {len(cohort)} "
             f"| nakit {book.cash:,.0f} TL | yatirilan {book.paid_in:,.0f} TL"]
    lines.append(al[cols].round(3).to_string(index=False))
    if book.shares:
        rows = []
        for ticker, qty in book.shares.items():
            price = float(known[ticker].dropna().iloc[-1])
            basis = book.cost_basis.get(ticker, 0.0)
            now = cohort.loc[cohort.ticker.eq(ticker)]
            rows.append({"hisse": ticker, "lot": qty, "fiyat": price, "deger": round(qty * price),
                         "maliyet": round(basis), "kar%": round(qty * price / basis - 1, 3) if basis else None,
                         "karar": now.decision.iloc[0] if len(now) else "-",
                         "pb_vs_sector": round(float(now.pb_vs_sector.iloc[0]), 3) if len(now) and pd.notna(now.pb_vs_sector.iloc[0]) else None,
                         "chg_20d": hist(ticker, 20)})
        lines.append("-- elimdekiler")
        lines.append(pd.DataFrame(rows).to_string(index=False))
    return "\n".join(lines)


def order(date: str, side: str, ticker: str, *, tl: float = 0.0, fraction: float = 1.0, why: str) -> dict:
    count = len([e for e in _ledger() if e["event"] == "ORDER"])
    event = {"event": "ORDER", "id": f"H{count + 1:04d}", "time": f"{date}T20:00:00+03:00",
             "decision_date": date, "side": side, "ticker": ticker.upper(), "why": why}
    if side == "BUY":
        event["tl"] = float(tl)
    else:
        event["fraction"] = float(fraction)
    _append(event)
    return event


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("advance"); a.add_argument("--to", required=True)
    s = sub.add_parser("sheet"); s.add_argument("--date", required=True); s.add_argument("--top", type=int, default=25)
    o = sub.add_parser("order"); o.add_argument("--date", required=True); o.add_argument("side", choices=["BUY", "SELL"])
    o.add_argument("ticker"); o.add_argument("--tl", type=float, default=0.0)
    o.add_argument("--fraction", type=float, default=1.0); o.add_argument("--why", required=True)
    args = parser.parse_args()
    if args.cmd == "advance":
        out = advance(args.to)
        print(json.dumps(out["last"], ensure_ascii=False))
    elif args.cmd == "sheet":
        print(sheet(args.date, args.top))
    else:
        print(json.dumps(order(args.date, args.side, args.ticker, tl=args.tl, fraction=args.fraction,
                               why=args.why), ensure_ascii=False))


if __name__ == "__main__":
    main()
