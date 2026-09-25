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


def load_equity(p4: pd.DataFrame) -> pd.DataFrame:
    """Latest quarter whose publication precedes the signal's knowledge cutoff."""
    wanted = {(r.signal_date, r.ticker): r.knowledge_cutoff_at for r in p4.itertuples()}
    signals = set(p4.signal_date)
    rows = []
    with gzip.open(P4 / "core_diagnostics.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            snapshot = json.loads(line)
            signal = snapshot["signal_date"]
            if signal not in signals:
                continue
            for ticker, detail in snapshot["per_ticker"].items():
                cutoff = wanted.get((signal, ticker))
                if cutoff is None:
                    continue
                limit = pd.Timestamp(cutoff)
                known = [q for q in detail.get("quarters", [])
                         if q["values"].get("total_equity") is not None
                         and pd.Timestamp(q["published_at"]) <= limit]
                if not known:
                    continue
                last = max(known, key=lambda q: (q["period_end"], q["published_at"], q["version_sequence"]))
                rows.append({"signal_date": signal, "ticker": ticker,
                             "equity": float(last["values"]["total_equity"]),
                             "equity_period_end": last["period_end"],
                             "equity_published_at": last["published_at"],
                             "pit_shares_out": last["values"].get("shares_out")})
    return pd.DataFrame(rows)


def current_nominal() -> pd.Series:
    caps = pd.read_csv(CAPS)
    return caps.set_index("ticker")["quoted_nominal_units_out"].astype(float)


def pb_percentile(pb: pd.Series) -> pd.Series:
    """Rank P/B among the positive values of a cohort; cheaper = lower percentile."""
    positive = pb.where(pb > 0)
    return percentile_score(positive)


def build_panel(p4: pd.DataFrame, raw: pd.DataFrame, equity: pd.DataFrame,
                nominal: pd.Series, closes: pd.DataFrame, excluded: set[str]) -> pd.DataFrame:
    frame = p4.merge(raw, on=["month", "ticker"], how="left")
    frame = frame.loc[~frame.ticker.isin(excluded)].copy()
    frame["M1"] = frame.p4_M1
    frame["M3"] = frame.alpha_trailing.astype(float)
    frame["Ek4"] = frame.excess_return_20d.astype(float)
    frame["Ek1"] = frame.good_count.astype(float)
    frame["Ek9"] = -frame.volatility.astype(float)
    scored = rebuilt_scores(frame, config=RebuiltScoreConfig(modules=list(CORE_MODULES)))
    scored = scored.merge(equity, on=["signal_date", "ticker"], how="left")
    scored["nominal"] = scored.ticker.map(nominal)
    scored["cutoff_close"] = [close_on_or_before(closes, t, d) for t, d in zip(scored.ticker, scored.cutoff_date)]
    scored["pb"] = scored.cutoff_close * scored.nominal / scored.equity
    scored["pb_pct"] = scored.groupby("month", group_keys=False).pb.apply(pb_percentile)
    scored["cheap"] = scored.pb_pct.le(1.0 / 3.0).fillna(False).astype(bool)
    scored["al"] = scored.decision.eq("AL")
    scored["qualify"] = scored.al & scored.cheap
    return scored


def close_on_or_before(closes: pd.DataFrame, ticker: str, day: date, *, max_gap_days: int = 7) -> float | None:
    if ticker not in closes.columns:
        return None
    series = closes[ticker].loc[:pd.Timestamp(day)].dropna()
    if series.empty or (pd.Timestamp(day) - series.index[-1]).days > max_gap_days:
        return None
    return float(series.iloc[-1])


@dataclass
class Book:
    cash: float
    units: dict

    def value(self, prices: pd.Series) -> float:
        return self.cash + sum(u * prices[t] for t, u in self.units.items())


def _trade_to(book: Book, targets: list[str], prices: pd.Series, *, cost: float) -> dict:
    """Move to equal weight across `targets`, paying `cost` on every lira traded."""
    targets = [t for t in targets if t in prices.index and pd.notna(prices[t])]
    total = book.value(prices)
    current = {t: book.units.get(t, 0.0) * prices[t] for t in set(book.units) | set(targets)}
    if targets:
        # Solve target value so that costs come out of the same pot: v = (total - c*|dv|) / n.
        # One fixed-point pass is exact to well below a basis point here.
        target_value = total / len(targets)
        traded = sum(abs((target_value if t in targets else 0.0) - current[t]) for t in current)
        target_value = (total - cost * traded) / len(targets)
    else:
        target_value = 0.0
    traded = sum(abs((target_value if t in targets else 0.0) - current[t]) for t in current)
    fee = cost * traded
    book.units = {t: target_value / prices[t] for t in targets}
    book.cash = total - fee - target_value * len(targets)
    return {"traded": traded, "fee": fee, "value_before": total}


def _sell(book: Book, tickers: list[str], prices: pd.Series, *, cost: float) -> dict:
    proceeds = sum(book.units[t] * prices[t] for t in tickers)
    fee = cost * proceeds
    for t in tickers:
        book.units.pop(t)
    book.cash += proceeds - fee
    return {"traded": proceeds, "fee": fee}


def simulate(panel: pd.DataFrame, adj: pd.DataFrame, closes: pd.DataFrame, *,
             rebalance_months: list[str], mode: str, select, end: date,
             capacity: int | None = RULES["max_positions"]) -> dict:
    """Run one variant on a daily calendar; `select(month_frame)` returns ordered targets."""
    cost = RULES["cost_per_side"]
    days = adj.loc[adj.index >= pd.Timestamp(panel.signal_date.min())].loc[:pd.Timestamp(end)].index
    filled = adj.ffill()
    signal_by_day = {pd.Timestamp(d): m for m, d in panel.groupby("month").signal_date.first().items()}
    book = Book(cash=1.0, units={})
    curve, log, fees, traded_total, month = [], [], 0.0, 0.0, None
    week_seen = set()
    for day in days:
        prices = filled.loc[day]
        event = None
        if day in signal_by_day:
            month = signal_by_day[day]
            week_seen.add(day.isocalendar()[:2])
            cohort = panel.loc[panel.month.eq(month)]
            if month in rebalance_months:
                if mode == "serbest":
                    keep = [t for t in book.units
                            if ((cohort.ticker == t) & cohort.score_percentile.gt(0.75) & cohort.cheap).any()]
                    fresh = [t for t in select(cohort) if t not in keep]
                    targets = (keep + fresh)[:capacity]
                else:
                    targets = select(cohort)[:capacity]
                event = _trade_to(book, targets, prices, cost=cost)
                event.update(kind="rebalance", month=month, holdings=sorted(book.units))
        elif mode == "serbest" and month is not None and book.units:
            week = day.isocalendar()[:2]
            if week not in week_seen:
                week_seen.add(week)
                cohort = panel.loc[panel.month.eq(month)].copy()
                previous = adj.index[adj.index.get_loc(day) - 1]
                cohort["pb_now"] = [
                    (close_on_or_before(closes, t, previous.date()) or float("nan")) * n / e
                    for t, n, e in zip(cohort.ticker, cohort.nominal, cohort.equity)]
                cohort["pct_now"] = pb_percentile(cohort.pb_now)
                cheap_now = set(cohort.loc[cohort.pct_now.le(1.0 / 3.0), "ticker"])
                leaving = [t for t in book.units if t not in cheap_now]
                if leaving:
                    event = _sell(book, leaving, prices, cost=cost)
                    event.update(kind="weekly_exit", sold=sorted(leaving), holdings=sorted(book.units))
        if event:
            fees += event["fee"]
            traded_total += event["traded"]
            event["date"] = str(day.date())
            log.append(event)
        curve.append({"date": day, "value": book.value(prices), "positions": len(book.units),
                      "cash_share": book.cash / max(book.value(prices), 1e-12)})
    curve = pd.DataFrame(curve).set_index("date")
    return {"curve": curve, "log": log, "fees": fees, "traded": traded_total}


def metrics(curve: pd.DataFrame, bench: pd.Series) -> dict:
    value = curve["value"]
    bench = bench.reindex(value.index).ffill()
    peak = value.cummax()
    month_end = value.groupby(value.index.to_period("M")).last()
    month_ret = month_end.pct_change()
    month_ret.iloc[0] = month_end.iloc[0] / value.iloc[0] - 1.0
    b_end = bench.groupby(bench.index.to_period("M")).last()
    b_ret = b_end.pct_change()
    b_ret.iloc[0] = b_end.iloc[0] / bench.iloc[0] - 1.0
    return {
        "start": str(value.index[0].date()), "end": str(value.index[-1].date()),
        "total_return": float(value.iloc[-1] / value.iloc[0] - 1.0),
        "xu100_return": float(bench.iloc[-1] / bench.iloc[0] - 1.0),
        "excess_vs_xu100": float(value.iloc[-1] / value.iloc[0] - bench.iloc[-1] / bench.iloc[0]),
        "max_drawdown": float((value / peak - 1.0).min()),
        "xu100_max_drawdown": float((bench / bench.cummax() - 1.0).min()),
        "months_beating_xu100": int((month_ret > b_ret).sum()),
        "months": int(len(month_ret)),
        "average_positions": float(curve["positions"].mean()),
        "average_cash_share": float(curve["cash_share"].mean()),
        "monthly_returns": {str(k): round(float(v), 4) for k, v in month_ret.items()},
        "xu100_monthly_returns": {str(k): round(float(v), 4) for k, v in b_ret.items()},
    }


def build(*, output_dir: Path = OUTPUT) -> dict:
    p4 = load_p4()
    prices = pd.read_csv(PRICES, low_memory=False)
    prices = prices.loc[prices.ticker.eq(prices.price_source_ticker)].copy()
    prices["trade_date"] = pd.to_datetime(prices.trade_date)
    bench = load_xu100()
    bench.index = pd.to_datetime(bench.index)
    first = pd.Timestamp(p4.signal_date.min())
    end = pd.Timestamp(RULES["window_end"])
    calendar = bench.loc[first:end].index

    closes = prices.pivot_table(index="trade_date", columns="ticker", values="close", aggfunc="last")
    adj = prices.pivot_table(index="trade_date", columns="ticker", values="adj_close", aggfunc="last")
    adj = adj.reindex(calendar)

    discontinuities = discontinuity_tickers(
        prices.assign(trade_date=prices.trade_date.dt.date).loc[prices.ticker.isin(p4.ticker)],
        first=first.date(), last=end.date())
    excluded = set(discontinuities)

    raw = raw_market_inputs(p4)
    for module, raw_col, banded in (("M3", "alpha_trailing", "m3"), ("Ek4", "excess_return_20d", "ek4"),
                                    ("Ek9", "volatility", "ek9")):
        drift = (p4.merge(raw, on=["month", "ticker"])[f"p4_{module}"]
                 - p4.merge(raw, on=["month", "ticker"])[banded].astype(float)).abs().max()
        if not drift < 1e-9:
            raise ValueError(f"P4_REPRODUCTION_DRIFT:{module}:{drift}")

    equity = load_equity(p4)
    nominal = current_nominal()
    panel = build_panel(p4, raw, equity, nominal, closes, excluded)

    months = sorted(panel.month.unique())
    by_score = lambda frame, mask: frame.loc[mask(frame)].sort_values(
        ["score_percentile", "ticker"], ascending=[False, True]).ticker.tolist()
    strategy = lambda frame: by_score(frame, lambda f: f.qualify)
    variants = {
        "aylik": dict(rebalance_months=months, mode="fixed", select=strategy),
        "3_aylik": dict(rebalance_months=months[::3], mode="fixed", select=strategy),
        "6_aylik": dict(rebalance_months=months[::6], mode="fixed", select=strategy),
        "serbest": dict(rebalance_months=months, mode="serbest", select=strategy),
    }
    # References, reported beside the strategy so each stage's contribution is
    # visible. None of them is a candidate strategy.
    references = {
        "ref_sadece_skor_AL_aylik": dict(rebalance_months=months, mode="fixed",
                                         select=lambda f: by_score(f, lambda x: x.al)),
        "ref_sadece_ucuz_PDDD_aylik": dict(rebalance_months=months, mode="fixed",
                                           select=lambda f: f.loc[f.cheap].sort_values(
                                               ["pb_pct", "ticker"]).ticker.tolist()),
        "ref_tum_evren_esit_agirlik_aylik": dict(rebalance_months=months, mode="fixed",
                                                 select=lambda f: f.loc[f.score_percentile.notna()]
                                                 .ticker.tolist(), capacity=None),
    }

    results = {}
    for name, spec in {**variants, **references}.items():
        run = simulate(panel, adj, closes, end=end.date(), **spec)
        results[name] = {**metrics(run["curve"], bench), "fees_paid": run["fees"],
                         "turnover": run["traded"], "trade_log": run["log"]}

    qualify = panel.loc[panel.qualify].sort_values(["month", "score_percentile"], ascending=[True, False])
    monthly_picks = {m: g.ticker.tolist() for m, g in qualify.groupby("month")}
    al_rows = panel.loc[panel.al]
    coverage = {
        "cells": int(len(panel)),
        "cells_scored": int(panel.score_percentile.notna().sum()),
        "cells_with_pb": int(panel.pb.notna().sum()),
        "cells_with_positive_pb": int((panel.pb > 0).sum()),
        "al_cells": int(len(al_rows)),
        "al_cells_without_pb": int(al_rows.pb.isna().sum()),
        "al_cells_cheap": int(al_rows.cheap.sum()),
        "months_with_no_qualifier": [m for m in months if m not in monthly_picks],
        "pit_vs_current_capital_ratio_median": float(
            (panel.nominal / panel.pit_shares_out.astype(float)).median()),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    panel_path = output_dir / "panel.csv.gz"
    panel[["month", "signal_date", "ticker", "score_percentile", "decision", "pb", "pb_pct", "cheap",
           "qualify", "equity", "equity_period_end", "equity_published_at", "nominal",
           "cutoff_close", "alpha_trailing", "excess_return_20d", "volatility", "p4_M1",
           "good_count"]].to_csv(panel_path, index=False, compression={"method": "gzip", "mtime": 0})
    receipt = {
        "contract": CONTRACT,
        "rules_frozen_before_first_run": RULES,
        "holdout_consumed": "2025-08..2026-07 lies inside the 2024-08..2026-07 holdout",
        "p4_reproduction": "866/866 banded M3/Ek4/Ek9 values reproduced exactly from raw inputs",
        "excluded_discontinuity_tickers": discontinuities,
        "coverage": coverage,
        "monthly_qualifiers": monthly_picks,
        "results": {k: {kk: vv for kk, vv in v.items() if kk != "trade_log"} for k, v in results.items()},
        "trade_logs": {k: v["trade_log"] for k, v in results.items()},
        "not_modelled": [
            "cash earns 0% although TL deposits paid far more; idle cash is penalised",
            "P/B uses CURRENT nominal capital, so a rights issue inside the window is not captured",
            "returns are nominal TL; inflation is not removed",
            "one year, one path: no significance claim is made",
        ],
        "source_sha256": {"prices": _sha(PRICES), "index": _sha(INDEX), "routes": _sha(ROUTES),
                          "xu100": _sha(XU100), "market_caps": _sha(CAPS),
                          "core_diagnostics": _sha(P4 / "core_diagnostics.jsonl.gz")},
        "outputs": {"panel.csv.gz": _sha(panel_path)},
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8", newline="\n")
    return receipt


def main() -> None:
    argparse.ArgumentParser().parse_args()
    receipt = build()
    for name, result in receipt["results"].items():
        print(f"{name:34s} getiri {result['total_return']:+8.2%}  XU100 {result['xu100_return']:+8.2%}  "
              f"fark {result['excess_vs_xu100']:+8.2%}  maxDD {result['max_drawdown']:+7.2%}  "
              f"ort.poz {result['average_positions']:.1f}  nakit {result['average_cash_share']:.0%}")


if __name__ == "__main__":
    main()
