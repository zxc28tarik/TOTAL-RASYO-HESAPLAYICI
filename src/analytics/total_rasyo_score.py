from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

MODULE_KEYS = ("M2", "M1", "M3", "Ek4", "Ek1", "Ek9")
DEFAULT_WEIGHTS = {
    "M2": 0.40,
    "M1": 0.18,
    "M3": 0.12,
    "Ek4": 0.16,
    "Ek1": 0.08,
    "Ek9": 0.06,
}


class TotalRasyoScoreError(ValueError):
    pass


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


def _stable_keys(values: set[Any]) -> list[str]:
    return sorted((repr(value) for value in values))


def _finite_number(name: str, value: Any, *, minimum: float, maximum: float) -> float:
    if _is_bool_like(value):
        raise TotalRasyoScoreError(f"{name} bool olamaz")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TotalRasyoScoreError(f"{name} sayiya cevrilemedi") from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise TotalRasyoScoreError(f"{name} [{minimum}, {maximum}] araliginda sonlu olmali")
    return result


def normalize_weights(weights: Mapping[str, Any] | None = None) -> dict[str, float]:
    raw = DEFAULT_WEIGHTS if weights is None else weights
    if not isinstance(raw, Mapping):
        raise TotalRasyoScoreError("weights mapping olmali")
    unknown = set(raw) - set(MODULE_KEYS)
    missing = set(MODULE_KEYS) - set(raw)
    if unknown or missing:
        raise TotalRasyoScoreError(
            f"weights anahtarlari tam olmali; eksik={_stable_keys(missing)}, fazla={_stable_keys(unknown)}"
        )
    parsed = {
        key: _finite_number(f"weights.{key}", raw[key], minimum=0.0, maximum=1.0)
        for key in MODULE_KEYS
    }
    total = math.fsum(parsed.values())
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise TotalRasyoScoreError(f"weights toplami 1 olmali; gelen={total}")
    return parsed


def total_rasyo_decision(final_score: Any) -> str:
    score = _finite_number("final_score", final_score, minimum=0.0, maximum=1.0)
    if score >= 0.70:
        return "AL"
    if score >= 0.55:
        return "IZLE"
    return "UZAK"


def compute_total_rasyo(
    module_scores: Mapping[str, Any],
    *,
    good_count_ge8: Any,
    weights: Mapping[str, Any] | None = None,
    veto_threshold: int = 5,
    veto_factor: Any = 0.60,
) -> dict[str, Any]:
    if not isinstance(module_scores, Mapping):
        raise TotalRasyoScoreError("module_scores mapping olmali")
    unknown = set(module_scores) - set(MODULE_KEYS)
    missing = set(MODULE_KEYS) - set(module_scores)
    if unknown or missing:
        raise TotalRasyoScoreError(
            f"module_scores anahtarlari tam olmali; eksik={_stable_keys(missing)}, fazla={_stable_keys(unknown)}"
        )
    parsed_scores = {
        key: _finite_number(f"module_scores.{key}", module_scores[key], minimum=0.0, maximum=1.0)
        for key in MODULE_KEYS
    }
    parsed_weights = normalize_weights(weights)
    if _is_bool_like(good_count_ge8):
        raise TotalRasyoScoreError("good_count_ge8 bool olamaz")
    try:
        good_count = int(good_count_ge8)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TotalRasyoScoreError("good_count_ge8 tam sayi olmali") from exc
    try:
        if float(good_count_ge8) != float(good_count):
            raise TotalRasyoScoreError("good_count_ge8 tam sayi olmali")
    except (TypeError, ValueError, OverflowError) as exc:
        raise TotalRasyoScoreError("good_count_ge8 tam sayi olmali") from exc
    if good_count < 0:
        raise TotalRasyoScoreError("good_count_ge8 negatif olamaz")
    if isinstance(veto_threshold, bool) or not isinstance(veto_threshold, int) or veto_threshold < 0:
        raise TotalRasyoScoreError("veto_threshold negatif olmayan Python int olmali")
    factor = _finite_number("veto_factor", veto_factor, minimum=0.0, maximum=1.0)

    contributions = {
        key: parsed_scores[key] * parsed_weights[key]
        for key in MODULE_KEYS
    }
    base_score = math.fsum(contributions.values())
    base_score = min(max(base_score, 0.0), 1.0)
    veto_flag = good_count < veto_threshold
    final_score = base_score * factor if veto_flag else base_score
    final_score = min(max(final_score, 0.0), 1.0)
    return {
        "module_scores": parsed_scores,
        "weights": parsed_weights,
        "contributions": contributions,
        "base_score": float(base_score),
        "good_count_ge8": good_count,
        "veto_threshold": veto_threshold,
        "veto_factor": factor,
        "veto_flag": veto_flag,
        "final_score": float(final_score),
        "total_rasyo_100": float(final_score * 100.0),
        "decision": total_rasyo_decision(final_score),
    }
