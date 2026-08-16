from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional

import pandas as pd
try:
    from psycopg2.extras import execute_values
except ImportError:  # pragma: no cover
    def execute_values(*args, **kwargs):
        raise RuntimeError("psycopg2 PostgreSQL persistence icin zorunlu")

from src.analytics.insurance_valuation import (
    InsuranceSnapshot,
    InsuranceValuationConfig,
    InsuranceValuationError,
    build_insurance_snapshot,
    evaluate_insurance_batch,
    validate_insurance_config,
)
from src.analytics.nonfin_batch_pipeline import daily_price_cutoff_date
from src.ingest.sector_routing import SectorRoutingConfig


class InsuranceBatchError(ValueError):
    pass


def _aware_datetime(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InsuranceBatchError(f"{name} timezone iceren datetime olmali")
    return value


def _coerce_aware_datetime(name: str, value: Any) -> datetime:
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    elif isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise InsuranceBatchError(f"{name} ISO datetime olmali") from exc
    return _aware_datetime(name, value)


def _normalize_tickers(values: Optional[Iterable[str]]) -> Optional[list[str]]:
    if values is None:
        return None
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise InsuranceBatchError("ticker degerleri dolu metin olmali")
        ticker = value.strip().upper()
        if ticker not in seen:
            result.append(ticker)
            seen.add(ticker)
    return result


def fetch_insurance_universe(
    conn: Any,
    *,
    tickers: Optional[Iterable[str]] = None,
    routing_config: SectorRoutingConfig | None = None,
) -> pd.DataFrame:
    ticker_list = _normalize_tickers(tickers)
    config = routing_config or SectorRoutingConfig.default()
    frame = pd.read_sql(
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
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in frame.itertuples(index=False):
        ticker = str(row.ticker).strip().upper()
        if ticker in seen:
            raise InsuranceBatchError(f"evren sorgusu yinelenen ticker dondurdu: {ticker}")
        seen.add(ticker)
        family = config.route(
            ticker=ticker,
            sector_index_code=row.sector_index_code,
            sector_code=row.sector_code,
        )
        if family == "INSURANCE":
            rows.append((ticker, family))
    return pd.DataFrame(rows, columns=["ticker", "sector_family"])


def fetch_insurance_metrics_asof(
    conn: Any,
    *,
    tickers: Iterable[str],
    analysis_at: datetime,
    config: InsuranceValuationConfig,
) -> pd.DataFrame:
    ticker_list = _normalize_tickers(tickers) or []
    analysis = _aware_datetime("analysis_at", analysis_at)
    columns = [
        "ticker", "period_end", "published_at", "business_type", "accounting_profile", "accounting_version",
        "currency", "shares_out", "share_basis", "total_equity", "net_income_ttm",
        "written_premiums_ttm", "technical_result_ttm", "investment_income_ttm",
        "earned_premiums_ttm", "net_claims_ttm", "operating_expenses_ttm", "solvency_ratio",
        "source_confidence", "source_document_id", "source_sha256", "metrics_profile", "metrics_version",
    ]
    if not ticker_list:
        return pd.DataFrame(columns=columns)
    return pd.read_sql(
        """
        SELECT DISTINCT ON (ticker)
               ticker, period_end, published_at, business_type,
               accounting_profile, accounting_version, currency, shares_out, share_basis,
               total_equity, net_income_ttm, written_premiums_ttm, technical_result_ttm,
               investment_income_ttm, earned_premiums_ttm, net_claims_ttm,
               operating_expenses_ttm, solvency_ratio, source_confidence,
               source_document_id, source_sha256, metrics_profile, metrics_version
        FROM core.insurance_metrics_snapshots
        WHERE ticker = ANY(%(tickers)s::text[])
          AND published_at <= %(analysis_at)s
          AND metrics_profile = %(metrics_profile)s
          AND metrics_version = %(metrics_version)s
          AND accounting_profile = %(accounting_profile)s
          AND accounting_version = %(accounting_version)s
        ORDER BY ticker, period_end DESC, published_at DESC,
                 version_sequence DESC, source_document_id DESC, source_sha256 DESC
        """,
        conn,
        params={
            "tickers": ticker_list,
            "analysis_at": analysis,
            "metrics_profile": config.source_metrics_profile,
            "metrics_version": config.source_metrics_version,
            "accounting_profile": config.accounting_profile,
            "accounting_version": config.accounting_version,
        },
    )


def fetch_insurance_prices(conn: Any, *, tickers: Iterable[str], analysis_at: datetime) -> pd.DataFrame:
    ticker_list = _normalize_tickers(tickers) or []
    analysis = _aware_datetime("analysis_at", analysis_at)
    if not ticker_list:
        return pd.DataFrame(columns=["ticker", "price_trade_date", "current_price"])
    cutoff = daily_price_cutoff_date(analysis)
    return pd.read_sql(
        """
        SELECT DISTINCT ON (ticker)
               ticker, trade_date AS price_trade_date,
               COALESCE(adj_close, close) AS current_price
        FROM core.prices_daily
        WHERE ticker = ANY(%(tickers)s::text[])
          AND trade_date <= %(cutoff)s
        ORDER BY ticker, trade_date DESC
        """,
        conn,
        params={"tickers": ticker_list, "cutoff": cutoff},
    )


def fetch_insurance_follow_contexts(conn: Any, *, tickers: Iterable[str], analysis_at: datetime) -> pd.DataFrame:
    ticker_list = _normalize_tickers(tickers) or []
    analysis = _aware_datetime("analysis_at", analysis_at)
    if not ticker_list:
        return pd.DataFrame(columns=["ticker", "follow_score", "follow_active"])
    cutoff = daily_price_cutoff_date(analysis)
    frame = pd.read_sql(
        """
        SELECT ticker, m2_follow_score AS follow_score
        FROM analytics.m2_period_comparison
        WHERE ticker = ANY(%(tickers)s::text[])
          AND asof_date = %(cutoff)s
        """,
        conn,
        params={"tickers": ticker_list, "cutoff": cutoff},
    )
    if frame.empty:
        return pd.DataFrame(columns=["ticker", "follow_score", "follow_active"])
    frame = frame.copy()
    frame["follow_active"] = pd.to_numeric(frame["follow_score"], errors="coerce").notna()
    return frame[["ticker", "follow_score", "follow_active"]]


def build_insurance_snapshots_from_frames(
    *,
    universe: pd.DataFrame,
    metrics: pd.DataFrame,
    prices: pd.DataFrame,
    analysis_at: datetime,
) -> tuple[list[InsuranceSnapshot], list[dict[str, str]]]:
    analysis = _aware_datetime("analysis_at", analysis_at)
    required_universe = {"ticker", "sector_family"}
    required_metrics = {
        "ticker", "period_end", "published_at", "business_type", "accounting_profile", "accounting_version",
        "currency", "shares_out", "share_basis", "total_equity", "net_income_ttm",
        "written_premiums_ttm", "technical_result_ttm", "investment_income_ttm",
        "earned_premiums_ttm", "net_claims_ttm", "operating_expenses_ttm", "solvency_ratio",
        "source_confidence", "source_document_id", "source_sha256", "metrics_profile", "metrics_version",
    }
    required_prices = {"ticker", "price_trade_date", "current_price"}
    for name, frame, required in (
        ("universe", universe, required_universe),
        ("metrics", metrics, required_metrics),
        ("prices", prices, required_prices),
    ):
        if not isinstance(frame, pd.DataFrame):
            raise InsuranceBatchError(f"{name} DataFrame olmali")
        missing = required - set(frame.columns)
        if missing:
            raise InsuranceBatchError(f"{name} eksik alanlar: {', '.join(sorted(missing))}")

    universe_rows: dict[str, Mapping[str, Any]] = {}
    for row in universe.to_dict("records"):
        ticker = str(row["ticker"]).strip().upper()
        if ticker in universe_rows:
            raise InsuranceBatchError(f"universe yinelenen ticker: {ticker}")
        if str(row["sector_family"]).strip().upper() != "INSURANCE":
            raise InsuranceBatchError(f"insurance batch beklenmeyen aile: {ticker}")
        universe_rows[ticker] = row

    def unique_map(name: str, frame: pd.DataFrame) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for row in frame.to_dict("records"):
            ticker = str(row["ticker"]).strip().upper()
            if ticker in result:
                raise InsuranceBatchError(f"{name} yinelenen ticker: {ticker}")
            result[ticker] = row
        return result

    metric_rows = unique_map("metrics", metrics)
    price_rows = unique_map("prices", prices)
    snapshots: list[InsuranceSnapshot] = []
    rejections: list[dict[str, str]] = []
    for ticker in sorted(universe_rows):
        metric = metric_rows.get(ticker)
        price = price_rows.get(ticker)
        if metric is None:
            rejections.append({"ticker": ticker, "reason": "SIGORTA_METRIKLERI_YOK"})
            continue
        if price is None:
            rejections.append({"ticker": ticker, "reason": "FIYAT_YOK"})
            continue
        try:
            snapshots.append(build_insurance_snapshot(
                ticker=ticker,
                analysis_at=analysis,
                business_type=metric["business_type"],
                currency=metric["currency"],
                share_basis=metric["share_basis"],
                current_price=price["current_price"],
                price_trade_date=pd.to_datetime(price["price_trade_date"], errors="raise").date(),
                period_end=pd.to_datetime(metric["period_end"], errors="raise").date(),
                published_at=_coerce_aware_datetime("published_at", metric["published_at"]),
                total_equity=metric["total_equity"],
                net_income_ttm=metric["net_income_ttm"],
                written_premiums_ttm=metric["written_premiums_ttm"],
                technical_result_ttm=metric["technical_result_ttm"],
                investment_income_ttm=metric["investment_income_ttm"],
                shares_out=metric["shares_out"],
                earned_premiums_ttm=metric["earned_premiums_ttm"],
                net_claims_ttm=metric["net_claims_ttm"],
                operating_expenses_ttm=metric["operating_expenses_ttm"],
                solvency_ratio=metric["solvency_ratio"],
                source_confidence=metric["source_confidence"],
                source_document_id=metric["source_document_id"],
                source_sha256=metric["source_sha256"],
                metrics_profile=metric["metrics_profile"],
                metrics_version=int(metric["metrics_version"]),
                accounting_profile=metric["accounting_profile"],
                accounting_version=int(metric["accounting_version"]),
            ))
        except (InsuranceValuationError, TypeError, ValueError, OverflowError) as exc:
            rejections.append({"ticker": ticker, "reason": str(exc)})
    return snapshots, rejections


def _follow_context_map(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty:
        return {}
    required = {"ticker", "follow_score", "follow_active"}
    missing = required - set(frame.columns)
    if missing:
        raise InsuranceBatchError("follow frame eksik alanlar: " + ", ".join(sorted(missing)))
    result: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        ticker = str(row["ticker"]).strip().upper()
        if ticker in result:
            raise InsuranceBatchError(f"follow frame yinelenen ticker: {ticker}")
        if type(row["follow_active"]) is not bool:
            raise InsuranceBatchError("follow_active Python bool olmali")
        result[ticker] = {"follow_score": row["follow_score"], "follow_active": row["follow_active"]}
    return result


def _strict_text(name: str, value: Any, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InsuranceBatchError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _strict_int(name: str, value: Any, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise InsuranceBatchError(f"{name} en az {minimum} olan Python int olmali")
    return value


def _strict_sha(name: str, value: Any) -> str:
    text = _strict_text(name, value).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise InsuranceBatchError(f"{name} 64 karakter hex SHA256 olmali")
    return text


def _finite(name: str, value: Any, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise InsuranceBatchError(f"{name} bool olamaz")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InsuranceBatchError(f"{name} sayisal olmali") from exc
    if not math.isfinite(result):
        raise InsuranceBatchError(f"{name} sonlu olmali")
    if minimum is not None and result < minimum:
        raise InsuranceBatchError(f"{name} {minimum} degerinden kucuk olamaz")
    if maximum is not None and result > maximum:
        raise InsuranceBatchError(f"{name} {maximum} degerini asamaz")
    return result


def persist_insurance_batch(conn: Any, report: Mapping[str, Any]) -> None:
    if not isinstance(report, Mapping):
        raise InsuranceBatchError("report mapping olmali")
    results = report.get("results")
    rejections = report.get("rejections", [])
    if not isinstance(results, list) or not isinstance(rejections, list):
        raise InsuranceBatchError("report results/rejections liste olmali")
    if report.get("result_count") != len(results):
        raise InsuranceBatchError("report.result_count results uzunluguyla eslesmeli")
    analysis_at = _coerce_aware_datetime("report.analysis_at", report.get("analysis_at"))
    profile = _strict_text("valuation_profile", report.get("valuation_profile"))
    version = _strict_int("valuation_version", report.get("valuation_version"))
    source_profile = _strict_text("source_metrics_profile", report.get("source_metrics_profile"))
    source_version = _strict_int("source_metrics_version", report.get("source_metrics_version"))
    accounting_profile = _strict_text("accounting_profile", report.get("accounting_profile"), uppercase=True)
    accounting_version = _strict_int("accounting_version", report.get("accounting_version"))
    config_sha = _strict_sha("config_sha256", report.get("config_sha256"))

    valuation_rows: list[tuple[Any, ...]] = []
    m2_rows: list[tuple[Any, ...]] = []
    success_tickers: set[str] = set()
    for index, item in enumerate(results):
        if not isinstance(item, Mapping) or not isinstance(item.get("valuation"), Mapping) or not isinstance(item.get("m2"), Mapping):
            raise InsuranceBatchError(f"results[{index}] valuation/m2 mapping olmali")
        valuation = item["valuation"]
        m2 = item["m2"]
        ticker = _strict_text(f"results[{index}].ticker", item.get("ticker"), uppercase=True)
        if ticker in success_tickers:
            raise InsuranceBatchError(f"yinelenen sonuc ticker: {ticker}")
        success_tickers.add(ticker)
        if valuation.get("ticker") != ticker or m2.get("ticker") != ticker:
            raise InsuranceBatchError(f"{ticker} sonuc ticker sozlesmesi uyusmuyor")
        if valuation.get("valuation_profile") != profile or valuation.get("valuation_version") != version:
            raise InsuranceBatchError(f"{ticker} valuation profil/surum report ile uyusmuyor")
        if valuation.get("source_metrics_profile") != source_profile or valuation.get("source_metrics_version") != source_version:
            raise InsuranceBatchError(f"{ticker} metrics profil/surum report ile uyusmuyor")
        if valuation.get("accounting_profile") != accounting_profile or valuation.get("accounting_version") != accounting_version:
            raise InsuranceBatchError(f"{ticker} muhasebe profil/surum report ile uyusmuyor")
        if valuation.get("config_sha256") != config_sha:
            raise InsuranceBatchError(f"{ticker} config SHA report ile uyusmuyor")
        _finite("current_price", valuation.get("current_price"), minimum=0.0)
        _finite("total_equity", valuation.get("total_equity"), minimum=0.0)
        _finite("shares_out", valuation.get("shares_out"), minimum=0.0)
        _finite("valuation_score", valuation.get("valuation_score"), minimum=0.0, maximum=1.0)
        _finite("v_conf", valuation.get("v_conf"), minimum=0.0, maximum=1.0)
        _finite("m2", m2.get("m2"), minimum=0.0, maximum=1.0)
        if type(m2.get("valuation_usable")) is not bool:
            raise InsuranceBatchError("m2.valuation_usable Python bool olmali")
        diagnostics = valuation.get("diagnostics") or {}
        score_inputs = m2.get("score_inputs") or {}
        if not isinstance(diagnostics, Mapping) or not isinstance(score_inputs, Mapping) or not score_inputs:
            raise InsuranceBatchError("diagnostics/score_inputs mapping olmali")
        try:
            diagnostics_json = json.dumps(diagnostics, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            score_inputs_json = json.dumps(score_inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, OverflowError) as exc:
            raise InsuranceBatchError("insurance diagnostics/score_inputs kanonik JSON olmali") from exc
        valuation_rows.append((
            ticker, analysis_at, valuation["period_end"], valuation["business_type"], valuation["currency"],
            valuation["share_basis"], valuation["price_trade_date"], valuation["current_price"], valuation["published_at"],
            valuation["total_equity"], valuation["net_income_ttm"], valuation["written_premiums_ttm"],
            valuation["technical_result_ttm"], valuation["investment_income_ttm"], valuation["shares_out"],
            valuation.get("earned_premiums_ttm"), valuation.get("net_claims_ttm"), valuation.get("operating_expenses_ttm"),
            valuation.get("solvency_ratio"), valuation["source_confidence"], valuation["source_document_id"],
            _strict_sha("source_sha256", valuation["source_sha256"]), source_profile, source_version,
            accounting_profile, accounting_version, profile, version, config_sha,
            valuation["status"], valuation.get("reason"), valuation.get("V_low"), valuation.get("V_mid"),
            valuation.get("V_high"), valuation.get("target_pb"), valuation.get("target_pe"),
            valuation.get("method_count"), valuation["technical_margin"], valuation.get("combined_ratio"),
            valuation["roe_ttm"], valuation["investment_dependency"], valuation["valuation_score"],
            valuation.get("z_val"), valuation["v_conf"], valuation.get("lower_halfwidth"),
            valuation.get("upper_halfwidth"), diagnostics_json,
        ))
        m2_rows.append((
            ticker, analysis_at, valuation["period_end"], m2["m2"],
            _strict_text("m2_source", m2["m2_source"]), m2["valuation_usable"], score_inputs_json,
        ))

    rejection_rows: list[tuple[str, str]] = []
    seen_rejections: set[str] = set()
    for index, item in enumerate(rejections):
        if not isinstance(item, Mapping):
            raise InsuranceBatchError(f"rejections[{index}] mapping olmali")
        ticker = _strict_text(f"rejections[{index}].ticker", item.get("ticker"), uppercase=True)
        reason = _strict_text(f"rejections[{index}].reason", item.get("reason"))
        if ticker in seen_rejections:
            raise InsuranceBatchError(f"yinelenen rejection ticker: {ticker}")
        seen_rejections.add(ticker)
        rejection_rows.append((ticker, reason))
    overlap = success_tickers & seen_rejections
    if overlap:
        raise InsuranceBatchError("ayni ticker hem sonuc hem rejection iceriyor")
    attempted = sorted(success_tickers | seen_rejections)

    with conn:
        with conn.cursor() as cur:
            if attempted:
                cur.execute(
                    """
                    DELETE FROM analytics.insurance_valuation_periods
                    WHERE analysis_at=%s AND valuation_profile=%s AND valuation_version=%s
                      AND ticker=ANY(%s::text[])
                    """,
                    (analysis_at, profile, version, attempted),
                )
                cur.execute(
                    "DELETE FROM analytics.insurance_m2_scores WHERE analysis_at=%s AND ticker=ANY(%s::text[])",
                    (analysis_at, attempted),
                )
            if success_tickers:
                cur.execute(
                    """
                    DELETE FROM analytics.insurance_valuation_rejections
                    WHERE analysis_at=%s AND valuation_profile=%s AND valuation_version=%s
                      AND ticker=ANY(%s::text[])
                    """,
                    (analysis_at, profile, version, sorted(success_tickers)),
                )
            for ticker, reason in rejection_rows:
                cur.execute(
                    """
                    INSERT INTO analytics.insurance_valuation_rejections (
                      ticker, analysis_at, valuation_profile, valuation_version,
                      reason, attempts, first_rejected_at, last_rejected_at
                    ) VALUES (%s,%s,%s,%s,%s,1,%s,%s)
                    ON CONFLICT (ticker, analysis_at, valuation_profile, valuation_version)
                    DO UPDATE SET reason=EXCLUDED.reason,
                      attempts=analytics.insurance_valuation_rejections.attempts+1,
                      last_rejected_at=EXCLUDED.last_rejected_at
                    """,
                    (ticker, analysis_at, profile, version, reason, analysis_at, analysis_at),
                )
            if valuation_rows:
                execute_values(cur, """
                    INSERT INTO analytics.insurance_valuation_periods (
                      ticker, analysis_at, period_end, business_type, currency, share_basis,
                      price_trade_date, current_price, published_at, total_equity, net_income_ttm,
                      written_premiums_ttm, technical_result_ttm, investment_income_ttm, shares_out,
                      earned_premiums_ttm, net_claims_ttm, operating_expenses_ttm, solvency_ratio,
                      source_confidence, source_document_id, source_sha256,
                      metrics_profile, metrics_version, accounting_profile, accounting_version,
                      valuation_profile, valuation_version, config_sha256, valuation_status, valuation_reason,
                      v_low, v_mid, v_high, target_pb, target_pe, method_count, technical_margin,
                      combined_ratio, roe_ttm, investment_dependency, valuation_score, z_val, v_conf,
                      lower_halfwidth, upper_halfwidth, diagnostics
                    ) VALUES %s
                """, valuation_rows, page_size=1000)
            if m2_rows:
                execute_values(cur, """
                    INSERT INTO analytics.insurance_m2_scores (
                      ticker, analysis_at, period_end, m2_score,
                      m2_source, valuation_usable, score_inputs
                    ) VALUES %s
                """, m2_rows, page_size=1000)


def run_insurance_batch(
    conn: Any,
    *,
    analysis_at: datetime,
    config: InsuranceValuationConfig,
    tickers: Optional[Iterable[str]] = None,
    routing_config: SectorRoutingConfig | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    if type(persist) is not bool:
        raise InsuranceBatchError("persist Python bool olmali")
    analysis = _aware_datetime("analysis_at", analysis_at)
    config = validate_insurance_config(config)
    universe = fetch_insurance_universe(conn, tickers=tickers, routing_config=routing_config)
    ticker_list = universe["ticker"].astype(str).tolist() if not universe.empty else []
    metrics = fetch_insurance_metrics_asof(conn, tickers=ticker_list, analysis_at=analysis, config=config)
    prices = fetch_insurance_prices(conn, tickers=ticker_list, analysis_at=analysis)
    follow = fetch_insurance_follow_contexts(conn, tickers=ticker_list, analysis_at=analysis)
    snapshots, rejections = build_insurance_snapshots_from_frames(
        universe=universe, metrics=metrics, prices=prices, analysis_at=analysis,
    )
    all_contexts = _follow_context_map(follow)
    prepared = {snapshot.ticker for snapshot in snapshots}
    contexts = {ticker: value for ticker, value in all_contexts.items() if ticker in prepared}
    report = evaluate_insurance_batch(snapshots, config=config, follow_contexts=contexts)
    report.update({"analysis_at": analysis, "rejections": rejections})
    if persist:
        persist_insurance_batch(conn, report)
    return report
