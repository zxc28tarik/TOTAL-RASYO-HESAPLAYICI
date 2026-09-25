from __future__ import annotations

"""One-year backtest: the rebuilt score says AL, then P/B decides.

The rules were fixed BEFORE any result was seen and are frozen in RULES below.
Every variant that was planned is reported; none is dropped for looking bad, and
none is promoted for looking good.

Stage 1 -- score. The rebuilt layer (src.analytics.rebuilt_total_score) ranks five
modules per month with equal weights: M1, M3, Ek4, Ek1, Ek9. M3, Ek4 and Ek9 are
ranked on their RAW production inputs (alpha_trailing, excess_return_20d,
volatility), recovered by re-running the same replays that produced the
historical P4 cells; the recomputed banded scores are checked against the P4
cells so it is visible that nothing drifted. AL = top 10% of the month.

Stage 2 -- P/B. P/B = close(cutoff day) x current nominal capital / PIT equity,
where PIT equity is the latest quarter whose publication preceded the signal's
knowledge cutoff. "Very good" is RELATIVE: P/B > 0 and in the cheapest third of
the month's scored cohort. Absolute P/B levels are not comparable across time in
Turkey because IAS 29 restates book values, so a level threshold would be a
parameter chosen by eye.

What this is not: an out-of-sample claim that survives multiple testing. The last
year sits inside the holdout; running this consumes it. The rebuilt layer has no
fitted parameters, which is what makes the consumption legitimate, but it also
means one year and four variants are one draw, not a distribution.
"""

import argparse
from dataclasses import dataclass
from datetime import date, datetime
import gzip
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.cross_sectional_score import percentile_score
from src.analytics.historical_pit_ek4_replay import run_historical_pit_ek4_replay
from src.analytics.historical_pit_ek9_replay import run_historical_pit_ek9_replay
from src.analytics.historical_pit_m3_replay import run_historical_pit_m3_replay
from src.analytics.rebuilt_total_score import RebuiltScoreConfig, rebuilt_scores

CONTRACT = "PB_TWO_STAGE_BACKTEST_V1"
P4 = ROOT / "data/audit/experimental_materialization_v3"
PRICES = ROOT / "data/backtest_sources/yahoo_resolved/historical_member_prices_resolved_2020-07_2026-08.csv.gz"
INDEX = ROOT / "data/backtest_sources/m3_source_package/index_closes.csv.gz"
ROUTES = ROOT / "data/backtest_sources/m3_source_package/sector_routes.csv.gz"
XU100 = ROOT / "data/backtest_sources/experimental_xu100_ohlc_v1/yahoo_chart.json"
CAPS = ROOT / "data/live/current_price_level_basis_v1/market_caps.csv"
OUTPUT = ROOT / "data/audit/pb_two_stage_backtest_v1"

CORE_MODULES = ("M1", "M3", "Ek4", "Ek1", "Ek9")

# Frozen before the first run. Changing any of these after seeing a result is the
# in-sample fitting the rebuild exists to refuse.
RULES = {
    "window_first_signal": "2025-08",
    "window_last_signal": "2026-07",
    "window_end": "2026-07-31",
    "score_layer": "REBUILT_TOTAL_SCORE_V1 equal weights, no veto",
    "score_buy": "decision == AL (top 10% of month cohort)",
    "score_hold_serbest": "score_percentile > 0.75 (top quarter)",
    "pb_definition": "close(cutoff day) * quoted_nominal_units_out(current) / total_equity(PIT)",
    "pb_very_good": "P/B > 0 and percentile among positive-P/B cohort <= 1/3",
    "max_positions": 6,
    "tie_break_when_over_capacity": "highest score_percentile first",
    "weighting": "equal weight among holdings, fully invested; cash only if nothing qualifies",
    "execution_price": "adj_close on execution day (total return incl. dividends)",
    "cost_per_side": 0.002,
    "cash_return": 0.0,
    "benchmark": "XU100 buy and hold, same start, same capital",
    "variants": {
        "aylik": "rebalance every month",
        "3_aylik": "rebalance every 3rd month from the first",
        "6_aylik": "rebalance every 6th month from the first",
        "serbest": ("monthly refresh: sell names that left top quarter or cheap third, "
                    "buy AL+cheap into free slots; weekly (first trading day of each ISO "
                    "week): sell a holding whose P/B left the cheapest third; freed cash "
                    "waits for the next monthly refresh"),
    },
    "discontinuity_rule": "any daily close move beyond +/-12% inside the window (BIST limit is +/-10%) "
                          "marks an unadjusted corporate action; the ticker is removed before scoring",
    "discontinuity_threshold": 0.12,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _months() -> list[str]:
    return [str(p) for p in pd.period_range(RULES["window_first_signal"], RULES["window_last_signal"], freq="M")]


def load_p4() -> pd.DataFrame:
    rows = []
    for month in _months():
        with gzip.open(P4 / f"p4_{month}.jsonl.gz", "rt", encoding="utf-8") as handle:
            for line in handle:
                cell = json.loads(line)
                values = cell["module_values"]
                rows.append({
                    "month": month, "ticker": cell["ticker"], "signal_date": cell["signal_date"],
                    "knowledge_cutoff_at": cell["knowledge_cutoff_at"], "good_count": cell["good_count"],
                    **{f"p4_{m}": values.get(m) for m in CORE_MODULES},
                })
    frame = pd.DataFrame(rows)
    # Fail closed: a cell missing any of the five modules is not scored.
    complete = frame[[f"p4_{m}" for m in CORE_MODULES]].notna().all(axis=1)
    return frame.loc[complete].reset_index(drop=True)


def load_prices() -> pd.DataFrame:
    prices = pd.read_csv(PRICES, usecols=["ticker", "trade_date", "close", "adj_close"])
    prices["trade_date"] = pd.to_datetime(prices.trade_date).dt.date
    return prices.dropna(subset=["close", "adj_close"]).sort_values(["ticker", "trade_date"])


def load_xu100() -> pd.Series:
    chart = json.loads(XU100.read_text(encoding="utf-8"))["chart"]["result"][0]
    days = pd.to_datetime(chart["timestamp"], unit="s", utc=True).tz_convert("Europe/Istanbul").date
    series = pd.Series(chart["indicators"]["quote"][0]["close"], index=days, dtype="float64").dropna()
    return series[~series.index.duplicated(keep="last")].sort_index()


def discontinuity_tickers(prices: pd.DataFrame, *, first: date, last: date) -> dict[str, list[dict]]:
    window = prices.loc[prices.trade_date.between(first, last)].copy()
    window["move"] = window.groupby("ticker").close.pct_change()
    bad = window.loc[window.move.abs() > RULES["discontinuity_threshold"]]
    return {ticker: [{"trade_date": str(r.trade_date), "move": round(float(r.move), 4)}
                     for r in group.itertuples()] for ticker, group in bad.groupby("ticker")}


def raw_market_inputs(p4: pd.DataFrame) -> pd.DataFrame:
    """Recover unbanded M3/Ek4/Ek9 inputs from the very call that built P4.

    build_market_modules is invoked exactly as scripts/materialize_experimental_p3_p4
    invokes it -- same membership, same bounded calendar, same price filter -- and
    each module's source_result row carries the raw input next to the banded
    value. Reproducing the banded value is the proof that nothing drifted.
    """
    from scripts.build_historical_m3_source_package import _historical_membership
    from scripts.experimental_historical_market_modules import build_market_modules

    members = _historical_membership(ROOT)
    indices = pd.read_csv(INDEX)
    indices["trade_date"] = pd.to_datetime(indices.trade_date)
    calendar = indices.loc[indices.index_code.eq("XU100"), ["trade_date"]].sort_values("trade_date")
    routes = pd.read_csv(ROUTES, dtype=str, keep_default_na=False)
    prices = pd.read_csv(PRICES, low_memory=False)
    prices["trade_date"] = pd.to_datetime(prices.trade_date)
    prices = prices.loc[prices.ticker.eq(prices.price_source_ticker)].copy()

    raw_fields = {"M3": ("alpha_trailing", "m3"), "Ek4": ("excess_return_20d", "ek4"),
                  "Ek9": ("volatility", "ek9")}
    out = []
    for month, cells in p4.groupby("month"):
        signal = cells.signal_date.iloc[0]
        cutoff = datetime.fromisoformat(cells.knowledge_cutoff_at.iloc[0])
        end = pd.Timestamp(cutoff.date())
        tickers = tuple(sorted(members.loc[members.signal_date.eq(signal), "ticker"]))
        bounded_calendar = calendar.loc[calendar.trade_date.le(end)]
        bounded_prices = prices.loc[prices.ticker.isin(tickers) & prices.trade_date.le(end)
                                    & prices.trade_date.isin(bounded_calendar.trade_date)]
        market = build_market_modules(cutoff, tickers, bounded_calendar, bounded_prices,
                                      indices.loc[indices.trade_date.le(end)], sector_routes=routes)
        for ticker in cells.ticker:
            row = {"month": month, "ticker": ticker, "cutoff_date": cutoff.date()}
            for module, (raw, banded) in raw_fields.items():
                source = market["per_ticker"].get(ticker, {}).get(module, {}).get("source_result") or {}
                row[raw] = source.get(raw)
                row[banded] = source.get(banded)
            out.append(row)
    return pd.DataFrame(out)
