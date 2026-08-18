from __future__ import annotations

"""Database-free RSC replay over an already PIT-safe ratio foundation.

Historical replay must never call the production RSC path that reads the whole
``analytics.ratios_quarterly`` table.  This module therefore has no database
connection argument.  Its only financial input is
``HistoricalPitRatioReplayResult.combined_ratios`` produced by the PIT CORE+VAL
foundation for the same historical ``analysis_at``.
"""

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Callable, Mapping

import pandas as pd

from src.analytics.historical_pit_ratio_replay import HistoricalPitRatioReplayResult
from src.analytics.rsc_scoring import load_ratio_meta, load_sector_config, score_quarter
from src.ingest.sector_routing import SUPPORTED_SECTOR_FAMILIES


class HistoricalPitRscReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitRscReplayResult:
    analysis_at: datetime
    tickers: tuple[str, ...]
    ratio_scores: pd.DataFrame
    rsc_summary: pd.DataFrame


def _strict_text(name: str, value: object, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitRscReplayError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _strict_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if type(value).__name__ == "bool_" and str(value) in {"True", "False"}:
        return str(value) == "True"
    raise HistoricalPitRscReplayError(f"is_na Python/numpy bool olmali: {value!r}")


def _normalized_routing(routing: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(routing, Mapping) or not routing:
        raise HistoricalPitRscReplayError("routing bos olmayan mapping olmali")
    out: dict[str, str] = {}
    for raw_ticker, raw_family in routing.items():
        ticker = _strict_text("routing ticker", raw_ticker, uppercase=True)
        if ticker in out:
            raise HistoricalPitRscReplayError(f"duplicate normalized routing ticker: {ticker}")
        family = _strict_text(f"routing.{ticker}", raw_family, uppercase=True)
        if family not in SUPPORTED_SECTOR_FAMILIES:
            raise HistoricalPitRscReplayError(
                f"routing.{ticker} desteklenmeyen sektor ailesi: {family}"
            )
        out[ticker] = family
    return out


def _validate_input_ratios(
    frame: pd.DataFrame,
    *,
    analysis_at: datetime,
    tickers: tuple[str, ...],
    known_ratios: set[str],
) -> pd.DataFrame:
    required = {"ticker", "period_end", "version_tag", "ratio_name", "ratio_value", "is_na"}
    if frame is None:
        raise HistoricalPitRscReplayError("combined_ratios None olamaz")
    missing = required - set(frame.columns)
    if missing:
        raise HistoricalPitRscReplayError(f"combined_ratios missing columns: {sorted(missing)}")

    out = frame.copy(deep=True)
    if out.empty:
        return out

    normalized_tickers = []
    for value in out["ticker"]:
        normalized_tickers.append(_strict_text("ratio ticker", value, uppercase=True))
    out["ticker"] = normalized_tickers
    foreign = sorted(set(out["ticker"]) - set(tickers))
    if foreign:
        raise HistoricalPitRscReplayError(f"PIT ratio routing disi ticker iceriyor: {foreign}")

    names = []
    for value in out["ratio_name"]:
        names.append(_strict_text("ratio_name", value))
    out["ratio_name"] = names
    unknown = sorted(set(names) - known_ratios)
    if unknown:
        raise HistoricalPitRscReplayError(f"PIT RSC bilinmeyen oran iceriyor: {unknown}")

    versions = []
    for value in out["version_tag"]:
        versions.append(_strict_text("version_tag", value))
    out["version_tag"] = versions

    try:
        out["period_end"] = pd.to_datetime(out["period_end"], errors="raise").dt.normalize()
    except Exception as exc:
        raise HistoricalPitRscReplayError("period_end gecersiz") from exc
    if out["period_end"].isna().any():
        raise HistoricalPitRscReplayError("period_end bos olamaz")

    analysis_day = pd.Timestamp(analysis_at).tz_convert("UTC").tz_localize(None).normalize()
    future = out.loc[out["period_end"] > analysis_day, ["ticker", "period_end"]]
    if not future.empty:
        first = future.iloc[0]
        raise HistoricalPitRscReplayError(
            f"analysis_at sonrasi period_end PIT RSC'ye sizdi: {first['ticker']} {first['period_end'].date()}"
        )

    out["is_na"] = out["is_na"].map(_strict_bool)
    numeric = pd.to_numeric(out["ratio_value"], errors="coerce")
    invalid_present = (~out["is_na"]) & numeric.map(
        lambda x: pd.notna(x) and not math.isfinite(float(x))
    )
    missing_present = (~out["is_na"]) & numeric.isna()
    if invalid_present.any() or missing_present.any():
        raise HistoricalPitRscReplayError("is_na=False satirinda sonlu ratio_value zorunlu")
    out["ratio_value"] = numeric

    if out.duplicated(["ticker", "period_end", "version_tag", "ratio_name"]).any():
        raise HistoricalPitRscReplayError("combined_ratios duplicate ratio key iceriyor")
    return out


def _validate_output(
    scores: pd.DataFrame,
    summary: pd.DataFrame,
    *,
    tickers: tuple[str, ...],
    input_groups: set[tuple[pd.Timestamp, str]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    score_required = {
        "ticker", "period_end", "version_tag", "ratio_name", "pillar",
        "score_1_10", "level_percentile", "trend_bonus", "is_na",
    }
    summary_required = {
        "ticker", "period_end", "version_tag", "rsc_core_norm", "rsc_val_norm",
        "good_count_ge8", "score_mean", "score_std",
    }
    if scores is None or summary is None:
        raise HistoricalPitRscReplayError("RSC scorer None donemez")
    for frame, required, name in (
        (scores, score_required, "ratio_scores"),
        (summary, summary_required, "rsc_summary"),
    ):
        missing = required - set(frame.columns)
        if missing:
            raise HistoricalPitRscReplayError(f"{name} missing columns: {sorted(missing)}")

    scores = scores.copy(deep=True)
    summary = summary.copy(deep=True)
    for frame, name in ((scores, "ratio_scores"), (summary, "rsc_summary")):
        if frame.empty:
            continue
        frame["ticker"] = frame["ticker"].map(lambda x: _strict_text(f"{name}.ticker", x, uppercase=True))
        foreign = sorted(set(frame["ticker"]) - set(tickers))
        if foreign:
            raise HistoricalPitRscReplayError(f"{name} routing disi ticker uretti: {foreign}")
        frame["period_end"] = pd.to_datetime(frame["period_end"], errors="raise").dt.normalize()
        frame["version_tag"] = frame["version_tag"].map(lambda x: _strict_text(f"{name}.version_tag", x))
        groups = set(zip(frame["period_end"], frame["version_tag"]))
        extra_groups = groups - input_groups
        if extra_groups:
            raise HistoricalPitRscReplayError(
                f"{name} PIT inputunda olmayan period/version uretti: {sorted(extra_groups)!r}"
            )

    if scores.duplicated(["ticker", "period_end", "version_tag", "ratio_name"]).any():
        raise HistoricalPitRscReplayError("ratio_scores duplicate key uretti")
    if summary.duplicated(["ticker", "period_end", "version_tag"]).any():
        raise HistoricalPitRscReplayError("rsc_summary duplicate key uretti")

    return (
        scores.sort_values(["period_end", "version_tag", "ticker", "ratio_name"]).reset_index(drop=True),
        summary.sort_values(["period_end", "version_tag", "ticker"]).reset_index(drop=True),
    )


def run_historical_pit_rsc_replay(
    ratio_replay: HistoricalPitRatioReplayResult,
    *,
    routing: Mapping[str, str],
    ratios_json_path: str,
    sectors_json_path: str,
    winsor_p: float = 0.01,
    min_group_size: int = 5,
    scorer: Callable[..., tuple[pd.DataFrame, pd.DataFrame]] = score_quarter,
) -> HistoricalPitRscReplayResult:
    """Score PIT CORE+VAL ratios without any database read path."""

    if not isinstance(ratio_replay, HistoricalPitRatioReplayResult):
        raise HistoricalPitRscReplayError("ratio_replay HistoricalPitRatioReplayResult olmali")
    analysis_at = ratio_replay.analysis_at
    if not isinstance(analysis_at, datetime) or analysis_at.tzinfo is None or analysis_at.utcoffset() is None:
        raise HistoricalPitRscReplayError("ratio_replay analysis_at timezone-aware olmali")
    if not isinstance(winsor_p, (int, float)) or isinstance(winsor_p, bool) or not 0 <= float(winsor_p) < 0.5:
        raise HistoricalPitRscReplayError("winsor_p [0,0.5) araliginda sayi olmali")
    if isinstance(min_group_size, bool) or not isinstance(min_group_size, int) or min_group_size < 1:
        raise HistoricalPitRscReplayError("min_group_size pozitif int olmali")
    ratios_path = _strict_text("ratios_json_path", ratios_json_path)
    sectors_path = _strict_text("sectors_json_path", sectors_json_path)

    normalized = _normalized_routing(routing)
    if tuple(sorted(normalized)) != tuple(ratio_replay.tickers):
        raise HistoricalPitRscReplayError(
            "RSC routing ticker seti PIT ratio foundation ile birebir ayni olmali"
        )

    try:
        meta = load_ratio_meta(ratios_path)
        _, policies = load_sector_config(sectors_path)
    except Exception as exc:
        raise HistoricalPitRscReplayError("RSC config okunamadi") from exc
    if not meta:
        raise HistoricalPitRscReplayError("ratios config bos olamaz")
    if not policies:
        raise HistoricalPitRscReplayError("sector_policies bos olamaz")

    ratios = _validate_input_ratios(
        ratio_replay.combined_ratios,
        analysis_at=analysis_at,
        tickers=ratio_replay.tickers,
        known_ratios=set(meta),
    )
    input_groups = set(zip(ratios["period_end"], ratios["version_tag"])) if not ratios.empty else set()

    scores, summary = scorer(
        ratios,
        ratios_json_path=ratios_path,
        winsor_p=float(winsor_p),
        sector_group_map=normalized,
        sector_policies=policies,
        min_group_size=min_group_size,
    )
    scores, summary = _validate_output(
        scores,
        summary,
        tickers=ratio_replay.tickers,
        input_groups=input_groups,
    )
    return HistoricalPitRscReplayResult(
        analysis_at=analysis_at,
        tickers=ratio_replay.tickers,
        ratio_scores=scores,
        rsc_summary=summary,
    )
