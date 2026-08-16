from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from src.analytics.bank_batch_pipeline import BankM2Context, compute_bank_m2_score
from src.analytics.bank_v47 import estimate_roe_uncertainty
from src.analytics.bank_valuation_pipeline import (
    BankValuationInputs,
    CanonicalBankRow,
    build_quarter_slots,
    run_bank_valuation,
    to_canonical_row,
)
from src.analytics.total_rasyo_score import compute_total_rasyo
from src.ingest.api.kap_financial_facts import KapFinancialFactConfig, KapFinancialFactExtractor
from src.ingest.api.mkk_kap import KapApiProtocolError, KapDisclosureEnvelope
from src.ingest.api.semantic_facts import SemanticFactMapper, SemanticMappingConfig
from src.ingest.bank_fact_materializer import BankDerivationConfig, derive_bank_metrics


class KapBankEndToEndError(ValueError):
    pass


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PUBLICATION_FETCH_SKEW = timedelta(minutes=5)


@dataclass(frozen=True)
class PreparedKapBank:
    ticker: str
    analysis_at: datetime
    anchor_period_end: date
    disclosures: tuple[KapDisclosureEnvelope, ...]
    raw_facts_extracted: int
    semantic_facts_mapped: int
    metric_periods: tuple[date, ...]
    canonical: CanonicalBankRow
    config_lineage: Mapping[str, Any]


@dataclass(frozen=True)
class KapBankEvaluationContext:
    valuation_inputs: BankValuationInputs
    current_price: Any
    price_trade_date: date | None
    other_module_scores: Mapping[str, Any]
    good_count_ge8: Any
    total_weights: Mapping[str, Any] | None = None
    uncertainty_kwargs: Mapping[str, Any] | None = None
    valuation_kwargs: Mapping[str, Any] | None = None


def _canonical_payload_hash(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        raise KapBankEndToEndError("envelope.payload mapping olmali")
    try:
        text = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise KapBankEndToEndError("envelope.payload JSON olarak kanoniklestirilemiyor") from exc
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _envelope_identity(row: KapDisclosureEnvelope) -> tuple[Any, ...]:
    return (
        row.published_at,
        row.ticker,
        row.company_id,
        row.notification_type,
        row.subject,
        row.source_url,
        row.source,
        row.payload_sha256,
    )


def _aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise KapBankEndToEndError(f"{name} timezone iceren datetime olmali")
    return value


def _ticker(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KapBankEndToEndError("ticker dolu metin olmali")
    return value.strip().upper()


def _materialize_envelopes(
    envelopes: Iterable[KapDisclosureEnvelope],
    *,
    ticker: str,
    analysis_at: datetime,
) -> tuple[KapDisclosureEnvelope, ...]:
    try:
        rows = tuple(envelopes)
    except TypeError as exc:
        raise KapBankEndToEndError("envelopes iterable olmali") from exc
    if not rows:
        raise KapBankEndToEndError("en az bir KAP bildirimi gerekli")
    dedup: dict[str, KapDisclosureEnvelope] = {}
    for row in rows:
        if not isinstance(row, KapDisclosureEnvelope):
            raise KapBankEndToEndError("envelopes yalniz KapDisclosureEnvelope icermeli")
        published_at = _aware("envelope.published_at", row.published_at)
        fetched_at = _aware("envelope.fetched_at", row.fetched_at)
        disclosure_id = row.disclosure_id
        if not isinstance(disclosure_id, str) or not disclosure_id.strip():
            raise KapBankEndToEndError("envelope.disclosure_id dolu metin olmali")
        if published_at > fetched_at + _MAX_PUBLICATION_FETCH_SKEW:
            raise KapBankEndToEndError("envelope published_at fetched_at sonrasinda")
        if not isinstance(row.payload_sha256, str) or not _HEX64.fullmatch(row.payload_sha256):
            raise KapBankEndToEndError("envelope.payload_sha256 gecersiz")
        actual_hash = _canonical_payload_hash(row.payload)
        if actual_hash != row.payload_sha256:
            raise KapBankEndToEndError("envelope payload SHA256 ile eslesmiyor")
        if row.ticker is not None and _ticker(row.ticker) != ticker:
            raise KapBankEndToEndError(
                f"tek sirket calismasi farkli ticker iceriyor: {row.ticker} != {ticker}"
            )
        previous = dedup.get(disclosure_id)
        if previous is not None:
            if previous.payload_sha256 != row.payload_sha256:
                raise KapBankEndToEndError(
                    f"ayni disclosure_id farkli payload iceriyor: {disclosure_id}"
                )
            if _envelope_identity(previous) != _envelope_identity(row):
                raise KapBankEndToEndError(
                    f"ayni disclosure_id farkli kimlik iceriyor: {disclosure_id}"
                )
            continue
        dedup[disclosure_id] = row
    usable = tuple(
        sorted(
            (row for row in dedup.values() if row.published_at <= analysis_at),
            key=lambda row: (row.published_at, row.disclosure_id, row.payload_sha256),
        )
    )
    if not usable:
        raise KapBankEndToEndError("analysis_at itibariyla bilinen KAP bildirimi yok")
    return usable


def _validate_configs(
    fact_config: KapFinancialFactConfig,
    semantic_config: SemanticMappingConfig,
    derivation_config: BankDerivationConfig,
) -> None:
    if not isinstance(fact_config, KapFinancialFactConfig):
        raise TypeError("fact_config KapFinancialFactConfig olmali")
    if not isinstance(semantic_config, SemanticMappingConfig):
        raise TypeError("semantic_config SemanticMappingConfig olmali")
    if not isinstance(derivation_config, BankDerivationConfig):
        raise TypeError("derivation_config BankDerivationConfig olmali")
    if semantic_config.mapping_profile != derivation_config.semantic_profile:
        raise KapBankEndToEndError("semantic ve derivation profilleri eslesmiyor")
    if semantic_config.mapping_version != derivation_config.semantic_version:
        raise KapBankEndToEndError("semantic ve derivation surumleri eslesmiyor")


def prepare_kap_bank_end_to_end(
    envelopes: Iterable[KapDisclosureEnvelope],
    *,
    ticker: str,
    analysis_at: datetime,
    anchor_period_end: date,
    fact_config: KapFinancialFactConfig,
    semantic_config: SemanticMappingConfig,
    derivation_config: BankDerivationConfig,
) -> PreparedKapBank:
    """Prepare lossless KAP disclosures into one canonical BANK row."""
    ticker_norm = _ticker(ticker)
    analysis = _aware("analysis_at", analysis_at)
    if not isinstance(anchor_period_end, date) or isinstance(anchor_period_end, datetime):
        raise KapBankEndToEndError("anchor_period_end date olmali")
    _validate_configs(fact_config, semantic_config, derivation_config)

    usable = _materialize_envelopes(envelopes, ticker=ticker_norm, analysis_at=analysis)
    extractor = KapFinancialFactExtractor(fact_config)
    mapper = SemanticFactMapper(semantic_config)
    raw_facts = []
    semantic_facts = []
    for envelope in usable:
        try:
            extracted = extractor.extract(envelope, extracted_at=analysis)
            mapped = mapper.map_facts(extracted, mapped_at=analysis)
        except (KapApiProtocolError, ValueError, ArithmeticError) as exc:
            raise KapBankEndToEndError(
                f"KAP bildirimi islenemedi {envelope.disclosure_id}: {exc}"
            ) from exc
        raw_facts.extend(extracted)
        semantic_facts.extend(mapped)

    metrics = derive_bank_metrics(
        semantic_facts,
        config=derivation_config,
        ticker=ticker_norm,
        analysis_at=analysis,
        anchor_period_end=anchor_period_end,
    )
    metric_by_period = {row.period_end: row for row in metrics}
    if len(metric_by_period) != len(metrics):
        raise KapBankEndToEndError("ayni period_end icin birden fazla turetilmis metric var")

    slot_rows = []
    for idx, period in enumerate(build_quarter_slots(anchor_period_end), start=1):
        metric = metric_by_period.get(period)
        if metric is None:
            slot_rows.append({
                "period_end": period,
                "record_id": None,
                "selected_version_tag": None,
                "selected_published_at": None,
                "selected_version_sequence": None,
                "roe_ttm": None,
                "bvps": None,
                "payout_sus": None,
            })
            continue
        slot_rows.append({
            "period_end": period,
            "record_id": idx,
            "selected_version_tag": metric.version_tag,
            "selected_published_at": metric.published_at,
            "selected_version_sequence": metric.version_sequence,
            "roe_ttm": metric.roe_ttm,
            "bvps": metric.bvps,
            "payout_sus": metric.payout_sus,
        })

    canonical = to_canonical_row(
        slot_rows,
        ticker=ticker_norm,
        analysis_at=analysis,
        anchor_period_end=anchor_period_end,
    )
    return PreparedKapBank(
        ticker=ticker_norm,
        analysis_at=analysis,
        anchor_period_end=anchor_period_end,
        disclosures=usable,
        raw_facts_extracted=len(raw_facts),
        semantic_facts_mapped=len(semantic_facts),
        metric_periods=tuple(row.period_end for row in metrics),
        canonical=canonical,
        config_lineage={
            "fact_mapping_profile": fact_config.mapping_profile,
            "fact_mapping_version": fact_config.mapping_version,
            "semantic_profile": semantic_config.mapping_profile,
            "semantic_version": semantic_config.mapping_version,
            "derivation_profile": derivation_config.derivation_profile,
            "derivation_version": derivation_config.derivation_version,
        },
    )


def evaluate_prepared_kap_bank(
    prepared: PreparedKapBank,
    context: KapBankEvaluationContext,
    *,
    sector_residual_scales: Sequence[Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(prepared, PreparedKapBank):
        raise TypeError("prepared PreparedKapBank olmali")
    if not isinstance(context, KapBankEvaluationContext):
        raise TypeError("context KapBankEvaluationContext olmali")
    if not isinstance(context.valuation_inputs, BankValuationInputs):
        raise TypeError("context.valuation_inputs BankValuationInputs olmali")

    valuation = run_bank_valuation(
        prepared.canonical,
        context.valuation_inputs,
        sector_residual_scales=sector_residual_scales,
        uncertainty_kwargs=context.uncertainty_kwargs,
        valuation_kwargs=context.valuation_kwargs,
    )
    valuation["sector_sample_size"] = 0 if sector_residual_scales is None else len(sector_residual_scales)
    m2 = compute_bank_m2_score(
        valuation,
        BankM2Context(
            current_price=context.current_price,
            price_trade_date=context.price_trade_date,
        ),
    )
    if not isinstance(context.other_module_scores, Mapping):
        raise KapBankEndToEndError("other_module_scores mapping olmali")
    expected_other = {"M1", "M3", "Ek4", "Ek1", "Ek9"}
    if set(context.other_module_scores) != expected_other:
        raise KapBankEndToEndError(
            f"other_module_scores tam olmali: {sorted(expected_other)}"
        )
    total = compute_total_rasyo(
        {"M2": m2["m2_score"], **dict(context.other_module_scores)},
        good_count_ge8=context.good_count_ge8,
        weights=context.total_weights,
    )
    return {
        "ticker": prepared.ticker,
        "analysis_at": prepared.analysis_at,
        "anchor_period_end": prepared.anchor_period_end,
        "disclosures_used": len(prepared.disclosures),
        "disclosure_lineage": [
            {
                "disclosure_id": row.disclosure_id,
                "published_at": row.published_at,
                "payload_sha256": row.payload_sha256,
                "source": row.source,
                "source_url": row.source_url,
            }
            for row in prepared.disclosures
        ],
        "config_lineage": dict(prepared.config_lineage),
        "raw_facts_extracted": prepared.raw_facts_extracted,
        "semantic_facts_mapped": prepared.semantic_facts_mapped,
        "bank_metrics_derived": len(prepared.metric_periods),
        "metric_periods": list(prepared.metric_periods),
        "canonical": asdict(prepared.canonical),
        "valuation": valuation,
        "m2": m2,
        "total_rasyo": total,
    }


def _residual_scale(prepared: PreparedKapBank) -> float | None:
    uncertainty = estimate_roe_uncertainty(prepared.canonical.roe_series)
    if int(uncertainty.get("n_valid", 0)) < 4:
        return None
    raw = uncertainty.get("sd_roe_residual")
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if value < 0 or value != value or value in {float("inf"), float("-inf")}:
        return None
    return value


def evaluate_kap_bank_batch_end_to_end(
    envelopes: Iterable[KapDisclosureEnvelope],
    *,
    analysis_at: datetime,
    anchor_period_end: date,
    fact_config: KapFinancialFactConfig,
    semantic_config: SemanticMappingConfig,
    derivation_config: BankDerivationConfig,
    contexts: Mapping[str, KapBankEvaluationContext],
    requested_tickers: Sequence[str] | None = None,
    continue_on_error: bool = True,
) -> dict[str, Any]:
    """Evaluate many BANK tickers with leave-one-out sector uncertainty floors."""
    analysis = _aware("analysis_at", analysis_at)
    if not isinstance(anchor_period_end, date) or isinstance(anchor_period_end, datetime):
        raise KapBankEndToEndError("anchor_period_end date olmali")
    _validate_configs(fact_config, semantic_config, derivation_config)
    if type(continue_on_error) is not bool:
        raise KapBankEndToEndError("continue_on_error Python bool olmali")
    if not isinstance(contexts, Mapping):
        raise KapBankEndToEndError("contexts mapping olmali")

    normalized_contexts: dict[str, KapBankEvaluationContext] = {}
    for raw_ticker, context in contexts.items():
        ticker = _ticker(raw_ticker)
        if ticker in normalized_contexts:
            raise KapBankEndToEndError(f"tekrarlanan context ticker: {ticker}")
        if not isinstance(context, KapBankEvaluationContext):
            raise KapBankEndToEndError(f"context gecersiz: {ticker}")
        normalized_contexts[ticker] = context

    if requested_tickers is None:
        requested = tuple(sorted(normalized_contexts))
        if not requested:
            raise KapBankEndToEndError("contexts dolu mapping olmali")
    else:
        if isinstance(requested_tickers, (str, bytes, bytearray)):
            raise KapBankEndToEndError("requested_tickers iterable olmali; tek metin olamaz")
        requested_rows: list[str] = []
        requested_seen: set[str] = set()
        try:
            iterator = iter(requested_tickers)
        except TypeError as exc:
            raise KapBankEndToEndError("requested_tickers iterable olmali") from exc
        for raw_ticker in iterator:
            ticker = _ticker(raw_ticker)
            if ticker not in requested_seen:
                requested_rows.append(ticker)
                requested_seen.add(ticker)
        if not requested_rows:
            raise KapBankEndToEndError("requested_tickers bos olamaz")
        unexpected_contexts = set(normalized_contexts) - requested_seen
        if unexpected_contexts:
            raise KapBankEndToEndError(
                f"requested_tickers disinda context var: {sorted(unexpected_contexts)}"
            )
        requested = tuple(sorted(requested_seen))

    try:
        rows = tuple(envelopes)
    except TypeError as exc:
        raise KapBankEndToEndError("envelopes iterable olmali") from exc
    if not rows:
        raise KapBankEndToEndError("en az bir KAP bildirimi gerekli")
    grouped: dict[str, list[KapDisclosureEnvelope]] = {ticker: [] for ticker in requested}
    identities: dict[str, tuple[Any, ...]] = {}
    for row in rows:
        if not isinstance(row, KapDisclosureEnvelope):
            raise KapBankEndToEndError("envelopes yalniz KapDisclosureEnvelope icermeli")
        if not isinstance(row.disclosure_id, str) or not row.disclosure_id.strip():
            raise KapBankEndToEndError("batch disclosure_id dolu metin olmali")
        if row.ticker is None:
            raise KapBankEndToEndError("batch bildirimlerinde ticker zorunlu")
        ticker = _ticker(row.ticker)
        if ticker not in grouped:
            raise KapBankEndToEndError(f"context disi ticker bildirimi: {ticker}")
        identity = _envelope_identity(row)
        previous = identities.get(row.disclosure_id)
        if previous is not None and previous != identity:
            raise KapBankEndToEndError(
                f"ayni disclosure_id batch icinde farkli kimlik iceriyor: {row.disclosure_id}"
            )
        identities[row.disclosure_id] = identity
        grouped[ticker].append(row)

    prepared: dict[str, PreparedKapBank] = {}
    rejections: dict[str, str] = {}
    for ticker in requested:
        try:
            prepared[ticker] = prepare_kap_bank_end_to_end(
                grouped[ticker],
                ticker=ticker,
                analysis_at=analysis,
                anchor_period_end=anchor_period_end,
                fact_config=fact_config,
                semantic_config=semantic_config,
                derivation_config=derivation_config,
            )
        except (KapBankEndToEndError, TypeError, ValueError, ArithmeticError) as exc:
            if not continue_on_error:
                raise
            rejections[ticker] = str(exc)

    scales = {ticker: _residual_scale(value) for ticker, value in prepared.items()}
    results: list[dict[str, Any]] = []
    for ticker in sorted(prepared):
        context = normalized_contexts.get(ticker)
        if context is None:
            rejections[ticker] = "EVALUATION_CONTEXT_MISSING"
            continue
        sector_scales = [
            scale for other, scale in scales.items()
            if other != ticker and scale is not None
        ]
        try:
            result = evaluate_prepared_kap_bank(
                prepared[ticker],
                context,
                sector_residual_scales=sector_scales,
            )
            result["sector_scale_rejected_tickers"] = sorted(
                other for other, scale in scales.items() if scale is None
            )
            results.append(result)
        except (KapBankEndToEndError, TypeError, ValueError, ArithmeticError) as exc:
            if not continue_on_error:
                raise
            rejections[ticker] = str(exc)

    ranking_source = sorted(
        results,
        key=lambda row: (-float(row["total_rasyo"]["total_rasyo_100"]), row["ticker"]),
    )
    ranking = [
        {
            "rank": idx,
            "ticker": row["ticker"],
            "total_rasyo_100": row["total_rasyo"]["total_rasyo_100"],
            "decision": row["total_rasyo"]["decision"],
            "m2_score": row["m2"]["m2_score"],
            "v_conf": row["valuation"].get("v_conf"),
            "valuation_status": row["valuation"].get("status"),
        }
        for idx, row in enumerate(ranking_source, start=1)
    ]
    requested_count = len(requested)
    result_count = len(results)
    status = "COMPLETE" if result_count == requested_count else ("PARTIAL" if result_count else "FAILED")
    return {
        "status": status,
        "analysis_at": analysis,
        "anchor_period_end": anchor_period_end,
        "requested_count": requested_count,
        "prepared_count": len(prepared),
        "result_count": result_count,
        "rejected_count": len(rejections),
        "sector_scale_eligible_count": sum(value is not None for value in scales.values()),
        "valuation_ok_count": sum(row["valuation"].get("status") == "OK" for row in results),
        "results": sorted(results, key=lambda row: row["ticker"]),
        "ranking": ranking,
        "rejections": [
            {"ticker": ticker, "reason": rejections[ticker]}
            for ticker in sorted(rejections)
        ],
    }


def evaluate_kap_bank_end_to_end(
    envelopes: Iterable[KapDisclosureEnvelope],
    *,
    ticker: str,
    analysis_at: datetime,
    anchor_period_end: date,
    fact_config: KapFinancialFactConfig,
    semantic_config: SemanticMappingConfig,
    derivation_config: BankDerivationConfig,
    valuation_inputs: BankValuationInputs,
    current_price: Any,
    price_trade_date: date | None,
    other_module_scores: Mapping[str, Any],
    good_count_ge8: Any,
    sector_residual_scales: Sequence[Any] | None = None,
    total_weights: Mapping[str, Any] | None = None,
    uncertainty_kwargs: Mapping[str, Any] | None = None,
    valuation_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure end-to-end BANK run from lossless KAP envelopes to Total Rasyo."""
    prepared = prepare_kap_bank_end_to_end(
        envelopes,
        ticker=ticker,
        analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
        fact_config=fact_config,
        semantic_config=semantic_config,
        derivation_config=derivation_config,
    )
    return evaluate_prepared_kap_bank(
        prepared,
        KapBankEvaluationContext(
            valuation_inputs=valuation_inputs,
            current_price=current_price,
            price_trade_date=price_trade_date,
            other_module_scores=other_module_scores,
            good_count_ge8=good_count_ge8,
            total_weights=total_weights,
            uncertainty_kwargs=uncertainty_kwargs,
            valuation_kwargs=valuation_kwargs,
        ),
        sector_residual_scales=sector_residual_scales,
    )
