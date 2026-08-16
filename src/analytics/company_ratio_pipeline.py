from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from src.analytics.ratios_calc import (
    FIN_NUM_FIELDS,
    compute_ratios_for_ticker,
    load_ratio_specs,
)
from src.db.bulk_upsert_ratios import upsert_ratios_copy

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")


class CompanyRatioPipelineError(ValueError):
    pass


def _aware_datetime(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CompanyRatioPipelineError(f"{name} timezone iceren datetime olmali")
    return value


def _normalize_tickers(tickers: Optional[Iterable[str]]) -> Optional[list[str]]:
    if tickers is None:
        return None
    result: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        if not isinstance(raw, str) or not raw.strip():
            raise CompanyRatioPipelineError("ticker degerleri dolu metin olmali")
        ticker = raw.strip().upper()
        if ticker not in seen:
            result.append(ticker)
            seen.add(ticker)
    return result


def fetch_company_financials_asof(
    conn: Any,
    *,
    analysis_at: datetime,
    tickers: Optional[Iterable[str]] = None,
    since_period_end: Optional[date] = None,
) -> pd.DataFrame:
    analysis = _aware_datetime("analysis_at", analysis_at)
    ticker_list = _normalize_tickers(tickers)
    if since_period_end is not None and (
        not isinstance(since_period_end, date) or isinstance(since_period_end, datetime)
    ):
        raise CompanyRatioPipelineError("since_period_end date olmali")
    params = {
        "analysis_at": analysis,
        "tickers": ticker_list,
        "since_period_end": since_period_end,
    }
    df = pd.read_sql(
        """
        SELECT DISTINCT ON (
          ticker, period_end, derivation_profile, derivation_version
        )
          ticker, period_end, version_tag,
          published_at, sector_family, derivation_profile, derivation_version, is_complete,
          revenue, cogs, gross_profit, ebit, net_income, interest_exp,
          total_assets, total_equity, current_assets, current_liabilities,
          cash_and_eq, st_investments, receivables, inventory,
          debt_st, debt_lt, cfo, capex, shares_out, shares_diluted
        FROM core.company_metrics_quarterly
        WHERE published_at <= %(analysis_at)s
          AND (%(tickers)s::text[] IS NULL OR ticker = ANY(%(tickers)s::text[]))
          AND (%(since_period_end)s::date IS NULL OR period_end >= %(since_period_end)s)
        ORDER BY ticker, period_end, derivation_profile, derivation_version,
                 published_at DESC, version_sequence DESC,
                 source_disclosure_id DESC, lineage_sha256 DESC
        """,
        conn,
        params=params,
    )
    if df.empty:
        return df
    df = df.copy()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["period_end"] = pd.to_datetime(df["period_end"], errors="raise").dt.date
    published = pd.to_datetime(df["published_at"], errors="raise", utc=True)
    df["report_date"] = published.dt.tz_convert(ISTANBUL_TZ).dt.date
    # CORE ratio calculation does not consume prices. t0_date is retained only
    # as trace; VAL ratios are deliberately excluded from this pipeline.
    df["t0_date"] = df["report_date"]
    df["t0_source"] = "PUBLISHED_AT_TRACE_ONLY"
    df["unit_scale"] = 1
    for field in FIN_NUM_FIELDS:
        if field not in df.columns:
            df[field] = None
        df[field] = pd.to_numeric(df[field], errors="coerce")
        df[field] = df[field].where(pd.notna(df[field]), None)
    return df


def compute_company_core_ratios_from_frame(
    frame: pd.DataFrame,
    *,
    ratios_json_path: str,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=[
            "ticker", "period_end", "version_tag", "ratio_name", "ratio_value", "is_na"
        ])
    required = {"ticker", "period_end", "version_tag"}
    missing = required - set(frame.columns)
    if missing:
        raise CompanyRatioPipelineError(
            "company ratio frame eksik alanlar: " + ", ".join(sorted(missing))
        )
    specs = {
        name: spec
        for name, spec in load_ratio_specs(ratios_json_path).items()
        if spec.core_or_val.upper() == "CORE"
    }
    if not specs:
        raise CompanyRatioPipelineError("CORE ratio tanimi bulunamadi")
    parts = []
    for ticker, group in frame.groupby("ticker", sort=True):
        ordered = group.sort_values(["period_end", "version_tag"]).reset_index(drop=True)
        parts.append(compute_ratios_for_ticker(str(ticker), ordered, specs, {}))
    if not parts:
        return pd.DataFrame()
    result = pd.concat(parts, ignore_index=True)
    # This pipeline must never silently emit valuation ratios because t0 pricing
    # has a separate intraday point-in-time contract.
    unknown = set(result["ratio_name"].astype(str)) - set(specs)
    if unknown:
        raise CompanyRatioPipelineError(
            "company CORE pipeline beklenmeyen ratio uretti: " + ", ".join(sorted(unknown))
        )
    return result


def compute_company_core_ratios_asof(
    conn: Any,
    *,
    analysis_at: datetime,
    ratios_json_path: str,
    tickers: Optional[Iterable[str]] = None,
    since_period_end: Optional[date] = None,
) -> pd.DataFrame:
    frame = fetch_company_financials_asof(
        conn,
        analysis_at=analysis_at,
        tickers=tickers,
        since_period_end=since_period_end,
    )
    return compute_company_core_ratios_from_frame(frame, ratios_json_path=ratios_json_path)


def run_company_core_ratios_asof(
    conn: Any,
    *,
    analysis_at: datetime,
    ratios_json_path: str,
    tickers: Optional[Iterable[str]] = None,
    since_period_end: Optional[date] = None,
    persist: bool = True,
) -> pd.DataFrame:
    if type(persist) is not bool:
        raise CompanyRatioPipelineError("persist Python bool olmali")
    result = compute_company_core_ratios_asof(
        conn,
        analysis_at=analysis_at,
        ratios_json_path=ratios_json_path,
        tickers=tickers,
        since_period_end=since_period_end,
    )
    if persist and not result.empty:
        upsert_ratios_copy(conn, result)
    return result
