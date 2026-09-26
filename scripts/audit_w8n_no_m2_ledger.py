from __future__ import annotations

"""W8-N step 2: the 60-month trade ledger for the five-module (no-M2) variant.

This runs the locked production portfolio engine (MonthlyTotalRasyoSimulator) on
the W8-N score series. The engine's rules are not reimplemented here and not
relaxed: at most six positions, buy only AL, held IZLE kept without new cash,
held UZAK sold, stronger AL rotates out the weakest holding, execution at the
signal-day OPEN, marking at the signal-day CLOSE, integer shares with the
residue left in cash.

Stated assumptions -- none of these are hidden defaults
------------------------------------------------------
* Contribution: two net minimum wages per month, from the CSGB schedule, the
  same rule the existing cash diagnostic used.
* Transaction costs, slippage, spread and tax: ZERO. Out of scope for the
  engine (V24-B), so reported returns are gross and therefore optimistic.
* Cash interest: ZERO. Idle cash earns nothing, which penalises cash-heavy
  months, and under "buy only AL" many months are cash-heavy.
* Benchmark: XU100, the identical contribution schedule bought as fractional
  index units at the signal-day OPEN and marked at the same day's CLOSE, zero
  fees and no interest.
* Corporate actions: the Yahoo-discovered split/dividend table. These are
  vendor-derived, NOT KAP-verified; each action that actually touched a held
  position is listed in the receipt so the exposure is auditable rather than
  assumed away.
* A month in which no name scores AL holds cash. Such a month is NOT a
  performance success and is counted separately.

What this is not: it is not the six-module Total Rasyo, and a five-module result
says nothing about the model the project actually ships until the same ledger
can be run with M2 present.
"""

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.monthly_total_rasyo_portfolio import (
    MonthlyTotalRasyoSimulator, benchmark_dca,
)

CONTRACT = "W8N_NO_M2_FIVE_MODULE_LEDGER_V1"
SERIES = ROOT / "data/audit/w8n_no_m2_backtest_v1/series.jsonl"
PRICES = ROOT / "data/backtest_sources/yahoo_resolved/historical_member_prices_resolved_2020-07_2026-08.csv.gz"
SIGNALS = ROOT / "data/backtest_sources/xu100_signal_dates_yahoo_2021-08_2026-07.csv"
ACTIONS = ROOT / "data/backtest_sources/yahoo_discovery/historical_member_actions_yahoo_2020-07_2026-08.csv"
CHANGES = ROOT / "data/backtest_sources/bist_ticker_code_changes_2021-08_2026-08.csv"
WAGES = ROOT / "data/backtest_sources/minimum_wage_csgb_2021_2026.csv"
BENCHMARK = ROOT / "data/backtest_sources/experimental_xu100_ohlc_v1"
OUTPUT = ROOT / "data/audit/w8n_no_m2_ledger_v1"

CONTRIBUTION_MULTIPLE = 2


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def benchmark_quotes(source_dir: Path = BENCHMARK) -> tuple[list[dict], dict]:
    from zoneinfo import ZoneInfo
    raw = (source_dir / "yahoo_chart.json").read_bytes()
    receipt = json.loads((source_dir / "source_receipt.json").read_bytes())
    if sha256(raw).hexdigest() != receipt["sha256"]:
        raise ValueError("BENCHMARK_SOURCE_HASH_MISMATCH")
    result = json.loads(raw)["chart"]["result"][0]
    if result["meta"]["symbol"] != "XU100.IS" or result["meta"]["currency"] != "TRY":
        raise ValueError("BENCHMARK_IDENTITY_MISMATCH")
    quote = result["indicators"]["quote"][0]
    rows = []
    for stamp, opening, close in zip(result["timestamp"], quote["open"], quote["close"], strict=True):
        if opening is None or close is None:
            continue
        day = datetime.fromtimestamp(stamp, timezone.utc).astimezone(
            ZoneInfo("Europe/Istanbul")).date().isoformat()
        rows.append({"trade_date": day, "open": opening, "close": close})
    return rows, receipt


def contribution_schedule(signal_days: list[str]) -> pd.DataFrame:
    wages = list(csv.DictReader(WAGES.open(encoding="utf-8")))
    rows = []
    for day in signal_days:
        matches = [w for w in wages if w["valid_from"] <= day < w["valid_to"]]
        if len(matches) != 1:
            raise ValueError(f"MINIMUM_WAGE_INTERVAL_NOT_UNIQUE:{day}")
        rows.append({
            "signal_date": day,
            "contribution": float(Decimal(matches[0]["net_min_wage"]) * CONTRIBUTION_MULTIPLE),
        })
    return pd.DataFrame(rows)


def build(*, output_dir: Path = OUTPUT) -> dict:
    series = [json.loads(line) for line in SERIES.read_text(encoding="utf-8").splitlines() if line.strip()]
    signals = pd.DataFrame([{
        "signal_date": row["signal_date"], "ticker": row["ticker"],
        "final_score": row["final_score"], "decision": row["decision"],
    } for row in series])

    signal_days = sorted({row["signal_date"] for row in series})
    declared = sorted(pd.read_csv(SIGNALS).signal_date.astype(str))
    if signal_days != declared:
        raise ValueError("W8N_SIGNAL_DATES_DISAGREE_WITH_SOURCE")

    prices = pd.read_csv(PRICES, low_memory=False)
    prices = prices.loc[prices.trade_date.isin(set(signal_days)),
                        ["trade_date", "ticker", "open", "close"]].drop_duplicates()
    if prices[["open", "close"]].isna().any().any():
        raise ValueError("W8N_LEDGER_PRICE_NULL")

    actions = pd.read_csv(ACTIONS)
    window = actions.action_date.between(min(signal_days), max(signal_days))
    actions = actions.loc[window, ["action_date", "ticker", "split_factor", "cash_dividend_per_share"]]
    # The engine refuses a row that is a no-op on both legs.
    actions = actions.loc[~(actions.split_factor.isin([0.0, 1.0])
                            & actions.cash_dividend_per_share.eq(0.0))]
    changes = pd.read_csv(CHANGES)[["effective_date", "old_ticker", "new_ticker"]]
    changes = changes.loc[changes.effective_date.between(min(signal_days), max(signal_days))]

    contributions = contribution_schedule(signal_days)
    simulator = MonthlyTotalRasyoSimulator()
    trades, monthly = simulator.run(signals, prices, contributions, actions, changes)

    quotes, benchmark_source = benchmark_quotes()
    quote_map = {row["trade_date"]: row for row in quotes}
    for day in signal_days:
        month_days = [q["trade_date"] for q in quotes if q["trade_date"][:7] == day[:7]]
        if not month_days or day != min(month_days):
            raise ValueError(f"SIGNAL_NOT_FIRST_BENCHMARK_SESSION:{day}")
    benchmark = benchmark_dca(contributions, pd.DataFrame(quotes))

    # Independent Decimal reconciliation of contributions and benchmark units;
    # the simulator's own arithmetic is never trusted on its own word.
    cumulative = Decimal(0)
    units = Decimal(0)
    ledger: list[dict] = []
    for row, bench in zip(monthly.itertuples(index=False), benchmark.itertuples(index=False), strict=True):
        day = row.date.date().isoformat()
        cumulative += Decimal(str(row.contribution))
        units += Decimal(str(row.contribution)) / Decimal(str(quote_map[day]["open"]))
        independent = units * Decimal(str(quote_map[day]["close"]))
        if abs(Decimal(str(bench.benchmark_value)) - independent) > Decimal("0.000001"):
            raise ValueError(f"BENCHMARK_INDEPENDENT_RECONCILIATION_FAILURE:{day}")
        if abs(Decimal(str(row.cumulative_contribution)) - cumulative) > Decimal("0.000001"):
            raise ValueError(f"CONTRIBUTION_RECONCILIATION_FAILURE:{day}")
        if row.cash < 0:
            raise ValueError(f"NEGATIVE_CASH:{day}")
        holdings = [name for name in str(row.holdings).split(", ") if name]
        ledger.append({
            "month": day[:7], "signal_date": day,
            "contribution": float(row.contribution),
            "cumulative_contribution": float(row.cumulative_contribution),
            "cash": float(row.cash), "holdings_value": float(row.holdings_value),
            "nav": float(row.nav), "position_count": len(holdings),
            "holdings": holdings,
            "buys": [name for name in str(row.buys).split(", ") if name],
            "sells": [name for name in str(row.sells).split(", ") if name],
            "xu100_open": quote_map[day]["open"], "xu100_close": quote_map[day]["close"],
            "xu100_units": float(bench.units), "xu100_nav": float(bench.benchmark_value),
        })

    trade_rows = json.loads(trades.to_json(orient="records", date_format="iso")) if not trades.empty else []
    held_tickers = sorted({row["ticker"] for row in trade_rows})
    # Corporate actions that actually touched a position the ledger held.
    touched = actions.loc[actions.ticker.isin(held_tickers)].copy()

    final = ledger[-1]
    total_contribution = final["cumulative_contribution"]
    cash_only_months = [row["month"] for row in ledger if row["position_count"] == 0]

    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "ledger.json"
    trades_path = output_dir / "trades.json"
    ledger_path.write_text(json.dumps({"contract": CONTRACT, "months": ledger},
                                      ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    trades_path.write_text(json.dumps({"contract": CONTRACT, "trades": trade_rows},
                                      ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")

    receipt = {
        "contract": CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "DIAGNOSTIC_ONLY_FIVE_MODULE_VARIANT_NOT_THE_PRODUCTION_MODEL",
        "excluded_module": "M2",
        "production_portfolio_engine": "MonthlyTotalRasyoSimulator",
        "allocation_contract": (
            "Locked engine: max 6 positions; buy only AL; held IZLE kept without new "
            "cash; held UZAK sold; stronger AL rotates out the weakest holding; "
            "OPEN execution, CLOSE marking; integer shares, residue stays cash."
        ),
        "assumptions": {
            "contribution": f"{CONTRIBUTION_MULTIPLE} x CSGB net minimum wage per month",
            "transaction_cost_and_slippage": "ZERO_OUT_OF_SCOPE_RETURNS_ARE_GROSS",
            "tax": "ZERO_OUT_OF_SCOPE",
            "cash_interest": "ZERO",
            "corporate_action_source": "YAHOO_DISCOVERY_VENDOR_DERIVED_NOT_KAP_VERIFIED",
            "benchmark": "XU100 fractional units, same contributions, OPEN buy / CLOSE mark, no fees",
            "price_basis": "RAW_OPEN_AND_CLOSE_NOT_ADJUSTED_CLOSE",
        },
        "month_count": len(ledger),
        "signal_date_span": [signal_days[0], signal_days[-1]],
        "scored_signal_rows": len(signals),
        "al_signal_rows": int((signals.decision == "AL").sum()),
        "months_with_at_least_one_al": int(sum(
            1 for day in signal_days
            if ((signals.signal_date == day) & (signals.decision == "AL")).any()
        )),
        "trade_count": len(trade_rows),
        "distinct_tickers_traded": len(held_tickers),
        "tickers_traded": held_tickers,
        "cash_only_month_count": len(cash_only_months),
        "cash_only_months": cash_only_months,
        "cash_only_months_are_not_performance_success": True,
        "corporate_actions_in_window": int(len(actions)),
        "corporate_actions_touching_held_tickers": int(len(touched)),
        "corporate_action_rows_touching_held_tickers": json.loads(
            touched.to_json(orient="records")) if len(touched) else [],
        "ticker_changes_in_window": int(len(changes)),
        "economics": {
            "total_contributions": total_contribution,
            "ending_nav": final["nav"],
            "ending_cash": final["cash"],
            "absolute_profit_loss": final["nav"] - total_contribution,
            "return_on_contributions_pct": (final["nav"] / total_contribution - 1) * 100,
            "xu100_ending_nav": final["xu100_nav"],
            "xu100_return_on_contributions_pct": (final["xu100_nav"] / total_contribution - 1) * 100,
            "portfolio_minus_xu100_value": final["nav"] - final["xu100_nav"],
            "final_position_count": final["position_count"],
        },
        "cash_conservation_independently_reconciled": True,
        "benchmark_independently_reconciled": True,
        "benchmark_source": benchmark_source,
        "source_sha256": {
            "series": _sha(SERIES), "prices": _sha(PRICES), "signal_dates": _sha(SIGNALS),
            "corporate_actions": _sha(ACTIONS), "ticker_changes": _sha(CHANGES),
            "minimum_wage": _sha(WAGES),
        },
        "outputs": {path.name: _sha(path) for path in (ledger_path, trades_path)},
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return receipt


def check(*, output_dir: Path = OUTPUT) -> dict:
    recorded = json.loads((output_dir / "receipt.json").read_text(encoding="utf-8"))
    for name, digest in recorded["outputs"].items():
        if _sha(output_dir / name) != digest:
            raise ValueError(f"W8N_LEDGER_OUTPUT_HASH_MISMATCH:{name}")
    for name, digest in recorded["source_sha256"].items():
        path = {"series": SERIES, "prices": PRICES, "signal_dates": SIGNALS,
                "corporate_actions": ACTIONS, "ticker_changes": CHANGES,
                "minimum_wage": WAGES}[name]
        if _sha(path) != digest:
            raise ValueError(f"W8N_LEDGER_SOURCE_HASH_MISMATCH:{name}")
    return {"status": "W8N_LEDGER_CHECK_PASS",
            "ending_nav": recorded["economics"]["ending_nav"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--check", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.check:
        print(json.dumps(check(output_dir=args.output_dir)))
    else:
        print(json.dumps(build(output_dir=args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
