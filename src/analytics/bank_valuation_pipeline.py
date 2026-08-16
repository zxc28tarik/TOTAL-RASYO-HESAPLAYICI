from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .bank_v47 import bank_valuation, estimate_roe_uncertainty
from .bank_v47.roe_uncertainty import coerce_finite_number, is_missing_like, is_bool_like


SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "012_bank_point_in_time_slots.sql"
EXPECTED_SLOT_COUNT = 8
DEFAULT_TIER_CAP = 0.80
DEFAULT_PAYOUT_MISSING_FACTOR = 0.70
UNCERTAINTY_ALLOWED = frozenset({
    "min_sector_sample", "sector_quantile", "absolute_floor",
    "dof_correction", "outlier_absolute_floor", "outlier_multiplier",
})
VALUATION_ALLOWED = frozenset({"d_coe", "d_g"})


class CanonicalizationError(ValueError):
    """DB/pandas satırı kanonik sözleşmeye çevrilemediğinde kullanılır."""


@dataclass(frozen=True)
class CanonicalBankRow:
    ticker: str
    analysis_at: datetime
    anchor_period_end: date
    quarter_slots: tuple[date, ...]
    roe_series: tuple[float | None, ...]
    selected_version_tags: tuple[str | None, ...]
    selected_publication_times: tuple[datetime | None, ...]
    selected_record_ids: tuple[int | None, ...]
    selected_version_sequences: tuple[int | None, ...]
    bvps: float | None
    payout_sus: float | None

    @property
    def roe_missing_count(self) -> int:
        return sum(v is None for v in self.roe_series)

    @property
    def selected_version_tag(self) -> str | None:
        return self.selected_version_tags[-1]

    @property
    def selected_published_at(self) -> datetime | None:
        return self.selected_publication_times[-1]


@dataclass(frozen=True)
class BankValuationInputs:
    coe: float
    macro_cap: float
    tier_cap: float = DEFAULT_TIER_CAP
    payout_missing_factor: float = DEFAULT_PAYOUT_MISSING_FACTOR
    band_width_shadow_mode: bool = True
    max_halfwidth: float = 0.80


def _quarter_end(value: date) -> date:
    quarter = (value.month - 1) // 3
    end_month = quarter * 3 + 3
    if end_month == 12:
        next_month = date(value.year + 1, 1, 1)
    else:
        next_month = date(value.year, end_month + 1, 1)
    from datetime import timedelta
    return next_month - timedelta(days=1)


def _shift_quarters(value: date, quarters: int) -> date:
    q_index = value.year * 4 + ((value.month - 1) // 3) + quarters
    year, q = divmod(q_index, 4)
    month = q * 3 + 3
    return _quarter_end(date(year, month, 1))


def build_quarter_slots(anchor_period_end: date, count: int = EXPECTED_SLOT_COUNT) -> tuple[date, ...]:
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise CanonicalizationError("count positive Python int olmali")
    anchor = _quarter_end(anchor_period_end)
    return tuple(_shift_quarters(anchor, offset) for offset in range(-(count - 1), 1))


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _as_date(name: str, value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise CanonicalizationError(f"{name} gecersiz tarih: {value!r}") from exc
    raise CanonicalizationError(f"{name} tarih olmali")


def _as_aware_datetime(name: str, value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CanonicalizationError(f"{name} gecersiz zaman: {value!r}") from exc
    if not isinstance(value, datetime):
        raise CanonicalizationError(f"{name} timestamptz/datetime olmali")
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanonicalizationError(f"{name} timezone bilgisi icermeli")
    return value


def _nullable_float(name: str, value: Any, *, minimum: float | None = None,
                    strict_minimum: bool = False) -> float | None:
    if is_missing_like(value):
        return None
    try:
        return coerce_finite_number(
            name,
            value,
            minimum=minimum,
            strict_minimum=strict_minimum,
        )
    except ValueError as exc:
        raise CanonicalizationError(str(exc)) from exc


def _nullable_int(name: str, value: Any, *, minimum: int | None = None) -> int | None:
    if is_missing_like(value):
        return None
    if is_bool_like(value):
        raise CanonicalizationError(f"{name} bool olamaz")
    try:
        ivalue = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CanonicalizationError(f"{name} tam sayiya cevrilemedi") from exc
    try:
        if float(value) != float(ivalue):
            raise CanonicalizationError(f"{name} tam sayi olmali")
    except (TypeError, ValueError, OverflowError) as exc:
        raise CanonicalizationError(f"{name} tam sayi olmali") from exc
    if minimum is not None and ivalue < minimum:
        raise CanonicalizationError(f"{name} >= {minimum} olmali")
    return ivalue


def to_canonical_row(
    rows: Iterable[Any],
    *,
    ticker: str,
    analysis_at: datetime,
    anchor_period_end: date,
) -> CanonicalBankRow:
    """Sekiz SQL yuvasını sırasını değiştirmeden Python float/None sözleşmesine çevirir.

    Fonksiyon satırları sessizce sıralamaz. Ters sıra veya yanlış pencere geçerli ama
    yanlış bir float listesi üreteceği için açıkça reddedilir.
    """
    if not isinstance(ticker, str) or not ticker.strip():
        raise CanonicalizationError("ticker bos olmayan metin olmali")
    analysis = _as_aware_datetime("analysis_at", analysis_at)
    anchor = _quarter_end(_as_date("anchor_period_end", anchor_period_end))
    expected_slots = build_quarter_slots(anchor)
    materialized = list(rows)
    if len(materialized) != EXPECTED_SLOT_COUNT:
        raise CanonicalizationError(
            f"tam {EXPECTED_SLOT_COUNT} takvim yuvasi bekleniyordu; {len(materialized)} geldi"
        )

    periods = tuple(_as_date("period_end", _get(row, "period_end")) for row in materialized)
    if periods != expected_slots:
        raise CanonicalizationError(
            f"takvim yuvalari/sirasi hatali: beklenen={expected_slots!r}, gelen={periods!r}"
        )
    if len(set(periods)) != EXPECTED_SLOT_COUNT:
        raise CanonicalizationError("yinelenen period_end bulundu")

    roe: list[float | None] = []
    versions: list[str | None] = []
    publications: list[datetime | None] = []
    record_ids: list[int | None] = []
    version_sequences: list[int | None] = []
    bvps_by_slot: list[float | None] = []
    payout_by_slot: list[float | None] = []

    for idx, row in enumerate(materialized):
        record_id = _nullable_int(f"rows[{idx}].record_id", _get(row, "record_id"), minimum=1)
        version_tag_raw = _get(row, "selected_version_tag")
        publication_raw = _get(row, "selected_published_at")
        version_sequence = _nullable_int(
            f"rows[{idx}].selected_version_sequence",
            _get(row, "selected_version_sequence"),
            minimum=0,
        )

        if record_id is None:
            # Eksik çeyrekte seçilmiş kayıt metadatası ve metrik bulunmamalı.
            for field_name, raw in (
                ("selected_version_tag", version_tag_raw),
                ("selected_published_at", publication_raw),
                ("selected_version_sequence", version_sequence),
                ("roe_ttm", _get(row, "roe_ttm")),
                ("bvps", _get(row, "bvps")),
                ("payout_sus", _get(row, "payout_sus")),
            ):
                if not is_missing_like(raw):
                    raise CanonicalizationError(
                        f"rows[{idx}] eksik yuva ama {field_name} dolu"
                    )
            versions.append(None)
            publications.append(None)
            record_ids.append(None)
            version_sequences.append(None)
            roe.append(None)
            bvps_by_slot.append(None)
            payout_by_slot.append(None)
            continue

        if not isinstance(version_tag_raw, str) or not version_tag_raw.strip():
            raise CanonicalizationError(f"rows[{idx}].selected_version_tag eksik")
        publication = _as_aware_datetime(
            f"rows[{idx}].selected_published_at", publication_raw
        )
        if publication > analysis:
            raise CanonicalizationError(
                f"rows[{idx}] gelecekte yayimlanan kayit analysis_at'e sizmis"
            )
        if version_sequence is None:
            raise CanonicalizationError(f"rows[{idx}].selected_version_sequence eksik")

        versions.append(version_tag_raw.strip())
        publications.append(publication)
        record_ids.append(record_id)
        version_sequences.append(version_sequence)
        roe.append(_nullable_float(f"rows[{idx}].roe_ttm", _get(row, "roe_ttm")))
        bvps_by_slot.append(
            _nullable_float(
                f"rows[{idx}].bvps", _get(row, "bvps"), minimum=0.0, strict_minimum=True
            )
        )
        payout_by_slot.append(
            _nullable_float(f"rows[{idx}].payout_sus", _get(row, "payout_sus"))
        )

    # Değerleme merkezi hedef dönemin yayımlanmış kaydına dayanır.
    latest_record_id = record_ids[-1]
    if latest_record_id is None:
        latest_bvps = None
        latest_payout = None
    else:
        latest_bvps = bvps_by_slot[-1]
        latest_payout = payout_by_slot[-1]

    result = CanonicalBankRow(
        ticker=ticker.strip().upper(),
        analysis_at=analysis,
        anchor_period_end=anchor,
        quarter_slots=periods,
        roe_series=tuple(roe),
        selected_version_tags=tuple(versions),
        selected_publication_times=tuple(publications),
        selected_record_ids=tuple(record_ids),
        selected_version_sequences=tuple(version_sequences),
        bvps=latest_bvps,
        payout_sus=latest_payout,
    )

    # Üretimde motor kapıları sessiz kalmalı: seri yalnız Python float/None.
    if not all(v is None or type(v) is float for v in result.roe_series):
        raise CanonicalizationError("roe_series kanonik float/None sozlesmesini bozdu")
    return result


def fetch_bank_quarter_slots(
    conn: Any,
    *,
    ticker: str,
    analysis_at: datetime,
    anchor_period_end: date,
) -> list[dict[str, Any]]:
    sql = SQL_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(
            sql,
            {
                "ticker": ticker,
                "analysis_at": analysis_at,
                "anchor_period_end": anchor_period_end,
            },
        )
        names = [desc[0] for desc in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def _finite_factor(name: str, value: Any, *, maximum: float = 1.0) -> float:
    try:
        return coerce_finite_number(name, value, minimum=0.0, maximum=maximum)
    except ValueError as exc:
        raise CanonicalizationError(str(exc)) from exc


def compute_v_conf(
    *,
    tier_cap: Any,
    payout_defaulted: bool,
    payout_missing_factor: Any,
    outlier_conf_penalty: Any,
    corner_conf_penalty: Any,
) -> tuple[float, dict[str, float]]:
    if type(payout_defaulted) is not bool:
        raise CanonicalizationError("payout_defaulted Python bool olmali")
    tier = _finite_factor("tier_cap", tier_cap)
    payout_missing = _finite_factor("payout_missing_factor", payout_missing_factor)
    payout_factor = payout_missing if payout_defaulted else 1.0
    outlier = _finite_factor("outlier_conf_penalty", outlier_conf_penalty)
    corner = _finite_factor("corner_conf_penalty", corner_conf_penalty)
    factors = {
        "tier_cap": tier,
        "payout_factor": payout_factor,
        "outlier_conf_penalty": outlier,
        "corner_conf_penalty": corner,
    }
    v_conf = math.prod(factors.values())
    if not math.isfinite(v_conf) or not 0.0 <= v_conf <= 1.0:
        raise CanonicalizationError("v_conf sozlesme disinda")
    return float(v_conf), factors


def _canonical_pipeline_kwargs(name: str, value: Any, allowed: frozenset[str]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CanonicalizationError(f"{name} mapping olmali")
    out = dict(value)
    if any(not isinstance(k, str) for k in out):
        raise CanonicalizationError(f"{name} anahtarlari string olmali")
    unknown = set(out) - set(allowed)
    if unknown:
        raise CanonicalizationError(f"{name} desteklenmeyen anahtarlar: {sorted(unknown)}")
    return out


def run_bank_valuation(
    canonical: CanonicalBankRow,
    inputs: BankValuationInputs,
    *,
    sector_residual_scales: Sequence[Any] | None = None,
    uncertainty_kwargs: Mapping[str, Any] | None = None,
    valuation_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Üretim için zorunlu iki adımlı çağrı ve güven zinciri."""
    u_kwargs = _canonical_pipeline_kwargs(
        "uncertainty_kwargs", uncertainty_kwargs, UNCERTAINTY_ALLOWED
    )
    v_kwargs = _canonical_pipeline_kwargs(
        "valuation_kwargs", valuation_kwargs, VALUATION_ALLOWED
    )
    tier_cap = _finite_factor("tier_cap", inputs.tier_cap)
    payout_missing_factor = _finite_factor(
        "payout_missing_factor", inputs.payout_missing_factor
    )

    try:
        uncertainty = estimate_roe_uncertainty(
            canonical.roe_series,
            sector_residual_scales=sector_residual_scales,
            **u_kwargs,
        )
    except ValueError as exc:
        raise CanonicalizationError(f"belirsizlik ayarlari/girdisi gecersiz: {exc}") from exc

    base: dict[str, Any] = {
        "ticker": canonical.ticker,
        "analysis_at": canonical.analysis_at,
        "anchor_period_end": canonical.anchor_period_end,
        "selected_version_tag": canonical.selected_version_tag,
        "selected_published_at": canonical.selected_published_at,
        "quarter_slots": list(canonical.quarter_slots),
        "roe_series_canonical": list(canonical.roe_series),
        "selected_versions": list(canonical.selected_version_tags),
        "selected_publication_times": list(canonical.selected_publication_times),
        "roe_missing_count": canonical.roe_missing_count,
        "uncertainty": uncertainty,
        "canonical_bvps": canonical.bvps,
        "canonical_payout_sus": canonical.payout_sus,
    }

    if canonical.bvps is None:
        base.update(status="YETERSIZ_VERI", reason="LATEST_BVPS_MISSING")
        return base

    valuation = bank_valuation(
        bvps=canonical.bvps,
        roe_ttm_series=canonical.roe_series,
        coe=inputs.coe,
        macro_cap=inputs.macro_cap,
        payout_sus=canonical.payout_sus,
        sd_roe=uncertainty["sd_roe_effective"],
        max_halfwidth=inputs.max_halfwidth,
        band_width_shadow_mode=inputs.band_width_shadow_mode,
        **v_kwargs,
    )
    base["valuation"] = valuation
    base["status"] = valuation.get("status", "YETERSIZ_VERI")
    base["reason"] = valuation.get("reason")

    if valuation.get("status") != "OK":
        base["confidence_factors"] = {
            "tier_cap": tier_cap,
            "payout_factor": None,
            "outlier_conf_penalty": float(uncertainty.get("conf_penalty", 1.0)),
            "corner_conf_penalty": None,
        }
        base["v_conf"] = None
        return base

    v_conf, factors = compute_v_conf(
        tier_cap=tier_cap,
        payout_defaulted=valuation["payout_defaulted"],
        payout_missing_factor=payout_missing_factor,
        outlier_conf_penalty=uncertainty.get("conf_penalty", 1.0),
        corner_conf_penalty=valuation.get("corner_conf_penalty", 1.0),
    )
    base["confidence_factors"] = factors
    base["v_conf"] = v_conf
    return base


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(v) for v in value), key=lambda item: repr(item))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def persist_bank_valuation(conn: Any, result: Mapping[str, Any]) -> None:
    """Ara ürünler ve güven çarpanlarıyla idempotent upsert."""
    uncertainty = dict(result.get("uncertainty") or {})
    valuation = dict(result.get("valuation") or {})
    factors = dict(result.get("confidence_factors") or {})
    sql = """
    INSERT INTO analytics.bank_valuation_periods (
      ticker, analysis_at, anchor_period_end,
      assumption_effective_at, assumption_source, coe, macro_cap, risk_free_rate,
      selected_version_tag, selected_published_at,
      quarter_slots, roe_series_canonical, selected_versions,
      selected_publication_times, roe_missing_count,
      trend_slope, sector_sample_size, sector_asof_cutoff,
      sd_roe_floor, floor_source, sd_roe_effective,
      valuation_status, valuation_reason, valuation_method,
      v_low, v_mid, v_high, lower_halfwidth, upper_halfwidth,
      justified_pb, roe_sus, growth_rate,
      implied_payout, payout_gap, would_be_band_too_wide,
      payout_factor, outlier_conf_penalty, corner_conf_penalty,
      tier_cap, v_conf, confidence_factors, diagnostics
    ) VALUES (
      %(ticker)s, %(analysis_at)s, %(anchor_period_end)s,
      %(assumption_effective_at)s, %(assumption_source)s, %(coe)s, %(macro_cap)s, %(risk_free_rate)s,
      %(selected_version_tag)s, %(selected_published_at)s,
      %(quarter_slots)s::jsonb, %(roe_series_canonical)s::jsonb, %(selected_versions)s::jsonb,
      %(selected_publication_times)s::jsonb, %(roe_missing_count)s,
      %(trend_slope)s, %(sector_sample_size)s, %(sector_asof_cutoff)s,
      %(sd_roe_floor)s, %(floor_source)s, %(sd_roe_effective)s,
      %(valuation_status)s, %(valuation_reason)s, %(valuation_method)s,
      %(v_low)s, %(v_mid)s, %(v_high)s, %(lower_halfwidth)s, %(upper_halfwidth)s,
      %(justified_pb)s, %(roe_sus)s, %(growth_rate)s,
      %(implied_payout)s, %(payout_gap)s, %(would_be_band_too_wide)s,
      %(payout_factor)s, %(outlier_conf_penalty)s, %(corner_conf_penalty)s,
      %(tier_cap)s, %(v_conf)s, %(confidence_factors)s::jsonb, %(diagnostics)s::jsonb
    )
    ON CONFLICT (ticker, analysis_at, anchor_period_end)
    DO UPDATE SET
      assumption_effective_at=EXCLUDED.assumption_effective_at,
      assumption_source=EXCLUDED.assumption_source,
      coe=EXCLUDED.coe,
      macro_cap=EXCLUDED.macro_cap,
      risk_free_rate=EXCLUDED.risk_free_rate,
      selected_version_tag=EXCLUDED.selected_version_tag,
      selected_published_at=EXCLUDED.selected_published_at,
      quarter_slots=EXCLUDED.quarter_slots,
      roe_series_canonical=EXCLUDED.roe_series_canonical,
      selected_versions=EXCLUDED.selected_versions,
      selected_publication_times=EXCLUDED.selected_publication_times,
      roe_missing_count=EXCLUDED.roe_missing_count,
      trend_slope=EXCLUDED.trend_slope,
      sector_sample_size=EXCLUDED.sector_sample_size,
      sector_asof_cutoff=EXCLUDED.sector_asof_cutoff,
      sd_roe_floor=EXCLUDED.sd_roe_floor,
      floor_source=EXCLUDED.floor_source,
      sd_roe_effective=EXCLUDED.sd_roe_effective,
      valuation_status=EXCLUDED.valuation_status,
      valuation_reason=EXCLUDED.valuation_reason,
      valuation_method=EXCLUDED.valuation_method,
      v_low=EXCLUDED.v_low,
      v_mid=EXCLUDED.v_mid,
      v_high=EXCLUDED.v_high,
      lower_halfwidth=EXCLUDED.lower_halfwidth,
      upper_halfwidth=EXCLUDED.upper_halfwidth,
      justified_pb=EXCLUDED.justified_pb,
      roe_sus=EXCLUDED.roe_sus,
      growth_rate=EXCLUDED.growth_rate,
      implied_payout=EXCLUDED.implied_payout,
      payout_gap=EXCLUDED.payout_gap,
      would_be_band_too_wide=EXCLUDED.would_be_band_too_wide,
      payout_factor=EXCLUDED.payout_factor,
      outlier_conf_penalty=EXCLUDED.outlier_conf_penalty,
      corner_conf_penalty=EXCLUDED.corner_conf_penalty,
      tier_cap=EXCLUDED.tier_cap,
      v_conf=EXCLUDED.v_conf,
      confidence_factors=EXCLUDED.confidence_factors,
      diagnostics=EXCLUDED.diagnostics,
      created_at=now()
    """
    assumption = dict(result.get("assumption") or {})
    params = {
        "ticker": result["ticker"],
        "analysis_at": result["analysis_at"],
        "anchor_period_end": result["anchor_period_end"],
        "assumption_effective_at": assumption.get("effective_at"),
        "assumption_source": assumption.get("source"),
        "coe": assumption.get("coe"),
        "macro_cap": assumption.get("macro_cap"),
        "risk_free_rate": assumption.get("risk_free_rate"),
        "selected_version_tag": result.get("selected_version_tag"),
        "selected_published_at": result.get("selected_published_at"),
        "quarter_slots": json.dumps(_json_safe(result.get("quarter_slots"))),
        "roe_series_canonical": json.dumps(_json_safe(result.get("roe_series_canonical"))),
        "selected_versions": json.dumps(_json_safe(result.get("selected_versions"))),
        "selected_publication_times": json.dumps(_json_safe(result.get("selected_publication_times"))),
        "roe_missing_count": result.get("roe_missing_count", 0),
        "trend_slope": uncertainty.get("trend_slope"),
        "sector_sample_size": result.get("sector_sample_size"),
        "sector_asof_cutoff": result.get("sector_asof_cutoff"),
        "sd_roe_floor": uncertainty.get("sd_roe_floor"),
        "floor_source": uncertainty.get("floor_source"),
        "sd_roe_effective": uncertainty.get("sd_roe_effective"),
        "valuation_status": result.get("status", "YETERSIZ_VERI"),
        "valuation_reason": result.get("reason"),
        "valuation_method": valuation.get("method"),
        "v_low": valuation.get("V_low"),
        "v_mid": valuation.get("V_mid"),
        "v_high": valuation.get("V_high"),
        "lower_halfwidth": valuation.get("lower_halfwidth"),
        "upper_halfwidth": valuation.get("upper_halfwidth"),
        "justified_pb": None,
        "roe_sus": valuation.get("roe_sus"),
        "growth_rate": valuation.get("g"),
        "implied_payout": valuation.get("implied_payout"),
        "payout_gap": valuation.get("payout_gap"),
        "would_be_band_too_wide": valuation.get("would_be_band_too_wide"),
        "payout_factor": factors.get("payout_factor"),
        "outlier_conf_penalty": factors.get("outlier_conf_penalty"),
        "corner_conf_penalty": factors.get("corner_conf_penalty"),
        "tier_cap": factors.get("tier_cap"),
        "v_conf": result.get("v_conf"),
        "confidence_factors": json.dumps(_json_safe(factors)),
        "diagnostics": json.dumps(_json_safe(result)),
    }
    # justified_pb = V_mid / BVPS; BVPS diagnostics içinde tutulur.
    bvps = result.get("canonical_bvps")
    if bvps is not None and valuation.get("V_mid") is not None:
        params["justified_pb"] = valuation["V_mid"] / bvps
    with conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def compute_sector_residual_scales(
    tickers: Iterable[str],
    row_provider: Any,
    *,
    analysis_at: datetime,
    anchor_period_end: date,
    target_ticker: str | None = None,
    leave_one_out: bool = True,
) -> dict[str, Any]:
    """Aynı analysis_at anında bilinen bankalardan artık-ölçek dağılımı kurar.

    `row_provider(ticker)` üretim SQL'inin sekiz yuvalı çıktısını vermelidir.
    Hedef banka varsayılan olarak leave-one-out ile dağılımdan çıkarılır.
    """
    analysis = _as_aware_datetime("analysis_at", analysis_at)
    anchor = _quarter_end(_as_date("anchor_period_end", anchor_period_end))
    if type(leave_one_out) is not bool:
        raise CanonicalizationError("leave_one_out Python bool olmali")
    target = target_ticker.strip().upper() if isinstance(target_ticker, str) else None

    ordered_tickers: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        if not isinstance(raw, str) or not raw.strip():
            raise CanonicalizationError("sector ticker degerleri bos olmayan metin olmali")
        ticker = raw.strip().upper()
        if ticker not in seen:
            seen.add(ticker)
            ordered_tickers.append(ticker)

    scales: list[float] = []
    included: list[str] = []
    rejected: dict[str, str] = {}
    excluded: list[str] = []
    for ticker in ordered_tickers:
        if leave_one_out and target is not None and ticker == target:
            excluded.append(ticker)
            continue
        try:
            canonical = to_canonical_row(
                row_provider(ticker),
                ticker=ticker,
                analysis_at=analysis,
                anchor_period_end=anchor,
            )
            u = estimate_roe_uncertainty(canonical.roe_series)
            scale = u.get("sd_roe_residual")
            if u.get("n_valid", 0) < 4 or scale is None:
                rejected[ticker] = "INSUFFICIENT_ROE_HISTORY"
                continue
            scale_f = coerce_finite_number(
                f"sector_scale[{ticker}]", scale, minimum=0.0
            )
        except (CanonicalizationError, ValueError) as exc:
            rejected[ticker] = str(exc)
            continue
        scales.append(scale_f)
        included.append(ticker)

    return {
        "scales": scales,
        "sample_size": len(scales),
        "included_tickers": included,
        "excluded_tickers": excluded,
        "rejected_tickers": rejected,
        "leave_one_out": leave_one_out,
        "sector_asof_cutoff": analysis,
    }


def fetch_point_in_time_bank_tickers(
    conn: Any,
    *,
    analysis_at: datetime,
    anchor_period_end: date,
) -> list[str]:
    analysis = _as_aware_datetime("analysis_at", analysis_at)
    slots = build_quarter_slots(_as_date("anchor_period_end", anchor_period_end))
    sql = """
    SELECT DISTINCT ticker
    FROM core.bank_metrics_quarterly
    WHERE published_at <= %(analysis_at)s::timestamptz
      AND period_end BETWEEN %(oldest_period)s AND %(anchor_period)s
    ORDER BY ticker
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            {
                "analysis_at": analysis,
                "oldest_period": slots[0],
                "anchor_period": slots[-1],
            },
        )
        return [str(row[0]).strip().upper() for row in cur.fetchall()]


def build_point_in_time_sector_scales(
    conn: Any,
    *,
    analysis_at: datetime,
    anchor_period_end: date,
    target_ticker: str,
    leave_one_out: bool = True,
) -> dict[str, Any]:
    tickers = fetch_point_in_time_bank_tickers(
        conn,
        analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
    )

    def provider(ticker: str) -> list[dict[str, Any]]:
        return fetch_bank_quarter_slots(
            conn,
            ticker=ticker,
            analysis_at=analysis_at,
            anchor_period_end=anchor_period_end,
        )

    return compute_sector_residual_scales(
        tickers,
        provider,
        analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
        target_ticker=target_ticker,
        leave_one_out=leave_one_out,
    )


def run_bank_valuation_for_ticker(
    conn: Any,
    *,
    ticker: str,
    analysis_at: datetime,
    anchor_period_end: date,
    inputs: BankValuationInputs,
    leave_one_out: bool = True,
    persist: bool = True,
    uncertainty_kwargs: Mapping[str, Any] | None = None,
    valuation_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Tek banka için sorgu → kanonikleştirme → sektör tabanı → motor → kayıt."""
    rows = fetch_bank_quarter_slots(
        conn,
        ticker=ticker,
        analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
    )
    canonical = to_canonical_row(
        rows,
        ticker=ticker,
        analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
    )
    sector = build_point_in_time_sector_scales(
        conn,
        analysis_at=analysis_at,
        anchor_period_end=anchor_period_end,
        target_ticker=canonical.ticker,
        leave_one_out=leave_one_out,
    )
    result = run_bank_valuation(
        canonical,
        inputs,
        sector_residual_scales=sector["scales"],
        uncertainty_kwargs=uncertainty_kwargs,
        valuation_kwargs=valuation_kwargs,
    )
    result["sector_sample_size"] = sector["sample_size"]
    result["sector_asof_cutoff"] = sector["sector_asof_cutoff"]
    result["sector_leave_one_out"] = sector["leave_one_out"]
    result["sector_included_tickers"] = sector["included_tickers"]
    result["sector_excluded_tickers"] = sector["excluded_tickers"]
    result["sector_rejected_tickers"] = sector["rejected_tickers"]
    if persist:
        persist_bank_valuation(conn, result)
    return result
