from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping, Optional

import pandas as pd
try:
    from psycopg2.extras import execute_values
except ImportError:  # tests and dry pure usage
    def execute_values(*args, **kwargs):
        raise RuntimeError("psycopg2 PostgreSQL persistence icin zorunlu")

from src.analytics.company_ratio_pipeline import fetch_company_financials_asof
from src.analytics.nonfin_valuation import (
    NonfinSnapshot,
    NonfinValuationConfig,
    NonfinValuationError,
    build_nonfin_snapshot,
    evaluate_nonfin_batch,
)
from src.ingest.sector_routing import SectorRoutingConfig
from src.analytics.price_level_adapter import (
    normalize_price_level_input, attach_action_bundles, valuation_basis_receipt, attach_basis_receipts,
    SOURCE_SHARE_BASIS, SHARE_BASIS,
)


class NonfinBatchError(ValueError):
    pass


def _aware_datetime(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise NonfinBatchError(f"{name} timezone iceren datetime olmali")
    return value



def daily_price_cutoff_date(analysis_at: datetime) -> date:
    from zoneinfo import ZoneInfo
    analysis = _aware_datetime("analysis_at", analysis_at).astimezone(ZoneInfo("Europe/Istanbul"))
    if analysis.timetz().replace(tzinfo=None) >= time(18, 30):
        return analysis.date()
    return analysis.date() - timedelta(days=1)

def _strict_date(name: str, value: Any) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise NonfinBatchError(f"{name} date olmali")
    return value


def _normalize_tickers(values: Optional[Iterable[str]]) -> Optional[list[str]]:
    if values is None:
        return None
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise NonfinBatchError("ticker degerleri dolu metin olmali")
        ticker = value.strip().upper()
        if ticker not in seen:
            result.append(ticker)
            seen.add(ticker)
    return result


def fetch_nonfin_universe(
    conn: Any,
    *,
    tickers: Optional[Iterable[str]] = None,
    routing_config: SectorRoutingConfig | None = None,
) -> pd.DataFrame:
    ticker_list = _normalize_tickers(tickers)
    config = routing_config or SectorRoutingConfig.default()
    df = pd.read_sql(
        """
        SELECT ticker, sector_index_code, sector_code
        FROM core.universe_stocks
        WHERE is_active = true
          AND (%(tickers)s::text[] IS NULL OR upper(ticker) = ANY(%(tickers)s::text[]))
        ORDER BY ticker
        """,
        conn,
        params={"tickers": ticker_list},
    )
    if df.empty:
        return pd.DataFrame(columns=["ticker", "peer_group", "sector_family"])
    rows = []
    seen: set[str] = set()
    for row in df.itertuples(index=False):
        ticker = str(row.ticker).strip().upper()
        if ticker in seen:
            raise NonfinBatchError(f"evren sorgusu yinelenen ticker dondurdu: {ticker}")
        seen.add(ticker)
        family = config.route(
            ticker=ticker,
            sector_index_code=row.sector_index_code,
            sector_code=row.sector_code,
        )
        if family != "NONFIN":
            continue
        explicit_code = row.sector_code
        if isinstance(explicit_code, str) and explicit_code.strip().upper() not in {
            "BANK", "HOLDING", "GYO", "INSURANCE", "FINANCIAL", "NONFIN"
        }:
            peer_raw = explicit_code
        else:
            peer_raw = row.sector_index_code or row.sector_code or "NONFIN"
        if not isinstance(peer_raw, str) or not peer_raw.strip():
            raise NonfinBatchError(f"{ticker} peer group belirlenemedi")
        rows.append((ticker, peer_raw.strip().upper(), family))
    return pd.DataFrame(rows, columns=["ticker", "peer_group", "sector_family"])


def fetch_nonfin_prices(
    conn: Any,
    *,
    tickers: Iterable[str],
    analysis_at: datetime,
) -> pd.DataFrame:
    ticker_list = _normalize_tickers(tickers) or []
    analysis = _aware_datetime("analysis_at", analysis_at)
    if not ticker_list:
        return pd.DataFrame(columns=["ticker", "price_trade_date", "current_price"])
    cutoff = daily_price_cutoff_date(analysis)
    df = pd.read_sql(
        """
        SELECT DISTINCT ON (ticker)
               ticker, trade_date AS price_trade_date,
               close AS current_price, 'POINT_IN_TIME_MARKET_CLOSE_V1' AS price_basis
        FROM core.prices_daily
        WHERE ticker = ANY(%(tickers)s::text[])
          AND trade_date <= %(cutoff)s
        ORDER BY ticker, trade_date DESC
        """,
        conn,
        params={"tickers": ticker_list, "cutoff": cutoff},
    )
    return df


def fetch_nonfin_follow_contexts(
    conn: Any,
    *,
    tickers: Iterable[str],
    analysis_at: datetime,
) -> pd.DataFrame:
    ticker_list = _normalize_tickers(tickers) or []
    analysis = _aware_datetime("analysis_at", analysis_at)
    if not ticker_list:
        return pd.DataFrame(columns=["ticker", "follow_score", "follow_active"])
    cutoff = daily_price_cutoff_date(analysis)
    df = pd.read_sql(
        """
        SELECT ticker, m2_follow_score AS follow_score
        FROM analytics.m2_period_comparison
        WHERE ticker = ANY(%(tickers)s::text[])
          AND asof_date = %(cutoff)s
        """,
        conn,
        params={"tickers": ticker_list, "cutoff": cutoff},
    )
    if df.empty:
        return pd.DataFrame(columns=["ticker", "follow_score", "follow_active"])
    df = df.copy()
    df["follow_active"] = pd.to_numeric(df["follow_score"], errors="coerce").notna()
    return df[["ticker", "follow_score", "follow_active"]]


def resolve_nonfin_anchor_period_end(
    frame: pd.DataFrame,
    *,
    requested: date | None = None,
) -> date | None:
    if requested is not None:
        return _strict_date("anchor_period_end", requested)
    if frame is None or frame.empty:
        return None
    periods = pd.to_datetime(frame["period_end"], errors="raise").dt.date
    return max(periods) if not periods.empty else None


def build_nonfin_snapshots_from_frames(
    *,
    universe: pd.DataFrame,
    financials: pd.DataFrame,
    prices: pd.DataFrame,
    analysis_at: datetime,
    anchor_period_end: date | None,
    basis_receipts: dict | None = None,
) -> tuple[list[NonfinSnapshot], list[dict[str, str]]]:
    analysis = _aware_datetime("analysis_at", analysis_at)
    requested_anchor = None if anchor_period_end is None else _strict_date("anchor_period_end", anchor_period_end)
    required_universe = {"ticker", "peer_group", "sector_family"}
    required_financials = {
        "ticker", "period_end", "revenue", "ebit", "net_income", "total_equity",
        "debt_st", "debt_lt", "cash_and_eq", "st_investments", "shares_out",
    }
    required_prices = {"ticker", "price_trade_date", "current_price"}
    for name, frame, required in (
        ("universe", universe, required_universe),
        ("financials", financials, required_financials),
        ("prices", prices, required_prices),
    ):
        if frame is None or not isinstance(frame, pd.DataFrame):
            raise NonfinBatchError(f"{name} DataFrame olmali")
        missing = required - set(frame.columns)
        if missing:
            raise NonfinBatchError(f"{name} eksik alanlar: {', '.join(sorted(missing))}")

    universe_rows: dict[str, Mapping[str, Any]] = {}
    for row in universe.to_dict("records"):
        ticker = str(row["ticker"]).strip().upper()
        if ticker in universe_rows:
            raise NonfinBatchError(f"universe yinelenen ticker: {ticker}")
        if str(row["sector_family"]).strip().upper() != "NONFIN":
            raise NonfinBatchError(f"NONFIN batch beklenmeyen aile: {ticker}")
        universe_rows[ticker] = row

    price_rows: dict[str, Mapping[str, Any]] = {}
    for row in prices.to_dict("records"):
        ticker = str(row["ticker"]).strip().upper()
        if ticker in price_rows:
            raise NonfinBatchError(f"prices yinelenen ticker: {ticker}")
        price_rows[ticker] = row

    prepared = financials.copy()
    prepared["ticker"] = prepared["ticker"].astype(str).str.strip().str.upper()
    prepared["period_end"] = pd.to_datetime(prepared["period_end"], errors="raise").dt.date
    snapshots: list[NonfinSnapshot] = []
    rejections: list[dict[str, str]] = []
    for ticker in sorted(universe_rows):
        price = price_rows.get(ticker)
        if price is None:
            rejections.append({"ticker": ticker, "reason": "FIYAT_YOK"})
            continue
        ticker_rows = prepared[prepared["ticker"] == ticker]
        if ticker_rows.empty:
            rejections.append({"ticker": ticker, "reason": "FINANSAL_VERI_YOK"})
            continue
        ticker_anchor = requested_anchor or max(ticker_rows["period_end"])
        group = ticker_rows[ticker_rows["period_end"] <= ticker_anchor]
        group = group.sort_values("period_end").tail(4)
        try:
            if group.empty:
                raise NonfinValuationError("hedef anchor donemi bulunamadi")
            latest = group.iloc[-1]
            basis = normalize_price_level_input(
                ticker=ticker, shares_out=latest["shares_out"], source_date=latest["period_end"],
                source_share_basis=SOURCE_SHARE_BASIS, price=price, analysis_at=analysis)
            normalized_group = group.copy()
            normalized_group.loc[normalized_group.index[-1], "shares_out"] = basis.normalized_shares_out
            snapshot = build_nonfin_snapshot(
                ticker=ticker,
                analysis_at=analysis,
                sector_code=universe_rows[ticker]["peer_group"],
                current_price=basis.raw_close,
                price_trade_date=pd.to_datetime(price["price_trade_date"], errors="raise").date(),
                quarters=normalized_group.to_dict("records"),
            )
            if snapshot.anchor_period_end != ticker_anchor:
                raise NonfinValuationError("hedef anchor donemi bulunamadi")
            snapshots.append(snapshot)
            if basis_receipts is not None:
                basis_receipts[ticker] = valuation_basis_receipt(basis)
        except (NonfinValuationError, TypeError, ValueError, OverflowError) as exc:
            rejections.append({"ticker": ticker, "reason": str(exc)})
    return snapshots, rejections


def _follow_context_map(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame is None or frame.empty:
        return {}
    required = {"ticker", "follow_score", "follow_active"}
    missing = required - set(frame.columns)
    if missing:
        raise NonfinBatchError("follow frame eksik alanlar: " + ", ".join(sorted(missing)))
    result: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        ticker = str(row["ticker"]).strip().upper()
        if ticker in result:
            raise NonfinBatchError(f"follow frame yinelenen ticker: {ticker}")
        active = row["follow_active"]
        if type(active) is not bool:
            raise NonfinBatchError("follow_active Python bool olmali")
        result[ticker] = {"follow_score": row["follow_score"], "follow_active": active}
    return result


def _require_keys(name: str, value: Mapping[str, Any], required: set[str]) -> None:
    missing = required - set(value)
    if missing:
        raise NonfinBatchError(
            f"{name} eksik alanlar: " + ", ".join(sorted(missing))
        )


def persist_nonfin_batch(conn: Any, report: Mapping[str, Any]) -> None:
    if not isinstance(report, Mapping):
        raise NonfinBatchError("report mapping olmali")
    results = report.get("results")
    if not isinstance(results, list):
        raise NonfinBatchError("report.results liste olmali")
    valuation_rows = []
    m2_rows = []
    for index, row in enumerate(results):
        if not isinstance(row, Mapping):
            raise NonfinBatchError("report result mapping olmali")
        valuation = row.get("valuation")
        m2 = row.get("m2")
        if not isinstance(valuation, Mapping) or not isinstance(m2, Mapping):
            raise NonfinBatchError("report valuation/m2 mapping olmali")
        _require_keys(f"results[{index}].valuation", valuation, {
            "ticker", "analysis_at", "anchor_period_end", "sector_code",
            "price_trade_date", "current_price", "valuation_profile",
            "valuation_version", "source_derivation_profile", "source_derivation_version",
            "config_sha256", "status", "valuation_score",
            "v_conf", "coverage_weight",
        })
        _require_keys(f"results[{index}].m2", m2, {
            "ticker", "analysis_at", "anchor_period_end", "m2", "m2_source",
            "valuation_usable", "score_inputs",
        })
        if valuation["ticker"] != m2["ticker"]:
            raise NonfinBatchError("valuation ve m2 ticker uyusmuyor")
        if valuation["analysis_at"] != m2["analysis_at"]:
            raise NonfinBatchError("valuation ve m2 analysis_at uyusmuyor")
        if valuation["anchor_period_end"] != m2["anchor_period_end"]:
            raise NonfinBatchError("valuation ve m2 anchor_period_end uyusmuyor")
        if type(m2["valuation_usable"]) is not bool:
            raise NonfinBatchError("m2.valuation_usable Python bool olmali")
        diagnostics_raw = valuation.get("diagnostics") or {}
        score_inputs_raw = m2.get("score_inputs") or {}
        if not isinstance(diagnostics_raw, Mapping):
            raise NonfinBatchError("valuation.diagnostics mapping olmali")
        if not isinstance(score_inputs_raw, Mapping) or not score_inputs_raw:
            raise NonfinBatchError("m2.score_inputs dolu mapping olmali")
        diagnostics = json.dumps(diagnostics_raw, sort_keys=True, ensure_ascii=False, default=str)
        score_inputs = json.dumps(score_inputs_raw, sort_keys=True, ensure_ascii=False, default=str)
        valuation_rows.append((
            valuation["ticker"], valuation["analysis_at"], valuation["anchor_period_end"],
            valuation["sector_code"], valuation["price_trade_date"], valuation["current_price"],
            valuation["valuation_profile"], valuation["valuation_version"],
            valuation["source_derivation_profile"], valuation["source_derivation_version"],
            valuation["config_sha256"],
            valuation["status"], valuation.get("reason"), valuation.get("V_low"), valuation.get("V_mid"),
            valuation.get("V_high"), valuation.get("valuation_score"), valuation.get("z_val"),
            valuation.get("v_conf"), valuation.get("coverage_weight"), valuation.get("lower_halfwidth"),
            valuation.get("upper_halfwidth"), diagnostics,
        ))
        m2_rows.append((
            m2["ticker"], m2["analysis_at"], m2["anchor_period_end"], m2["m2"],
            m2["m2_source"], m2["valuation_usable"], score_inputs,
        ))
    with conn:
        with conn.cursor() as cur:
            if valuation_rows:
                execute_values(cur, """
                    INSERT INTO analytics.nonfin_valuation_periods (
                      ticker, analysis_at, anchor_period_end, sector_code,
                      price_trade_date, current_price, valuation_profile, valuation_version,
                      source_derivation_profile, source_derivation_version,
                      config_sha256, valuation_status, valuation_reason,
                      v_low, v_mid, v_high, valuation_score, z_val, v_conf,
                      coverage_weight, lower_halfwidth, upper_halfwidth, diagnostics
                    ) VALUES %s
                    ON CONFLICT (ticker, analysis_at, anchor_period_end, valuation_profile, valuation_version)
                    DO NOTHING
                """, valuation_rows, page_size=1000)
            if m2_rows:
                execute_values(cur, """
                    INSERT INTO analytics.nonfin_m2_scores (
                      ticker, analysis_at, anchor_period_end, m2_score,
                      m2_source, valuation_usable, score_inputs
                    ) VALUES %s
                    ON CONFLICT (ticker, analysis_at, anchor_period_end)
                    DO NOTHING
                """, m2_rows, page_size=1000)


def run_nonfin_batch(
    conn: Any,
    *,
    analysis_at: datetime,
    config: NonfinValuationConfig,
    anchor_period_end: date | None = None,
    tickers: Optional[Iterable[str]] = None,
    routing_config: SectorRoutingConfig | None = None,
    persist: bool = True,
    action_bundles=None,
) -> dict[str, Any]:
    if type(persist) is not bool:
        raise NonfinBatchError("persist Python bool olmali")
    analysis = _aware_datetime("analysis_at", analysis_at)
    universe = fetch_nonfin_universe(conn, tickers=tickers, routing_config=routing_config)
    ticker_list = universe["ticker"].astype(str).tolist() if not universe.empty else []
    financials = fetch_company_financials_asof(conn, analysis_at=analysis, tickers=ticker_list)
    if not financials.empty:
        required_source = {"derivation_profile", "derivation_version"}
        missing_source = required_source - set(financials.columns)
        if missing_source:
            raise NonfinBatchError(
                "financials source izleri eksik: " + ", ".join(sorted(missing_source))
            )
        financials = financials[
            (financials["derivation_profile"].astype(str) == config.source_derivation_profile)
            & (pd.to_numeric(financials["derivation_version"], errors="coerce") == config.source_derivation_version)
        ].copy()
    requested_anchor = None if anchor_period_end is None else _strict_date("anchor_period_end", anchor_period_end)
    if financials.empty:
        return {
            "valuation_profile": config.valuation_profile,
            "valuation_version": config.valuation_version,
            "config_sha256": config.config_sha256,
            "analysis_at": analysis,
            "anchor_period_end": requested_anchor,
            "result_count": 0,
            "rejections": [],
            "results": [],
        }
    prices = fetch_nonfin_prices(conn, tickers=ticker_list, analysis_at=analysis)
    prices = attach_action_bundles(prices, action_bundles)
    follow = fetch_nonfin_follow_contexts(conn, tickers=ticker_list, analysis_at=analysis)
    basis_receipts = {}
    snapshots, rejections = build_nonfin_snapshots_from_frames(
        basis_receipts=basis_receipts,
        universe=universe,
        financials=financials,
        prices=prices,
        analysis_at=analysis,
        anchor_period_end=requested_anchor,
    )
    report = evaluate_nonfin_batch(
        snapshots,
        config=config,
        follow_contexts=_follow_context_map(follow),
    )
    report.update({
        "analysis_at": analysis,
        "anchor_period_end": requested_anchor,
        "rejections": rejections,
    })
    attach_basis_receipts(report, basis_receipts)
    if persist:
        persist_nonfin_batch(conn, report)
    return report
