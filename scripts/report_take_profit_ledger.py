from __future__ import annotations

"""Readable ledger for the primary take-profit variant: what, when, why, at what price.

Nothing here decides anything; it re-reads the published positions and looks up,
for every buy, the facts the strategy saw at that moment (score, P/B, which
balance sheet), and for every buy and sale the quoted close of that session.

Prices: ``close`` is the exchange close as Yahoo serves it, split-adjusted but
not dividend-adjusted -- the figure a chart shows. Returns in the ledger come
from ``adj_close`` (dividends included), which is what the backtest measured.
"""

import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backtest_horizon_strategy import PointInTime, build_panel, priced_cohort
from scripts.backtest_pb_two_stage import PRICES, current_nominal, load_equity, load_p4, raw_market_inputs
from scripts.backtest_trailing_hold import RULES as HOLD_RULES

OUTPUT = ROOT / "data/audit/take_profit_vs_random_v1"
VARIANT = "A_yarisi_30_kalani_70"


def build() -> dict:
    positions = json.loads((OUTPUT / "positions.json").read_text(encoding="utf-8"))[VARIANT]
    months = [str(p) for p in pd.period_range(HOLD_RULES["window_first_signal"],
                                              HOLD_RULES["window_last_signal"], freq="M")]
    p4 = load_p4(months)
    raw = raw_market_inputs(p4)
    panel = build_panel(p4, raw, load_equity(p4), current_nominal())

    prices = pd.read_csv(PRICES, low_memory=False)
    prices = prices.loc[prices.ticker.eq(prices.price_source_ticker)].copy()
    prices["trade_date"] = pd.to_datetime(prices.trade_date)
    closes = prices.pivot_table(index="trade_date", columns="ticker", values="close", aggfunc="last")
    view = PointInTime(panel, closes)

    rows = []
    for position in sorted(positions, key=lambda p: (p["entry_date"], p["ticker"])):
        ticker, entry = position["ticker"], pd.Timestamp(position["entry_date"])
        seen = priced_cohort(view, entry).loc[ticker]
        rows.append({
            "hisse": ticker, "islem": "AL", "tarih": position["entry_date"],
            "kapanis_fiyati_tl": round(float(closes.at[entry, ticker]), 2),
            "pay": 1.0, "neden": "SKOR_AL_VE_PDDD_UCUZ_YARI",
            "skor_yuzdelik": round(float(seen.score_percentile), 3),
            "pddd": round(float(seen.pb_now), 2),
            "pddd_yuzdelik": round(float(seen.pb_pct_now), 3),
            "bilanco_donemi": seen.equity_period_end,
            "bilanco_yayim": seen.equity_published_at,
            "getiri_alistan": None,
        })
        for sale in position["exits"]:
            day = pd.Timestamp(sale["date"])
            rows.append({
                "hisse": ticker, "islem": "ACIK" if sale["reason"] == "ACIK" else "SAT",
                "tarih": sale["date"],
                "kapanis_fiyati_tl": round(float(closes.at[day, ticker]), 2),
                "pay": sale["share"], "neden": sale["reason"],
                "skor_yuzdelik": None, "pddd": None, "pddd_yuzdelik": None,
                "bilanco_donemi": None, "bilanco_yayim": None,
                "getiri_alistan": round(sale["price"] / position["entry_price"] - 1.0, 4),
            })
    ledger = pd.DataFrame(rows)

    cohorts = {m: sorted(g.ticker) for m, g in panel.groupby("month")}
    al = {m: sorted(g.loc[g.decision.eq("AL"), "ticker"]) for m, g in panel.groupby("month")}
    universe = {
        "window": [HOLD_RULES["window_first_signal"], HOLD_RULES["window_last_signal"], HOLD_RULES["window_end"]],
        "months": len(cohorts),
        "tickers_ever_scored": sorted(set(panel.ticker)),
        "cohort_size_per_month": {m: len(v) for m, v in cohorts.items()},
        "al_per_month": al,
        "balance_sheet_periods_used": sorted(set(panel.equity_period_end.dropna())),
    }
    ledger.to_csv(OUTPUT / f"ledger_{VARIANT}.csv", index=False)
    (OUTPUT / "universe.json").write_text(json.dumps(universe, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8", newline="\n")
    return {"ledger": ledger, "universe": universe}


if __name__ == "__main__":
    out = build()
    pd.set_option("display.width", 250)
    print(out["ledger"].to_string(index=False))
    u = out["universe"]
    print("months", u["months"], "tickers", len(u["tickers_ever_scored"]))
    print("balance sheets", u["balance_sheet_periods_used"])
