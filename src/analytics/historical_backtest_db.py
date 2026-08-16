from __future__ import annotations

"""V24-E — PostgreSQL-backed, fail-closed historical backtest bridge.

This layer connects the V24-D historical universe store and the existing
production price / Total Rasyo tables to the V24-C input contract.  It does not
invent minimum-wage schedules, information cutoffs, historical memberships,
prices, or Total Rasyo runs.

Only authoritative Total Rasyo runs are backtest-eligible:
* run_scope = FULL_UNIVERSE
* overall_status in COMPLETE / COMPLETE_NO_RESULTS
* persistence_status = OK
* company_count == universe_company_count
* persisted result row count == company_count
* exactly one eligible run_id per analysis_at
"""

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Tuple

import pandas as pd

from src.analytics.historical_backtest_inputs import (
    BacktestInputBundle,
    HistoricalBacktestInputError,
    build_backtest_input_bundle,
    build_execution_calendar,
    build_signal_cutoffs,
    expand_historical_universe,
)
from src.analytics.monthly_total_rasyo_portfolio import (
    MonthlyTotalRasyoSimulator,
    PortfolioConfig,
    benchmark_dca,
)


VALID_RUN_STATUSES = frozenset({"COMPLETE", "COMPLETE_NO_RESULTS"})


class HistoricalBacktestDatabaseError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalBacktestRun:
    inputs: BacktestInputBundle
    trades: pd.DataFrame
    monthly: pd.DataFrame
    benchmark: pd.DataFrame


def _required(frame: pd.DataFrame, columns: Iterable[str], name: str) -> None:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise HistoricalBacktestDatabaseError(f"{name} missing columns: {sorted(missing)}")


def _aware_timestamp(value: object, name: str) -> pd.Timestamp:
    try:
        ts = pd.Timestamp(value)
    except Exception as exc:
        raise HistoricalBacktestDatabaseError(f"{name} timestamp olmali") from exc
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise HistoricalBacktestDatabaseError(f"{name} timezone-aware olmali")
    return ts


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise HistoricalBacktestDatabaseError(f"{name} negatif olmayan int olmali")
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalBacktestDatabaseError(f"{name} negatif olmayan int olmali") from exc
    try:
        exact = float(value)
    except (TypeError, ValueError):
        exact = float(out)
    if out < 0 or exact != float(out):
        raise HistoricalBacktestDatabaseError(f"{name} negatif olmayan int olmali")
    return out


def validate_total_rasyo_run_registry(
    total_rasyo_results: pd.DataFrame,
    run_registry: pd.DataFrame,
) -> pd.DataFrame:
    """Return only authoritative full-universe result rows.

    Targeted / partial / failed runs may coexist in production and are ignored,
    but a run that *claims* to be authoritative and is internally inconsistent
    fails closed instead of being silently skipped.
    """
    _required(
        total_rasyo_results,
        {"run_id", "analysis_at", "ticker", "final_score", "decision", "total_rasyo_status"},
        "total_rasyo_results",
    )
    _required(
        run_registry,
        {
            "run_id", "analysis_at", "overall_status", "persistence_status",
            "run_scope", "company_count", "universe_company_count",
        },
        "run_registry",
    )

    r = total_rasyo_results.copy()
    g = run_registry.copy()

    if g["run_id"].isna().any() or (g["run_id"].astype(str).str.strip() == "").any():
        raise HistoricalBacktestDatabaseError("run_registry run_id dolu olmali")
    g["run_id"] = g["run_id"].astype(str).str.strip()
    if g["run_id"].duplicated().any():
        raise HistoricalBacktestDatabaseError("duplicate run_registry run_id")
    g["analysis_at"] = [_aware_timestamp(v, "run_registry.analysis_at") for v in g["analysis_at"]]
    g["overall_status"] = g["overall_status"].astype(str).str.strip().str.upper()
    g["persistence_status"] = g["persistence_status"].astype(str).str.strip().str.upper()
    g["run_scope"] = g["run_scope"].astype(str).str.strip().str.upper()

    # Legacy/targeted rows may legitimately predate the scope/count columns and
    # therefore carry NULLs.  They are not backtest authority and must not poison
    # a later valid FULL_UNIVERSE run.  Count strictness applies only to rows that
    # claim authority under the V24-E contract.
    claims_authority = (
        g["run_scope"].eq("FULL_UNIVERSE")
        & g["overall_status"].isin(VALID_RUN_STATUSES)
        & g["persistence_status"].eq("OK")
    )
    for idx in g.index[claims_authority]:
        g.at[idx, "company_count"] = _nonnegative_int(
            g.at[idx, "company_count"], "company_count"
        )
        g.at[idx, "universe_company_count"] = _nonnegative_int(
            g.at[idx, "universe_company_count"], "universe_company_count"
        )

    bad_counts = g[claims_authority & (g["company_count"] != g["universe_company_count"])]
    if not bad_counts.empty:
        row = bad_counts.iloc[0]
        raise HistoricalBacktestDatabaseError(
            f"authoritative run count mismatch: {row.run_id} "
            f"company_count={row.company_count} universe_company_count={row.universe_company_count}"
        )

    eligible = g[claims_authority].copy()
    if eligible.empty:
        raise HistoricalBacktestDatabaseError("authoritative FULL_UNIVERSE Total Rasyo run yok")
    if eligible["analysis_at"].duplicated().any():
        dup = eligible.loc[eligible["analysis_at"].duplicated(keep=False), "analysis_at"].iloc[0]
        raise HistoricalBacktestDatabaseError(f"ambiguous authoritative analysis_at: {dup}")

    r["analysis_at"] = [_aware_timestamp(v, "total_rasyo_results.analysis_at") for v in r["analysis_at"]]
    r["run_id"] = r["run_id"].where(r["run_id"].notna(), None)
    r["run_id"] = r["run_id"].map(lambda x: None if x is None else str(x).strip())
    r["ticker"] = r["ticker"].astype(str).str.strip().str.upper()

    eligible_ids = set(eligible["run_id"])
    out = r[r["run_id"].isin(eligible_ids)].copy()

    registry_by_id = eligible.set_index("run_id")
    for run_id, group in out.groupby("run_id", sort=False):
        reg = registry_by_id.loc[run_id]
        expected_analysis = reg["analysis_at"]
        if not group["analysis_at"].map(lambda x: x == expected_analysis).all():
            raise HistoricalBacktestDatabaseError(
                f"result analysis_at run registry ile uyusmuyor: {run_id}"
            )
        if group["ticker"].duplicated().any():
            raise HistoricalBacktestDatabaseError(f"duplicate result ticker in run: {run_id}")
        expected_count = int(reg["company_count"])
        if len(group) != expected_count:
            raise HistoricalBacktestDatabaseError(
                f"persisted result count mismatch: {run_id} rows={len(group)} expected={expected_count}"
            )

    missing_runs = sorted(eligible_ids - set(out["run_id"].dropna()))
    if missing_runs:
        run_id = missing_runs[0]
        expected = int(registry_by_id.loc[run_id, "company_count"])
        if expected != 0:
            raise HistoricalBacktestDatabaseError(
                f"authoritative run has no persisted result rows: {run_id} expected={expected}"
            )

    return out.sort_values(["analysis_at", "ticker"]).reset_index(drop=True)


def build_verified_backtest_input_bundle(
    *,
    index_prices: pd.DataFrame,
    prices_daily: pd.DataFrame,
    membership: pd.DataFrame,
    minimum_wage_schedule: pd.DataFrame,
    cutoffs: pd.DataFrame,
    total_rasyo_results: pd.DataFrame,
    run_registry: pd.DataFrame,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
    contribution_multiplier: float = 2.0,
) -> BacktestInputBundle:
    verified_results = validate_total_rasyo_run_registry(total_rasyo_results, run_registry)
    try:
        return build_backtest_input_bundle(
            index_prices=index_prices,
            prices_daily=prices_daily,
            membership=membership,
            minimum_wage_schedule=minimum_wage_schedule,
            cutoffs=cutoffs,
            total_rasyo_results=verified_results,
            start_month=start_month,
            end_month=end_month,
            index_code=index_code,
            expected_months=expected_months,
            contribution_multiplier=contribution_multiplier,
        )
    except HistoricalBacktestInputError as exc:
        raise HistoricalBacktestDatabaseError(str(exc)) from exc


def _read_sql(conn: Any, query: str, params: Tuple[object, ...]) -> pd.DataFrame:
    try:
        return pd.read_sql_query(query, conn, params=params)
    except Exception as exc:
        raise HistoricalBacktestDatabaseError(f"database read failed: {exc}") from exc


def _date_window(start_month: str, end_month: str) -> Tuple[pd.Timestamp, pd.Timestamp]:
    try:
        start = pd.Period(start_month, freq="M")
        end = pd.Period(end_month, freq="M")
    except Exception as exc:
        raise HistoricalBacktestDatabaseError("start_month/end_month YYYY-MM olmali") from exc
    if end < start:
        raise HistoricalBacktestDatabaseError("end_month start_month'tan once olamaz")
    start_date = start.start_time.normalize()
    end_exclusive = (end + 1).start_time.normalize()
    return start_date, end_exclusive


def fetch_database_backtest_frames(
    conn: Any,
    *,
    cutoffs: pd.DataFrame,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch only DB-owned historical inputs needed by V24-C.

    Returns: index_prices, prices_daily, membership, results, run_registry.
    Wage schedule and cutoffs remain caller-supplied audited inputs.
    """
    start_date, end_exclusive = _date_window(start_month, end_month)
    index_code_norm = str(index_code).strip().upper()
    if not index_code_norm:
        raise HistoricalBacktestDatabaseError("index_code dolu metin olmali")

    index_prices = _read_sql(
        conn,
        """
        SELECT index_code, trade_date, open, close
          FROM core.index_prices_daily
         WHERE index_code = %s AND trade_date >= %s AND trade_date < %s
         ORDER BY trade_date
        """,
        (index_code_norm, start_date.date(), end_exclusive.date()),
    )
    try:
        calendar = build_execution_calendar(
            index_prices,
            start_month=start_month,
            end_month=end_month,
            index_code=index_code_norm,
            expected_months=expected_months,
        )
        cutoff_frame = build_signal_cutoffs(calendar, cutoffs)
    except HistoricalBacktestInputError as exc:
        raise HistoricalBacktestDatabaseError(str(exc)) from exc

    membership = _read_sql(
        conn,
        """
        SELECT ticker, valid_from, valid_to, is_tradable,
               company_name, sector_index_code, sector_code,
               source, source_ref, source_sha256, row_sha256
          FROM core.universe_membership_history
         WHERE valid_from < %s AND (valid_to IS NULL OR valid_to > %s)
         ORDER BY ticker, valid_from
        """,
        (end_exclusive.date(), start_date.date()),
    )
    try:
        monthly_universe = expand_historical_universe(calendar, membership)
    except HistoricalBacktestInputError as exc:
        raise HistoricalBacktestDatabaseError(str(exc)) from exc

    dates = sorted({d.date() for d in monthly_universe["signal_date"]})
    tickers = sorted(set(monthly_universe["ticker"]))
    prices_daily = _read_sql(
        conn,
        """
        SELECT ticker, trade_date, open, close
          FROM core.prices_daily
         WHERE trade_date = ANY(%s) AND ticker = ANY(%s)
         ORDER BY trade_date, ticker
        """,
        (dates, tickers),
    )

    max_cutoff = max(cutoff_frame["cutoff_at"])
    run_registry = _read_sql(
        conn,
        """
        SELECT run_id, analysis_at, overall_status, persistence_status,
               run_scope, company_count, universe_company_count
          FROM analytics.total_rasyo_run
         WHERE analysis_at <= %s
         ORDER BY analysis_at, run_id
        """,
        (max_cutoff.to_pydatetime(),),
    )
    candidate_ids = [
        str(x).strip() for x in run_registry.get("run_id", pd.Series(dtype=object)).dropna()
        if str(x).strip()
    ]
    if not candidate_ids:
        raise HistoricalBacktestDatabaseError("cutoff oncesi Total Rasyo run registry yok")

    total_rasyo_results = _read_sql(
        conn,
        """
        SELECT run_id, analysis_at, ticker, final_score, decision, total_rasyo_status
          FROM analytics.company_total_rasyo_result
         WHERE run_id = ANY(%s)
         ORDER BY analysis_at, ticker
        """,
        (candidate_ids,),
    )
    return index_prices, prices_daily, membership, total_rasyo_results, run_registry


def build_backtest_input_bundle_from_database(
    conn: Any,
    *,
    minimum_wage_schedule: pd.DataFrame,
    cutoffs: pd.DataFrame,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
    contribution_multiplier: float = 2.0,
) -> BacktestInputBundle:
    index_prices, prices_daily, membership, results, registry = fetch_database_backtest_frames(
        conn,
        cutoffs=cutoffs,
        start_month=start_month,
        end_month=end_month,
        index_code=index_code,
        expected_months=expected_months,
    )
    return build_verified_backtest_input_bundle(
        index_prices=index_prices,
        prices_daily=prices_daily,
        membership=membership,
        minimum_wage_schedule=minimum_wage_schedule,
        cutoffs=cutoffs,
        total_rasyo_results=results,
        run_registry=registry,
        start_month=start_month,
        end_month=end_month,
        index_code=index_code,
        expected_months=expected_months,
        contribution_multiplier=contribution_multiplier,
    )


def run_monthly_backtest_from_database(
    conn: Any,
    *,
    minimum_wage_schedule: pd.DataFrame,
    cutoffs: pd.DataFrame,
    start_month: str = "2021-08",
    end_month: str = "2026-07",
    index_code: str = "XU100",
    expected_months: Optional[int] = 60,
    contribution_multiplier: float = 2.0,
    config: PortfolioConfig = PortfolioConfig(),
) -> HistoricalBacktestRun:
    bundle = build_backtest_input_bundle_from_database(
        conn,
        minimum_wage_schedule=minimum_wage_schedule,
        cutoffs=cutoffs,
        start_month=start_month,
        end_month=end_month,
        index_code=index_code,
        expected_months=expected_months,
        contribution_multiplier=contribution_multiplier,
    )
    simulator = MonthlyTotalRasyoSimulator(config)
    trades, monthly = simulator.run(bundle.signals, bundle.prices, bundle.contributions)
    benchmark = benchmark_dca(bundle.contributions, bundle.benchmark_prices)
    return HistoricalBacktestRun(bundle, trades, monthly, benchmark)
