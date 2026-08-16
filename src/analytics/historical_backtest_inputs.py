from __future__ import annotations

"""V24-C — fail-closed historical backtest input contract.

This layer prepares the deterministic monthly input frames consumed by V24-B.
It deliberately does *not* invent historical universe membership, market-open
cutoffs, Total Rasyo runs, minimum-wage values, or prices.  Every one of those
inputs must be supplied from an auditable source; gaps are explicit errors.

Interval convention for historical universe and wage schedules is half-open:
``valid_from <= date < valid_to``.  ``valid_to`` may be null for an open-ended
interval.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd


DEFAULT_START_MONTH = "2021-08"
DEFAULT_END_MONTH = "2026-07"
DEFAULT_BENCHMARK = "XU100"
EXPECTED_MONTHS = 60
VALID_DECISIONS = frozenset({"AL", "IZLE", "UZAK"})


class HistoricalBacktestInputError(ValueError):
    pass


@dataclass(frozen=True)
class BacktestInputBundle:
    calendar: pd.DataFrame
    contributions: pd.DataFrame
    monthly_universe: pd.DataFrame
    signals: pd.DataFrame
    signal_audit: pd.DataFrame
    prices: pd.DataFrame
    benchmark_prices: pd.DataFrame


def _required(frame: pd.DataFrame, columns: Iterable[str], name: str) -> None:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise HistoricalBacktestInputError(f"{name} missing columns: {sorted(missing)}")


def _ticker(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalBacktestInputError("ticker dolu metin olmali")
    return value.strip().upper()


def _positive(name: str, value: object) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalBacktestInputError(f"{name} sayisal olmali") from exc
    if not isfinite(out) or out <= 0:
        raise HistoricalBacktestInputError(f"{name} pozitif ve sonlu olmali")
    return out


def _month_range(start_month: str, end_month: str) -> pd.PeriodIndex:
    try:
        start = pd.Period(start_month, freq="M")
        end = pd.Period(end_month, freq="M")
    except Exception as exc:
        raise HistoricalBacktestInputError("start_month/end_month YYYY-MM olmali") from exc
    if end < start:
        raise HistoricalBacktestInputError("end_month start_month'tan once olamaz")
    return pd.period_range(start, end, freq="M")


def build_execution_calendar(
    index_prices: pd.DataFrame,
    *,
    start_month: str = DEFAULT_START_MONTH,
    end_month: str = DEFAULT_END_MONTH,
    index_code: str = DEFAULT_BENCHMARK,
    expected_months: Optional[int] = EXPECTED_MONTHS,
) -> pd.DataFrame:
    """Choose the first *observed* benchmark trading day in every target month."""
    _required(index_prices, {"index_code", "trade_date", "open", "close"}, "index_prices")
    if not isinstance(index_code, str) or not index_code.strip():
        raise HistoricalBacktestInputError("index_code dolu metin olmali")
    if expected_months is not None and (
        isinstance(expected_months, bool) or not isinstance(expected_months, int) or expected_months < 1
    ):
        raise HistoricalBacktestInputError("expected_months pozitif int veya None olmali")

    months = _month_range(start_month, end_month)
    if expected_months is not None and len(months) != expected_months:
        raise HistoricalBacktestInputError(
            f"hedef ay sayisi {len(months)}; beklenen {expected_months}"
        )

    p = index_prices.copy()
    p["index_code"] = p["index_code"].astype(str).str.strip().str.upper()
    p = p[p["index_code"] == index_code.strip().upper()].copy()
    if p.empty:
        raise HistoricalBacktestInputError(f"{index_code} benchmark fiyatlari yok")
    p["trade_date"] = pd.to_datetime(p["trade_date"], errors="raise").dt.normalize()
    if p.duplicated(["index_code", "trade_date"]).any():
        raise HistoricalBacktestInputError("duplicate benchmark rows")
    p["open"] = [_positive("benchmark open", x) for x in p["open"]]
    p["close"] = [_positive("benchmark close", x) for x in p["close"]]
    p["month"] = p["trade_date"].dt.to_period("M")
    p = p[p["month"].isin(months)].copy()

    rows = []
    missing = []
    for month in months:
        month_rows = p[p["month"] == month].sort_values("trade_date")
        if month_rows.empty:
            missing.append(str(month))
            continue
        row = month_rows.iloc[0]
        rows.append({
            "month": str(month),
            "signal_date": row["trade_date"],
            "index_code": row["index_code"],
            "benchmark_open": float(row["open"]),
            "benchmark_close": float(row["close"]),
        })
    if missing:
        raise HistoricalBacktestInputError(f"benchmark trading calendar missing months: {missing}")
    return pd.DataFrame(rows)


def build_contributions(
    calendar: pd.DataFrame,
    minimum_wage_schedule: pd.DataFrame,
    *,
    multiplier: float = 2.0,
) -> pd.DataFrame:
    """Map each execution date to exactly one auditable net minimum-wage interval."""
    _required(calendar, {"signal_date"}, "calendar")
    _required(minimum_wage_schedule, {"valid_from", "valid_to", "net_min_wage"}, "minimum_wage_schedule")
    multiplier = _positive("multiplier", multiplier)

    c = calendar[["signal_date"]].copy()
    c["signal_date"] = pd.to_datetime(c["signal_date"], errors="raise").dt.normalize()
    if c["signal_date"].duplicated().any():
        raise HistoricalBacktestInputError("calendar duplicate signal_date")

    w = minimum_wage_schedule.copy()
    w["valid_from"] = pd.to_datetime(w["valid_from"], errors="raise").dt.normalize()
    w["valid_to"] = pd.to_datetime(w["valid_to"], errors="coerce").dt.normalize()
    w["net_min_wage"] = [_positive("net_min_wage", x) for x in w["net_min_wage"]]
    if (w["valid_to"].notna() & (w["valid_to"] <= w["valid_from"])).any():
        raise HistoricalBacktestInputError("wage valid_to must be after valid_from")

    ordered = w.sort_values(["valid_from", "valid_to"], na_position="last").reset_index(drop=True)
    for i in range(len(ordered) - 1):
        left = ordered.iloc[i]
        right = ordered.iloc[i + 1]
        left_to = left["valid_to"]
        if pd.isna(left_to) or right["valid_from"] < left_to:
            raise HistoricalBacktestInputError("overlapping minimum-wage intervals")
        if right["valid_from"] > left_to:
            raise HistoricalBacktestInputError("minimum-wage coverage has a gap")

    rows: List[dict] = []
    for day in c["signal_date"]:
        mask = (w["valid_from"] <= day) & (w["valid_to"].isna() | (day < w["valid_to"]))
        matches = w[mask]
        if len(matches) != 1:
            raise HistoricalBacktestInputError(
                f"minimum-wage coverage for {day.date()} is {len(matches)}, expected 1"
            )
        wage = float(matches.iloc[0]["net_min_wage"])
        rows.append({"signal_date": day, "contribution": wage * multiplier, "net_min_wage": wage})
    return pd.DataFrame(rows)


def expand_historical_universe(
    calendar: pd.DataFrame,
    membership: pd.DataFrame,
) -> pd.DataFrame:
    """Expand half-open historical membership intervals to monthly execution dates."""
    _required(calendar, {"signal_date"}, "calendar")
    _required(membership, {"ticker", "valid_from", "valid_to", "is_tradable"}, "membership")
    c = calendar[["signal_date"]].copy()
    c["signal_date"] = pd.to_datetime(c["signal_date"], errors="raise").dt.normalize()

    m = membership.copy()
    m["ticker"] = m["ticker"].map(_ticker)
    m["valid_from"] = pd.to_datetime(m["valid_from"], errors="raise").dt.normalize()
    m["valid_to"] = pd.to_datetime(m["valid_to"], errors="coerce").dt.normalize()
    if m["is_tradable"].isna().any():
        raise HistoricalBacktestInputError("membership is_tradable cannot be null")
    m["is_tradable"] = m["is_tradable"].astype(bool)
    if (m["valid_to"].notna() & (m["valid_to"] <= m["valid_from"])).any():
        raise HistoricalBacktestInputError("membership valid_to must be after valid_from")

    for ticker, group in m.sort_values(["ticker", "valid_from"]).groupby("ticker"):
        prev_to = None
        for row in group.itertuples(index=False):
            if prev_to is None:
                pass
            elif pd.isna(prev_to) or row.valid_from < prev_to:
                raise HistoricalBacktestInputError(f"overlapping membership intervals for {ticker}")
            prev_to = row.valid_to

    rows: List[dict] = []
    for day in c["signal_date"]:
        active = m[(m["valid_from"] <= day) & (m["valid_to"].isna() | (day < m["valid_to"])) & m["is_tradable"]]
        if active.empty:
            raise HistoricalBacktestInputError(f"historical universe empty on {day.date()}")
        for ticker in sorted(active["ticker"].unique()):
            rows.append({"signal_date": day, "ticker": ticker})
    return pd.DataFrame(rows, columns=["signal_date", "ticker"])


def build_signal_cutoffs(calendar: pd.DataFrame, cutoffs: pd.DataFrame) -> pd.DataFrame:
    """Validate explicit, timezone-aware information cutoffs for every execution date."""
    _required(calendar, {"signal_date"}, "calendar")
    _required(cutoffs, {"signal_date", "cutoff_at"}, "cutoffs")
    cal = calendar[["signal_date"]].copy()
    cal["signal_date"] = pd.to_datetime(cal["signal_date"], errors="raise").dt.normalize()
    x = cutoffs.copy()
    x["signal_date"] = pd.to_datetime(x["signal_date"], errors="raise").dt.normalize()
    if x["signal_date"].duplicated().any():
        raise HistoricalBacktestInputError("duplicate signal cutoffs")

    parsed: List[pd.Timestamp] = []
    for value in x["cutoff_at"]:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise HistoricalBacktestInputError("cutoff_at timezone-aware olmali")
        parsed.append(ts)
    x["cutoff_at"] = parsed

    merged = cal.merge(x, on="signal_date", how="left", validate="one_to_one")
    if merged["cutoff_at"].isna().any():
        missing = [str(d.date()) for d in merged.loc[merged["cutoff_at"].isna(), "signal_date"]]
        raise HistoricalBacktestInputError(f"missing signal cutoffs: {missing}")
    return merged


def select_pit_total_rasyo_signals(
    calendar: pd.DataFrame,
    monthly_universe: pd.DataFrame,
    cutoffs: pd.DataFrame,
    total_rasyo_results: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Select one latest whole-run Total Rasyo cut per month, never after cutoff.

    Coverage is strict: the chosen run must contain a result row for every ticker
    in that month's historical universe.  Non-OK rows remain visible in the audit
    frame but are omitted from actionable signals, producing a no-action state in
    V24-B rather than an invented decision.
    """
    _required(monthly_universe, {"signal_date", "ticker"}, "monthly_universe")
    _required(total_rasyo_results, {"analysis_at", "ticker", "final_score", "decision", "total_rasyo_status"}, "total_rasyo_results")
    cutoff_frame = build_signal_cutoffs(calendar, cutoffs)

    u = monthly_universe.copy()
    u["signal_date"] = pd.to_datetime(u["signal_date"], errors="raise").dt.normalize()
    u["ticker"] = u["ticker"].map(_ticker)
    if u.duplicated(["signal_date", "ticker"]).any():
        raise HistoricalBacktestInputError("duplicate monthly universe rows")

    r = total_rasyo_results.copy()
    r["ticker"] = r["ticker"].map(_ticker)
    parsed_analysis: List[pd.Timestamp] = []
    for value in r["analysis_at"]:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise HistoricalBacktestInputError("analysis_at timezone-aware olmali")
        parsed_analysis.append(ts)
    r["analysis_at"] = parsed_analysis
    if r.duplicated(["analysis_at", "ticker"]).any():
        raise HistoricalBacktestInputError("duplicate total rasyo result rows")

    signals: List[dict] = []
    audits: List[dict] = []
    available_runs = sorted(r["analysis_at"].unique())
    for cutoff_row in cutoff_frame.itertuples(index=False):
        day = cutoff_row.signal_date
        cutoff = cutoff_row.cutoff_at
        eligible_runs = [ts for ts in available_runs if ts <= cutoff]
        if not eligible_runs:
            raise HistoricalBacktestInputError(f"no Total Rasyo run before cutoff for {day.date()}")
        run_at = max(eligible_runs)
        run = r[r["analysis_at"] == run_at]
        tickers = set(u.loc[u["signal_date"] == day, "ticker"])
        run_by_ticker = {row.ticker: row for row in run.itertuples(index=False)}
        missing = sorted(tickers - set(run_by_ticker))
        if missing:
            raise HistoricalBacktestInputError(
                f"Total Rasyo run {run_at} missing {len(missing)} universe tickers on {day.date()}: {missing[:8]}"
            )

        for ticker in sorted(tickers):
            row = run_by_ticker[ticker]
            status = str(row.total_rasyo_status).strip().upper()
            decision = None if pd.isna(row.decision) else str(row.decision).strip().upper()
            score = None if pd.isna(row.final_score) else float(row.final_score)
            audits.append({
                "signal_date": day,
                "cutoff_at": cutoff,
                "analysis_at": run_at,
                "ticker": ticker,
                "total_rasyo_status": status,
                "decision": decision,
                "final_score": score,
                "actionable": bool(status == "OK" and decision in VALID_DECISIONS and score is not None),
            })
            if status != "OK":
                continue
            if decision not in VALID_DECISIONS or score is None or not isfinite(score):
                raise HistoricalBacktestInputError(f"OK Total Rasyo row malformed for {ticker} at {run_at}")
            signals.append({
                "signal_date": day,
                "ticker": ticker,
                "final_score": score,
                "decision": decision,
                "analysis_at": run_at,
            })

    return (
        pd.DataFrame(signals, columns=["signal_date", "ticker", "final_score", "decision", "analysis_at"]),
        pd.DataFrame(audits, columns=["signal_date", "cutoff_at", "analysis_at", "ticker", "total_rasyo_status", "decision", "final_score", "actionable"]),
    )


def select_execution_prices(
    monthly_universe: pd.DataFrame,
    prices_daily: pd.DataFrame,
) -> pd.DataFrame:
    """Require exact execution-day open+close for every tradable universe member."""
    _required(monthly_universe, {"signal_date", "ticker"}, "monthly_universe")
    _required(prices_daily, {"ticker", "trade_date", "open", "close"}, "prices_daily")
    u = monthly_universe.copy()
    u["signal_date"] = pd.to_datetime(u["signal_date"], errors="raise").dt.normalize()
    u["ticker"] = u["ticker"].map(_ticker)
    p = prices_daily.copy()
    p["ticker"] = p["ticker"].map(_ticker)
    p["trade_date"] = pd.to_datetime(p["trade_date"], errors="raise").dt.normalize()
    if p.duplicated(["trade_date", "ticker"]).any():
        raise HistoricalBacktestInputError("duplicate prices_daily rows")
    p["open"] = [_positive("open", x) for x in p["open"]]
    p["close"] = [_positive("close", x) for x in p["close"]]

    joined = u.merge(
        p[["trade_date", "ticker", "open", "close"]],
        left_on=["signal_date", "ticker"],
        right_on=["trade_date", "ticker"],
        how="left",
        validate="one_to_one",
    )
    missing = joined[joined["open"].isna() | joined["close"].isna()]
    if not missing.empty:
        sample = [f"{r.ticker}@{r.signal_date.date()}" for r in missing.head(8).itertuples()]
        raise HistoricalBacktestInputError(
            f"missing exact execution prices for {len(missing)} universe rows: {sample}"
        )
    return joined[["signal_date", "ticker", "open", "close"]].rename(columns={"signal_date": "trade_date"})


def benchmark_prices_for_portfolio(calendar: pd.DataFrame) -> pd.DataFrame:
    _required(calendar, {"signal_date", "benchmark_open", "benchmark_close"}, "calendar")
    out = calendar[["signal_date", "benchmark_open", "benchmark_close"]].copy()
    return out.rename(columns={
        "signal_date": "trade_date",
        "benchmark_open": "open",
        "benchmark_close": "close",
    })


def build_backtest_input_bundle(
    *,
    index_prices: pd.DataFrame,
    prices_daily: pd.DataFrame,
    membership: pd.DataFrame,
    minimum_wage_schedule: pd.DataFrame,
    cutoffs: pd.DataFrame,
    total_rasyo_results: pd.DataFrame,
    start_month: str = DEFAULT_START_MONTH,
    end_month: str = DEFAULT_END_MONTH,
    index_code: str = DEFAULT_BENCHMARK,
    expected_months: Optional[int] = EXPECTED_MONTHS,
    contribution_multiplier: float = 2.0,
) -> BacktestInputBundle:
    calendar = build_execution_calendar(
        index_prices,
        start_month=start_month,
        end_month=end_month,
        index_code=index_code,
        expected_months=expected_months,
    )
    contributions = build_contributions(
        calendar, minimum_wage_schedule, multiplier=contribution_multiplier
    )[["signal_date", "contribution"]]
    universe = expand_historical_universe(calendar, membership)
    signals, audit = select_pit_total_rasyo_signals(
        calendar, universe, cutoffs, total_rasyo_results
    )
    prices = select_execution_prices(universe, prices_daily)
    benchmark = benchmark_prices_for_portfolio(calendar)
    return BacktestInputBundle(
        calendar=calendar,
        contributions=contributions,
        monthly_universe=universe,
        signals=signals,
        signal_audit=audit,
        prices=prices,
        benchmark_prices=benchmark,
    )
