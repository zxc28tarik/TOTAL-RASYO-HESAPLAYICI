from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from src.analytics.bank_batch_pipeline import (
    ISTANBUL_TZ,
    BankM2Context,
    ResolvedBankAssumption,
    daily_price_cutoff_date,
    fetch_active_bank_tickers,
    fetch_bank_m2_contexts,
    resolve_bank_assumptions,
)
from src.analytics.bank_valuation_pipeline import BankValuationInputs
from src.analytics.kap_bank_batch_persistence import (
    PersistedKapBankBatch,
    persist_kap_bank_batch_report,
)
from src.analytics.kap_bank_end_to_end import (
    KapBankEvaluationContext,
    evaluate_kap_bank_batch_end_to_end,
)
from src.analytics.total_rasyo_score import normalize_weights
from src.ingest.api.kap_financial_facts import KapFinancialFactConfig
from src.ingest.api.mkk_kap import KapDisclosureEnvelope
from src.ingest.api.semantic_facts import SemanticMappingConfig
from src.ingest.bank_fact_materializer import BankDerivationConfig


class KapBankDatabaseWorkflowError(ValueError):
    pass


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_OTHER_MODULE_KEYS = ("M1", "M3", "Ek4", "Ek1", "Ek9")


@dataclass(frozen=True)
class DatabaseBankModuleContext:
    other_module_scores: Mapping[str, float]
    good_count_ge8: int
    source_asof_date: date
    source_analysis_at: datetime | None


@dataclass(frozen=True)
class KapBankDatabaseBatchResult:
    report: Mapping[str, Any]
    persistence: PersistedKapBankBatch | None
    tickers: tuple[str, ...]
    disclosures_loaded: int
    context_ready_count: int
    context_rejections: Mapping[str, str]


def _aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise KapBankDatabaseWorkflowError(f"{name} timezone iceren datetime olmali")
    return value


def _date(name: str, value: Any) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise KapBankDatabaseWorkflowError(f"{name} date olmali")
    return value


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KapBankDatabaseWorkflowError(f"{name} dolu metin olmali")
    return value.strip()


def _ticker(value: Any) -> str:
    ticker = _text("ticker", value).upper()
    if not re.fullmatch(r"[A-Z0-9._-]{1,32}", ticker):
        raise KapBankDatabaseWorkflowError(f"gecersiz ticker: {ticker!r}")
    return ticker


def _normalize_tickers(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise KapBankDatabaseWorkflowError("tickers iterable olmali; tek metin olamaz")
    result: list[str] = []
    seen: set[str] = set()
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise KapBankDatabaseWorkflowError("tickers iterable olmali") from exc
    for value in iterator:
        ticker = _ticker(value)
        if ticker not in seen:
            result.append(ticker)
            seen.add(ticker)
    if not result:
        raise KapBankDatabaseWorkflowError("en az bir BANK ticker gerekli")
    return tuple(sorted(result))


def _is_bool_like(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    try:
        import numpy as np  # type: ignore
    except ImportError:
        np = None
    if np is not None and isinstance(value, np.bool_):
        return True
    dtype = getattr(value, "dtype", None)
    return getattr(dtype, "kind", None) == "b"


def _positive_int(name: str, value: Any) -> int:
    if _is_bool_like(value) or not isinstance(value, int) or value <= 0:
        raise KapBankDatabaseWorkflowError(f"{name} pozitif Python int olmali")
    return value


def _payload_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise KapBankDatabaseWorkflowError("raw.kap_disclosures payload gecersiz JSON") from exc
    if not isinstance(value, Mapping):
        raise KapBankDatabaseWorkflowError("raw.kap_disclosures payload object olmali")
    return dict(value)


def _optional_text(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise KapBankDatabaseWorkflowError(f"{name} metin veya null olmali")
    text = value.strip()
    return text or None


def _finite_score(name: str, value: Any) -> float:
    if _is_bool_like(value):
        raise KapBankDatabaseWorkflowError(f"{name} bool olamaz")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise KapBankDatabaseWorkflowError(f"{name} sayiya cevrilemedi") from exc
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise KapBankDatabaseWorkflowError(f"{name} [0,1] araliginda sonlu olmali")
    return parsed


def fetch_kap_bank_disclosures(
    conn: Any,
    *,
    tickers: Sequence[str],
    analysis_at: datetime,
    source: str = "MKK_KAP_API",
    notification_type: str | None = "FINANCIAL_STATEMENT",
) -> tuple[KapDisclosureEnvelope, ...]:
    """Load immutable raw KAP envelopes for requested BANK tickers point-in-time."""
    ticker_list = _normalize_tickers(tickers)
    analysis = _aware("analysis_at", analysis_at)
    source_text = _text("source", source)
    notification = None if notification_type is None else _text("notification_type", notification_type)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source, disclosure_id, published_at, upper(ticker) AS ticker,
                   company_id, notification_type, subject, source_url,
                   payload, payload_sha256, fetched_at
            FROM raw.kap_disclosures
            WHERE source = %(source)s
              AND upper(ticker) = ANY(%(tickers)s)
              AND published_at <= %(analysis_at)s
              AND (%(notification_type)s IS NULL OR notification_type = %(notification_type)s)
            ORDER BY upper(ticker), published_at, disclosure_id, payload_sha256
            """,
            {
                "source": source_text,
                "tickers": list(ticker_list),
                "analysis_at": analysis,
                "notification_type": notification,
            },
        )
        names = [item[0] for item in cur.description]
        rows = [dict(zip(names, row)) for row in cur.fetchall()]

    envelopes: list[KapDisclosureEnvelope] = []
    requested = set(ticker_list)
    identities: dict[tuple[str, str], tuple[Any, ...]] = {}
    for index, row in enumerate(rows):
        ticker = _ticker(row.get("ticker"))
        if ticker not in requested:
            raise KapBankDatabaseWorkflowError(
                f"raw KAP sorgusu beklenmeyen ticker dondurdu: {ticker}"
            )
        row_source = _text(f"disclosures[{index}].source", row.get("source"))
        disclosure_id = _text(
            f"disclosures[{index}].disclosure_id", row.get("disclosure_id")
        )
        published_at = _aware(
            f"disclosures[{index}].published_at", row.get("published_at")
        )
        fetched_at = _aware(f"disclosures[{index}].fetched_at", row.get("fetched_at"))
        if published_at > analysis:
            raise KapBankDatabaseWorkflowError("raw KAP sorgusu gelecekteki bildirim dondurdu")
        payload_sha = _text(
            f"disclosures[{index}].payload_sha256", row.get("payload_sha256")
        )
        if not _HEX64.fullmatch(payload_sha):
            raise KapBankDatabaseWorkflowError("raw KAP payload_sha256 gecersiz")
        envelope = KapDisclosureEnvelope(
            disclosure_id=disclosure_id,
            published_at=published_at,
            ticker=ticker,
            company_id=_optional_text(
                f"disclosures[{index}].company_id", row.get("company_id")
            ),
            notification_type=_optional_text(
                f"disclosures[{index}].notification_type", row.get("notification_type")
            ),
            subject=_optional_text(f"disclosures[{index}].subject", row.get("subject")),
            source_url=_optional_text(
                f"disclosures[{index}].source_url", row.get("source_url")
            ),
            payload=_payload_mapping(row.get("payload")),
            payload_sha256=payload_sha,
            fetched_at=fetched_at,
            source=row_source,
        )
        key = (row_source, disclosure_id)
        identity = (
            envelope.published_at,
            envelope.ticker,
            envelope.payload_sha256,
            envelope.fetched_at,
        )
        previous = identities.get(key)
        if previous is not None and previous != identity:
            raise KapBankDatabaseWorkflowError(
                f"raw KAP ayni kaynak/id icin farkli kimlik dondurdu: {key}"
            )
        if previous is None:
            envelopes.append(envelope)
            identities[key] = identity
    return tuple(envelopes)


def resolve_kap_bank_anchor_period_end(
    conn: Any,
    *,
    tickers: Sequence[str],
    analysis_at: datetime,
    fact_config: KapFinancialFactConfig,
) -> date:
    """Resolve one common latest period from point-in-time extracted KAP facts."""
    ticker_list = _normalize_tickers(tickers)
    analysis = _aware("analysis_at", analysis_at)
    if not isinstance(fact_config, KapFinancialFactConfig):
        raise KapBankDatabaseWorkflowError("fact_config KapFinancialFactConfig olmali")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT max(period_end)
            FROM raw.kap_financial_facts
            WHERE upper(ticker) = ANY(%(tickers)s)
              AND published_at <= %(analysis_at)s
              AND mapping_profile = %(mapping_profile)s
              AND mapping_version = %(mapping_version)s
            """,
            {
                "tickers": list(ticker_list),
                "analysis_at": analysis,
                "mapping_profile": fact_config.mapping_profile,
                "mapping_version": fact_config.mapping_version,
            },
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        raise KapBankDatabaseWorkflowError(
            "point-in-time KAP fact anchor bulunamadi; once extract-kap-facts calistirilmali"
        )
    return _date("anchor_period_end", row[0])


def fetch_bank_module_contexts(
    conn: Any,
    *,
    tickers: Sequence[str],
    analysis_at: datetime,
    horizon_days: int,
    max_context_age_days: int = 7,
) -> tuple[dict[str, DatabaseBankModuleContext], dict[str, str]]:
    """Load non-M2 module inputs without allowing later intraday rows to leak."""
    ticker_list = _normalize_tickers(tickers)
    analysis = _aware("analysis_at", analysis_at)
    horizon = _positive_int("horizon_days", horizon_days)
    max_age = _positive_int("max_context_age_days", max_context_age_days)
    context_asof = daily_price_cutoff_date(analysis)
    local_date = analysis.astimezone(ISTANBUL_TZ).date()
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH candidates AS (
              SELECT ms.*,
                     row_number() OVER (
                       PARTITION BY ms.ticker
                       ORDER BY ms.asof_date DESC,
                                ms.analysis_at DESC NULLS LAST,
                                ms.period_end DESC NULLS LAST
                     ) AS rn
              FROM analytics.module_scores ms
              WHERE upper(ms.ticker) = ANY(%(tickers)s)
                AND ms.horizon_days = %(horizon_days)s
                AND ms.asof_date <= %(context_asof)s
                AND ms.asof_date >= %(context_asof)s - %(max_context_age_days)s
                AND (
                  (ms.analysis_at IS NOT NULL AND ms.analysis_at <= %(analysis_at)s)
                  OR (
                    ms.analysis_at IS NULL
                    AND ms.asof_date < %(local_date)s
                  )
                )
            )
            SELECT upper(ticker) AS ticker, asof_date, analysis_at,
                   m1, m3, ek4, ek1, ek9, good_count_ge8
            FROM candidates
            WHERE rn = 1
            ORDER BY upper(ticker)
            """,
            {
                "tickers": list(ticker_list),
                "horizon_days": horizon,
                "context_asof": context_asof,
                "max_context_age_days": max_age,
                "analysis_at": analysis,
                "local_date": local_date,
            },
        )
        names = [item[0] for item in cur.description]
        rows = [dict(zip(names, row)) for row in cur.fetchall()]

    contexts: dict[str, DatabaseBankModuleContext] = {}
    rejections: dict[str, str] = {}
    by_ticker: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        ticker = _ticker(row["ticker"])
        if ticker in by_ticker:
            raise KapBankDatabaseWorkflowError(
                f"module context sorgusu tekrarlanan ticker dondurdu: {ticker}"
            )
        by_ticker[ticker] = row
    for ticker in ticker_list:
        row = by_ticker.get(ticker)
        if row is None:
            rejections[ticker] = (
                f"NON_M2_MODULE_CONTEXT_MISSING asof={context_asof.isoformat()} horizon={horizon}"
            )
            continue
        try:
            source_asof = _date(f"{ticker}.source_asof_date", row["asof_date"])
            age_days = (context_asof - source_asof).days
            if age_days < 0 or age_days > max_age:
                raise KapBankDatabaseWorkflowError(
                    f"module context yasi gecersiz: {age_days} gun"
                )
            scores = {
                key: _finite_score(f"{ticker}.{key}", row[key.lower()])
                for key in _OTHER_MODULE_KEYS
            }
            raw_good = row.get("good_count_ge8")
            if _is_bool_like(raw_good):
                raise KapBankDatabaseWorkflowError("good_count_ge8 bool olamaz")
            good_count = int(raw_good)
            if float(raw_good) != float(good_count) or good_count < 0:
                raise KapBankDatabaseWorkflowError("good_count_ge8 negatif olmayan tam sayi olmali")
            source_analysis = row.get("analysis_at")
            if source_analysis is not None:
                source_analysis = _aware(f"{ticker}.source_analysis_at", source_analysis)
                if source_analysis > analysis:
                    raise KapBankDatabaseWorkflowError("gelecekteki module_scores kaydi")
            contexts[ticker] = DatabaseBankModuleContext(
                other_module_scores=scores,
                good_count_ge8=good_count,
                source_asof_date=source_asof,
                source_analysis_at=source_analysis,
            )
        except (KapBankDatabaseWorkflowError, TypeError, ValueError, OverflowError) as exc:
            rejections[ticker] = f"NON_M2_MODULE_CONTEXT_INVALID: {exc}"
    return contexts, rejections


def _build_database_bank_contexts_with_lineage(
    conn: Any,
    *,
    tickers: Sequence[str],
    analysis_at: datetime,
    horizon_days: int,
    total_weights: Mapping[str, Any] | None = None,
    max_context_age_days: int = 7,
) -> tuple[
    dict[str, KapBankEvaluationContext],
    dict[str, str],
    dict[str, Mapping[str, Any]],
]:
    ticker_list = _normalize_tickers(tickers)
    analysis = _aware("analysis_at", analysis_at)
    weights = normalize_weights(total_weights)
    assumptions, missing_assumptions = resolve_bank_assumptions(
        conn, tickers=ticker_list, analysis_at=analysis
    )
    price_contexts = fetch_bank_m2_contexts(
        conn, tickers=ticker_list, analysis_at=analysis
    )
    module_contexts, module_rejections = fetch_bank_module_contexts(
        conn,
        tickers=ticker_list,
        analysis_at=analysis,
        horizon_days=horizon_days,
        max_context_age_days=max_context_age_days,
    )
    rejections = dict(module_rejections)
    for ticker in missing_assumptions:
        rejections[ticker] = "POINT_IN_TIME_ASSUMPTION_MISSING"

    contexts: dict[str, KapBankEvaluationContext] = {}
    lineage: dict[str, Mapping[str, Any]] = {}
    for ticker in ticker_list:
        assumption: ResolvedBankAssumption | None = assumptions.get(ticker)
        module = module_contexts.get(ticker)
        if assumption is None or module is None:
            continue
        price = price_contexts.get(ticker) or BankM2Context(current_price=None)
        contexts[ticker] = KapBankEvaluationContext(
            valuation_inputs=BankValuationInputs(**asdict(assumption.inputs)),
            current_price=price.current_price,
            price_trade_date=price.price_trade_date,
            other_module_scores=dict(module.other_module_scores),
            good_count_ge8=module.good_count_ge8,
            total_weights=weights,
        )
        lineage[ticker] = {
            "assumption": {
                "scope_type": assumption.scope_type,
                "scope_code": assumption.scope_code,
                "effective_at": assumption.effective_at,
                "source": assumption.source,
                "risk_free_rate": assumption.risk_free_rate,
                "coe": assumption.inputs.coe,
                "macro_cap": assumption.inputs.macro_cap,
                "metadata": dict(assumption.metadata),
            },
            "non_m2_modules": {
                "source_asof_date": module.source_asof_date,
                "source_analysis_at": module.source_analysis_at,
                "scores": dict(module.other_module_scores),
                "good_count_ge8": module.good_count_ge8,
            },
            "price": {
                "trade_date": price.price_trade_date,
                "current_price": price.current_price,
                "source": price.price_source,
            },
        }
    return contexts, rejections, lineage


def build_database_bank_contexts(
    conn: Any,
    *,
    tickers: Sequence[str],
    analysis_at: datetime,
    horizon_days: int,
    total_weights: Mapping[str, Any] | None = None,
    max_context_age_days: int = 7,
) -> tuple[dict[str, KapBankEvaluationContext], dict[str, str]]:
    contexts, rejections, _ = _build_database_bank_contexts_with_lineage(
        conn,
        tickers=tickers,
        analysis_at=analysis_at,
        horizon_days=horizon_days,
        total_weights=total_weights,
        max_context_age_days=max_context_age_days,
    )
    return contexts, rejections


def _replace_context_rejections(
    report: Mapping[str, Any], context_rejections: Mapping[str, str]
) -> dict[str, Any]:
    result = dict(report)
    rows = []
    seen: set[str] = set()
    for raw in report.get("rejections", []):
        row = dict(raw)
        ticker = _ticker(row.get("ticker"))
        if ticker in context_rejections:
            row["reason"] = context_rejections[ticker]
        rows.append(row)
        seen.add(ticker)
    for ticker in sorted(context_rejections):
        if ticker not in seen:
            rows.append({"ticker": ticker, "reason": context_rejections[ticker]})
    rows.sort(key=lambda row: row["ticker"])
    result["rejections"] = rows
    result["rejected_count"] = len(rows)
    requested = int(result["requested_count"])
    result_count = int(result["result_count"])
    result["status"] = "COMPLETE" if result_count == requested else (
        "PARTIAL" if result_count else "FAILED"
    )
    return result


def run_kap_bank_database_batch(
    conn: Any,
    *,
    analysis_at: datetime,
    fact_config: KapFinancialFactConfig,
    semantic_config: SemanticMappingConfig,
    derivation_config: BankDerivationConfig,
    anchor_period_end: date | None = None,
    tickers: Sequence[str] | None = None,
    total_weights: Mapping[str, Any] | None = None,
    source: str = "MKK_KAP_API",
    notification_type: str | None = "FINANCIAL_STATEMENT",
    horizon_days: int = 63,
    max_context_age_days: int = 7,
    pipeline_version: str = "KAP_BANK_DB_BATCH_V8",
    batch_source: str = "RAW_KAP_DATABASE",
    continue_on_error: bool = True,
    persist: bool = True,
) -> KapBankDatabaseBatchResult:
    """Evaluate all active BANKs directly from PostgreSQL raw KAP storage."""
    analysis = _aware("analysis_at", analysis_at)
    if type(continue_on_error) is not bool or type(persist) is not bool:
        raise KapBankDatabaseWorkflowError("continue_on_error ve persist Python bool olmali")
    horizon = _positive_int("horizon_days", horizon_days)
    pipeline = _text("pipeline_version", pipeline_version)
    batch_source_text = _text("batch_source", batch_source)
    ticker_list = _normalize_tickers(
        fetch_active_bank_tickers(conn) if tickers is None else tickers
    )
    anchor = (
        resolve_kap_bank_anchor_period_end(
            conn,
            tickers=ticker_list,
            analysis_at=analysis,
            fact_config=fact_config,
        )
        if anchor_period_end is None
        else _date("anchor_period_end", anchor_period_end)
    )
    envelopes = fetch_kap_bank_disclosures(
        conn,
        tickers=ticker_list,
        analysis_at=analysis,
        source=source,
        notification_type=notification_type,
    )
    contexts, context_rejections, context_lineage = _build_database_bank_contexts_with_lineage(
        conn,
        tickers=ticker_list,
        analysis_at=analysis,
        horizon_days=horizon,
        total_weights=total_weights,
        max_context_age_days=max_context_age_days,
    )
    report = evaluate_kap_bank_batch_end_to_end(
        envelopes,
        analysis_at=analysis,
        anchor_period_end=anchor,
        fact_config=fact_config,
        semantic_config=semantic_config,
        derivation_config=derivation_config,
        contexts=contexts,
        requested_tickers=ticker_list,
        continue_on_error=continue_on_error,
    )
    report = _replace_context_rejections(report, context_rejections)
    for result in report.get("results", []):
        ticker = result["ticker"]
        trace = dict(context_lineage.get(ticker) or {})
        result["database_context_lineage"] = trace
        result["config_lineage"] = {
            **dict(result.get("config_lineage") or {}),
            "database_context": trace,
        }
        assumption_trace = dict(trace.get("assumption") or {})
        result["valuation"]["assumption"] = assumption_trace
        result["valuation"]["sector_asof_cutoff"] = analysis
    saved = None
    if persist:
        saved = persist_kap_bank_batch_report(
            conn,
            report,
            horizon_days=horizon,
            pipeline_version=pipeline,
            source=batch_source_text,
        )
    return KapBankDatabaseBatchResult(
        report=report,
        persistence=saved,
        tickers=ticker_list,
        disclosures_loaded=len(envelopes),
        context_ready_count=len(contexts),
        context_rejections=dict(context_rejections),
    )
