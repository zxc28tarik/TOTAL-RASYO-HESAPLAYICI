from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from .bank_valuation_pipeline import CanonicalizationError, _as_aware_datetime
from .bank_v47.roe_uncertainty import coerce_finite_number, is_bool_like, is_missing_like


DEFAULT_THRESHOLDS = (0.80, 0.90, 1.00)


def canonical_thresholds(values: Iterable[Any]) -> tuple[float, ...]:
    result: list[float] = []
    for index, value in enumerate(values):
        try:
            threshold = coerce_finite_number(
                f"threshold[{index}]", value, minimum=0.0, strict_minimum=True
            )
        except ValueError as exc:
            raise CanonicalizationError(str(exc)) from exc
        if threshold not in result:
            result.append(threshold)
    if not result:
        raise CanonicalizationError("en az bir shadow threshold gerekli")
    ordered = tuple(sorted(result))
    labels = [f"{value:.2f}" for value in ordered]
    if len(labels) != len(set(labels)):
        raise CanonicalizationError(
            "shadow threshold degerleri iki ondalik rapor etiketinde cakismamali"
        )
    return ordered


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _canonical_numeric_column(name: str, series: pd.Series) -> pd.Series:
    converted: list[float | None] = []
    for index, value in series.items():
        if is_missing_like(value):
            converted.append(None)
            continue
        if is_bool_like(value):
            raise CanonicalizationError(f"shadow report {name}[{index}] bool olamaz")
        try:
            number = coerce_finite_number(f"{name}[{index}]", value)
        except ValueError as exc:
            raise CanonicalizationError(f"shadow report {exc}") from exc
        converted.append(number)
    return pd.Series(converted, index=series.index, dtype="float64")


def _quantile(series: pd.Series, q: float) -> float | None:
    values = _numeric(series).dropna()
    if values.empty:
        return None
    return float(values.quantile(q))


def _rate(count: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return float(count / denominator)


def build_bank_shadow_distribution(
    data: pd.DataFrame,
    *,
    thresholds: Sequence[Any] = DEFAULT_THRESHOLDS,
) -> pd.DataFrame:
    """Build sector x anchor-period diagnostics without turning on a hard gate."""
    threshold_values = canonical_thresholds(thresholds)
    required = {
        "ticker", "sector_code", "anchor_period_end", "valuation_status",
        "lower_halfwidth", "upper_halfwidth", "floor_source",
        "sd_roe_floor", "sd_roe_effective",
        "justified_pb", "roe_sus", "outlier_conf_penalty",
        "v_mid", "current_price", "z_val", "s_valuation", "v_conf",
        "coe", "macro_cap", "risk_free_rate",
    }
    missing = sorted(required - set(data.columns))
    if missing:
        raise CanonicalizationError(f"shadow report eksik kolonlar: {missing}")
    if data.empty:
        return pd.DataFrame()

    df = data.copy()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["sector_code"] = df["sector_code"].fillna("BANK").astype(str).str.strip().str.upper()
    df["anchor_period_end"] = pd.to_datetime(df["anchor_period_end"], errors="coerce").dt.date
    if df["anchor_period_end"].isna().any():
        raise CanonicalizationError("shadow report anchor_period_end gecersiz")
    for column in (
        "lower_halfwidth", "upper_halfwidth", "sd_roe_floor", "sd_roe_effective",
        "justified_pb", "roe_sus",
        "outlier_conf_penalty", "v_mid", "current_price", "z_val",
        "s_valuation", "v_conf",
        "coe", "macro_cap", "risk_free_rate",
    ):
        df[column] = _canonical_numeric_column(column, df[column])

    duplicate_mask = df.duplicated(["ticker", "anchor_period_end"], keep=False)
    if duplicate_mask.any():
        duplicate_keys = (
            df.loc[duplicate_mask, ["ticker", "anchor_period_end"]]
            .drop_duplicates()
            .astype(str)
            .agg("/".join, axis=1)
            .tolist()
        )
        raise CanonicalizationError(
            f"shadow report ayni ticker/donem icin birden fazla satir aldi: {duplicate_keys[:5]}"
        )

    rows: list[dict[str, Any]] = []
    for (sector, period), group in df.groupby(["sector_code", "anchor_period_end"], sort=True):
        usable = group[group["valuation_status"].astype(str) == "OK"].copy()
        usable_count = int(usable.shape[0])
        total_count = int(group.shape[0])
        floor_binding = (
            usable["sd_roe_floor"].notna()
            & usable["sd_roe_effective"].notna()
            & np.isclose(
                usable["sd_roe_effective"], usable["sd_roe_floor"],
                rtol=1e-9, atol=1e-12,
            )
        )
        outlier = usable["outlier_conf_penalty"].notna() & (usable["outlier_conf_penalty"] < 1.0 - 1e-12)
        sat_zero = usable["s_valuation"].notna() & np.isclose(usable["s_valuation"], 0.0, atol=1e-12)
        sat_one = usable["s_valuation"].notna() & np.isclose(usable["s_valuation"], 1.0, atol=1e-12)
        price_to_mid = usable["current_price"] / usable["v_mid"]
        price_to_mid = price_to_mid.where((usable["current_price"] > 0) & (usable["v_mid"] > 0))
        max_halfwidth = pd.concat(
            [usable["lower_halfwidth"], usable["upper_halfwidth"]], axis=1
        ).max(axis=1, skipna=False)

        row: dict[str, Any] = {
            "sector_code": sector,
            "anchor_period_end": period,
            "company_type": "BANK",
            "total_count": total_count,
            "valuation_usable_count": usable_count,
            "valuation_usable_rate": _rate(usable_count, total_count),
            "floor_binding_count": int(floor_binding.sum()),
            "floor_binding_rate": _rate(int(floor_binding.sum()), usable_count),
            "outlier_count": int(outlier.sum()),
            "outlier_rate": _rate(int(outlier.sum()), usable_count),
            "saturation_zero_count": int(sat_zero.sum()),
            "saturation_zero_rate": _rate(int(sat_zero.sum()), usable_count),
            "saturation_one_count": int(sat_one.sum()),
            "saturation_one_rate": _rate(int(sat_one.sum()), usable_count),
            "justified_pb_p10": _quantile(usable["justified_pb"], 0.10),
            "justified_pb_p50": _quantile(usable["justified_pb"], 0.50),
            "justified_pb_p90": _quantile(usable["justified_pb"], 0.90),
            "z_val_p10": _quantile(usable["z_val"], 0.10),
            "z_val_p50": _quantile(usable["z_val"], 0.50),
            "z_val_p90": _quantile(usable["z_val"], 0.90),
            "roe_sus_p10": _quantile(usable["roe_sus"], 0.10),
            "roe_sus_p50": _quantile(usable["roe_sus"], 0.50),
            "roe_sus_p90": _quantile(usable["roe_sus"], 0.90),
            "coe_p10": _quantile(usable["coe"], 0.10),
            "coe_p50": _quantile(usable["coe"], 0.50),
            "coe_p90": _quantile(usable["coe"], 0.90),
            "macro_cap_p10": _quantile(usable["macro_cap"], 0.10),
            "macro_cap_p50": _quantile(usable["macro_cap"], 0.50),
            "macro_cap_p90": _quantile(usable["macro_cap"], 0.90),
            "risk_free_rate_p10": _quantile(usable["risk_free_rate"], 0.10),
            "risk_free_rate_p50": _quantile(usable["risk_free_rate"], 0.50),
            "risk_free_rate_p90": _quantile(usable["risk_free_rate"], 0.90),
            "v_conf_p10": _quantile(usable["v_conf"], 0.10),
            "v_conf_p50": _quantile(usable["v_conf"], 0.50),
            "v_conf_p90": _quantile(usable["v_conf"], 0.90),
            "price_to_v_mid_p10": _quantile(price_to_mid, 0.10),
            "price_to_v_mid_p50": _quantile(price_to_mid, 0.50),
            "price_to_v_mid_p90": _quantile(price_to_mid, 0.90),
        }
        for threshold in threshold_values:
            label = f"{threshold:.2f}".replace(".", "_")
            rejected = int((max_halfwidth > threshold + 1e-12).sum())
            row[f"reject_{label}_count"] = rejected
            row[f"reject_{label}_rate"] = _rate(rejected, usable_count)
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["sector_code", "anchor_period_end"]).reset_index(drop=True)


def load_bank_shadow_inputs(conn: Any, *, analysis_at: datetime) -> pd.DataFrame:
    cutoff = _as_aware_datetime("analysis_at", analysis_at)
    return pd.read_sql(
        """
        WITH valuations AS (
          SELECT DISTINCT ON (v.ticker, v.anchor_period_end)
                 v.ticker, v.analysis_at, v.anchor_period_end,
                 v.valuation_status, v.lower_halfwidth, v.upper_halfwidth,
                 v.floor_source, v.sd_roe_floor, v.sd_roe_effective,
                 v.justified_pb, v.roe_sus,
                 v.outlier_conf_penalty, v.v_mid, v.v_conf,
                 v.coe, v.macro_cap, v.risk_free_rate
          FROM analytics.bank_valuation_periods v
          WHERE v.analysis_at <= %(analysis_at)s
          ORDER BY v.ticker, v.anchor_period_end, v.analysis_at DESC
        )
        SELECT v.ticker,
               COALESCE(u.sector_code, 'BANK') AS sector_code,
               v.analysis_at, v.anchor_period_end,
               v.valuation_status, v.lower_halfwidth, v.upper_halfwidth,
               v.floor_source, v.sd_roe_floor, v.sd_roe_effective,
               v.justified_pb, v.roe_sus,
               v.outlier_conf_penalty, v.v_mid, v.v_conf,
               v.coe, v.macro_cap, v.risk_free_rate,
               m.current_price, m.z_val, m.s_valuation
        FROM valuations v
        LEFT JOIN core.universe_stocks u USING (ticker)
        LEFT JOIN analytics.bank_m2_scores m
          ON m.ticker = v.ticker
         AND m.analysis_at = v.analysis_at
         AND m.anchor_period_end = v.anchor_period_end
        ORDER BY sector_code, v.anchor_period_end, v.ticker
        """,
        conn,
        params={"analysis_at": cutoff},
    )


def run_bank_shadow_report(
    conn: Any,
    *,
    analysis_at: datetime,
    thresholds: Sequence[Any] = DEFAULT_THRESHOLDS,
    output_path: str | Path | None = None,
) -> pd.DataFrame:
    inputs = load_bank_shadow_inputs(conn, analysis_at=analysis_at)
    report = build_bank_shadow_distribution(inputs, thresholds=thresholds)
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".json":
            path.write_text(
                json.dumps(report.to_dict(orient="records"), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        else:
            report.to_csv(path, index=False)
    return report
