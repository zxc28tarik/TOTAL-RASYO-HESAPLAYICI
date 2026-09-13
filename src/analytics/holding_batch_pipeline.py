from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional

import pandas as pd
try:
    from psycopg2.extras import execute_values
except ImportError:  # tests and pure usage
    def execute_values(*args, **kwargs):
        raise RuntimeError("psycopg2 PostgreSQL persistence icin zorunlu")

from src.analytics.holding_valuation import (
    HoldingSnapshot,
    HoldingValuationConfig,
    HoldingValuationError,
    build_holding_snapshot,
    evaluate_holding_batch,
    validate_holding_config,
)
from src.analytics.nonfin_batch_pipeline import daily_price_cutoff_date
from src.ingest.sector_routing import SectorRoutingConfig
from src.analytics.price_level_adapter import (
    normalize_price_level_input, attach_action_bundles, valuation_basis_receipt, attach_basis_receipts,
    SOURCE_SHARE_BASIS, SHARE_BASIS,
)


class HoldingBatchError(ValueError):
    pass


def _aware_datetime(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HoldingBatchError(f"{name} timezone iceren datetime olmali")
    return value


def _coerce_aware_datetime(name: str, value: Any) -> datetime:
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    elif isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise HoldingBatchError(f"{name} ISO datetime olmali") from exc
    return _aware_datetime(name, value)


def _normalize_tickers(values: Optional[Iterable[str]]) -> Optional[list[str]]:
    if values is None:
        return None
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise HoldingBatchError("ticker degerleri dolu metin olmali")
        ticker = value.strip().upper()
        if ticker not in seen:
            result.append(ticker)
            seen.add(ticker)
    return result


def fetch_holding_universe(
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
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    generic = {"BANK", "HOLDING", "GYO", "INSURANCE", "FINANCIAL", "NONFIN"}
    for row in df.itertuples(index=False):
        ticker = str(row.ticker).strip().upper()
        if ticker in seen:
            raise HoldingBatchError(f"evren sorgusu yinelenen ticker dondurdu: {ticker}")
        seen.add(ticker)
        family = config.route(
            ticker=ticker,
            sector_index_code=row.sector_index_code,
            sector_code=row.sector_code,
        )
        if family != "HOLDING":
            continue
        explicit = row.sector_code
        if isinstance(explicit, str) and explicit.strip().upper() not in generic:
            peer_raw = explicit
        else:
            peer_raw = row.sector_index_code or "XHOLD"
        if not isinstance(peer_raw, str) or not peer_raw.strip():
            raise HoldingBatchError(f"{ticker} peer group belirlenemedi")
        rows.append((ticker, peer_raw.strip().upper(), family))
    return pd.DataFrame(rows, columns=["ticker", "peer_group", "sector_family"])


def fetch_holding_navs_asof(
    conn: Any,
    *,
    tickers: Iterable[str],
    analysis_at: datetime,
    config: HoldingValuationConfig,
) -> pd.DataFrame:
    ticker_list = _normalize_tickers(tickers) or []
    analysis = _aware_datetime("analysis_at", analysis_at)
    if not ticker_list:
        return pd.DataFrame(columns=[
            "ticker", "nav_asof_date", "nav_published_at", "nav_total", "shares_out", "share_basis", "currency",
            "source_confidence", "source_document_id", "source_sha256", "nav_profile", "nav_version",
        ])
    return pd.read_sql(
        """
        SELECT DISTINCT ON (ticker)
               ticker, nav_asof_date, published_at AS nav_published_at,
               nav_total, shares_out, share_basis, currency, source_confidence,
               source_document_id, source_sha256, nav_profile, nav_version
        FROM core.holding_nav_snapshots
        WHERE ticker = ANY(%(tickers)s::text[])
          AND published_at <= %(analysis_at)s
          AND nav_profile = %(nav_profile)s
          AND nav_version = %(nav_version)s
        ORDER BY ticker, nav_asof_date DESC, published_at DESC,
                 version_sequence DESC, source_document_id DESC, source_sha256 DESC
        """,
        conn,
        params={
            "tickers": ticker_list,
            "analysis_at": analysis,
            "nav_profile": config.source_nav_profile,
            "nav_version": config.source_nav_version,
        },
    )


def fetch_holding_prices(
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
    return pd.read_sql(
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


def fetch_holding_follow_contexts(
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


def build_holding_snapshots_from_frames(
    *,
    universe: pd.DataFrame,
    navs: pd.DataFrame,
    prices: pd.DataFrame,
    analysis_at: datetime,
    basis_receipts: dict | None = None,
) -> tuple[list[HoldingSnapshot], list[dict[str, str]]]:
    analysis = _aware_datetime("analysis_at", analysis_at)
    required_universe = {"ticker", "peer_group", "sector_family"}
    required_navs = {
        "ticker", "nav_asof_date", "nav_published_at", "nav_total", "shares_out", "currency",
        "source_confidence", "source_document_id", "source_sha256", "nav_profile", "nav_version",
    }
    required_prices = {"ticker", "price_trade_date", "current_price"}
    for name, frame, required in (
        ("universe", universe, required_universe),
        ("navs", navs, required_navs),
        ("prices", prices, required_prices),
    ):
        if not isinstance(frame, pd.DataFrame):
            raise HoldingBatchError(f"{name} DataFrame olmali")
        missing = required - set(frame.columns)
        if missing:
            raise HoldingBatchError(f"{name} eksik alanlar: {', '.join(sorted(missing))}")

    universe_rows: dict[str, Mapping[str, Any]] = {}
    for row in universe.to_dict("records"):
        ticker = str(row["ticker"]).strip().upper()
        if ticker in universe_rows:
            raise HoldingBatchError(f"universe yinelenen ticker: {ticker}")
        if str(row["sector_family"]).strip().upper() != "HOLDING":
            raise HoldingBatchError(f"HOLDING batch beklenmeyen aile: {ticker}")
        universe_rows[ticker] = row

    def unique_map(name: str, frame: pd.DataFrame) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for row in frame.to_dict("records"):
            ticker = str(row["ticker"]).strip().upper()
            if ticker in result:
                raise HoldingBatchError(f"{name} yinelenen ticker: {ticker}")
            result[ticker] = row
        return result

    nav_rows = unique_map("navs", navs)
    price_rows = unique_map("prices", prices)
    snapshots: list[HoldingSnapshot] = []
    rejections: list[dict[str, str]] = []
    for ticker in sorted(universe_rows):
        nav = nav_rows.get(ticker)
        price = price_rows.get(ticker)
        if nav is None:
            rejections.append({"ticker": ticker, "reason": "NAV_YOK"})
            continue
        if price is None:
            rejections.append({"ticker": ticker, "reason": "FIYAT_YOK"})
            continue
        try:
            basis = normalize_price_level_input(
                ticker=ticker, shares_out=nav["shares_out"],
                source_date=pd.Timestamp(nav["nav_asof_date"]).date(),
                source_share_basis=nav["share_basis"], price=price, analysis_at=analysis)
            snapshots.append(build_holding_snapshot(
                ticker=ticker,
                analysis_at=analysis,
                peer_group=universe_rows[ticker]["peer_group"],
                currency=nav["currency"],
                share_basis=SHARE_BASIS,
                current_price=basis.raw_close,
                price_trade_date=pd.to_datetime(price["price_trade_date"], errors="raise").date(),
                nav_asof_date=pd.to_datetime(nav["nav_asof_date"], errors="raise").date(),
                nav_published_at=_coerce_aware_datetime("nav_published_at", nav["nav_published_at"]),
                nav_total=nav["nav_total"],
                shares_out=basis.normalized_shares_out,
                source_confidence=nav["source_confidence"],
                source_document_id=nav["source_document_id"],
                source_sha256=nav["source_sha256"],
                nav_profile=nav["nav_profile"],
                nav_version=nav["nav_version"],
            ))
            if basis_receipts is not None:
                basis_receipts[ticker] = valuation_basis_receipt(basis)
        except (HoldingValuationError, TypeError, ValueError, OverflowError) as exc:
            rejections.append({"ticker": ticker, "reason": str(exc)})
    return snapshots, rejections


def _follow_context_map(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty:
        return {}
    required = {"ticker", "follow_score", "follow_active"}
    missing = required - set(frame.columns)
    if missing:
        raise HoldingBatchError("follow frame eksik alanlar: " + ", ".join(sorted(missing)))
    result: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        ticker = str(row["ticker"]).strip().upper()
        if ticker in result:
            raise HoldingBatchError(f"follow frame yinelenen ticker: {ticker}")
        if type(row["follow_active"]) is not bool:
            raise HoldingBatchError("follow_active Python bool olmali")
        result[ticker] = {"follow_score": row["follow_score"], "follow_active": row["follow_active"]}
    return result


def _require_keys(name: str, value: Mapping[str, Any], required: set[str]) -> None:
    missing = required - set(value)
    if missing:
        raise HoldingBatchError(f"{name} eksik alanlar: " + ", ".join(sorted(missing)))


def _strict_text(name: str, value: Any, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HoldingBatchError(f"{name} dolu metin olmali")
    result = value.strip()
    return result.upper() if uppercase else result


def _strict_positive_int(name: str, value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise HoldingBatchError(f"{name} pozitif Python int olmali")
    return value


def _strict_sha256(name: str, value: Any) -> str:
    text = _strict_text(name, value).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise HoldingBatchError(f"{name} 64 karakter hex SHA256 olmali")
    return text


def _finite_number(
    name: str, value: Any, *, minimum: float | None = None, maximum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool):
        raise HoldingBatchError(f"{name} bool olamaz")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HoldingBatchError(f"{name} sayisal olmali") from exc
    if not math.isfinite(result):
        raise HoldingBatchError(f"{name} sonlu olmali")
    if minimum is not None and (result <= minimum if strict_minimum else result < minimum):
        relation = "buyuk olmali" if strict_minimum else "degerinden kucuk olamaz"
        raise HoldingBatchError(f"{name} {minimum} {relation}")
    if maximum is not None and result > maximum:
        raise HoldingBatchError(f"{name} {maximum} degerini asamaz")
    return result


def persist_holding_batch(conn: Any, report: Mapping[str, Any]) -> None:
    if not isinstance(report, Mapping):
        raise HoldingBatchError("report mapping olmali")
    results = report.get("results")
    if not isinstance(results, list):
        raise HoldingBatchError("report.results liste olmali")
    result_count = report.get("result_count")
    if type(result_count) is not int or result_count != len(results):
        raise HoldingBatchError("report.result_count results uzunluguyla eslesmeli")

    rejection_rows = report.get("rejections", [])
    if not isinstance(rejection_rows, list):
        raise HoldingBatchError("report.rejections liste olmali")
    has_work = bool(results or rejection_rows)
    analysis_at = report.get("analysis_at")
    profile = report.get("valuation_profile")
    version = report.get("valuation_version")
    source_profile = report.get("source_nav_profile")
    source_version = report.get("source_nav_version")
    config_sha = report.get("config_sha256")
    if has_work:
        analysis_at = _aware_datetime("report.analysis_at", analysis_at)
        profile = _strict_text("report.valuation_profile", profile)
        version = _strict_positive_int("report.valuation_version", version)
        source_profile = _strict_text("report.source_nav_profile", source_profile)
        source_version = _strict_positive_int("report.source_nav_version", source_version)
        config_sha = _strict_sha256("report.config_sha256", config_sha)

    valuation_rows: list[tuple[Any, ...]] = []
    m2_rows: list[tuple[Any, ...]] = []
    success_tickers: list[str] = []
    success_seen: set[str] = set()
    for index, row in enumerate(results):
        if not isinstance(row, Mapping):
            raise HoldingBatchError(f"results[{index}] mapping olmali")
        row_ticker = _strict_text(f"results[{index}].ticker", row.get("ticker"), uppercase=True)
        valuation = row.get("valuation")
        m2 = row.get("m2")
        if not isinstance(valuation, Mapping) or not isinstance(m2, Mapping):
            raise HoldingBatchError("report valuation/m2 mapping olmali")
        _require_keys(f"results[{index}].valuation", valuation, {
            "ticker", "analysis_at", "nav_asof_date", "peer_group", "currency", "share_basis", "price_trade_date",
            "current_price", "nav_published_at", "nav_total", "shares_out", "nav_per_share",
            "source_confidence", "source_document_id", "source_sha256", "nav_profile", "nav_version",
            "valuation_profile", "valuation_version", "config_sha256", "status",
            "valuation_score", "v_conf", "current_discount",
        })
        _require_keys(f"results[{index}].m2", m2, {
            "ticker", "analysis_at", "nav_asof_date", "m2", "m2_source",
            "valuation_usable", "score_inputs",
        })
        valuation_ticker = _strict_text(
            f"results[{index}].valuation.ticker", valuation["ticker"], uppercase=True
        )
        m2_ticker = _strict_text(f"results[{index}].m2.ticker", m2["ticker"], uppercase=True)
        if row_ticker != valuation_ticker or valuation_ticker != m2_ticker:
            raise HoldingBatchError("result, valuation ve m2 ticker uyusmuyor")
        if valuation_ticker in success_seen:
            raise HoldingBatchError(f"yinelenen result ticker: {valuation_ticker}")
        success_seen.add(valuation_ticker)
        success_tickers.append(valuation_ticker)

        valuation_analysis = _coerce_aware_datetime(
            f"results[{index}].valuation.analysis_at", valuation["analysis_at"]
        )
        m2_analysis = _coerce_aware_datetime(f"results[{index}].m2.analysis_at", m2["analysis_at"])
        if valuation_analysis != analysis_at or m2_analysis != analysis_at:
            raise HoldingBatchError("valuation/m2 analysis_at report.analysis_at ile uyusmuyor")
        if valuation["nav_asof_date"] != m2["nav_asof_date"]:
            raise HoldingBatchError("valuation ve m2 nav_asof_date uyusmuyor")
        if not isinstance(valuation["nav_asof_date"], date) or isinstance(valuation["nav_asof_date"], datetime):
            raise HoldingBatchError("valuation.nav_asof_date date olmali")
        if not isinstance(valuation["price_trade_date"], date) or isinstance(valuation["price_trade_date"], datetime):
            raise HoldingBatchError("valuation.price_trade_date date olmali")
        nav_published_at = _coerce_aware_datetime(
            f"results[{index}].valuation.nav_published_at", valuation["nav_published_at"]
        )
        if nav_published_at > analysis_at:
            raise HoldingBatchError("valuation.nav_published_at analysis_at sonrasinda olamaz")

        if _strict_text("valuation.valuation_profile", valuation["valuation_profile"]) != profile:
            raise HoldingBatchError("valuation_profile report ile uyusmuyor")
        if _strict_positive_int("valuation.valuation_version", valuation["valuation_version"]) != version:
            raise HoldingBatchError("valuation_version report ile uyusmuyor")
        if _strict_text("valuation.nav_profile", valuation["nav_profile"]) != source_profile:
            raise HoldingBatchError("nav_profile report ile uyusmuyor")
        if _strict_positive_int("valuation.nav_version", valuation["nav_version"]) != source_version:
            raise HoldingBatchError("nav_version report ile uyusmuyor")
        if _strict_sha256("valuation.config_sha256", valuation["config_sha256"]) != config_sha:
            raise HoldingBatchError("valuation config_sha256 report ile uyusmuyor")
        source_sha = _strict_sha256("valuation.source_sha256", valuation["source_sha256"])

        _finite_number("valuation.current_price", valuation["current_price"], minimum=0.0, strict_minimum=True)
        _finite_number("valuation.nav_total", valuation["nav_total"], minimum=0.0, strict_minimum=True)
        _finite_number("valuation.shares_out", valuation["shares_out"], minimum=0.0, strict_minimum=True)
        _finite_number("valuation.nav_per_share", valuation["nav_per_share"], minimum=0.0, strict_minimum=True)
        _finite_number("valuation.source_confidence", valuation["source_confidence"], minimum=0.0, maximum=1.0)
        _finite_number("valuation.valuation_score", valuation["valuation_score"], minimum=0.0, maximum=1.0)
        _finite_number("valuation.v_conf", valuation["v_conf"], minimum=0.0, maximum=1.0)
        _finite_number("valuation.current_discount", valuation["current_discount"])
        _finite_number("m2.m2", m2["m2"], minimum=0.0, maximum=1.0)
        if type(m2["valuation_usable"]) is not bool:
            raise HoldingBatchError("m2.valuation_usable Python bool olmali")

        diagnostics = valuation.get("diagnostics") or {}
        score_inputs = m2.get("score_inputs") or {}
        if not isinstance(diagnostics, Mapping):
            raise HoldingBatchError("valuation.diagnostics mapping olmali")
        if not isinstance(score_inputs, Mapping) or not score_inputs:
            raise HoldingBatchError("m2.score_inputs dolu mapping olmali")
        try:
            diagnostics_json = json.dumps(
                diagnostics, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            )
            score_inputs_json = json.dumps(
                score_inputs, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise HoldingBatchError("holding diagnostics/score_inputs kanonik JSON olmali") from exc

        valuation_rows.append((
            valuation_ticker, analysis_at, valuation["nav_asof_date"],
            _strict_text("valuation.peer_group", valuation["peer_group"], uppercase=True),
            _strict_text("valuation.currency", valuation["currency"], uppercase=True),
            _strict_text("valuation.share_basis", valuation["share_basis"], uppercase=True),
            valuation["price_trade_date"], valuation["current_price"],
            nav_published_at, valuation["nav_total"], valuation["shares_out"],
            valuation["nav_per_share"], valuation["source_confidence"],
            _strict_text("valuation.source_document_id", valuation["source_document_id"]),
            source_sha, source_profile, source_version, profile, version, config_sha,
            _strict_text("valuation.status", valuation["status"]), valuation.get("reason"),
            valuation.get("V_low"), valuation.get("V_mid"), valuation.get("V_high"),
            valuation.get("target_discount"), valuation.get("current_discount"),
            valuation["valuation_score"], valuation.get("z_val"), valuation["v_conf"],
            valuation.get("lower_halfwidth"), valuation.get("upper_halfwidth"), diagnostics_json,
        ))
        m2_rows.append((
            m2_ticker, analysis_at, valuation["nav_asof_date"], m2["m2"],
            _strict_text("m2.m2_source", m2["m2_source"]), m2["valuation_usable"], score_inputs_json,
        ))

    normalized_rejections: list[tuple[str, str]] = []
    seen_rejections: set[str] = set()
    for index, rejection in enumerate(rejection_rows):
        if not isinstance(rejection, Mapping):
            raise HoldingBatchError(f"rejections[{index}] mapping olmali")
        _require_keys(f"rejections[{index}]", rejection, {"ticker", "reason"})
        ticker = _strict_text(f"rejections[{index}].ticker", rejection["ticker"], uppercase=True)
        reason = _strict_text(f"rejections[{index}].reason", rejection["reason"])
        if ticker in seen_rejections:
            raise HoldingBatchError(f"yinelenen rejection ticker: {ticker}")
        seen_rejections.add(ticker)
        normalized_rejections.append((ticker, reason))
    overlap = set(success_tickers) & seen_rejections
    if overlap:
        raise HoldingBatchError(
            "ayni ticker hem sonuc hem rejection iceriyor: " + ", ".join(sorted(overlap))
        )

    attempted_tickers = sorted(set(success_tickers) | seen_rejections)
    with conn:
        with conn.cursor() as cur:
            if attempted_tickers:
                # The current rerun is authoritative for the exact analysis/config.
                # Delete stale successes before inserting current successes/rejections;
                # the surrounding transaction guarantees rollback on any later failure.
                cur.execute(
                    """
                    DELETE FROM analytics.holding_valuation_periods
                    WHERE analysis_at = %s
                      AND valuation_profile = %s
                      AND valuation_version = %s
                      AND ticker = ANY(%s::text[])
                    """,
                    (analysis_at, profile, version, attempted_tickers),
                )
                cur.execute(
                    """
                    DELETE FROM analytics.holding_m2_scores
                    WHERE analysis_at = %s
                      AND ticker = ANY(%s::text[])
                    """,
                    (analysis_at, attempted_tickers),
                )
            if success_tickers:
                cur.execute(
                    """
                    DELETE FROM analytics.holding_valuation_rejections
                    WHERE analysis_at = %s
                      AND valuation_profile = %s
                      AND valuation_version = %s
                      AND ticker = ANY(%s::text[])
                    """,
                    (analysis_at, profile, version, success_tickers),
                )
            for ticker, reason in normalized_rejections:
                cur.execute(
                    """
                    INSERT INTO analytics.holding_valuation_rejections (
                      ticker, analysis_at, valuation_profile, valuation_version,
                      reason, attempts, first_rejected_at, last_rejected_at
                    ) VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                    ON CONFLICT (ticker, analysis_at, valuation_profile, valuation_version)
                    DO UPDATE SET
                      reason = EXCLUDED.reason,
                      attempts = analytics.holding_valuation_rejections.attempts + 1,
                      last_rejected_at = EXCLUDED.last_rejected_at
                    """,
                    (ticker, analysis_at, profile, version, reason, analysis_at, analysis_at),
                )
            if valuation_rows:
                execute_values(cur, """
                    INSERT INTO analytics.holding_valuation_periods (
                      ticker, analysis_at, nav_asof_date, peer_group, currency, share_basis,
                      price_trade_date, current_price, nav_published_at,
                      nav_total, shares_out, nav_per_share, source_confidence,
                      source_document_id, source_sha256, nav_profile, nav_version,
                      valuation_profile, valuation_version, config_sha256,
                      valuation_status, valuation_reason, v_low, v_mid, v_high,
                      target_discount, current_discount, valuation_score, z_val, v_conf,
                      lower_halfwidth, upper_halfwidth, diagnostics
                    ) VALUES %s
                """, valuation_rows, page_size=1000)
            if m2_rows:
                execute_values(cur, """
                    INSERT INTO analytics.holding_m2_scores (
                      ticker, analysis_at, nav_asof_date, m2_score,
                      m2_source, valuation_usable, score_inputs
                    ) VALUES %s
                """, m2_rows, page_size=1000)

def run_holding_batch(
    conn: Any,
    *,
    analysis_at: datetime,
    config: HoldingValuationConfig,
    tickers: Optional[Iterable[str]] = None,
    routing_config: SectorRoutingConfig | None = None,
    persist: bool = True,
    action_bundles=None,
) -> dict[str, Any]:
    if type(persist) is not bool:
        raise HoldingBatchError("persist Python bool olmali")
    analysis = _aware_datetime("analysis_at", analysis_at)
    config = validate_holding_config(config)
    universe = fetch_holding_universe(conn, tickers=tickers, routing_config=routing_config)
    ticker_list = universe["ticker"].astype(str).tolist() if not universe.empty else []
    navs = fetch_holding_navs_asof(conn, tickers=ticker_list, analysis_at=analysis, config=config)
    prices = fetch_holding_prices(conn, tickers=ticker_list, analysis_at=analysis)
    prices = attach_action_bundles(prices, action_bundles)
    follow = fetch_holding_follow_contexts(conn, tickers=ticker_list, analysis_at=analysis)
    basis_receipts = {}
    snapshots, rejections = build_holding_snapshots_from_frames(
        basis_receipts=basis_receipts,
        universe=universe, navs=navs, prices=prices, analysis_at=analysis,
    )
    all_contexts = _follow_context_map(follow)
    prepared_tickers = {snapshot.ticker for snapshot in snapshots}
    prepared_contexts = {ticker: value for ticker, value in all_contexts.items() if ticker in prepared_tickers}
    report = evaluate_holding_batch(
        snapshots, config=config, follow_contexts=prepared_contexts,
    )
    report.update({"analysis_at": analysis, "rejections": rejections})
    attach_basis_receipts(report, basis_receipts)
    if persist:
        persist_holding_batch(conn, report)
    return report
