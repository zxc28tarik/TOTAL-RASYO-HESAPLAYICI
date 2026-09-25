from __future__ import annotations

"""Monthly score + P/B panel for the whole routed BIST universe, point in time.

The historical P4 cells cover only the ~100 BIST100 members of each month. The
live line scores ~500 names but only today. This rebuilds the live line's own
inputs at past month-ends so the whole universe can be looked at as it looked
then:

* M1, Ek1 and total equity from the live CORE builder (build_core_modules) run
  on the KAP bulk archives with the month-end as the knowledge cutoff: a report
  published after the cutoff is excluded by the builder itself.
* M3, Ek4, Ek9 from the production replays on Yahoo daily prices, cut at the
  month-end session.
* The rebuilt score (equal-weight rank of the five, AL = top 10%).
* P/B = split-adjusted close at the cutoff x current nominal capital / PIT equity.

Known look-ahead, stated: sector routes are today's (valid from 2025-01-01);
nominal capital is today's, so a rights issue after the cutoff overstates past
P/B. Neither touches prices or returns.
"""

import json
import pickle
from datetime import datetime, time as dtime
from pathlib import Path
import sys
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.materialize_current_nonfin_valuation as nonfin
from scripts.experimental_core_module_materializer import build_core_modules
from scripts.materialize_current_market_modules import scored_routes
from src.analytics.historical_pit_ek4_replay import run_historical_pit_ek4_replay
from src.analytics.historical_pit_ek9_replay import run_historical_pit_ek9_replay
from src.analytics.historical_pit_m3_replay import run_historical_pit_m3_replay
from src.analytics.rebuilt_total_score import RebuiltScoreConfig, rebuilt_scores

OUT = ROOT / "data/full_bist_history_v1"
ARCHIVES = ROOT / "private/reconstructed_kap_archives"
ROUTES = ROOT / "data/live/current_sector_routes_v1/sector_routes.csv.gz"
INDEX = ROOT / "data/live/current_market_modules_v1/index_prices.csv.gz"
CAPS = ROOT / "data/live/current_price_level_basis_v1/market_caps.csv"
ISTANBUL = ZoneInfo("Europe/Istanbul")
FIRST_CUTOFF_MONTH, LAST_CUTOFF_MONTH = "2025-03", "2026-08"
ARCHIVE_NAMES = [f"KAP_{y}_{p}.zip" for y in (2023, 2024, 2025, 2026) for p in ("3A", "6A", "9A", "Y")]
MODULES = ("M1", "M3", "Ek4", "Ek1", "Ek9")


def load_routes() -> pd.DataFrame:
    routes = pd.read_csv(ROUTES, dtype=str)
    routes["valid_from"] = pd.to_datetime(routes.valid_from)
    routes["valid_to"] = pd.to_datetime(routes.valid_to, errors="coerce")
    return routes


def active(routes: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    live = routes.loc[scored_routes(routes) & routes.valid_from.le(day)
                      & (routes.valid_to.isna() | routes.valid_to.gt(day))].copy()
    live["ticker"] = live.ticker.str.upper()
    return live.drop_duplicates("ticker")


def mapped_reports(tickers: set[str], cache: Path) -> list[dict]:
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    out = []
    for name in ARCHIVE_NAMES:
        path = ARCHIVES / name
        if not path.exists() or name < "KAP_2023_9A.zip":
            continue
        digest = nonfin._sha(path)
        with ZipFile(path) as bundle:
            for member in bundle.namelist():
                if member.split("_", 1)[0] not in tickers:
                    continue
                report = nonfin.parse_kap_bulk_export_report(
                    archive_name=name, archive_sha256=digest, member_name=member, raw_html=bundle.read(member))
                out.append(nonfin.map_report((str(path), {
                    "member_name": member, "member_sha256": report.member_sha256,
                    "archive_name": name, "archive_sha256": digest})))
    cache.write_bytes(pickle.dumps(out))
    return out


def core_at(reports: list[dict], cutoff: datetime, tickers: list[str], routes: pd.DataFrame) -> pd.DataFrame:
    result = build_core_modules(reports, cutoff, tickers, family_routes=routes)
    rows = []
    for ticker in tickers:
        diag = result["per_ticker"].get(ticker, {})
        m1, ek1 = diag.get("m1") or [], diag.get("ek1") or []
        known = [q for q in diag.get("quarters", []) if q["values"].get("total_equity") is not None
                 and pd.Timestamp(q["published_at"]) <= pd.Timestamp(cutoff)]
        last = max(known, key=lambda q: (q["period_end"], q["published_at"])) if known else None
        rows.append({
            "ticker": ticker,
            "m1": m1[-1]["m1"] if m1 and ek1 else None,
            "good_count": m1[-1]["good_count_ge8"] if m1 and ek1 else None,
            "core_period_end": str(m1[-1]["period_end"]) if m1 else None,
            "equity": float(last["values"]["total_equity"]) if last else None,
            "equity_period_end": last["period_end"] if last else None,
            "equity_published_at": last["published_at"] if last else None,
        })
    return pd.DataFrame(rows)


def market_at(prices: pd.DataFrame, index: pd.DataFrame, universe: pd.DataFrame, cutoff_day) -> pd.DataFrame:
    calendar = pd.DataFrame({"trade_date": sorted(
        d for d in index.loc[index.index_code.eq("XU100"), "trade_date"].unique() if d <= cutoff_day)})
    stocks = prices.loc[prices.ticker.isin(universe.ticker) & prices.trade_date.le(cutoff_day)
                        & prices.trade_date.isin(set(calendar.trade_date)),
                        ["ticker", "trade_date", "close", "adj_close"]]
    analysis = datetime.combine(cutoff_day, dtime(18, 10), tzinfo=ISTANBUL)
    common = dict(analysis_at=analysis, asof_date=cutoff_day, market_asof_date=cutoff_day,
                  universe=universe[["ticker", "sector_index_code"]], trading_calendar=calendar,
                  stock_prices=stocks)
    idx = index.loc[index.trade_date.le(cutoff_day)]
    m3 = run_historical_pit_m3_replay(**common, index_prices=idx).m3_scores
    ek4 = run_historical_pit_ek4_replay(**common, index_prices=idx).ek4_scores
    ek9 = run_historical_pit_ek9_replay(**common).ek9_scores
    frame = universe[["ticker", "sector_index_code"]].copy()
    frame = frame.merge(m3[["ticker", "alpha_trailing"]], on="ticker", how="left")
    frame = frame.merge(ek4[["ticker", "excess_return_20d"]], on="ticker", how="left")
    return frame.merge(ek9[["ticker", "volatility"]], on="ticker", how="left")


def build(prices_path: Path, cache: Path) -> pd.DataFrame:
    routes = load_routes()
    prices = pd.read_csv(prices_path)
    prices["trade_date"] = pd.to_datetime(prices.trade_date).dt.date
    index = pd.read_csv(INDEX)
    index["trade_date"] = pd.to_datetime(index.trade_date).dt.date
    xu = sorted(index.loc[index.index_code.eq("XU100"), "trade_date"].unique())
    nominal = pd.read_csv(CAPS).set_index("ticker")["quoted_nominal_units_out"].astype(float)
    all_tickers = set(active(routes, pd.Timestamp("2026-08-31")).ticker)
    reports = mapped_reports(all_tickers, cache)

    months = [str(p) for p in pd.period_range(FIRST_CUTOFF_MONTH, LAST_CUTOFF_MONTH, freq="M")]
    panels = []
    for month in months:
        cutoff_day = max(d for d in xu if str(d)[:7] == month)
        signal_day = min(d for d in xu if d > cutoff_day) if any(d > cutoff_day for d in xu) else None
        universe = active(routes, pd.Timestamp(cutoff_day))
        universe = universe.loc[universe.ticker.isin(set(prices.ticker))]
        core = core_at(reports, datetime.combine(cutoff_day, dtime(18, 10), tzinfo=ISTANBUL),
                       sorted(universe.ticker), routes)
        market = market_at(prices, index, universe, cutoff_day)
        frame = market.merge(core, on="ticker", how="left")
        close = prices.loc[prices.trade_date.eq(cutoff_day)].set_index("ticker").close
        frame["cutoff_close"] = frame.ticker.map(close)
        frame["nominal"] = frame.ticker.map(nominal)
        frame["pb"] = frame.cutoff_close * frame.nominal / frame.equity
        frame["month"] = str(signal_day)[:7] if signal_day else month
        frame["cutoff_date"] = str(cutoff_day)
        frame["signal_date"] = str(signal_day) if signal_day else None
        frame["M1"], frame["M3"], frame["Ek4"] = frame.m1, frame.alpha_trailing, frame.excess_return_20d
        frame["Ek1"], frame["Ek9"] = frame.good_count, -frame.volatility
        scored = rebuilt_scores(frame, config=RebuiltScoreConfig(modules=list(MODULES)))
        panels.append(scored.drop(columns=["ranked_columns"]))
        print(month, "universe", len(universe), "scored", int(scored.score_percentile.notna().sum()),
              "AL", int(scored.decision.eq("AL").sum()), "with P/B", int(scored.pb.notna().sum()), flush=True)
    panel = pd.concat(panels, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    panel.to_csv(OUT / "panel.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    return panel


if __name__ == "__main__":
    scratch = Path(sys.argv[1])
    build(scratch / "full_prices.csv.gz", scratch / "reports.pkl")
