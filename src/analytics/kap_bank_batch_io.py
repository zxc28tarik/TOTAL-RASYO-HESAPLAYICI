from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping

from src.analytics.bank_valuation_pipeline import BankValuationInputs
from src.analytics.kap_bank_end_to_end import (
    KapBankEndToEndError,
    KapBankEvaluationContext,
    evaluate_kap_bank_batch_end_to_end,
)
from src.ingest.api.kap_financial_facts import KapFinancialFactConfig
from src.ingest.api.mkk_kap import KapDisclosureEnvelope
from src.ingest.api.semantic_facts import SemanticMappingConfig
from src.ingest.bank_fact_materializer import BankDerivationConfig


class KapBankBatchIoError(ValueError):
    pass


def _aware(name: str, value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise KapBankBatchIoError(f"{name} dolu ISO-8601 metni olmali")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise KapBankBatchIoError(f"{name} gecersiz ISO-8601 zaman damgasi") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KapBankBatchIoError(f"{name} timezone icermeli")
    return parsed


def _date(name: str, value: Any, *, allow_none: bool = False) -> date | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise KapBankBatchIoError(f"{name} ISO tarih metni olmali")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise KapBankBatchIoError(f"{name} gecersiz ISO tarih") from exc




def _required_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KapBankBatchIoError(f"{name} dolu metin olmali")
    return value.strip()


def _optional_text(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise KapBankBatchIoError(f"{name} metin veya null olmali")
    text = value.strip()
    return text or None

def _mapping(name: str, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise KapBankBatchIoError(f"{name} mapping olmali")
    return value


def load_disclosures_jsonl(path: str | Path) -> tuple[KapDisclosureEnvelope, ...]:
    source = Path(path)
    if not source.is_file():
        raise KapBankBatchIoError(f"bildirim dosyasi bulunamadi: {source}")
    rows: list[KapDisclosureEnvelope] = []
    for line_no, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            raw = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise KapBankBatchIoError(f"JSONL satir {line_no} gecersiz JSON") from exc
        data = _mapping(f"JSONL satir {line_no}", raw)
        required = {"disclosure_id", "published_at", "payload", "payload_sha256", "fetched_at"}
        missing = required - set(data)
        if missing:
            raise KapBankBatchIoError(
                f"JSONL satir {line_no} eksik alanlar: {sorted(missing)}"
            )
        payload = _mapping(f"JSONL satir {line_no}.payload", data["payload"])
        rows.append(KapDisclosureEnvelope(
            disclosure_id=_required_text(f"satir {line_no}.disclosure_id", data["disclosure_id"]),
            published_at=_aware(f"satir {line_no}.published_at", data["published_at"]),
            ticker=_optional_text(f"satir {line_no}.ticker", data.get("ticker")),
            company_id=_optional_text(f"satir {line_no}.company_id", data.get("company_id")),
            notification_type=_optional_text(f"satir {line_no}.notification_type", data.get("notification_type")),
            subject=_optional_text(f"satir {line_no}.subject", data.get("subject")),
            source_url=_optional_text(f"satir {line_no}.source_url", data.get("source_url")),
            payload=payload,
            payload_sha256=_required_text(f"satir {line_no}.payload_sha256", data["payload_sha256"]),
            fetched_at=_aware(f"satir {line_no}.fetched_at", data["fetched_at"]),
            source=_required_text(f"satir {line_no}.source", data.get("source", "MKK_KAP_API")),
        ))
    if not rows:
        raise KapBankBatchIoError("bildirim JSONL dosyasi bos")
    return tuple(rows)


def load_batch_contexts_json(path: str | Path) -> dict[str, KapBankEvaluationContext]:
    source = Path(path)
    if not source.is_file():
        raise KapBankBatchIoError(f"context dosyasi bulunamadi: {source}")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KapBankBatchIoError("context dosyasi gecersiz JSON") from exc
    root = _mapping("contexts", raw)
    if not root:
        raise KapBankBatchIoError("contexts bos olamaz")
    result: dict[str, KapBankEvaluationContext] = {}
    for ticker, value in root.items():
        if not isinstance(ticker, str) or not ticker.strip():
            raise KapBankBatchIoError("context ticker anahtarlari dolu metin olmali")
        row = _mapping(f"contexts.{ticker}", value)
        valuation = _mapping(f"contexts.{ticker}.valuation_inputs", row.get("valuation_inputs"))
        required_valuation = {"coe", "macro_cap"}
        missing = required_valuation - set(valuation)
        if missing:
            raise KapBankBatchIoError(
                f"contexts.{ticker}.valuation_inputs eksik: {sorted(missing)}"
            )
        other_scores = _mapping(
            f"contexts.{ticker}.other_module_scores", row.get("other_module_scores")
        )
        result[ticker] = KapBankEvaluationContext(
            valuation_inputs=BankValuationInputs(
                coe=valuation["coe"],
                macro_cap=valuation["macro_cap"],
                tier_cap=valuation.get("tier_cap", 0.80),
                payout_missing_factor=valuation.get("payout_missing_factor", 0.70),
                band_width_shadow_mode=valuation.get("band_width_shadow_mode", True),
                max_halfwidth=valuation.get("max_halfwidth", 0.80),
            ),
            current_price=row.get("current_price"),
            price_trade_date=_date(
                f"contexts.{ticker}.price_trade_date",
                row.get("price_trade_date"),
                allow_none=True,
            ),
            other_module_scores=dict(other_scores),
            good_count_ge8=row.get("good_count_ge8"),
            total_weights=row.get("total_weights"),
            uncertainty_kwargs=row.get("uncertainty_kwargs"),
            valuation_kwargs=row.get("valuation_kwargs"),
        )
    return result


def json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((json_safe(item) for item in value), key=lambda item: repr(item))
    return value


def run_batch_preview_from_files(
    *,
    disclosures_path: str | Path,
    contexts_path: str | Path,
    analysis_at: datetime,
    anchor_period_end: date,
    fact_config_path: str | Path,
    semantic_config_path: str | Path,
    derivation_config_path: str | Path,
    continue_on_error: bool = True,
) -> dict[str, Any]:
    try:
        return evaluate_kap_bank_batch_end_to_end(
            load_disclosures_jsonl(disclosures_path),
            analysis_at=analysis_at,
            anchor_period_end=anchor_period_end,
            fact_config=KapFinancialFactConfig.from_json_file(str(fact_config_path)),
            semantic_config=SemanticMappingConfig.from_json_file(str(semantic_config_path)),
            derivation_config=BankDerivationConfig.from_json_file(str(derivation_config_path)),
            contexts=load_batch_contexts_json(contexts_path),
            continue_on_error=continue_on_error,
        )
    except (KapBankEndToEndError, TypeError, ValueError, ArithmeticError) as exc:
        if isinstance(exc, KapBankBatchIoError):
            raise
        raise KapBankBatchIoError(str(exc)) from exc
