from __future__ import annotations

"""V24-G — report-only readiness audit for the locked historical backtest.

The audit never repairs data and never invents fallbacks.  It scans every
requested month and reports all missing/invalid inputs before a real backtest is
allowed to start.
"""

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from src.analytics.historical_backtest_db import (
    HistoricalBacktestDatabaseError,
    validate_total_rasyo_run_registry,
)


CATEGORIES = ("BENCHMARK", "WAGE", "CUTOFF", "UNIVERSE", "PRICE", "TOTAL_RASYO")


@dataclass(frozen=True)
class BacktestReadinessReport:
    start_month: str
    end_month: str
    expected_months: int
    checked_months: int
    findings: pd.DataFrame

    @property
    def ready(self) -> bool:
        return self.checked_months == self.expected_months and self.findings.empty

    def category_counts(self) -> dict[str, int]:
        if self.findings.empty:
            return {name: 0 for name in CATEGORIES}
        counts = self.findings["category"].value_counts().to_dict()
        return {name: int(counts.get(name, 0)) for name in CATEGORIES}


def _periods(start_month: str, end_month: str, expected_months: Optional[int]) -> list[pd.Period]:
    try:
        start = pd.Period(start_month, freq="M")
        end = pd.Period(end_month, freq="M")
    except Exception as exc:
        raise HistoricalBacktestDatabaseError("start_month/end_month YYYY-MM olmali") from exc
    if end < start:
        raise HistoricalBacktestDatabaseError("end_month start_month'tan once olamaz")
    out = list(pd.period_range(start, end, freq="M"))
    if expected_months is not None and len(out) != int(expected_months):
        raise HistoricalBacktestDatabaseError(
            f"month window count mismatch: got={len(out)} expected={int(expected_months)}"
        )
    return out


def _finite_positive(value: object) -> bool:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite(x) and x > 0.0)


def _aware(value: object) -> Optional[pd.Timestamp]:
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if ts.tzinfo is None or ts.utcoffset() is None:
        return None
    return ts


def _norm_ticker(value: object) -> str:
    return str(value).strip().upper()


def audit_backtest_readiness_frames(
    *,
    index_prices: pd.DataFrame,
    membership: pd.DataFrame,
    prices_daily: pd.DataFrame,
    wages: pd.DataFrame,
    cutoffs: pd.DataFrame,
    total_rasyo_results: pd.DataFrame,
    run_registry: pd.DataFrame,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
) -> BacktestReadinessReport:
    """Audit all monthly inputs without mutating any source frame.

    Findings columns: month, signal_date, category, code, detail.
    `ready` is true only when all requested months were checked and no finding
    exists.  Invalid Total Rasyo authority is reported rather than repaired.
    """
    periods = _periods(start_month, end_month, expected_months)
    findings: list[tuple[str, object, str, str, str]] = []

    idx = index_prices.copy()
    if not idx.empty:
        for col in ("trade_date",):
            if col in idx:
                idx[col] = pd.to_datetime(idx[col], errors="coerce").dt.normalize()
        if "index_code" in idx:
            idx = idx[idx["index_code"].astype(str).str.strip().str.upper() == str(index_code).strip().upper()]

    mem = membership.copy()
    if not mem.empty:
        mem["valid_from"] = pd.to_datetime(mem["valid_from"], errors="coerce").dt.normalize()
        mem["valid_to"] = pd.to_datetime(mem["valid_to"], errors="coerce").dt.normalize()
        mem["ticker"] = mem["ticker"].map(_norm_ticker)

    px = prices_daily.copy()
    if not px.empty:
        px["trade_date"] = pd.to_datetime(px["trade_date"], errors="coerce").dt.normalize()
        px["ticker"] = px["ticker"].map(_norm_ticker)

    wage = wages.copy()
    if not wage.empty:
        wage["valid_from"] = pd.to_datetime(wage["valid_from"], errors="coerce").dt.normalize()
        wage["valid_to"] = pd.to_datetime(wage["valid_to"], errors="coerce").dt.normalize()

    cut = cutoffs.copy()
    if not cut.empty:
        cut["signal_date"] = pd.to_datetime(cut["signal_date"], errors="coerce").dt.normalize()

    verified: Optional[pd.DataFrame]
    try:
        verified = validate_total_rasyo_run_registry(total_rasyo_results, run_registry)
    except Exception as exc:
        verified = None
        findings.append(("*", None, "TOTAL_RASYO", "AUTHORITY_INVALID", str(exc)))

    checked = 0
    for period in periods:
        month = str(period)
        month_start = period.start_time.normalize()
        month_end = (period + 1).start_time.normalize()
        month_idx = idx[(idx["trade_date"] >= month_start) & (idx["trade_date"] < month_end)] if "trade_date" in idx else idx.iloc[0:0]
        if month_idx.empty:
            findings.append((month, None, "BENCHMARK", "NO_TRADING_DAY", "index month has no trading day"))
            continue

        signal_date = month_idx["trade_date"].min()
        first_rows = month_idx[month_idx["trade_date"] == signal_date]
        if len(first_rows) != 1:
            findings.append((month, signal_date, "BENCHMARK", "AMBIGUOUS_EXECUTION_DAY", f"rows={len(first_rows)}"))
            continue
        first = first_rows.iloc[0]
        if not _finite_positive(first.get("open")) or not _finite_positive(first.get("close")):
            findings.append((month, signal_date, "BENCHMARK", "INVALID_PRICE", "benchmark open/close must be finite positive"))
        checked += 1

        active_wage = wage[
            (wage["valid_from"] <= signal_date)
            & (wage["valid_to"].isna() | (signal_date < wage["valid_to"]))
        ] if {"valid_from", "valid_to"}.issubset(wage.columns) else wage.iloc[0:0]
        if len(active_wage) != 1:
            findings.append((month, signal_date, "WAGE", "COVERAGE", f"matching_rows={len(active_wage)}"))
        elif not _finite_positive(active_wage.iloc[0].get("net_min_wage")):
            findings.append((month, signal_date, "WAGE", "INVALID_VALUE", "net_min_wage must be finite positive"))

        month_cut = cut[cut["signal_date"] == signal_date] if "signal_date" in cut else cut.iloc[0:0]
        cutoff_at: Optional[pd.Timestamp] = None
        if len(month_cut) != 1:
            findings.append((month, signal_date, "CUTOFF", "COVERAGE", f"matching_rows={len(month_cut)}"))
        else:
            cutoff_at = _aware(month_cut.iloc[0].get("cutoff_at"))
            execution_at = _aware(month_cut.iloc[0].get("execution_at")) if "execution_at" in month_cut else None
            if cutoff_at is None:
                findings.append((month, signal_date, "CUTOFF", "NAIVE_OR_INVALID", "cutoff_at must be timezone-aware"))
            if execution_at is not None:
                if cutoff_at is not None and cutoff_at >= execution_at:
                    findings.append((month, signal_date, "CUTOFF", "ORDER", "cutoff_at must precede execution_at"))
                if execution_at.tz_convert("Europe/Istanbul").date() != signal_date.date():
                    findings.append((month, signal_date, "CUTOFF", "EXECUTION_DAY", "Istanbul execution day mismatch"))

        active = mem[
            (mem["valid_from"] <= signal_date)
            & (mem["valid_to"].isna() | (signal_date < mem["valid_to"]))
        ] if {"valid_from", "valid_to"}.issubset(mem.columns) else mem.iloc[0:0]
        if "is_tradable" in active:
            active = active[active["is_tradable"].fillna(False).astype(bool)]
        if active.empty:
            findings.append((month, signal_date, "UNIVERSE", "EMPTY", "no tradable historical universe"))
            universe: set[str] = set()
        else:
            dup = sorted(active.loc[active["ticker"].duplicated(keep=False), "ticker"].unique())
            if dup:
                findings.append((month, signal_date, "UNIVERSE", "DUPLICATE", f"duplicate_tickers={dup[:10]}"))
            universe = set(active["ticker"])

        if universe:
            day_px = px[(px["trade_date"] == signal_date) & px["ticker"].isin(universe)] if {"trade_date", "ticker"}.issubset(px.columns) else px.iloc[0:0]
            counts = day_px.groupby("ticker").size() if not day_px.empty else pd.Series(dtype=int)
            missing = sorted(universe - set(counts.index))
            duplicates = sorted(counts[counts != 1].index)
            invalid = sorted(
                row.ticker for row in day_px.itertuples(index=False)
                if not _finite_positive(getattr(row, "open", None)) or not _finite_positive(getattr(row, "close", None))
            )
            if missing or duplicates or invalid:
                findings.append((month, signal_date, "PRICE", "EXACT_DAY_COVERAGE", f"missing={missing[:10]} duplicate={duplicates[:10]} invalid={invalid[:10]}"))

        if verified is not None and cutoff_at is not None and universe:
            vr = verified[verified["analysis_at"].map(lambda x: pd.Timestamp(x) <= cutoff_at)]
            if vr.empty:
                findings.append((month, signal_date, "TOTAL_RASYO", "NO_RUN", "no authoritative run at/before cutoff"))
            else:
                latest_at = max(pd.Timestamp(x) for x in vr["analysis_at"])
                snap = vr[vr["analysis_at"].map(lambda x: pd.Timestamp(x) == latest_at)]
                run_tickers = set(snap["ticker"].map(_norm_ticker))
                missing = sorted(universe - run_tickers)
                extra = sorted(run_tickers - universe)
                if missing or extra:
                    findings.append((month, signal_date, "TOTAL_RASYO", "UNIVERSE_COVERAGE", f"missing={missing[:10]} extra={extra[:10]}"))

    frame = pd.DataFrame(findings, columns=["month", "signal_date", "category", "code", "detail"])
    if not frame.empty:
        frame = frame.sort_values(["month", "category", "code"], kind="stable").reset_index(drop=True)
    return BacktestReadinessReport(
        start_month=start_month,
        end_month=end_month,
        expected_months=len(periods),
        checked_months=checked,
        findings=frame,
    )
