from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from src.analytics.bank_batch_pipeline import ISTANBUL_TZ, persist_bank_m2_score
from src.analytics.bank_valuation_pipeline import persist_bank_valuation
from src.analytics.kap_bank_batch_io import json_safe
from src.analytics.total_rasyo_score import MODULE_KEYS, total_rasyo_decision


class KapBankBatchPersistenceError(ValueError):
    pass


@dataclass(frozen=True)
class PersistedKapBankBatch:
    run_key: str
    status: str
    results_written: int
    rejections_written: int
    ranking_written: int
    module_scores_written: int


class _CursorConnectionProxy:
    """Run legacy single-row persistors inside one caller-owned transaction."""

    def __init__(self, cursor: Any):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> bool:
        return False

    def cursor(self):
        cursor = self._cursor

        class _CursorContext:
            def __enter__(self):
                return cursor

            def __exit__(self, *_: Any) -> bool:
                return False

        return _CursorContext()


def _aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise KapBankBatchPersistenceError(f"{name} timezone iceren datetime olmali")
    return value


def _date(name: str, value: Any) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise KapBankBatchPersistenceError(f"{name} date olmali")
    return value


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KapBankBatchPersistenceError(f"{name} dolu metin olmali")
    return value.strip()


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


def _python_int(name: str, value: Any, *, minimum: int = 0) -> int:
    if _is_bool_like(value) or not isinstance(value, int) or value < minimum:
        raise KapBankBatchPersistenceError(f"{name} >= {minimum} Python int olmali")
    return value


def _finite(name: str, value: Any, *, minimum: float, maximum: float) -> float:
    if _is_bool_like(value):
        raise KapBankBatchPersistenceError(f"{name} bool olamaz")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise KapBankBatchPersistenceError(f"{name} sayiya cevrilemedi") from exc
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        raise KapBankBatchPersistenceError(
            f"{name} [{minimum}, {maximum}] araliginda sonlu olmali"
        )
    return parsed


def _mapping(name: str, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise KapBankBatchPersistenceError(f"{name} mapping olmali")
    return value


def _sequence(name: str, value: Any) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise KapBankBatchPersistenceError(f"{name} liste olmali")
    return value


def _require_keys(name: str, value: Mapping[str, Any], required: set[str]) -> None:
    missing = required - set(value)
    if missing:
        raise KapBankBatchPersistenceError(
            f"{name} zorunlu alanlari eksik: {sorted(missing)}"
        )


def _strict_bool(name: str, value: Any) -> bool:
    if type(value) is not bool:
        raise KapBankBatchPersistenceError(f"{name} Python bool olmali")
    return value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise KapBankBatchPersistenceError("batch raporu JSON olarak kanoniklestirilemiyor") from exc


def _run_key(
    *, analysis_at: datetime, anchor_period_end: date, horizon_days: int, pipeline_version: str
) -> str:
    material = {
        "analysis_at": analysis_at.astimezone(timezone.utc).isoformat(),
        "anchor_period_end": anchor_period_end.isoformat(),
        "horizon_days": horizon_days,
        "pipeline_version": pipeline_version,
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _validate_report(
    report: Mapping[str, Any], *, horizon_days: int, pipeline_version: str, source: str
) -> dict[str, Any]:
    report = _mapping("report", report)
    analysis_at = _aware("report.analysis_at", report.get("analysis_at"))
    anchor = _date("report.anchor_period_end", report.get("anchor_period_end"))
    horizon = _python_int("horizon_days", horizon_days, minimum=1)
    version = _text("pipeline_version", pipeline_version)
    source_text = _text("source", source)
    status = _text("report.status", report.get("status"))
    if status not in {"COMPLETE", "PARTIAL", "FAILED"}:
        raise KapBankBatchPersistenceError("report.status gecersiz")

    count_names = (
        "requested_count", "prepared_count", "result_count", "rejected_count",
        "sector_scale_eligible_count", "valuation_ok_count",
    )
    counts = {name: _python_int(f"report.{name}", report.get(name)) for name in count_names}
    if counts["requested_count"] < 1:
        raise KapBankBatchPersistenceError("requested_count en az 1 olmali")
    results = _sequence("report.results", report.get("results"))
    ranking = _sequence("report.ranking", report.get("ranking"))
    rejections = _sequence("report.rejections", report.get("rejections"))
    if counts["result_count"] != len(results) or counts["rejected_count"] != len(rejections):
        raise KapBankBatchPersistenceError("report sayaclari liste boylariyla eslesmiyor")
    if counts["requested_count"] != len(results) + len(rejections):
        raise KapBankBatchPersistenceError("requested_count sonuc + ret sayisina esit olmali")
    if counts["prepared_count"] < counts["result_count"]:
        raise KapBankBatchPersistenceError("prepared_count result_count altinda olamaz")
    if counts["prepared_count"] > counts["requested_count"]:
        raise KapBankBatchPersistenceError("prepared_count requested_count ustunde olamaz")
    if counts["sector_scale_eligible_count"] > counts["prepared_count"]:
        raise KapBankBatchPersistenceError("sector_scale_eligible_count prepared_count ustunde olamaz")
    if counts["valuation_ok_count"] > counts["result_count"]:
        raise KapBankBatchPersistenceError("valuation_ok_count result_count ustunde olamaz")
    expected_status = "COMPLETE" if counts["result_count"] == counts["requested_count"] else ("PARTIAL" if counts["result_count"] else "FAILED")
    if status != expected_status:
        raise KapBankBatchPersistenceError(f"report.status sayaclarla eslesmiyor; beklenen={expected_status}")
    if len(ranking) != len(results):
        raise KapBankBatchPersistenceError("ranking tum basarili sonuclari icermeli")

    result_by_ticker: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(results):
        row = _mapping(f"report.results[{index}]", raw)
        _require_keys(
            f"report.results[{index}]", row,
            {
                "ticker", "analysis_at", "anchor_period_end", "disclosures_used",
                "disclosure_lineage", "config_lineage", "raw_facts_extracted",
                "semantic_facts_mapped", "bank_metrics_derived", "metric_periods",
                "canonical", "valuation", "m2", "total_rasyo",
                "sector_scale_rejected_tickers",
            },
        )
        raw_ticker = _text(f"report.results[{index}].ticker", row.get("ticker"))
        ticker = raw_ticker.upper()
        if raw_ticker != ticker:
            raise KapBankBatchPersistenceError(f"sonuc ticker canonical buyuk harf olmali: {raw_ticker}")
        if ticker in result_by_ticker:
            raise KapBankBatchPersistenceError(f"tekrarlanan sonuc ticker: {ticker}")
        if _aware(f"results.{ticker}.analysis_at", row.get("analysis_at")) != analysis_at:
            raise KapBankBatchPersistenceError(f"{ticker} analysis_at batch ile eslesmiyor")
        if _date(f"results.{ticker}.anchor_period_end", row.get("anchor_period_end")) != anchor:
            raise KapBankBatchPersistenceError(f"{ticker} anchor_period_end batch ile eslesmiyor")
        disclosures_used = _python_int(f"{ticker}.disclosures_used", row.get("disclosures_used"), minimum=1)
        disclosure_lineage = _sequence(f"{ticker}.disclosure_lineage", row.get("disclosure_lineage"))
        if len(disclosure_lineage) != disclosures_used:
            raise KapBankBatchPersistenceError(f"{ticker} disclosure_lineage sayisi disclosures_used ile eslesmiyor")
        for line_idx, lineage_row in enumerate(disclosure_lineage):
            lineage = _mapping(f"{ticker}.disclosure_lineage[{line_idx}]", lineage_row)
            _require_keys(
                f"{ticker}.disclosure_lineage[{line_idx}]", lineage,
                {"disclosure_id", "published_at", "payload_sha256", "source", "source_url"},
            )
            _text(f"{ticker}.disclosure_lineage[{line_idx}].disclosure_id", lineage.get("disclosure_id"))
            published = _aware(f"{ticker}.disclosure_lineage[{line_idx}].published_at", lineage.get("published_at"))
            if published > analysis_at:
                raise KapBankBatchPersistenceError(f"{ticker} disclosure_lineage gelecekteki kayit iceriyor")
            payload_sha = _text(f"{ticker}.disclosure_lineage[{line_idx}].payload_sha256", lineage.get("payload_sha256"))
            if len(payload_sha) != 64 or any(ch not in "0123456789abcdef" for ch in payload_sha):
                raise KapBankBatchPersistenceError(f"{ticker} disclosure_lineage SHA256 gecersiz")
        _mapping(f"{ticker}.config_lineage", row.get("config_lineage"))
        raw_count = _python_int(f"{ticker}.raw_facts_extracted", row.get("raw_facts_extracted"), minimum=1)
        semantic_count = _python_int(f"{ticker}.semantic_facts_mapped", row.get("semantic_facts_mapped"), minimum=1)
        metric_count = _python_int(f"{ticker}.bank_metrics_derived", row.get("bank_metrics_derived"), minimum=1)
        metric_periods = _sequence(f"{ticker}.metric_periods", row.get("metric_periods"))
        if len(metric_periods) != metric_count:
            raise KapBankBatchPersistenceError(f"{ticker} metric_periods sayisi bank_metrics_derived ile eslesmiyor")
        for period in metric_periods:
            _date(f"{ticker}.metric_periods[]", period)
        if semantic_count > raw_count:
            raise KapBankBatchPersistenceError(f"{ticker} semantic fact sayisi raw fact sayisini asamaz")
        canonical = _mapping(f"{ticker}.canonical", row.get("canonical"))
        _require_keys(
            f"{ticker}.canonical", canonical,
            {
                "ticker", "analysis_at", "anchor_period_end", "quarter_slots",
                "roe_series", "selected_version_tags", "selected_publication_times",
                "selected_record_ids", "selected_version_sequences", "bvps", "payout_sus",
            },
        )
        if canonical.get("ticker") != ticker:
            raise KapBankBatchPersistenceError(f"{ticker} canonical ticker eslesmiyor")
        if _aware(f"{ticker}.canonical.analysis_at", canonical.get("analysis_at")) != analysis_at:
            raise KapBankBatchPersistenceError(f"{ticker} canonical analysis_at eslesmiyor")
        if _date(f"{ticker}.canonical.anchor_period_end", canonical.get("anchor_period_end")) != anchor:
            raise KapBankBatchPersistenceError(f"{ticker} canonical anchor eslesmiyor")
        for field in (
            "quarter_slots", "roe_series", "selected_version_tags",
            "selected_publication_times", "selected_record_ids", "selected_version_sequences",
        ):
            if len(_sequence(f"{ticker}.canonical.{field}", canonical.get(field))) != 8:
                raise KapBankBatchPersistenceError(f"{ticker} canonical.{field} sekiz elemanli olmali")
        _sequence(f"{ticker}.sector_scale_rejected_tickers", row.get("sector_scale_rejected_tickers"))
        valuation = _mapping(f"results.{ticker}.valuation", row.get("valuation"))
        m2 = _mapping(f"results.{ticker}.m2", row.get("m2"))
        _require_keys(
            f"results.{ticker}.valuation", valuation,
            {
                "ticker", "analysis_at", "anchor_period_end", "selected_version_tag",
                "selected_published_at", "quarter_slots", "roe_series_canonical",
                "selected_versions", "selected_publication_times", "roe_missing_count",
                "uncertainty", "canonical_bvps", "canonical_payout_sus", "status",
                "reason", "sector_sample_size",
            },
        )
        _require_keys(
            f"results.{ticker}.m2", m2,
            {
                "ticker", "asof_date", "analysis_at", "anchor_period_end",
                "current_price", "price_trade_date", "price_source",
                "valuation_status", "valuation_reason", "v_conf", "z_val",
                "s_valuation", "s_val_effective", "s_lag_effective", "lag_active",
                "lag_source", "valuation_usable", "m2_score", "score_inputs",
                "diagnostics",
            },
        )
        if valuation.get("ticker") != ticker or m2.get("ticker") != ticker:
            raise KapBankBatchPersistenceError(f"{ticker} alt sonuc ticker eslesmiyor")
        if _aware(f"{ticker}.valuation.analysis_at", valuation.get("analysis_at")) != analysis_at:
            raise KapBankBatchPersistenceError(f"{ticker} valuation analysis_at eslesmiyor")
        if _aware(f"{ticker}.m2.analysis_at", m2.get("analysis_at")) != analysis_at:
            raise KapBankBatchPersistenceError(f"{ticker} m2 analysis_at eslesmiyor")
        if _date(f"{ticker}.m2.anchor_period_end", m2.get("anchor_period_end")) != anchor:
            raise KapBankBatchPersistenceError(f"{ticker} m2 anchor_period_end eslesmiyor")
        expected_asof = analysis_at.astimezone(ISTANBUL_TZ).date()
        if _date(f"{ticker}.m2.asof_date", m2.get("asof_date")) != expected_asof:
            raise KapBankBatchPersistenceError(f"{ticker} m2 asof_date analysis_at ile eslesmiyor")
        score_inputs = _mapping(f"{ticker}.m2.score_inputs", m2.get("score_inputs"))
        diagnostics = _mapping(f"{ticker}.m2.diagnostics", m2.get("diagnostics"))
        expected_score_inputs = {
            "s_val_effective", "v_status", "v_conf",
            "s_lag_effective", "lag_active",
        }
        if set(score_inputs) != expected_score_inputs:
            raise KapBankBatchPersistenceError(f"{ticker} m2.score_inputs sozlesmesi bozuk")
        required_diagnostics = {
            "current_price", "price_trade_date", "price_source",
            "valuation_status", "valuation_reason", "z_val",
            "s_valuation", "lag_source",
        }
        if not required_diagnostics.issubset(diagnostics):
            raise KapBankBatchPersistenceError(f"{ticker} m2.diagnostics zorunlu alanlari eksik")
        lag_active = _strict_bool(f"{ticker}.m2.lag_active", m2.get("lag_active"))
        _strict_bool(f"{ticker}.m2.valuation_usable", m2.get("valuation_usable"))
        if _strict_bool(f"{ticker}.m2.score_inputs.lag_active", score_inputs.get("lag_active")) != lag_active:
            raise KapBankBatchPersistenceError(f"{ticker} m2 lag_active score_inputs ile eslesmiyor")
        for field in ("s_val_effective", "s_lag_effective"):
            left = _finite(f"{ticker}.m2.{field}", m2.get(field), minimum=0.0, maximum=1.0)
            right = _finite(f"{ticker}.m2.score_inputs.{field}", score_inputs.get(field), minimum=0.0, maximum=1.0)
            if not math.isclose(left, right, rel_tol=0, abs_tol=1e-12):
                raise KapBankBatchPersistenceError(f"{ticker} m2 {field} score_inputs ile eslesmiyor")
        if score_inputs.get("v_status") != m2.get("valuation_status"):
            raise KapBankBatchPersistenceError(f"{ticker} m2 v_status score_inputs ile eslesmiyor")
        if score_inputs.get("v_conf") is None or m2.get("v_conf") is None:
            if score_inputs.get("v_conf") is not None or m2.get("v_conf") is not None:
                raise KapBankBatchPersistenceError(f"{ticker} m2 v_conf score_inputs ile eslesmiyor")
        else:
            if not math.isclose(
                _finite(f"{ticker}.m2.v_conf", m2.get("v_conf"), minimum=0.0, maximum=1.0),
                _finite(f"{ticker}.m2.score_inputs.v_conf", score_inputs.get("v_conf"), minimum=0.0, maximum=1.0),
                rel_tol=0, abs_tol=1e-12,
            ):
                raise KapBankBatchPersistenceError(f"{ticker} m2 v_conf score_inputs ile eslesmiyor")
        for field in ("current_price", "price_trade_date", "price_source", "valuation_status", "valuation_reason", "z_val", "s_valuation", "lag_source"):
            if diagnostics.get(field) != m2.get(field):
                raise KapBankBatchPersistenceError(f"{ticker} m2 diagnostics.{field} ust sonuc ile eslesmiyor")
        price_date = m2.get("price_trade_date")
        if price_date is not None:
            price_date = _date(f"{ticker}.m2.price_trade_date", price_date)
            local = analysis_at.astimezone(ISTANBUL_TZ)
            if price_date > local.date() or (price_date == local.date() and local.time() < time(18, 30)):
                raise KapBankBatchPersistenceError(f"{ticker} m2 fiyat tarihi analysis_at sonrasina siziyor")
        for sequence_name in ("quarter_slots", "roe_series_canonical", "selected_versions", "selected_publication_times"):
            values = _sequence(f"{ticker}.valuation.{sequence_name}", valuation.get(sequence_name))
            if len(values) != 8:
                raise KapBankBatchPersistenceError(f"{ticker} valuation.{sequence_name} sekiz elemanli olmali")
        missing_count = _python_int(f"{ticker}.valuation.roe_missing_count", valuation.get("roe_missing_count"))
        actual_missing = sum(value is None for value in valuation["roe_series_canonical"] )
        if missing_count != actual_missing:
            raise KapBankBatchPersistenceError(f"{ticker} roe_missing_count seriyle eslesmiyor")
        _mapping(f"{ticker}.valuation.uncertainty", valuation.get("uncertainty"))
        status_value = _text(f"{ticker}.valuation.status", valuation.get("status"))
        if status_value == "OK":
            _mapping(f"{ticker}.valuation.valuation", valuation.get("valuation"))
            _mapping(f"{ticker}.valuation.confidence_factors", valuation.get("confidence_factors"))
            _finite(f"{ticker}.valuation.v_conf", valuation.get("v_conf"), minimum=0.0, maximum=1.0)
        total = _mapping(f"results.{ticker}.total_rasyo", row.get("total_rasyo"))
        _require_keys(
            f"results.{ticker}.total_rasyo", total,
            {
                "module_scores", "weights", "contributions", "base_score",
                "good_count_ge8", "veto_threshold", "veto_factor", "veto_flag",
                "final_score", "total_rasyo_100", "decision",
            },
        )
        modules = _mapping(f"results.{ticker}.total_rasyo.module_scores", total.get("module_scores"))
        if set(modules) != set(MODULE_KEYS):
            raise KapBankBatchPersistenceError(f"{ticker} module_scores anahtarlari tam degil")
        for key in MODULE_KEYS:
            _finite(f"{ticker}.module_scores.{key}", modules[key], minimum=0.0, maximum=1.0)
        _finite(f"{ticker}.m2.m2_score", m2.get("m2_score"), minimum=0.0, maximum=1.0)
        if not math.isclose(float(modules["M2"]), float(m2["m2_score"]), rel_tol=0, abs_tol=1e-12):
            raise KapBankBatchPersistenceError(f"{ticker} M2 modulu m2 sonucu ile eslesmiyor")
        base_score = _finite(f"{ticker}.base_score", total.get("base_score"), minimum=0.0, maximum=1.0)
        final_score = _finite(f"{ticker}.final_score", total.get("final_score"), minimum=0.0, maximum=1.0)
        total_100 = _finite(f"{ticker}.total_rasyo_100", total.get("total_rasyo_100"), minimum=0.0, maximum=100.0)
        if not math.isclose(total_100, final_score * 100.0, rel_tol=0, abs_tol=1e-10):
            raise KapBankBatchPersistenceError(f"{ticker} total_rasyo_100 final_score ile eslesmiyor")
        good_count = _python_int(f"{ticker}.good_count_ge8", total.get("good_count_ge8"))
        veto_threshold = _python_int(f"{ticker}.veto_threshold", total.get("veto_threshold"))
        veto_factor = _finite(f"{ticker}.veto_factor", total.get("veto_factor"), minimum=0.0, maximum=1.0)
        veto_flag = _strict_bool(f"{ticker}.veto_flag", total.get("veto_flag"))
        expected_veto = good_count < veto_threshold
        if veto_flag != expected_veto:
            raise KapBankBatchPersistenceError(f"{ticker} veto_flag good_count/veto_threshold ile eslesmiyor")
        expected_final = base_score * veto_factor if expected_veto else base_score
        if not math.isclose(final_score, expected_final, rel_tol=0, abs_tol=1e-12):
            raise KapBankBatchPersistenceError(f"{ticker} final_score veto zinciriyle eslesmiyor")
        weights = _mapping(f"{ticker}.weights", total.get("weights"))
        contributions = _mapping(f"{ticker}.contributions", total.get("contributions"))
        if set(weights) != set(MODULE_KEYS) or set(contributions) != set(MODULE_KEYS):
            raise KapBankBatchPersistenceError(f"{ticker} weights/contributions anahtarlari tam degil")
        contribution_sum = 0.0
        for key in MODULE_KEYS:
            weight = _finite(f"{ticker}.weights.{key}", weights[key], minimum=0.0, maximum=1.0)
            contribution = _finite(f"{ticker}.contributions.{key}", contributions[key], minimum=0.0, maximum=1.0)
            expected_contribution = float(modules[key]) * weight
            if not math.isclose(contribution, expected_contribution, rel_tol=0, abs_tol=1e-12):
                raise KapBankBatchPersistenceError(f"{ticker} contribution.{key} skor ve agirlikla eslesmiyor")
            contribution_sum += contribution
        if not math.isclose(sum(float(weights[key]) for key in MODULE_KEYS), 1.0, rel_tol=0, abs_tol=1e-12):
            raise KapBankBatchPersistenceError(f"{ticker} weights toplami 1 degil")
        if not math.isclose(base_score, contribution_sum, rel_tol=0, abs_tol=1e-12):
            raise KapBankBatchPersistenceError(f"{ticker} base_score contributions toplamiyla eslesmiyor")
        decision = _text(f"{ticker}.decision", total.get("decision"))
        if decision not in {"AL", "IZLE", "UZAK"}:
            raise KapBankBatchPersistenceError(f"{ticker} decision gecersiz")
        if decision != total_rasyo_decision(final_score):
            raise KapBankBatchPersistenceError(f"{ticker} decision final_score ile eslesmiyor")
        _mapping(f"results.{ticker}.valuation", valuation)
        result_by_ticker[ticker] = row

    rejection_by_ticker: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(rejections):
        row = _mapping(f"report.rejections[{index}]", raw)
        raw_ticker = _text(f"report.rejections[{index}].ticker", row.get("ticker"))
        ticker = raw_ticker.upper()
        if raw_ticker != ticker:
            raise KapBankBatchPersistenceError(f"ret ticker canonical buyuk harf olmali: {raw_ticker}")
        _text(f"report.rejections[{index}].reason", row.get("reason"))
        if ticker in rejection_by_ticker or ticker in result_by_ticker:
            raise KapBankBatchPersistenceError(f"tekrarlanan/cakisan ticker: {ticker}")
        rejection_by_ticker[ticker] = row

    ranking_by_ticker: dict[str, Mapping[str, Any]] = {}
    previous_score = float("inf")
    previous_ticker = ""
    for index, raw in enumerate(ranking, start=1):
        row = _mapping(f"report.ranking[{index - 1}]", raw)
        rank = _python_int(f"ranking[{index - 1}].rank", row.get("rank"), minimum=1)
        if rank != index:
            raise KapBankBatchPersistenceError("ranking 1'den baslayan kesintisiz sira olmali")
        raw_ticker = _text(f"ranking[{index - 1}].ticker", row.get("ticker"))
        ticker = raw_ticker.upper()
        if raw_ticker != ticker:
            raise KapBankBatchPersistenceError(f"ranking ticker canonical buyuk harf olmali: {raw_ticker}")
        if ticker not in result_by_ticker or ticker in ranking_by_ticker:
            raise KapBankBatchPersistenceError(f"ranking ticker sonucu yok/tekrarli: {ticker}")
        score = _finite(
            f"ranking[{index - 1}].total_rasyo_100",
            row.get("total_rasyo_100"), minimum=0.0, maximum=100.0,
        )
        expected = float(result_by_ticker[ticker]["total_rasyo"]["total_rasyo_100"])
        if not math.isclose(score, expected, rel_tol=0, abs_tol=1e-12):
            raise KapBankBatchPersistenceError(f"{ticker} ranking skoru sonuc ile eslesmiyor")
        result = result_by_ticker[ticker]
        total = result["total_rasyo"]
        m2 = result["m2"]
        valuation = result["valuation"]
        if row.get("decision") != total.get("decision"):
            raise KapBankBatchPersistenceError(f"{ticker} ranking karari sonuc ile eslesmiyor")
        rank_m2 = _finite(f"ranking[{index - 1}].m2_score", row.get("m2_score"), minimum=0.0, maximum=1.0)
        if not math.isclose(rank_m2, float(m2["m2_score"]), rel_tol=0, abs_tol=1e-12):
            raise KapBankBatchPersistenceError(f"{ticker} ranking M2 sonucu ile eslesmiyor")
        rank_v_conf = row.get("v_conf")
        result_v_conf = valuation.get("v_conf")
        if rank_v_conf is None or result_v_conf is None:
            if rank_v_conf is not None or result_v_conf is not None:
                raise KapBankBatchPersistenceError(f"{ticker} ranking v_conf sonucu ile eslesmiyor")
        elif not math.isclose(
            _finite(f"ranking[{index - 1}].v_conf", rank_v_conf, minimum=0.0, maximum=1.0),
            _finite(f"{ticker}.valuation.v_conf", result_v_conf, minimum=0.0, maximum=1.0),
            rel_tol=0, abs_tol=1e-12,
        ):
            raise KapBankBatchPersistenceError(f"{ticker} ranking v_conf sonucu ile eslesmiyor")
        if row.get("valuation_status") != valuation.get("status"):
            raise KapBankBatchPersistenceError(f"{ticker} ranking valuation_status eslesmiyor")
        if score > previous_score + 1e-12:
            raise KapBankBatchPersistenceError("ranking azalan skora gore sirali olmali")
        if math.isclose(score, previous_score, rel_tol=0, abs_tol=1e-12) and previous_ticker and ticker < previous_ticker:
            raise KapBankBatchPersistenceError("esit skorlu ranking ticker artan sirada olmali")
        previous_score = score
        previous_ticker = ticker
        ranking_by_ticker[ticker] = row
    if set(ranking_by_ticker) != set(result_by_ticker):
        raise KapBankBatchPersistenceError("ranking ve sonuc ticker kumeleri eslesmiyor")
    actual_ok = sum(row["valuation"].get("status") == "OK" for row in result_by_ticker.values())
    if counts["valuation_ok_count"] != actual_ok:
        raise KapBankBatchPersistenceError("valuation_ok_count gercek sonuc sayisiyla eslesmiyor")

    safe_report = json_safe(report)
    report_json = _canonical_json(safe_report)
    run_key = _run_key(
        analysis_at=analysis_at,
        anchor_period_end=anchor,
        horizon_days=horizon,
        pipeline_version=version,
    )
    return {
        "analysis_at": analysis_at,
        "asof_date": analysis_at.astimezone(ISTANBUL_TZ).date(),
        "anchor_period_end": anchor,
        "horizon_days": horizon,
        "pipeline_version": version,
        "source": source_text,
        "status": status,
        "counts": counts,
        "results": result_by_ticker,
        "ranking": ranking_by_ticker,
        "rejections": rejection_by_ticker,
        "run_key": run_key,
        "report_json": report_json,
        "report_sha256": hashlib.sha256(report_json.encode("utf-8")).hexdigest(),
    }


def _module_score_params(
    *, run_key: str, analysis_at: datetime, anchor: date, horizon_days: int, result: Mapping[str, Any]
) -> dict[str, Any]:
    total = result["total_rasyo"]
    modules = total["module_scores"]
    m2 = result["m2"]
    return {
        "ticker": result["ticker"],
        "asof_date": analysis_at.astimezone(ISTANBUL_TZ).date(),
        "period_end": anchor,
        "horizon_days": horizon_days,
        "m1": modules["M1"], "m2": modules["M2"], "m3": modules["M3"],
        "m2_source": "KAP_BANK_E2E_V47",
        "m2_score_inputs": _canonical_json(m2["score_inputs"]),
        "ek1": modules["Ek1"], "ek3": None, "ek4": modules["Ek4"],
        "ek5_dilution": None, "ek9": modules["Ek9"],
        "base_score": total["base_score"], "final_score": total["final_score"],
        "good_count_ge8": total["good_count_ge8"], "decision": total["decision"],
        "veto_flag": total["veto_flag"], "analysis_at": analysis_at,
        "source_run_key": run_key,
    }


_MODULE_UPSERT_SQL = """
INSERT INTO analytics.module_scores (
  ticker, asof_date, period_end, horizon_days,
  m1, m2, m3, m2_source, m2_score_inputs,
  ek1, ek3, ek4, ek5_dilution, ek9,
  base_score, final_score, good_count_ge8, decision, veto_flag,
  analysis_at, source_run_key
) VALUES (
  %(ticker)s, %(asof_date)s, %(period_end)s, %(horizon_days)s,
  %(m1)s, %(m2)s, %(m3)s, %(m2_source)s, %(m2_score_inputs)s::jsonb,
  %(ek1)s, %(ek3)s, %(ek4)s, %(ek5_dilution)s, %(ek9)s,
  %(base_score)s, %(final_score)s, %(good_count_ge8)s, %(decision)s, %(veto_flag)s,
  %(analysis_at)s, %(source_run_key)s
)
ON CONFLICT (ticker, asof_date, horizon_days)
DO UPDATE SET
  period_end=EXCLUDED.period_end,
  m1=EXCLUDED.m1, m2=EXCLUDED.m2, m3=EXCLUDED.m3,
  m2_source=EXCLUDED.m2_source, m2_score_inputs=EXCLUDED.m2_score_inputs,
  ek1=EXCLUDED.ek1, ek3=EXCLUDED.ek3, ek4=EXCLUDED.ek4,
  ek5_dilution=EXCLUDED.ek5_dilution, ek9=EXCLUDED.ek9,
  base_score=EXCLUDED.base_score, final_score=EXCLUDED.final_score,
  good_count_ge8=EXCLUDED.good_count_ge8, decision=EXCLUDED.decision,
  veto_flag=EXCLUDED.veto_flag, analysis_at=EXCLUDED.analysis_at,
  source_run_key=EXCLUDED.source_run_key
WHERE analytics.module_scores.analysis_at IS NULL
   OR EXCLUDED.analysis_at >= analytics.module_scores.analysis_at
"""


def persist_kap_bank_batch_report(
    conn: Any,
    report: Mapping[str, Any],
    *,
    horizon_days: int = 63,
    pipeline_version: str = "KAP_BANK_BATCH_V7",
    source: str = "MKK_KAP_BANK_E2E",
) -> PersistedKapBankBatch:
    """Persist one full batch atomically; reruns replace stale successes/rejections."""
    validated = _validate_report(
        report, horizon_days=horizon_days, pipeline_version=pipeline_version, source=source
    )
    run_key = validated["run_key"]
    requested_tickers = sorted({*validated["results"], *validated["rejections"]})
    config_lineage = {
        ticker: dict(row.get("config_lineage") or {})
        for ticker, row in validated["results"].items()
    }
    counts = validated["counts"]

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO analytics.kap_bank_batch_runs (
                  run_key, analysis_at, asof_date, anchor_period_end, horizon_days,
                  pipeline_version, source, status,
                  requested_count, prepared_count, result_count, rejected_count,
                  sector_scale_eligible_count, valuation_ok_count,
                  report_sha256, config_lineage
                ) VALUES (
                  %(run_key)s, %(analysis_at)s, %(asof_date)s, %(anchor_period_end)s, %(horizon_days)s,
                  %(pipeline_version)s, %(source)s, %(status)s,
                  %(requested_count)s, %(prepared_count)s, %(result_count)s, %(rejected_count)s,
                  %(sector_scale_eligible_count)s, %(valuation_ok_count)s,
                  %(report_sha256)s, %(config_lineage)s::jsonb
                )
                ON CONFLICT (run_key) DO UPDATE SET
                  status=EXCLUDED.status,
                  requested_count=EXCLUDED.requested_count,
                  prepared_count=EXCLUDED.prepared_count,
                  result_count=EXCLUDED.result_count,
                  rejected_count=EXCLUDED.rejected_count,
                  sector_scale_eligible_count=EXCLUDED.sector_scale_eligible_count,
                  valuation_ok_count=EXCLUDED.valuation_ok_count,
                  report_sha256=EXCLUDED.report_sha256,
                  config_lineage=EXCLUDED.config_lineage,
                  source=EXCLUDED.source,
                  updated_at=now()
                """,
                {
                    "run_key": run_key,
                    "analysis_at": validated["analysis_at"],
                    "asof_date": validated["asof_date"],
                    "anchor_period_end": validated["anchor_period_end"],
                    "horizon_days": validated["horizon_days"],
                    "pipeline_version": validated["pipeline_version"],
                    "source": validated["source"],
                    "status": validated["status"],
                    **counts,
                    "report_sha256": validated["report_sha256"],
                    "config_lineage": _canonical_json(config_lineage),
                },
            )
            cur.execute("DELETE FROM analytics.kap_bank_batch_rankings WHERE run_key=%s", (run_key,))
            cur.execute("DELETE FROM analytics.kap_bank_batch_rejections WHERE run_key=%s", (run_key,))
            if requested_tickers:
                cleanup = {
                    "analysis_at": validated["analysis_at"],
                    "anchor_period_end": validated["anchor_period_end"],
                    "asof_date": validated["asof_date"],
                    "horizon_days": validated["horizon_days"],
                    "run_key": run_key,
                    "tickers": requested_tickers,
                }
                cur.execute(
                    """DELETE FROM analytics.bank_valuation_periods
                       WHERE analysis_at=%(analysis_at)s AND anchor_period_end=%(anchor_period_end)s
                         AND ticker = ANY(%(tickers)s::text[])""",
                    cleanup,
                )
                cur.execute(
                    """DELETE FROM analytics.bank_m2_scores
                       WHERE analysis_at=%(analysis_at)s AND anchor_period_end=%(anchor_period_end)s
                         AND ticker = ANY(%(tickers)s::text[])""",
                    cleanup,
                )
                cur.execute(
                    """DELETE FROM analytics.module_scores
                       WHERE asof_date=%(asof_date)s AND horizon_days=%(horizon_days)s
                         AND source_run_key IS NOT NULL
                         AND analysis_at <= %(analysis_at)s
                         AND ticker = ANY(%(tickers)s::text[])""",
                    cleanup,
                )

            proxy = _CursorConnectionProxy(cur)
            rank_lookup = validated["ranking"]
            for ticker in sorted(validated["results"]):
                result = validated["results"][ticker]
                persist_bank_valuation(proxy, result["valuation"])
                persist_bank_m2_score(proxy, result["m2"])
                cur.execute(
                    _MODULE_UPSERT_SQL,
                    _module_score_params(
                        run_key=run_key,
                        analysis_at=validated["analysis_at"],
                        anchor=validated["anchor_period_end"],
                        horizon_days=validated["horizon_days"],
                        result=result,
                    ),
                )
                rank_row = rank_lookup[ticker]
                lineage = {
                    "config_lineage": result.get("config_lineage") or {},
                    "disclosure_lineage": result.get("disclosure_lineage") or [],
                }
                cur.execute(
                    """
                    INSERT INTO analytics.kap_bank_batch_rankings (
                      run_key, ticker, rank, total_rasyo_100, decision,
                      m2_score, v_conf, valuation_status, result_payload, lineage
                    ) VALUES (
                      %(run_key)s, %(ticker)s, %(rank)s, %(total_rasyo_100)s, %(decision)s,
                      %(m2_score)s, %(v_conf)s, %(valuation_status)s,
                      %(result_payload)s::jsonb, %(lineage)s::jsonb
                    )
                    """,
                    {
                        "run_key": run_key,
                        "ticker": ticker,
                        "rank": rank_row["rank"],
                        "total_rasyo_100": rank_row["total_rasyo_100"],
                        "decision": rank_row["decision"],
                        "m2_score": rank_row["m2_score"],
                        "v_conf": rank_row.get("v_conf"),
                        "valuation_status": rank_row["valuation_status"],
                        "result_payload": _canonical_json(result),
                        "lineage": _canonical_json(lineage),
                    },
                )

            for ticker in sorted(validated["rejections"]):
                rejection = validated["rejections"][ticker]
                cur.execute(
                    """INSERT INTO analytics.kap_bank_batch_rejections
                       (run_key, ticker, stage, reason)
                       VALUES (%(run_key)s, %(ticker)s, 'EVALUATION', %(reason)s)""",
                    {"run_key": run_key, "ticker": ticker, "reason": rejection["reason"]},
                )

    return PersistedKapBankBatch(
        run_key=run_key,
        status=validated["status"],
        results_written=len(validated["results"]),
        rejections_written=len(validated["rejections"]),
        ranking_written=len(validated["ranking"]),
        module_scores_written=len(validated["results"]),
    )


def fetch_latest_kap_bank_ranking(
    conn: Any,
    *,
    asof_date: date,
    horizon_days: int = 63,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read the latest persisted BANK ranking for one local analysis date."""
    asof = _date("asof_date", asof_date)
    horizon = _python_int("horizon_days", horizon_days, minimum=1)
    row_limit = _python_int("limit", limit, minimum=1)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              analysis_at, asof_date, anchor_period_end, horizon_days,
              pipeline_version, source, status,
              rank, ticker, total_rasyo_100, decision,
              m2_score, v_conf, valuation_status
            FROM analytics.latest_kap_bank_batch_rankings
            WHERE asof_date=%(asof_date)s AND horizon_days=%(horizon_days)s
            ORDER BY rank
            LIMIT %(limit)s
            """,
            {"asof_date": asof, "horizon_days": horizon, "limit": row_limit},
        )
        rows = cur.fetchall()
        columns = [item[0] for item in cur.description]
    return [dict(zip(columns, row)) for row in rows]
