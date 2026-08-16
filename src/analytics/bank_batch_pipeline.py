from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from .bank_valuation_pipeline import (
    BankValuationInputs,
    CanonicalBankRow,
    CanonicalizationError,
    _as_aware_datetime,
    _as_date,
    _json_safe,
    persist_bank_valuation,
    run_bank_valuation,
    to_canonical_row,
)
from .bank_v47 import estimate_roe_uncertainty
from .bank_v47.roe_uncertainty import coerce_finite_number
from .bank_v47.spec_v46 import OK, INSUFFICIENT, m2, valuation_score


BATCH_SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "014_bank_point_in_time_slots_batch.sql"
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
DAILY_CLOSE_AVAILABLE_AT = time(18, 30)


@dataclass(frozen=True)
class ResolvedBankAssumption:
    inputs: BankValuationInputs
    scope_type: str
    scope_code: str
    effective_at: datetime
    source: str
    metadata: Mapping[str, Any]
    risk_free_rate: float | None = None


@dataclass(frozen=True)
class BankM2Context:
    current_price: float | None
    price_trade_date: date | None = None
    price_source: str = "DAILY_CLOSE"
    s_lag_effective: float = 0.5
    lag_active: bool = False
    lag_source: str = "NONE"


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        if not isinstance(raw, str) or not raw.strip():
            raise CanonicalizationError("ticker degerleri bos olmayan metin olmali")
        ticker = raw.strip().upper()
        if ticker not in seen:
            result.append(ticker)
            seen.add(ticker)
    return result


def fetch_active_bank_tickers(conn: Any) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker
            FROM core.universe_stocks
            WHERE is_active = true
              AND (
                upper(COALESCE(sector_code, '')) = 'BANK'
                OR upper(COALESCE(sector_index_code, '')) = 'XBANK'
              )
            ORDER BY ticker
            """
        )
        return _normalize_tickers(row[0] for row in cur.fetchall())


def resolve_latest_anchor_period_end(
    conn: Any,
    *,
    tickers: Sequence[str],
    analysis_at: datetime,
) -> date | None:
    ticker_list = _normalize_tickers(tickers)
    analysis = _as_aware_datetime("analysis_at", analysis_at)
    if not ticker_list:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT max(period_end)
            FROM core.bank_metrics_quarterly
            WHERE ticker = ANY(%(tickers)s)
              AND published_at <= %(analysis_at)s
            """,
            {"tickers": ticker_list, "analysis_at": analysis},
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return _as_date("anchor_period_end", row[0])


def fetch_bank_quarter_slots_batch(
    conn: Any,
    *,
    tickers: Sequence[str],
    analysis_at: datetime,
    anchor_period_end: date,
) -> dict[str, list[dict[str, Any]]]:
    ticker_list = _normalize_tickers(tickers)
    analysis = _as_aware_datetime("analysis_at", analysis_at)
    anchor = _as_date("anchor_period_end", anchor_period_end)
    if not ticker_list:
        return {}
    sql = BATCH_SQL_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(
            sql,
            {
                "tickers": ticker_list,
                "analysis_at": analysis,
                "anchor_period_end": anchor,
            },
        )
        names = [desc[0] for desc in cur.description]
        rows = [dict(zip(names, row)) for row in cur.fetchall()]
    grouped = {ticker: [] for ticker in ticker_list}
    for row in rows:
        ticker = str(row.pop("ticker")).strip().upper()
        if ticker not in grouped:
            raise CanonicalizationError(f"batch sorgusu beklenmeyen ticker dondurdu: {ticker}")
        grouped[ticker].append(row)
    return grouped


def _as_factor(
    name: str, value: Any, *, positive: bool = False, maximum: float | None = None
) -> float:
    try:
        return coerce_finite_number(
            name,
            value,
            minimum=0.0,
            strict_minimum=positive,
            maximum=maximum,
        )
    except ValueError as exc:
        raise CanonicalizationError(str(exc)) from exc


def _assumption_from_row(row: Mapping[str, Any]) -> ResolvedBankAssumption:
    scope_type = str(row["scope_type"]).strip().upper()
    scope_code = str(row["scope_code"]).strip().upper()
    if scope_type not in {"BANK", "TICKER"}:
        raise CanonicalizationError(f"gecersiz assumption scope_type: {scope_type}")
    if scope_type == "BANK" and scope_code != "BANK":
        raise CanonicalizationError("BANK varsayiminin scope_code degeri BANK olmali")
    effective_at = _as_aware_datetime("effective_at", row["effective_at"])
    shadow = row["band_width_shadow_mode"]
    if type(shadow) is not bool:
        raise CanonicalizationError("band_width_shadow_mode Python bool olmali")
    risk_free_raw = row.get("risk_free_rate")
    risk_free_rate = None if risk_free_raw is None else _as_factor("risk_free_rate", risk_free_raw)
    inputs = BankValuationInputs(
        coe=_as_factor("coe", row["coe"], positive=True),
        macro_cap=_as_factor("macro_cap", row["macro_cap"]),
        tier_cap=_as_factor("tier_cap", row["tier_cap"], maximum=1.0),
        payout_missing_factor=_as_factor("payout_missing_factor", row["payout_missing_factor"], maximum=1.0),
        band_width_shadow_mode=shadow,
        max_halfwidth=_as_factor("max_halfwidth", row["max_halfwidth"], positive=True),
    )
    source = str(row.get("source") or "UNKNOWN").strip() or "UNKNOWN"
    metadata = row.get("metadata")
    if metadata is None:
        metadata = {}
    elif isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise CanonicalizationError("assumption metadata gecerli JSON olmali") from exc
    if not isinstance(metadata, Mapping):
        raise CanonicalizationError("assumption metadata mapping olmali")
    return ResolvedBankAssumption(
        inputs=inputs,
        scope_type=scope_type,
        scope_code=scope_code,
        effective_at=effective_at,
        source=source,
        metadata=dict(metadata),
        risk_free_rate=risk_free_rate,
    )


def resolve_bank_assumptions(
    conn: Any,
    *,
    tickers: Sequence[str],
    analysis_at: datetime,
) -> tuple[dict[str, ResolvedBankAssumption], list[str]]:
    ticker_list = _normalize_tickers(tickers)
    analysis = _as_aware_datetime("analysis_at", analysis_at)
    if not ticker_list:
        return {}, []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT scope_type, scope_code, effective_at,
                   coe, macro_cap, risk_free_rate, tier_cap, payout_missing_factor,
                   band_width_shadow_mode, max_halfwidth, source, metadata
            FROM analytics.bank_valuation_assumptions
            WHERE effective_at <= %(analysis_at)s
              AND (
                (scope_type = 'BANK' AND scope_code = 'BANK')
                OR (scope_type = 'TICKER' AND upper(scope_code) = ANY(%(tickers)s))
              )
            ORDER BY effective_at DESC
            """,
            {"analysis_at": analysis, "tickers": ticker_list},
        )
        names = [desc[0] for desc in cur.description]
        raw_rows = [dict(zip(names, row)) for row in cur.fetchall()]

    bank_default: ResolvedBankAssumption | None = None
    overrides: dict[str, ResolvedBankAssumption] = {}
    for raw in raw_rows:
        assumption = _assumption_from_row(raw)
        if assumption.scope_type == "BANK":
            if bank_default is None:
                bank_default = assumption
        elif assumption.scope_code in ticker_list and assumption.scope_code not in overrides:
            overrides[assumption.scope_code] = assumption

    resolved: dict[str, ResolvedBankAssumption] = {}
    missing: list[str] = []
    for ticker in ticker_list:
        selected = overrides.get(ticker) or bank_default
        if selected is None:
            missing.append(ticker)
        else:
            resolved[ticker] = selected
    return resolved, missing


def daily_price_cutoff_date(analysis_at: datetime) -> date:
    """Daily close is usable on the same date only after a conservative close cutoff."""
    analysis = _as_aware_datetime("analysis_at", analysis_at).astimezone(ISTANBUL_TZ)
    if analysis.timetz().replace(tzinfo=None) >= DAILY_CLOSE_AVAILABLE_AT:
        return analysis.date()
    return analysis.date() - timedelta(days=1)


def fetch_bank_m2_contexts(
    conn: Any,
    *,
    tickers: Sequence[str],
    analysis_at: datetime,
) -> dict[str, BankM2Context]:
    ticker_list = _normalize_tickers(tickers)
    analysis = _as_aware_datetime("analysis_at", analysis_at)
    if not ticker_list:
        return {}
    context_asof_date = daily_price_cutoff_date(analysis)
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH requested AS (
              SELECT unnest(%(tickers)s::text[]) AS ticker
            ),
            prices AS (
              SELECT DISTINCT ON (p.ticker)
                     p.ticker, p.trade_date AS price_trade_date,
                     COALESCE(p.adj_close, p.close) AS current_price
              FROM core.prices_daily p
              WHERE p.ticker = ANY(%(tickers)s)
                AND p.trade_date <= %(asof_date)s
              ORDER BY p.ticker, p.trade_date DESC
            ),
            lag AS (
              SELECT ticker, m2_follow_score
              FROM analytics.m2_period_comparison
              WHERE asof_date = %(asof_date)s
            )
            SELECT r.ticker, p.price_trade_date, p.current_price, l.m2_follow_score
            FROM requested r
            LEFT JOIN prices p USING (ticker)
            LEFT JOIN lag l USING (ticker)
            ORDER BY r.ticker
            """,
            {"tickers": ticker_list, "asof_date": context_asof_date},
        )
        rows = cur.fetchall()
    contexts: dict[str, BankM2Context] = {}
    for ticker_raw, price_date_raw, price_raw, lag_raw in rows:
        ticker = str(ticker_raw).strip().upper()
        price = None
        if price_raw is not None:
            try:
                price = coerce_finite_number("current_price", price_raw, minimum=0.0, strict_minimum=True)
            except ValueError:
                price = None
        if lag_raw is None:
            contexts[ticker] = BankM2Context(
                current_price=price,
                price_trade_date=None if price_date_raw is None else _as_date("price_trade_date", price_date_raw),
            )
            continue
        try:
            lag_score = coerce_finite_number("m2_follow_score", lag_raw, minimum=0.0, maximum=1.0)
        except ValueError:
            contexts[ticker] = BankM2Context(
                current_price=price,
                price_trade_date=None if price_date_raw is None else _as_date("price_trade_date", price_date_raw),
            )
            continue
        contexts[ticker] = BankM2Context(
            current_price=price,
            price_trade_date=None if price_date_raw is None else _as_date("price_trade_date", price_date_raw),
            s_lag_effective=lag_score,
            lag_active=True,
            lag_source="M2_PERIOD_FOLLOW_PROXY_V1",
        )
    return contexts


def compute_bank_m2_score(
    valuation_result: Mapping[str, Any],
    context: BankM2Context,
) -> dict[str, Any]:
    analysis = _as_aware_datetime("analysis_at", valuation_result["analysis_at"])
    if type(context.lag_active) is not bool:
        raise CanonicalizationError("lag_active Python bool olmali")
    lag_score = _as_factor("s_lag_effective", context.s_lag_effective, maximum=1.0)
    if not isinstance(context.price_source, str) or not context.price_source.strip():
        raise CanonicalizationError("price_source bos olmayan metin olmali")
    if not isinstance(context.lag_source, str) or not context.lag_source.strip():
        raise CanonicalizationError("lag_source bos olmayan metin olmali")
    price_trade_date = (
        None
        if context.price_trade_date is None
        else _as_date("price_trade_date", context.price_trade_date)
    )
    if price_trade_date is not None and price_trade_date > daily_price_cutoff_date(analysis):
        raise CanonicalizationError(
            "price_trade_date analysis_at aninda kullanilabilir daily close kesimini asiyor"
        )

    valuation = dict(valuation_result.get("valuation") or {})
    valuation_status = str(valuation_result.get("status") or INSUFFICIENT)
    valuation_reason = valuation_result.get("reason")
    v_conf_raw = valuation_result.get("v_conf")
    v_conf = 0.0 if v_conf_raw is None else _as_factor("v_conf", v_conf_raw, maximum=1.0)

    price = context.current_price
    if price is not None:
        price = _as_factor("current_price", price, positive=True)
    score_status = valuation_status
    val_axis = {"z_val": None, "s_valuation": None, "s_val_effective": 0.5}
    if price is None:
        score_status = INSUFFICIENT
        valuation_reason = valuation_reason or "CURRENT_PRICE_MISSING"
    elif valuation_status == OK:
        try:
            val_axis = valuation_score(
                float(valuation["V_mid"]),
                float(valuation["V_low"]),
                float(valuation["V_high"]),
                price,
                float(valuation["lower_halfwidth"]),
                float(valuation["upper_halfwidth"]),
                v_conf,
            )
        except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise CanonicalizationError(f"valuation score girdileri gecersiz: {exc}") from exc

    combined = m2(
        val_axis["s_val_effective"],
        score_status,
        v_conf,
        lag_score,
        context.lag_active,
    )
    score_inputs = {
        "s_val_effective": float(val_axis["s_val_effective"]),
        "v_status": score_status,
        "v_conf": float(v_conf),
        "s_lag_effective": float(lag_score),
        "lag_active": context.lag_active,
    }
    diagnostics = {
        "current_price": price,
        "price_trade_date": price_trade_date,
        "price_source": context.price_source.strip(),
        "valuation_status": valuation_status,
        "valuation_reason": valuation_reason,
        "z_val": val_axis["z_val"],
        "s_valuation": val_axis["s_valuation"],
        "lag_source": context.lag_source.strip(),
    }
    return {
        "ticker": valuation_result["ticker"],
        "asof_date": analysis.astimezone(ISTANBUL_TZ).date(),
        "analysis_at": analysis,
        "anchor_period_end": valuation_result["anchor_period_end"],
        "current_price": price,
        "price_trade_date": price_trade_date,
        "price_source": context.price_source.strip(),
        "valuation_status": valuation_status,
        "valuation_reason": valuation_reason,
        "v_conf": None if v_conf_raw is None else float(v_conf),
        "z_val": val_axis["z_val"],
        "s_valuation": val_axis["s_valuation"],
        "s_val_effective": float(val_axis["s_val_effective"]),
        "s_lag_effective": float(lag_score),
        "lag_active": context.lag_active,
        "lag_source": context.lag_source.strip(),
        "valuation_usable": bool(combined["valuation_usable"]),
        "m2_score": float(combined["m2"]),
        "score_inputs": score_inputs,
        "diagnostics": diagnostics,
    }


def persist_bank_m2_score(conn: Any, result: Mapping[str, Any]) -> None:
    sql = """
    INSERT INTO analytics.bank_m2_scores (
      ticker, asof_date, analysis_at, anchor_period_end,
      current_price, price_trade_date, price_source, valuation_status, valuation_reason, v_conf,
      z_val, s_valuation, s_val_effective,
      s_lag_effective, lag_active, lag_source,
      valuation_usable, m2_score, score_inputs, diagnostics
    ) VALUES (
      %(ticker)s, %(asof_date)s, %(analysis_at)s, %(anchor_period_end)s,
      %(current_price)s, %(price_trade_date)s, %(price_source)s, %(valuation_status)s, %(valuation_reason)s, %(v_conf)s,
      %(z_val)s, %(s_valuation)s, %(s_val_effective)s,
      %(s_lag_effective)s, %(lag_active)s, %(lag_source)s,
      %(valuation_usable)s, %(m2_score)s, %(score_inputs)s::jsonb, %(diagnostics)s::jsonb
    )
    ON CONFLICT (ticker, analysis_at, anchor_period_end)
    DO UPDATE SET
      asof_date=EXCLUDED.asof_date,
      current_price=EXCLUDED.current_price,
      price_trade_date=EXCLUDED.price_trade_date,
      price_source=EXCLUDED.price_source,
      valuation_status=EXCLUDED.valuation_status,
      valuation_reason=EXCLUDED.valuation_reason,
      v_conf=EXCLUDED.v_conf,
      z_val=EXCLUDED.z_val,
      s_valuation=EXCLUDED.s_valuation,
      s_val_effective=EXCLUDED.s_val_effective,
      s_lag_effective=EXCLUDED.s_lag_effective,
      lag_active=EXCLUDED.lag_active,
      lag_source=EXCLUDED.lag_source,
      valuation_usable=EXCLUDED.valuation_usable,
      m2_score=EXCLUDED.m2_score,
      score_inputs=EXCLUDED.score_inputs,
      diagnostics=EXCLUDED.diagnostics,
      created_at=now()
    """
    params = dict(result)
    params["score_inputs"] = json.dumps(_json_safe(result["score_inputs"]))
    params["diagnostics"] = json.dumps(_json_safe(result["diagnostics"]))
    with conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def _raw_residual_scale(canonical: CanonicalBankRow) -> float | None:
    uncertainty = estimate_roe_uncertainty(canonical.roe_series)
    if int(uncertainty.get("n_valid", 0)) < 4:
        return None
    raw = uncertainty.get("sd_roe_residual")
    if raw is None:
        return None
    try:
        return coerce_finite_number("sd_roe_residual", raw, minimum=0.0)
    except ValueError:
        return None


def evaluate_bank_batch(
    canonicals: Mapping[str, CanonicalBankRow],
    assumptions: Mapping[str, ResolvedBankAssumption],
    contexts: Mapping[str, BankM2Context] | None = None,
) -> list[dict[str, Any]]:
    contexts = contexts or {}
    scales = {ticker: _raw_residual_scale(row) for ticker, row in canonicals.items()}
    scale_rejects = sorted(ticker for ticker, value in scales.items() if value is None)
    results: list[dict[str, Any]] = []
    for ticker in sorted(canonicals):
        canonical = canonicals[ticker]
        assumption = assumptions.get(ticker)
        if assumption is None:
            results.append({
                "ticker": ticker,
                "status": "YETERSIZ_VERI",
                "reason": "POINT_IN_TIME_ASSUMPTION_MISSING",
                "analysis_at": canonical.analysis_at,
                "anchor_period_end": canonical.anchor_period_end,
            })
            continue
        sector_scales = [
            value for other, value in scales.items()
            if other != ticker and value is not None
        ]
        try:
            valuation_result = run_bank_valuation(
                canonical,
                assumption.inputs,
                sector_residual_scales=sector_scales,
            )
        except CanonicalizationError as exc:
            results.append({
                "ticker": ticker,
                "status": "YETERSIZ_VERI",
                "reason": "VALUATION_PIPELINE_INPUT_INVALID",
                "detail": str(exc),
                "analysis_at": canonical.analysis_at,
                "anchor_period_end": canonical.anchor_period_end,
            })
            continue
        valuation_result["sector_sample_size"] = len(sector_scales)
        valuation_result["sector_asof_cutoff"] = canonical.analysis_at
        valuation_result["sector_scale_rejected_tickers"] = scale_rejects
        valuation_result["assumption"] = {
            "scope_type": assumption.scope_type,
            "scope_code": assumption.scope_code,
            "effective_at": assumption.effective_at,
            "source": assumption.source,
            "coe": assumption.inputs.coe,
            "macro_cap": assumption.inputs.macro_cap,
            "risk_free_rate": assumption.risk_free_rate,
            "tier_cap": assumption.inputs.tier_cap,
            "payout_missing_factor": assumption.inputs.payout_missing_factor,
            "band_width_shadow_mode": assumption.inputs.band_width_shadow_mode,
            "max_halfwidth": assumption.inputs.max_halfwidth,
            "metadata": dict(assumption.metadata),
        }
        try:
            valuation_result["m2"] = compute_bank_m2_score(
                valuation_result,
                contexts.get(ticker, BankM2Context(current_price=None)),
            )
        except CanonicalizationError as exc:
            valuation_result["m2"] = None
            valuation_result["m2_error"] = str(exc)
        results.append(valuation_result)
    return results


def run_bank_batch(
    conn: Any,
    *,
    analysis_at: datetime,
    anchor_period_end: date | None = None,
    tickers: Sequence[str] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    analysis = _as_aware_datetime("analysis_at", analysis_at)
    ticker_list = _normalize_tickers(tickers) if tickers is not None else fetch_active_bank_tickers(conn)
    if not ticker_list:
        return {"status": "EMPTY_UNIVERSE", "requested_count": 0, "results": []}
    anchor = (
        _as_date("anchor_period_end", anchor_period_end)
        if anchor_period_end is not None
        else resolve_latest_anchor_period_end(conn, tickers=ticker_list, analysis_at=analysis)
    )
    if anchor is None:
        return {
            "status": "YETERSIZ_VERI",
            "reason": "NO_PUBLISHED_BANK_PERIOD",
            "requested_count": len(ticker_list),
            "results": [],
        }

    grouped_rows = fetch_bank_quarter_slots_batch(
        conn,
        tickers=ticker_list,
        analysis_at=analysis,
        anchor_period_end=anchor,
    )
    canonicals: dict[str, CanonicalBankRow] = {}
    rejected: dict[str, str] = {}
    for ticker in ticker_list:
        try:
            canonicals[ticker] = to_canonical_row(
                grouped_rows.get(ticker, []),
                ticker=ticker,
                analysis_at=analysis,
                anchor_period_end=anchor,
            )
        except CanonicalizationError as exc:
            rejected[ticker] = str(exc)

    assumptions, missing_assumptions = resolve_bank_assumptions(
        conn,
        tickers=list(canonicals),
        analysis_at=analysis,
    )
    contexts = fetch_bank_m2_contexts(
        conn,
        tickers=list(canonicals),
        analysis_at=analysis,
    )
    results = evaluate_bank_batch(canonicals, assumptions, contexts)
    if persist:
        for result in results:
            if "uncertainty" not in result:
                continue
            persist_bank_valuation(conn, result)
            if result.get("m2") is not None:
                persist_bank_m2_score(conn, result["m2"])

    ok_count = sum(result.get("status") == OK for result in results)
    m2_error_count = sum(result.get("m2_error") is not None for result in results)
    controlled_reject_count = len(results) - ok_count + len(rejected)
    overall_status = (
        "OK" if controlled_reject_count == 0 and m2_error_count == 0
        else "PARTIAL" if ok_count > 0
        else "YETERSIZ_VERI"
    )
    return {
        "status": overall_status,
        "analysis_at": analysis,
        "anchor_period_end": anchor,
        "requested_count": len(ticker_list),
        "canonical_count": len(canonicals),
        "ok_count": ok_count,
        "controlled_reject_count": controlled_reject_count,
        "m2_error_count": m2_error_count,
        "canonical_rejected": rejected,
        "missing_assumptions": missing_assumptions,
        "results": results,
    }
